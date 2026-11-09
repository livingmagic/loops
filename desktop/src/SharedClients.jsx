import { useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowRight, CheckCircle, DownloadSimple, Globe, SpinnerGap } from '@phosphor-icons/react';
import { native, request, subscribe } from './api.js';
import { SharingDialog } from './SharingDialog.jsx';

const states = { queued: '排队中', running: '运行中', correcting: '运行中', stopping: '停止中', success: '已完成', failed: '失败', stopped: '已取消', interrupted: '已中断', approved: '已授权', pending: '等待同意', rejected: '已拒绝', revoked: '授权已撤销', expired: '申请已过期' };
const active = status => ['queued', 'running', 'correcting', 'stopping'].includes(status);

export function ConnectSharedClient({ act, onClose }) {
  const [nearby, setNearby] = useState([]), [address, setAddress] = useState(''), [scanning, setScanning] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function scan() {
    setScanning(true); setError('');
    try { setNearby(await request('sharing_discover')); } catch (e) { setError(e.message); } finally { setScanning(false); }
  }
  useEffect(() => { scan(); }, []);
  return <SharingDialog title="连接 Loops 客户端" onClose={onClose}><form className="sharing-form" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    try { await act('sharing_connect', { address: address.trim() }); onClose(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }}>
    <div className="sharing-section-title"><h3>附近的 Loops</h3><button type="button" className="text-button" onClick={scan} disabled={scanning}>{scanning ? '搜索中…' : '刷新'}</button></div>
    {nearby.map(peer => <button key={peer.id} type="button" className={`sharing-nearby ${address === peer.address ? 'selected' : ''}`} onClick={() => setAddress(peer.address)}><Globe size={22} /><span>{peer.name}<small>{peer.address}</small></span>{address === peer.address && <CheckCircle size={20} />}</button>)}
    {!scanning && !nearby.length && <p className="sharing-note">未发现客户端。请在对方「共享设置」开启外部连接，并复制 Wi-Fi 或以太网的完整地址。</p>}
    <label>连接地址<input type="url" required placeholder="https://192.168.1.10:端口" value={address} onChange={event => setAddress(event.target.value)} /></label>
    <p className="sharing-note">发送申请后，在对方 Loops 核对配对码并同意连接。</p>
    {error && <p className="form-error" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={!address || busy}>{busy ? '申请中…' : '申请连接'}</button></div>
  </form></SharingDialog>;
}

function RemoteFlow({ peer, flow, onClose }) {
  const key = `shared-task:${peer.id}:${flow.id}`;
  const [attempt] = useState(() => { try { return JSON.parse(sessionStorage.getItem(key + ':request')); } catch { return null; } });
  const [inputs, setInputs] = useState(attempt?.inputs || {}), [job, setJob] = useState(() => { try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; } });
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [downloads, setDownloads] = useState({});
  const requestId = useRef(attempt?.requestId || crypto.randomUUID());
  useEffect(() => {
    if (!job) return;
    sessionStorage.setItem(key, JSON.stringify(job));
    if (!active(job.status)) return;
    let live = true;
    const timer = setInterval(async () => {
      try { const result = await request('sharing_peer_task', { peerId: peer.id, taskId: job.id }); if (live) { setJob(result); setError(''); } }
      catch (e) { if (live) setError(e.message); }
    }, 1500);
    return () => { live = false; clearInterval(timer); };
  }, [job?.id, job?.status]);
  async function fileInput(id, file) {
    if (!file) return;
    if (file.size > 10000000) { setError('文件不能超过 10MB。'); return; }
    const reader = new FileReader();
    reader.onload = () => { setInputs(previous => ({ ...previous, [id]: { name: file.name, base64: reader.result.split(',')[1] } })); requestId.current = crypto.randomUUID(); };
    reader.readAsDataURL(file);
  }
  return <SharingDialog title={flow.name} onClose={onClose}><form className="sharing-form" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const params = { peerId: peer.id, flowId: flow.id, revision: flow.revision, inputs, requestId: requestId.current };
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify({ flowId: flow.id, revision: flow.revision, inputs }))))).map(value => value.toString(16).padStart(2, '0')).join('');
      const pending = JSON.parse(sessionStorage.getItem(key + ':request') || 'null');
      if (pending) params.requestId = pending.hash === hash ? pending.requestId : crypto.randomUUID();
      const saved = { requestId: params.requestId, hash, inputs: Object.fromEntries(Object.entries(inputs).filter(([, value]) => typeof value === 'string')) };
      try { sessionStorage.setItem(key + ':request', JSON.stringify(saved)); }
      catch { sessionStorage.setItem(key + ':request', JSON.stringify({ requestId: params.requestId, hash })); }
      setJob(await request('sharing_peer_submit', params));
      sessionStorage.removeItem(key + ':request');
      requestId.current = crypto.randomUUID();
    }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }}>
    <p className="sharing-note">{flow.description}</p>
    {flow.inputs.map(item => <label key={item.id}>{item.label}{item.required === false ? '（可选）' : ''}{item.kind === 'file' ? <input type="file" disabled={active(job?.status)} required={item.required !== false && !inputs[item.id]} onChange={event => fileInput(item.id, event.target.files[0])} /> : <textarea rows={3} required={item.required !== false} disabled={active(job?.status)} value={inputs[item.id] || ''} onChange={event => { setInputs(previous => ({ ...previous, [item.id]: event.target.value })); requestId.current = crypto.randomUUID(); }} />}</label>)}
    {job && <section className="sharing-result" aria-live="polite"><strong>{states[job.status]}</strong>{job.error && <p className="form-error">{job.error}</p>}{job.outputs && Object.keys(job.outputs).length > 0 && <pre>{Object.values(job.outputs).map(value => typeof value === 'string' ? value : JSON.stringify(value, null, 2)).join('\n')}</pre>}{job.files.map(file => <div className="sharing-result-file" key={file.index}><span>{file.label}</span><button type="button" className="button button-secondary" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        if (native && downloads[file.index]) { await window.loops.request('sharing_reveal_download', { path: downloads[file.index] }); return; }
        const result = await request('sharing_download', { peerId: peer.id, taskId: job.id, index: file.index }); setDownloads(previous => ({ ...previous, [file.index]: result.path })); if (native) await window.loops.request('sharing_reveal_download', { path: result.path }); }
      catch (e) { setError(e.message); } finally { setBusy(false); }
    }}><DownloadSimple size={16} />{downloads[file.index] ? native ? '打开文件位置' : '再次下载' : '下载'}</button></div>)}{!native && Object.values(downloads).map(path => <small key={path} className="sharing-download-path">{path}</small>)}</section>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>关闭</button>{active(job?.status) ? <button type="button" className="button button-secondary" disabled={busy} onClick={async () => { try { setJob(await request('sharing_peer_cancel', { peerId: peer.id, taskId: job.id })); } catch (e) { setError(e.message); } }}>取消任务</button> : <button className="button button-primary" disabled={busy}>{busy ? '提交中…' : job ? '再次运行' : '运行'}</button>}</div>
  </form></SharingDialog>;
}


export function useSharedClients(items, refreshPlugins) {
  const [peers, setPeers] = useState([]);
  const [error, setError] = useState('');
  const registration = useRef(new Set());
  useEffect(() => {
    let current = true;
    async function refresh() {
      try {
        const state = await request('sharing_state');
        if (current) setPeers(state.peers);
      } catch (error) { if (current) setError(error.message); }
    }
    refresh();
    const off = subscribe(event => { if (event.event === 'sharing_state') refresh(); });
    return () => { current = false; off(); };
  }, []);

  const monitoredIds = peers.filter(peer => ['pending', 'approved'].includes(peer.status)).map(peer => peer.id).join(',');
  useEffect(() => {
    if (!monitoredIds) return;
    let current = true, polling = false;
    async function poll() {
      if (polling) return;
      polling = true;
      try {
        for (const peerId of monitoredIds.split(',')) {
          if (!current) break;
          try {
            const state = await request('sharing_refresh_peer', { peerId });
            if (current) setPeers(state.peers);
          } catch { /* The peer's offline state is delivered by sharing_state. */ }
        }
      } finally { polling = false; }
    }
    poll();
    const timer = setInterval(poll, 5000);
    return () => { current = false; clearInterval(timer); };
  }, [monitoredIds]);

  useEffect(() => {
    for (const peer of peers) {
      if (peer.status !== 'approved' || peer.online === false || items.some(item => item.manifest.id === `shared-${peer.id}`) || registration.current.has(peer.id)) continue;
      registration.current.add(peer.id);
      request('sharing_add_plugin', { peerId: peer.id }).then(() => refreshPlugins({ invalidate: true })).catch(async error => {
        try {
          const state = await request('sharing_state');
          if (state.peers.find(item => item.id === peer.id)?.online === false) {
            // Retry when polling observes recovery, without a page-level offline error.
            registration.current.delete(peer.id);
          } else setError(error.message);
          setPeers(state.peers);
        } catch (readError) { setError(readError.message); }
      });
    }
  }, [peers, items, refreshPlugins]);

  async function act(method, params) {
    const state = await request(method, params);
    setPeers(state.peers);
    setError('');
    return state;
  }
  return { peers, error, act };
}

export function SharedClientCard({ peer, plugin, onOpen }) {
  const ready = peer.online !== false && peer.status === 'approved' && plugin?.status === 'connected';
  const status = peer.status === 'approved' && peer.online === false ? '无法连接' : peer.status === 'approved' ? plugin?.enabled === false ? '已停用' : ready ? '已连接' : plugin?.status === 'connecting' ? '连接中' : '已授权' : states[peer.status] || '未连接';
  return <div className="sharing-row">
    <span className="sharing-client-icon"><Globe size={21} /></span>
    <div className="sharing-row-main"><strong>{peer.name}</strong><small>{peer.status === 'pending' ? <>配对码 <span className="sharing-code">{peer.code}</span></> : peer.address}</small></div>
    <span className={`sharing-status ${ready ? 'online' : ''}`}>{status}</span>
    <button className="button button-secondary" onClick={onOpen}>{peer.status === 'pending' ? '查看申请' : '管理'}<ArrowRight size={14} /></button>
  </div>;
}

export function SharedClientDetails({ peer, plugin, act, refreshPlugins, onClose }) {
  const [flows, setFlows] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(null);
  const refreshId = useRef(0);
  async function refresh() {
    const id = ++refreshId.current;
    setBusy(true); setError('');
    try {
      const state = await act('sharing_refresh_peer', { peerId: peer.id });
      const current = state.peers.find(item => item.id === peer.id);
      const nextFlows = current?.status === 'approved' ? (await request('sharing_peer_flows', { peerId: peer.id })).flows : null;
      if (id === refreshId.current) setFlows(nextFlows);
    } catch (error) { if (id === refreshId.current) { setError(error.message); setFlows(null); } }
    finally { if (id === refreshId.current) setBusy(false); }
  }
  useEffect(() => { setSelected(null); }, [peer.id, peer.status]);
  useEffect(() => { setFlows(null); refresh(); return () => { refreshId.current++; }; }, [peer.id, peer.status, peer.online]);
  const enabled = plugin?.enabled;
  return <>
    <SharingDialog title={peer.name} onClose={onClose}>
      <div className="shared-client-details">
        <div className="shared-client-address"><Globe size={17} /><span>{peer.address}</span><span className="sharing-status">{peer.online === false ? '无法连接' : states[peer.status] || peer.status}</span></div>
        {peer.status === 'pending' && <div className="shared-client-pairing"><strong className="sharing-code">{peer.code}</strong><p>在对方 Loops 的「共享 → 访问授权」中核对并同意。</p></div>}
        {['expired', 'rejected', 'revoked', 'unknown'].includes(peer.status) && <p className="sharing-note">需要对方再次同意后才能使用已发布流程。</p>}
        {peer.status === 'approved' && peer.online !== false && <>
          <div className="sharing-section-title"><h3>可用流程</h3><button className="icon-button" aria-label="刷新流程" disabled={busy} onClick={refresh}><ArrowClockwise size={18} className={busy ? 'spinning' : ''} /></button></div>
          {busy && flows === null && <p className="sharing-loading"><SpinnerGap size={16} className="spinning" />正在读取…</p>}
          {flows?.length === 0 && <p className="sharing-note">对方还没有发布流程。</p>}
          {flows?.map(flow => <div className="sharing-row" key={flow.id}><div className="sharing-row-main"><strong>{flow.name}</strong><small>{flow.description}</small></div><button className="button button-secondary" onClick={() => setSelected(flow)}>运行</button></div>)}
        </>}
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
      <footer className="dialog-actions shared-client-actions">
        {peer.status === 'approved' && <button className="text-button" disabled={busy} onClick={async () => {
          setBusy(true); setError('');
          try {
            if (!plugin) await request('sharing_add_plugin', { peerId: peer.id });
            else await request(enabled ? 'connector_disconnect' : 'connector_connect', { connectorId: plugin.manifest.id });
            await refreshPlugins({ invalidate: true });
          } catch (error) { setError(error.message); } finally { setBusy(false); }
        }}>{enabled ? '停用自动化调用' : '启用自动化调用'}</button>}
        {['expired', 'rejected', 'revoked', 'unknown'].includes(peer.status) && <button className="button button-secondary" disabled={busy} onClick={async () => {
          setBusy(true); setError('');
          try { await act('sharing_reapply', { peerId: peer.id }); }
          catch (error) { setError(error.message); } finally { setBusy(false); }
        }}>重新申请</button>}
        <button className="button button-secondary" disabled={busy} onClick={refresh}>检查连接</button>
        <button className="button button-primary" onClick={onClose}>完成</button>
      </footer>
    </SharingDialog>
    {selected && peer.status === 'approved' && <RemoteFlow peer={peer} flow={selected} onClose={() => setSelected(null)} />}
  </>;
}
