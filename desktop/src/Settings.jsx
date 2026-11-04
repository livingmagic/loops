import { useEffect, useRef, useState } from 'react';
import { Gear, Cube, Trash, X, Plus } from '@phosphor-icons/react';
import { request } from './api.js';

function RecycleBin({ act }) {
  const [items, setItems] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState('');
  useEffect(() => { Promise.all([request('trash'), request('automation_trash')]).then(([loops, tasks]) => setItems([...loops.map(item => ({ ...item, kind: 'loop' })), ...tasks.map(item => ({ ...item, kind: 'task' }))])).catch(e => setError(e.message)); }, []);
  return <>{error && <p role="alert" className="form-error">{error}</p>}{items === null ? <p>{error ? '加载失败' : '正在加载…'}</p> : !items.length ? <p className="empty-copy">回收站为空</p> : <div className="settings-card">{items.map(item => <div className="settings-row" key={`${item.kind}:${item.id}`}><div><strong>{item.title}</strong><small>{item.kind === 'loop' ? 'Loop' : '自动化任务'}</small></div><button className="button button-secondary" disabled={Boolean(busy)} onClick={async () => { setBusy(item.id); setError(''); try { await act(item.kind === 'loop' ? 'restore' : 'automation_restore', item.kind === 'loop' ? { loopId: item.id } : { taskId: item.id }); setItems(items.filter(entry => entry !== item)); } catch(e) { setError(e.message); } finally { setBusy(''); } }}>恢复</button></div>)}</div>}</>;
}

const effortNames = { none: '关闭', minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高', ultra: '超高' };

function AgentModel({ client, onSave, busy }) {
  const [saving, setSaving] = useState(false), [error, setError] = useState('');
  const model = client.defaultModel || '', reasoning = client.defaultReasoning || '';
  const models = client.models || [];
  const selected = models.find(item => item.id === model);
  const levels = selected?.reasoningLevels || [];
  async function save(nextModel, nextReasoning) {
    setSaving(true); setError('');
    try { await onSave({ agentId: client.id, model: nextModel || null, reasoning: nextReasoning || null }); }
    catch (e) { setError(e.message); } finally { setSaving(false); }
  }
  return <section className="settings-card agent-settings-card" aria-label={`${client.name} 执行设置`}>
    <header className="agent-settings-heading"><div><strong>{client.name}</strong><small>{client.version ? `v${client.version}` : '未安装'}</small></div><span className="agent-settings-status" role="status">{saving ? '保存中…' : client.detail}</span></header>
    <div className="agent-settings-fields">
      <label>模型<select aria-label={`${client.name} 模型`} value={model} disabled={busy || saving || !client.installed} onChange={event => { const next = models.find(m => m.id === event.target.value); save(event.target.value, next?.reasoningLevels.includes(reasoning) ? reasoning : next?.defaultReasoning || (next?.reasoningLevels.includes('low') ? 'low' : next?.reasoningLevels[0]) || ''); }}>
        {!model && <option value="" disabled>请选择模型</option>}
        {model && !models.some(m => m.id === model) && <option value={model} disabled>{model} · 当前不可用</option>}
        {models.map(item => <option key={item.id} value={item.id}>{item.name}{item.provider && models.filter(m => m.name === item.name).length > 1 ? ` · ${item.provider}` : ''}</option>)}
      </select></label>
      <label>思考强度<select aria-label={`${client.name} 思考强度`} value={reasoning} disabled={busy || saving || !levels.length} onChange={event => save(model, event.target.value)}>
        {!reasoning && <option value="" disabled>{levels.length ? '请选择思考强度' : '无可选档位'}</option>}
        {reasoning && !levels.includes(reasoning) && <option value={reasoning} disabled>{effortNames[reasoning] || reasoning} · 当前不可用</option>}
        {levels.map(level => <option key={level} value={level}>{effortNames[level] || level}</option>)}
      </select></label>
    </div>
    {(error || client.catalogError) && <p className="form-error" role="alert">{error || client.catalogError}</p>}
  </section>;
}

function ModelForm({ item, settings, onSave, onCancel }) {
  const [provider, setProvider] = useState(item?.provider || 'deepseek');
  const [baseUrl, setBaseUrl] = useState(item?.baseUrl || '');
  const existingService = settings.services?.some(entry => entry.configured && entry.provider === provider && (provider !== 'compatible' || entry.baseUrl === baseUrl.trim().replace(/\/$/, '')));
  const [working, setWorking] = useState(false), [error, setError] = useState('');
  const form = useRef(null);
  const catalog = settings.providers?.find(entry => entry.id === provider)?.models || [];
  return <form className="settings-card model-form" ref={form} onSubmit={async event => {
    event.preventDefault(); const values = new FormData(form.current); setWorking(true); setError('');
    const apiKey = values.get('apiKey'); form.current.elements.apiKey.value = '';
    try { await onSave({ id: item?.id, provider, name: values.get('name'), model: values.get('model'), baseUrl: values.get('baseUrl'), apiKey }); }
    catch(e) { setError(e.message); } finally { setWorking(false); }
  }}>
    <h3>{item ? '编辑模型' : '添加模型'}</h3>
    <label>名称<input name="name" required maxLength={80} defaultValue={item?.name || ''} placeholder="例如：日常工作" /></label>
    <label>服务<select value={provider} onChange={e => setProvider(e.target.value)}>{settings.providers?.map(entry => <option value={entry.id} key={entry.id}>{entry.name}</option>)}<option value="compatible">OpenAI 兼容服务</option></select></label>
    <label>模型{provider === 'compatible' ? <input key={provider} name="model" required defaultValue={item?.provider === provider ? item.model : ''} placeholder="服务提供的模型 ID" /> : <select key={provider} name="model" defaultValue={item?.provider === provider ? item.model : catalog[0]?.id}>{catalog.map(model => <option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}</select>}</label>
    {provider === 'compatible' && <label>API 地址<input name="baseUrl" required type="url" value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://example.com/v1" /></label>}
    <label>API Key<input name="apiKey" type="password" autoComplete="off" required={!existingService} placeholder={existingService ? '已配置，留空使用此服务的已有密钥' : '输入密钥'} /></label>
    {existingService && <small className="provider-note">此服务的模型共用密钥，填写新密钥会统一更新。</small>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="settings-actions"><button type="button" className="button button-secondary" onClick={onCancel} disabled={working}>取消</button><button className="button button-primary" disabled={working}>{working ? '保存中…' : '保存'}</button></div>
  </form>;
}

export function Settings({ settings, refresh, act, onClose }) {
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
  async function change(method, params) { setBusy(true); setError(''); try { await request(method, params); await refresh(); } catch(e) { setError(e.message); throw e; } finally { setBusy(false); } }
  const menu = [['general', Gear, '通用'], ['models', Cube, '模型'], ['trash', Trash, '回收站']];
  return <div className="dialog-backdrop"><section className="settings-window" role="dialog" aria-modal="true" aria-label="设置" tabIndex={-1} ref={panel}>
    <nav className="settings-nav" aria-label="设置分类"><h2>设置</h2>{menu.map(([id, Icon, title]) => <button key={id} className={section === id ? 'selected' : ''} aria-current={section === id ? 'page' : undefined} onClick={() => { setSection(id); setEditing(null); setError(''); }}><Icon size={21} />{title}</button>)}</nav>
    <div className="settings-content"><header><h2>{menu.find(item => item[0] === section)[2]}</h2><button className="icon-button" aria-label="关闭设置" onClick={onClose}><X size={22} /></button></header>
      {error && <p className="form-error" role="alert">{error}</p>}
      {!settings ? <p>正在加载…</p> : section === 'general' ? <>{settings.agentPluginErrors?.map(item => <p key={item.plugin} className="form-error" role="alert">{item.plugin}：{item.message}</p>)}{(settings.agents || []).map(client => <AgentModel key={client.id} client={client} busy={busy} onSave={async params => { const updated = await request('agent_model_default', params); await refresh(updated); }} />)}<div className="settings-card"><div className="settings-row"><div><strong>本地数据</strong><small>{settings.dataDirectory}</small></div></div></div><button className="button button-secondary" onClick={() => refresh().catch(e => setError(e.message))}>重新检查</button></> : section === 'trash' ? <RecycleBin act={act} /> : <>
        <div className="settings-card"><div className="settings-row"><strong>默认模型</strong><select aria-label="默认模型" disabled={busy} value={settings.defaultModelId} onChange={e => change('model_default', { id: e.target.value }).catch(() => {})}>{settings.models?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div></div>
        {editing ? <ModelForm key={editing.id || 'new'} item={editing.id ? editing : null} settings={settings} onCancel={() => setEditing(null)} onSave={async values => { await change('model_save', values); setEditing(null); }} /> : <><div className="settings-card">{settings.models?.map(item => <div className="settings-row" key={item.id}><div><strong>{item.name}</strong><small>{item.model} · {item.configured ? '已配置' : '未配置'}</small></div>{<div className="settings-actions"><button className="button button-secondary" onClick={() => setEditing(item)}>编辑</button><button className="button button-secondary" disabled={busy || item.id === settings.defaultModelId} onClick={() => change('model_remove', { id: item.id }).catch(() => {})}>移除</button></div>}</div>)}</div><button className="button button-primary" onClick={() => setEditing({})}><Plus size={18} />添加模型</button></>}
      </>}
    </div>
  </section></div>;
}
