import { useEffect, useState } from 'react';
import { CheckCircle, Copy, Gear, FlowArrow, Globe, Plus, SpinnerGap, Users } from '@phosphor-icons/react';
import { request, subscribe } from './api.js';
import { SharingDialog } from './SharingDialog.jsx';
import { SharingRecords } from './SharingRecords.jsx';

function Publish({ loops, flows, enabled, initialId, act, onClose }) {
  const available = loops.filter(loop => loop.savedRevision && !flows.some(flow => flow.id === loop.id && flow.revision === loop.savedRevision));
  const [selected, setSelected] = useState(initialId || available[0]?.id || '');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const loop = available.find(item => item.id === selected);
  return <SharingDialog title="发布流程" onClose={onClose}><form className="sharing-form" onSubmit={async event => {
    event.preventDefault(); setBusy(true);
    try { await act('sharing_publish', { loopId: selected, revision: loop.savedRevision, enableSharing: !enabled }); onClose(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }}>
    {available.length ? <><label>流程<select value={selected} onChange={event => setSelected(event.target.value)}>{available.map(loop => <option key={loop.id} value={loop.id}>{loop.title} · 版本 {loop.savedRevision}</option>)}</select></label><p className="sharing-note">{flows.some(flow => flow.id === selected) ? `将替换当前共享的版本 ${flows.find(flow => flow.id === selected).revision}，已接受的任务继续按原版本完成。` : '已授权客户端可发现并调用此版本。'}{!enabled && '发布将开启外部连接，其他已发布流程也将恢复可用。'}</p></> : <p className="sharing-empty">还没有可发布的新版本。请先试跑并保存流程。</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={!loop || busy}>{busy ? '发布中…' : enabled ? '发布' : '发布并开启共享'}</button></div>
  </form></SharingDialog>;
}

function Options({ state, act, onClose }) {
  const [name, setName] = useState(state.config.name), [concurrency, setConcurrency] = useState(state.config.concurrency), [enabled, setEnabled] = useState(state.config.enabled);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [copied, setCopied] = useState('');
  return <SharingDialog title="共享设置" onClose={onClose}><form className="sharing-form" onSubmit={async event => {
    event.preventDefault(); setBusy(true);
    try { await act('sharing_settings', { name, concurrency: Number(concurrency), enabled }); onClose(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }}>
    <label>本机名称<input required maxLength={80} value={name} onChange={event => setName(event.target.value)} /></label>
    <label>最大并发任务数<input type="number" min="1" max="32" required value={concurrency} onChange={event => setConcurrency(event.target.value)} /></label>
    <label className="sharing-toggle"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />允许外部连接</label>
    {!enabled && state.config.enabled && <p className="sharing-note">关闭后停止接受连接，已接受的任务继续执行。</p>}
    {!!state.addresses.length && <div className="sharing-addresses"><span>连接地址</span>{state.addresses.map(address => <div key={address}><code>{address}</code><button className="icon-button" type="button" aria-label={`复制 ${address}`} onClick={async () => { try { await navigator.clipboard.writeText(address); setCopied(address); } catch { setError('复制失败，请手动复制地址。'); } }}>{copied === address ? <CheckCircle size={17} /> : <Copy size={17} />}</button></div>)}</div>}
    {error && <p className="form-error" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={busy}>保存</button></div>
  </form></SharingDialog>;
}

function ClientIdentity({ client }) {
  return <div className="sharing-row-main"><strong>{client.note || client.name}</strong><small>{client.note ? `${client.name} · ${client.source}` : client.source}</small></div>;
}

function ClientNote({ client, act, onClose }) {
  const [note, setNote] = useState(client.note || ''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <SharingDialog title="客户端备注" onClose={onClose}><form className="sharing-form" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    try { await act('sharing_client_note', { clientId: client.id, note }); onClose(); }
    catch (error) { setError(error.message); } finally { setBusy(false); }
  }}>
    <p className="sharing-note">{client.name} · {client.source}</p>
    <label>备注<input autoFocus maxLength={120} placeholder="例如：办公室 Mac" value={note} onChange={event => setNote(event.target.value)} /></label>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={busy}>{busy ? '保存中…' : '保存'}</button></div>
  </form></SharingDialog>;
}

export function Sharing({ loops }) {
  const [state, setState] = useState(null);
  const [tab, setTab] = useState('flows');
  const [selectedFlow, setSelectedFlow] = useState(null);
  const [error, setError] = useState('');
  const [modal, setModal] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    let current = true;
    async function refresh() {
      try {
        const value = await request('sharing_state');
        if (current) setState(value);
      } catch (error) { if (current) setError(error.message); }
    }
    refresh();
    const off = subscribe(event => { if (['sharing_state', 'state'].includes(event.event)) refresh(); });
    return () => { current = false; off(); };
  }, []);

  async function act(method, params) {
    const result = await request(method, params);
    setState(result);
    setError('');
    if (['sharing_publish', 'sharing_resume'].includes(method)) setSelectedFlow(params.loopId);
    return result;
  }
  const pending = state?.clients.filter(client => client.status === 'pending') || [];
  const clients = state?.clients.filter(client => client.status === 'approved') || [];
  const managed = state?.managedFlows || state?.flows || [];
  const flows = [...managed, ...(state?.pastFlows || []).filter(flow => !managed.some(item => item.id === flow.id)).map(flow => ({ ...flow, stopped: true, unavailable: '该流程已不再发布，历史记录仍可查看。' }))];
  const pastClients = state?.clients.filter(client => !['pending', 'approved'].includes(client.status)) || [];
  useEffect(() => {
    if (!pending.length) return;
    const timer = setInterval(() => request('sharing_state').then(setState).catch(error => setError(error.message)), 5000);
    return () => clearInterval(timer);
  }, [pending.length]);
  async function perform(method, params) {
    setWorking(true);
    try { await act(method, params); } catch (error) { setError(error.message); } finally { setWorking(false); }
  }
  const flow = flows.find(item => item.id === selectedFlow) || flows[0];
  const latest = loops.find(item => item.id === flow?.id)?.savedRevision;
  const showPublish = state && (flows.length > 0 || tab !== 'flows');

  return <main className="workspace sharing-workspace">
    <header className="topbar sharing-topbar">
      <div className="sharing-heading"><h1>共享</h1>{state && <span className={`sharing-availability ${state.online ? 'online' : ''}`}><i />{state.online ? '已开启' : '未开启'}</span>}</div>
      <div className="toolbar-actions">
        <button className="button button-secondary" disabled={!state} onClick={() => setModal('settings')}><Gear size={17} />共享设置</button>
        {showPublish && <button className="button button-primary" onClick={() => setModal('publish')}><Plus size={18} />发布流程</button>}
      </div>
    </header>
    <div className="sharing-tabs" role="tablist" aria-label="共享管理">
      {[['flows', '流程'], ['access', '访问授权']].map(([id, label]) => <button key={id} id={`sharing-tab-${id}`} role="tab" aria-selected={tab === id} aria-controls="sharing-panel" tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)} onKeyDown={event => {
        const ids = ['flows', 'access'];
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'flows' : event.key === 'End' ? 'access' : ids[(ids.indexOf(id) + 1) % 2];
        setTab(next); document.getElementById(`sharing-tab-${next}`)?.focus();
      }}>{label}{id === 'access' && pending.length > 0 && <span className="sharing-tab-count">{pending.length}</span>}</button>)}
    </div>
    <div className="sharing-body" id="sharing-panel" role="tabpanel" aria-labelledby={`sharing-tab-${tab}`}>
      <div className={`sharing-content ${tab === 'flows' && flows.length ? 'sharing-split-content' : ''}`}>
        {(error || state?.error) && <p className="form-error" role="alert">{error || state.error}</p>}
        {state && !state.online && flows.length > 0 && <div className="sharing-service-notice"><span>共享未开启，已发布流程暂不可调用。</span><button className="button button-secondary" disabled={working} onClick={() => perform('sharing_settings', { enabled: true })}>开启共享</button></div>}
        {!state && <p className="sharing-loading"><SpinnerGap className="spinning" size={18} />正在读取…</p>}
        {state && tab === 'flows' && (flows.length ? <div className="sharing-split">
          <nav className="sharing-flow-list" aria-label="共享流程">
            {flows.map(item => <button className={`sharing-flow-choice ${flow.id === item.id ? 'selected' : ''}`} key={item.id} aria-pressed={flow.id === item.id} aria-controls="sharing-flow-records" onClick={() => setSelectedFlow(item.id)}>
              <span className="sharing-flow-icon"><FlowArrow size={22} /></span>
              <span className="sharing-row-main"><strong>{item.name}</strong><small>{item.unavailable ? '暂不可用' : item.stopped ? `已停止 · 版本 ${item.revision}` : `版本 ${item.revision}`}</small></span>
            </button>)}
          </nav>
          <section className="sharing-record-panel" id="sharing-flow-records" aria-label={`${flow.name}的调用记录`}>
            <header className="sharing-record-heading"><div><h2>{flow.name}</h2>{flow.description && <p>{flow.description}</p>}</div><div className="sharing-flow-actions">{!flow.unavailable && latest && latest !== flow.revision && <button className="text-button" disabled={working} onClick={() => setModal({ type: 'publish', flowId: flow.id })}>更新至版本 {latest}</button>}{flow.stopped ? <button className="button button-secondary" disabled={working || Boolean(flow.unavailable)} onClick={() => perform('sharing_resume', { loopId: flow.id })}>恢复共享</button> : <button className="text-button" disabled={working} onClick={() => setConfirm({ type: 'unpublish', flow })}>停止共享</button>}</div></header>
            {flow.unavailable && <p className="sharing-note sharing-unavailable">{flow.unavailable}</p>}
            <SharingRecords key={flow.id} flow={flow} />
          </section>
        </div> : <div className="sharing-empty-state">
          <span className="sharing-empty-icon"><FlowArrow size={30} /></span>
          <h2>发布你的第一个流程</h2>
          <p>已授权的客户端可以发现并调用。</p>
          <button className="button button-primary" onClick={() => setModal('publish')}><Plus size={18} />发布流程</button>
        </div>)}
        {state && tab === 'access' && <>
          {!!pending.length && <section className="sharing-access-section">
            <h2>待确认</h2>
            {pending.map(client => <div className="sharing-approval" key={client.id}>
              <ClientIdentity client={client} /><button className="text-button" onClick={() => setModal({ type: 'note', client })}>{client.note ? '编辑备注' : '备注'}</button>
              <span className="sharing-code" aria-label={`配对码 ${client.code}`}>{client.code}</span>
              <button className="button button-secondary" disabled={working} onClick={async () => {
                setWorking(true);
                try { await act('sharing_reject', { clientId: client.id }); } catch (error) { setError(error.message); }
                finally { setWorking(false); }
              }}>拒绝</button>
              <button className="button button-primary" disabled={working} onClick={() => setConfirm({ type: 'approve', client, note: client.note || '' })}>同意连接</button>
            </div>)}
          </section>}
          {!!clients.length && <section className="sharing-access-section">
            {pending.length > 0 && <h2>已授权</h2>}
            {clients.map(client => <div className="sharing-row" key={client.id}>
              <span className="sharing-client-icon"><Globe size={21} /></span>
              <ClientIdentity client={client} /><button className="text-button" onClick={() => setModal({ type: 'note', client })}>{client.note ? '编辑备注' : '备注'}</button>
              <span className="sharing-status online">已授权</span>
              <button className="text-button" onClick={() => setConfirm({ type: 'revoke', client })}>撤销授权</button>
            </div>)}
          </section>}
          {pastClients.length > 0 && <details className="sharing-past-clients"><summary>历史授权</summary>{pastClients.map(client => <div className="sharing-row" key={client.id}><ClientIdentity client={client} /><button className="text-button" onClick={() => setModal({ type: 'note', client })}>{client.note ? '编辑备注' : '备注'}</button><span className="sharing-status">{{ rejected: '已拒绝', revoked: '已撤销', expired: '申请已过期' }[client.status] || client.status}</span></div>)}</details>}
          {!clients.length && !pending.length && !pastClients.length && <div className="sharing-empty-state"><span className="sharing-empty-icon"><Users size={30} /></span><h2>暂无访问授权</h2><p>其他客户端申请连接后，在这里确认。</p></div>}
        </>}
      </div>
    </div>
    {(modal === 'publish' || modal?.type === 'publish') && <Publish loops={loops} flows={state.flows} enabled={state.config.enabled} initialId={modal?.flowId} act={act} onClose={() => setModal(null)} />}
    {modal?.type === 'note' && <ClientNote client={modal.client} act={act} onClose={() => setModal(null)} />}
    {modal === 'settings' && <Options state={state} act={act} onClose={() => setModal(null)} />}
    {confirm && <SharingDialog title={confirm.type === 'approve' ? `允许「${confirm.client.note || confirm.client.name}」连接？` : confirm.type === 'revoke' ? `撤销「${confirm.client.note || confirm.client.name}」的授权？` : '停止共享流程？'} onClose={() => setConfirm(null)}>
      <div className="sharing-form">
        {confirm.type === 'approve' ? <><p>核对调用方的配对码 <strong className="sharing-code">{confirm.client.code}</strong></p><p>同意后，可发现和调用全部已发布流程，包括以后新增发布的流程。</p></> : <p>{confirm.type === 'revoke' ? '立即停止该客户端的访问并取消排队任务。正在执行的任务可在调用记录中停止。' : '停止接受新任务，已接受的任务继续完成。'}</p>}
        {confirm.type === 'approve' && <label>备注（可选）<input maxLength={120} placeholder="例如：办公室 Mac" value={confirm.note} onChange={event => setConfirm({ ...confirm, note: event.target.value })} /></label>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="dialog-actions"><button className="button button-secondary" onClick={() => setConfirm(null)}>取消</button><button className="button button-primary" disabled={working} onClick={async () => {
          setWorking(true);
          try { await act(`sharing_${confirm.type}`, confirm.flow ? { loopId: confirm.flow.id } : { clientId: confirm.client.id, ...(confirm.type === 'approve' ? { note: confirm.note } : {}) }); setConfirm(null); }
          catch (error) { setError(error.message); } finally { setWorking(false); }
        }}>{confirm.type === 'approve' ? '同意连接' : confirm.type === 'revoke' ? '撤销授权' : '停止共享'}</button></div>
      </div>
    </SharingDialog>}
  </main>;
}
