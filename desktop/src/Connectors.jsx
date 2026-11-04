import { useEffect, useRef, useState } from 'react';
import { ArrowRight, ArrowSquareOut, Check, Code, FileText, GithubLogo, Kanban, Link, Plus, SpinnerGap, TerminalWindow, X } from '@phosphor-icons/react';
import { native, request, subscribe } from './api.js';

const labels = { connected: '已连接', disconnected: '未连接', connecting: '连接中', authorization_required: '需要授权', error: '连接异常' };
const blank = (type = 'mcp', id = '') => type === 'cli'
  ? { id: '', name: '', type: 'cli', version: '1', command: [''], auth: { mode: 'auto' } }
  : { id, name: '', type: 'mcp', version: '1', transport: 'http', url: '', auth: { mode: 'oauth' } };

function useDialog(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement, dialog = ref.current;
    dialog?.querySelector('button')?.focus();
    const onKey = event => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab') return;
      const nodes = [...dialog.querySelectorAll('button:not(:disabled), input:not([type="file"]), textarea, select, a[href], summary')].filter(node => node.getClientRects().length);
      if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0]?.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, []);
  return ref;
}

function Mark({ manifest }) {
  const provider = manifest.provider || manifest.id;
  const Icon = { tapd: Kanban, 'tencent-docs': FileText, github: GithubLogo }[provider];
  return <span className={`connector-mark ${['feishu', 'wecom', 'tapd', 'tencent-docs', 'github'].includes(provider) ? provider : 'custom'}`}>
    {['feishu', 'wecom'].includes(provider) ? <span className="connector-brand" /> : Icon ? <Icon size={27} weight={provider === 'github' ? 'fill' : 'regular'} /> : manifest.type === 'cli' ? <TerminalWindow size={25} /> : <Link size={25} />}
  </span>;
}
function Status({ status }) { return <span className={`connector-status ${status}`}><i />{labels[status] || '未连接'}</span>; }
function ToolList({ tools }) {
  return <details className="connector-detail-section connector-tools" open={tools.length <= 6 || undefined}>
    <summary>可用操作 <span>{tools.length}</span></summary>
    {tools.map(tool => <div key={tool.name}><b title={tool.description}>{tool.title || tool.description || tool.name}</b><small>{tool.risk === 'unknown' ? '待评估' : tool.effect === 'read' ? '读取' : '写入'}</small></div>)}
  </details>;
}
function description(manifest, generated, isBuiltin = Boolean(manifest.provider)) {
  if (isBuiltin && manifest.provider === 'feishu') return '读取云文档与知识库，发送任务结果';
  if (isBuiltin && manifest.provider === 'wecom') return '连接企业文档与消息，让流程触达团队';
  return generated?.text || manifest.description || (manifest.type === 'cli' ? '通过本机命令行接入工具与服务' : '通过 MCP 接入外部工具与数据');
}
function date(value) { return new Date(value).toLocaleString('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }

function ConnectorEditor({ initial, onClose, onSaved }) {
  const [generatedId] = useState(() => `mcp-${crypto.randomUUID()}`);
  const [manifest, setManifest] = useState(() => JSON.stringify(initial || blank('mcp', generatedId), null, 2));
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const dialog = useDialog(onClose);
  let value; try { value = JSON.parse(manifest); } catch { value = null; }
  function field(key, next) { if (value) setManifest(JSON.stringify({ ...value, [key]: next }, null, 2)); }
  function program(path) {
    if (!value) return;
    const slug = name => name.replace(/[^a-zA-Z0-9_-]/g, '-').replace(/^[^a-zA-Z]+/, '').slice(0, 55);
    const name = path.split('/').at(-1), oldName = value.command?.[0]?.split('/').at(-1) || '';
    setManifest(JSON.stringify({ ...value, command: [path], name: !value.name || value.name === oldName ? name : value.name,
      id: !value.id || value.id === slug(oldName) ? slug(name) || `cli-${crypto.randomUUID().slice(0, 8)}` : value.id }, null, 2));
  }
  async function chooseProgram() {
    try { const path = await window.loops.choose('file'); if (path) program(path); }
    catch (e) { setError(e.message); }
  }
  async function importConfig(event) {
    try {
      const file = event.target.files?.[0]; if (!file) return;
      const definition = JSON.parse(await file.text());
      if (!definition || !['cli', 'mcp'].includes(definition.type)) throw new Error('请选择 Loops 连接器配置。');
      if (definition.type === 'mcp') definition.id = initial?.id || definition.id || generatedId;
      setManifest(JSON.stringify(definition, null, 2)); setError('');
    } catch (e) { setError(e instanceof SyntaxError ? '导入的配置不是有效 JSON。' : e.message); }
    finally { event.target.value = ''; }
  }
  async function save(event) {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      const value = JSON.parse(manifest);
      if (value.type === 'mcp') value.id = initial?.id || value.id || generatedId;
      await request('connector_save', { manifest: value });
      if (!initial) await request('connector_connect', { connectorId: value.id });
      await onSaved(value.id, !initial); onClose();
    } catch (e) { setError(e instanceof SyntaxError ? '连接器定义不是有效 JSON。' : e.message); }
    finally { setBusy(false); }
  }
  return <div className="dialog-backdrop connector-backdrop"><section ref={dialog} className="dialog connector-editor" role="dialog" aria-modal="true" aria-label={initial ? '编辑连接器' : '自定义连接器'}>
    <header className="dialog-header"><h2>{initial ? '编辑连接器' : '自定义连接器'}</h2><button className="icon-button" aria-label="关闭连接器编辑" onClick={onClose}><X size={20} /></button></header>
    <form onSubmit={save} className="connector-form"><div className="connector-template-actions"><div className="connector-kind-options">{[['mcp', 'MCP 服务', Link], ['cli', 'CLI 工具', TerminalWindow]].map(([type, label, Icon]) => <button key={type} type="button" aria-pressed={value?.type === type} onClick={() => { if (value?.type === type) return; setManifest(JSON.stringify(blank(type, initial?.id || generatedId), null, 2)); setError(''); }}><Icon size={17} />{label}</button>)}</div><label className="connector-import-label">导入配置<input type="file" accept=".json,application/json" className="connector-import" onChange={importConfig} /></label></div>
      {value && <div className="connector-basic-fields">{value.type === 'cli' && <label className="connector-field-wide">命令行工具<div className="connector-program-field"><input aria-label="命令行工具" value={value.command?.[0] || ''} placeholder="输入命令名或选择本机程序" onChange={e => program(e.target.value)} required />{native && <button type="button" className="button button-secondary" onClick={chooseProgram}>选择文件</button>}</div></label>}<label className="connector-field-wide">名称<input value={value.name || ''} placeholder="例如：团队知识库" onChange={e => field('name', e.target.value)} required /></label>{value.type === 'mcp' && value.transport !== 'stdio' && <label className="connector-field-wide">服务地址<input type="url" value={value.url || ''} placeholder="https://example.com/mcp" onChange={e => field('url', e.target.value)} required /></label>}{value.type === 'mcp' && <label className="connector-field-wide">授权方式<select aria-label="授权方式" value={value.auth?.mode || 'none'} onChange={e => field('auth', { ...value.auth, mode: e.target.value })}><option value="none">无需授权</option>{value.transport !== 'stdio' && <><option value="oauth">网页登录（OAuth）</option><option value="token">访问令牌</option></>}{['cli', 'auto'].includes(value.auth?.mode) && <option value={value.auth.mode} disabled>命令行登录（已有配置）</option>}</select></label>}</div>}
      {value?.type === 'cli' && <p className="connector-hint">连接时自动发现能力与授权方式。</p>}
      <details className="connector-config" open={!value || undefined}><summary><Code size={16} />Loops 配置</summary><p className="connector-hint">可导入或编辑完整连接器定义。</p><textarea aria-label="连接器定义" className="connector-manifest" value={manifest} onChange={e => setManifest(e.target.value)} rows={13} spellCheck={false} required /></details>
      {error && <p className="form-error" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={busy}>{busy ? '保存中…' : !initial ? '添加并连接' : '保存连接器'}</button></div>
    </form></section></div>;
}

function ConnectionInformation({ request, busy, onSubmit }) {
  const [token, setToken] = useState(''), [clientId, setClientId] = useState(''), [secret, setSecret] = useState('');
  async function submit(event) {
    event.preventDefault();
    const values = request.kind === 'token' ? { token } : { client_id: clientId, ...(secret ? { client_secret: secret } : {}) };
    setToken(''); setSecret('');
    await onSubmit(values);
  }
  return <form className="connector-information" onSubmit={submit}>
    <p>{request.kind === 'token' ? '此服务需要访问密钥。' : '此服务需要使用服务方提供的应用信息完成授权。'}</p>
    <div className="connector-secret-fields">{request.kind === 'token'
      ? <label>访问密钥<input type="password" autoComplete="new-password" value={token} onChange={e => setToken(e.target.value)} required disabled={busy} /></label>
      : <><label>应用 ID<input autoComplete="off" value={clientId} onChange={e => setClientId(e.target.value)} required disabled={busy} /></label>{request.secretSupported && <label>应用密钥{!request.secretRequired && '（选填）'}<input type="password" autoComplete="new-password" value={secret} onChange={e => setSecret(e.target.value)} required={request.secretRequired} disabled={busy} /></label>}</>}
    </div><button className="button button-primary" disabled={busy}>继续连接</button>
  </form>;
}

function ConnectorDetails({ item, busy, checking, error, action, onEdit, onClose }) {
  const dialog = useDialog(onClose), { manifest } = item, working = busy === manifest.id;
  const operationCount = item.capabilities?.operationCount ?? item.tools?.length ?? 0;
  const authorizationMode = item.authorizationInfo?.mode || manifest.auth?.mode;
  return <div className="dialog-backdrop connector-backdrop"><section ref={dialog} className="dialog connector-details" role="dialog" aria-modal="true" aria-label={`${manifest.name}连接设置`}>
    <header className="dialog-header"><h2>连接设置</h2><button className="icon-button" aria-label="关闭连接设置" onClick={onClose}><X size={20} /></button></header>
    <div className="connector-detail-body"><div className="connector-detail-heading"><Mark manifest={manifest} /><div><h3>{manifest.name}</h3><p>{description(manifest, item.generatedDescription, item.builtin)}</p></div><Status status={item.status} /></div>
      <div className="connector-detail-section"><div className="connector-info-row"><span>账号</span><b>{item.authorizationInfo?.account || (item.status === 'connected' ? '已授权' : '尚未连接')}</b></div>{manifest.provider === 'feishu' && <div className="connector-info-row"><label htmlFor="connector-identity">连接身份</label><select id="connector-identity" aria-label="飞书连接身份" value={manifest.identity || 'user'} disabled={working || item.enabled || item.status === 'connecting'} onChange={e => action('connector_save', manifest.id, { manifest: { ...manifest, identity: e.target.value } })}><option value="user">用户</option><option value="bot">应用机器人</option></select></div>}
      {item.authorizationInfo?.renewable && <div className="connector-info-row"><span>自动续期</span><b className="connector-renew"><Check size={15} />已支持</b></div>}{item.authorizationInfo?.refreshExpiresAt && <div className="connector-info-row"><span>续期凭证有效期</span><b>{date(item.authorizationInfo.refreshExpiresAt)}</b></div>}</div>
      <p className={`connector-detail-message${!checking && item.status === 'connected' ? ' success' : ''}`} role="status" aria-live="polite">{checking ? '正在检查授权状态与可用操作…' : item.status === 'connected' ? <>{`检查通过，已发现 ${operationCount} 项操作。`}{item.checkedAt && <span className="connector-check-time">{new Date(item.checkedAt).toLocaleString('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>}</> : item.message}</p>{error && <p className="form-error" role="alert">{error}</p>}
      {item.credentialRequest && <ConnectionInformation key={item.credentialRequest.kind} request={item.credentialRequest} busy={working} onSubmit={values => action('connector_provide_credentials', manifest.id, { kind: item.credentialRequest.kind, values })} />}
      {item.authorization?.qrcode && <img className="connector-qrcode" src={item.authorization.qrcode} alt="企业微信授权二维码" />}{item.authorization?.code && <p className="connector-device-code">在 GitHub 授权页输入 <strong>{item.authorization.code}</strong></p>}{item.authorization?.url && <a className="connector-auth-link" href={item.authorization.url} target="_blank" rel="noreferrer" onClick={e => { if (native) { e.preventDefault(); action('connector_open_authorization', manifest.id); } }}>{item.credentialRequest?.kind === 'token' ? '获取访问密钥' : '打开授权页面'}<ArrowSquareOut size={16} /></a>}
      {item.capabilities ? <div className="connector-detail-section connector-tools"><h4>自动发现的能力 <span>{item.capabilities.operationCount}</span></h4><p className="connector-hint">搭建任务时按需检查操作参数与授权。已保存的任务使用固定契约。</p><p className="connector-hint">{item.capabilities.toolVersion}</p></div> : !!item.tools?.length && <ToolList tools={item.tools} />}
      {authorizationMode !== 'none' && <p className="connector-hint">{item.authorizationInfo?.renewable ? '任务运行时自动续期；授权被撤销或无法续期时，任务会等待重新连接。' : authorizationMode === 'token' ? '访问密钥失效时会提示更新。' : item.status === 'connected' ? '此连接未声明自动续期能力，授权方式由服务或工具自身管理。' : '连接后检查授权与自动续期能力。'}</p>}
      {!item.builtin && <div className="connector-custom-settings"><button className="auto-link" disabled={working} onClick={onEdit}>编辑连接器</button></div>}
    </div><footer className="dialog-actions">{item.enabled && <button className="connector-disconnect" disabled={working} onClick={() => action('connector_disconnect', manifest.id)}>断开连接</button>}{item.credentialRequest ? <button className="button button-secondary" onClick={onClose}>稍后</button> : item.status === 'connecting' ? <button className="button button-secondary" disabled={working} onClick={() => action('connector_disconnect', manifest.id)}>取消连接</button> : <button className="button button-primary" disabled={working} onClick={() => action(item.enabled ? 'connector_check' : 'connector_connect', manifest.id)}>{working && <SpinnerGap className="spinning" size={16} />}{checking ? '检查中…' : item.enabled ? '检查连接' : '连接'}</button>}</footer>
  </section></div>;
}

export function Connectors() {
  const [items, setItems] = useState([]), [presets, setPresets] = useState([]), [editor, setEditor] = useState(null), [selected, setSelected] = useState(null), [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(''), [busy, setBusy] = useState(''), [pendingAction, setPendingAction] = useState('');
  async function refresh() {
    const [connections, available] = await Promise.all([request('connector_list'), request('connector_presets')]);
    setItems(connections); setPresets(available); setLoaded(true);
  }
  useEffect(() => {
    refresh().catch(e => { setError(e.message); setLoaded(true); });
    const off = subscribe(event => { if (['connector_state', 'connected'].includes(event.event)) refresh().catch(e => setError(e.message)); });
    return off;
  }, []);
  async function action(method, connectorId, extra = {}) {
    setBusy(connectorId); setPendingAction(method); setError('');
    try { await request(method, { connectorId, ...extra }); await refresh(); }
    catch (e) { setError(e.message); await refresh().catch(() => {}); }
    finally { setBusy(''); setPendingAction(''); }
  }
  const installed = new Set(items.map(item => item.manifest.id)), detail = items.find(item => item.manifest.id === selected);
  async function connect(manifest, added = false) {
    if (!added) await action('connector_save', manifest.id, { manifest });
    setSelected(manifest.id);
    await action('connector_connect', manifest.id);
  }
  return <main className="workspace connectors-workspace"><header className="topbar connectors-topbar"><h1>连接器</h1><button className="button button-secondary" onClick={() => setEditor({})}><Plus size={17} />自定义连接器</button></header>
    <div className="connectors-body"><div className="connectors-content">{error && !detail && <p className="form-error" role="alert">{error}</p>}{!loaded && <p className="connector-loading"><SpinnerGap className="spinning" size={18} />正在读取连接器…</p>}
      <div className="connector-grid">{[...items, ...presets.filter(p => !installed.has(p.id)).map(manifest => ({ manifest, status: 'disconnected', builtin: true }))].map(item => {
        const { manifest } = item, added = installed.has(manifest.id), connected = item.status === 'connected', working = busy === manifest.id;
        return <article className="connector-card" key={manifest.id}><header><Mark manifest={manifest} /><Status status={item.status} /></header><h3>{manifest.name}</h3><p className="connector-description">{description(manifest, item.generatedDescription, item.builtin)}</p><footer><span className="connector-account">{connected ? item.authorizationInfo?.account || '连接可用' : item.status === 'connecting' ? item.message : item.builtin || ['feishu', 'wecom'].includes(manifest.id) ? '尚未连接' : manifest.type === 'cli' ? '本机 CLI' : 'MCP'}{connected && item.authorizationInfo?.renewable && <span className="connector-auto-renew" title="支持自动续期"><Check size={12} />自动续期</span>}</span><button className={`button ${connected || item.status === 'connecting' ? 'button-secondary' : 'button-primary'}`} disabled={working} aria-label={`${connected ? '管理' : item.status === 'connecting' ? '查看' : '连接'}${manifest.name}`} onClick={() => connected || item.status === 'connecting' || item.credentialRequest ? setSelected(manifest.id) : connect(manifest, added)}>{working && <SpinnerGap className="spinning" size={14} />}{connected ? '管理' : item.status === 'connecting' ? '查看' : '连接'}{!working && <ArrowRight size={14} />}</button></footer></article>;
      })}</div>
    </div></div>{detail && !editor && <ConnectorDetails item={detail} error={error} busy={busy} checking={busy === selected && pendingAction === 'connector_check'} action={action} onClose={() => setSelected(null)} onEdit={() => { setEditor({ manifest: detail.manifest }); }} />}{editor && <ConnectorEditor initial={editor.manifest} onClose={() => setEditor(null)} onSaved={async (id, connected) => { await refresh(); if (connected) setSelected(id); }} />}
  </main>;
}
