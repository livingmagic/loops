import { useEffect, useRef, useState } from 'react';
import { Gear, Cube, Trash, X, Plus } from '@phosphor-icons/react';
import { PathLink } from './PathLink.jsx';
import { request } from './api.js';

function RecycleBin({ act }) {
  const [items, setItems] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState('');
  useEffect(() => { Promise.all([request('trash'), request('automation_trash')]).then(([loops, tasks]) => setItems([...loops.map(item => ({ ...item, kind: 'loop' })), ...tasks.map(item => ({ ...item, kind: 'task' }))])).catch(e => setError(e.message)); }, []);
  return <>{error && <p role="alert" className="form-error">{error}</p>}{items === null ? <p>{error ? '加载失败' : '正在加载…'}</p> : !items.length ? <p className="empty-copy">回收站为空</p> : <div className="settings-card">{items.map(item => <div className="settings-row" key={`${item.kind}:${item.id}`}><div><strong>{item.title}</strong><small>{item.kind === 'loop' ? 'Loop' : '自动化任务'}</small></div><button className="button button-secondary" disabled={Boolean(busy)} onClick={async () => { setBusy(item.id); setError(''); try { await act(item.kind === 'loop' ? 'restore' : 'automation_restore', item.kind === 'loop' ? { loopId: item.id } : { taskId: item.id }); setItems(items.filter(entry => entry !== item)); } catch(e) { setError(e.message); } finally { setBusy(''); } }}>恢复</button></div>)}</div>}</>;
}

function ModelForm({ item, settings, onSave, onCancel }) {
  const [provider, setProvider] = useState(item?.provider || '');
  const [baseUrl, setBaseUrl] = useState(item?.baseUrl || '');
  const existingService = settings.services?.some(entry => entry.configured && entry.provider === provider && (provider !== 'compatible' || entry.baseUrl === baseUrl.trim().replace(/\/$/, '')));
  const [working, setWorking] = useState(false), [testing, setTesting] = useState(false), [error, setError] = useState(''), [tested, setTested] = useState(false);
  const form = useRef(null);
  const selectedProvider = settings.providers?.find(entry => entry.id === provider);
  const catalog = selectedProvider?.models || [];
  function values() {
    const data = new FormData(form.current);
    return { id: item?.id, provider, name: data.get('name')?.trim() || data.get('model'), model: data.get('model'), baseUrl: data.get('baseUrl'), apiKey: data.get('apiKey') };
  }
  async function testConnection() {
    if (!form.current.reportValidity()) return;
    setTesting(true); setError(''); setTested(false);
    try { await request('model_test', values()); setTested(true); }
    catch (e) { setError(e.message); }
    finally { setTesting(false); }
  }
  return <form className="settings-card model-form" ref={form} onChange={() => { setTested(false); setError(''); }} onSubmit={async event => {
    event.preventDefault(); if (testing || working) return; const params = values(); setWorking(true); setError('');
    form.current.elements.apiKey.value = '';
    try { await onSave(params); }
    catch(e) { setError(e.message); } finally { setWorking(false); }
  }}>
    <fieldset disabled={working || testing} style={{ border: 0, padding: 0, margin: 0, minWidth: 0, display: 'contents' }}>
    <label>服务<select required value={provider} onChange={e => setProvider(e.target.value)}><option value="" disabled>请选择服务</option>{settings.providers?.map(entry => <option value={entry.id} key={entry.id}>{entry.name}</option>)}<option value="compatible">OpenAI 兼容服务</option></select></label>
    {provider && <>
      {selectedProvider?.description && <small className="provider-note">{selectedProvider.description}</small>}
      <label>模型{provider === 'compatible' ? <input key={provider} name="model" required defaultValue={item?.provider === provider ? item.model : ''} placeholder="服务提供的模型 ID" /> : <select key={provider} name="model" defaultValue={item?.provider === provider ? item.model : catalog[0]?.id}>{catalog.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}</select>}</label>
      {provider === 'compatible' && <label>API 地址<input name="baseUrl" required type="url" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" /></label>}
      <label>名称<input name="name" maxLength={80} defaultValue={item?.name || ''} placeholder="留空使用模型 ID" /></label>
      <label>API Key<input name="apiKey" type="password" autoComplete="off" required={!existingService} placeholder={existingService ? '已配置，留空使用此服务的已有密钥' : '输入密钥'} /></label>
      {existingService && <small className="provider-note">此服务的模型共用密钥，填写新密钥会统一更新。</small>}
    </>}
    </fieldset>
    {error && <p className="form-error" role="alert">{error}</p>}
    {tested && <p role="status">连接成功</p>}
    <div className="settings-actions"><button type="button" className="button button-secondary" onClick={testConnection} disabled={working || testing || !provider}>{testing ? '测试中…' : '测试连接'}</button><button type="button" className="button button-secondary" onClick={onCancel} disabled={working || testing}>取消</button><button className="button button-primary" disabled={working || testing || !provider}>{working ? '保存中…' : '保存'}</button></div>
  </form>;
}

export function Settings({ settings, loadError, onRetry, updateModels, act, onClose }) {
  const [section, setSection] = useState('models'), [editing, setEditing] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const panel = useRef(null);
  useEffect(() => {
    const previous = document.activeElement; panel.current?.focus();
    const handle = event => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const controls = [...panel.current.querySelectorAll('button:not(:disabled), input, select, [tabindex="0"]')];
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', handle); return () => { document.removeEventListener('keydown', handle); previous?.focus(); };
  }, [onClose]);
  async function change(method, params) {
    setBusy(true); setError('');
    try { updateModels(await request(method, params)); }
    catch (e) { if (method !== 'model_save') setError(e.message); throw e; }
    finally { setBusy(false); }
  }
  const needsSetup = settings && !settings.defaultModelId;
  const menu = [['general', Gear, '通用'], ['models', Cube, '模型'], ['trash', Trash, '回收站']];
  return <div className="dialog-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}><section className="settings-window" role="dialog" aria-modal="true" aria-label={needsSetup && section === 'models' ? '配置模型' : '设置'} tabIndex={-1} ref={panel}>
    <nav className="settings-nav" aria-label="设置分类"><h2>设置</h2>{menu.map(([id, Icon, title]) => <button key={id} className={section === id ? 'selected' : ''} aria-current={section === id ? 'page' : undefined} onClick={() => { setSection(id); setEditing(null); setError(''); }}><Icon size={21} />{title}</button>)}</nav>
    <div className="settings-content"><header><h2>{section === 'models' && needsSetup ? '配置模型' : menu.find(item => item[0] === section)[2]}</h2><button className="icon-button" aria-label="关闭设置" onClick={onClose}><X size={22} /></button></header>
      {error && <p className="form-error" role="alert">{error}</p>}
      {!settings ? loadError ? <><p className="form-error" role="alert">{loadError}</p><button className="button button-secondary" onClick={onRetry}>重试</button></> : <p>正在加载…</p> : section === 'general' ? <><div className="settings-card"><div className="settings-row"><div><strong>本地数据</strong><small><PathLink path={settings.dataDirectory} home={settings.homeDirectory} levels={2} /></small></div></div></div></> : section === 'trash' ? <RecycleBin act={act} /> : needsSetup ? <ModelForm settings={settings} onCancel={onClose} onSave={async values => { await change('model_save', values); onClose(); }} /> : <>
        <div className="settings-card"><div className="settings-row"><strong>默认模型</strong><select aria-label="默认模型" disabled={busy} value={settings.defaultModelId} onChange={e => change('model_default', { id: e.target.value }).catch(() => {})}>{settings.models?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div></div>
        {editing ? <><h3>{editing.id ? '编辑模型' : '添加模型'}</h3><ModelForm key={editing.id || 'new'} item={editing.id ? editing : null} settings={settings} onCancel={() => setEditing(null)} onSave={async values => { await change('model_save', values); setEditing(null); }} /></> : <><div className="settings-card">{settings.models?.map(item => <div className="settings-row" key={item.id}><div><strong>{item.name}</strong><small>{item.model} · {item.configured ? '已配置' : '未配置'}</small></div>{<div className="settings-actions"><button className="button button-secondary" onClick={() => setEditing(item)}>编辑</button><button className="button button-secondary" disabled={busy || item.id === settings.defaultModelId} onClick={() => change('model_remove', { id: item.id }).catch(() => {})}>移除</button></div>}</div>)}</div><button className="button button-primary" onClick={() => setEditing({})}><Plus size={18} />添加模型</button></>}
      </>}
    </div>
  </section></div>;
}
