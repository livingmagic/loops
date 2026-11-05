import { PluginIcon } from './PluginIcon.jsx';
import { useContext, useEffect, useRef, useState } from 'react';
import { ArrowRight, ArrowSquareOut, Check, Code, FileText, GithubLogo, Kanban, Link, Plus, Robot, SpinnerGap, TerminalWindow, X } from '@phosphor-icons/react';
import { native, request } from './api.js';
import { AgentModel } from './AgentModel.jsx';
import { PluginContext } from './PluginContext.jsx';
import { pluginTypes } from './plugin-selection.mjs';

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
  return <span className="connector-mark"><PluginIcon plugin={manifest} size={29} /></span>;
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

function PluginEditor({ initial, onClose, onSaved, agents, onAgent, onRefreshAgents }) {
  const [kind, setKind] = useState(initial?.type || 'mcp');
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
      if (!definition || !['cli', 'mcp'].includes(definition.type)) throw new Error('请选择 Loops 插件配置。');
      if (definition.type === 'mcp') definition.id = initial?.id || definition.id || generatedId;
      setKind(definition.type); setManifest(JSON.stringify(definition, null, 2)); setError('');
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
    } catch (e) { setError(e instanceof SyntaxError ? '插件定义不是有效 JSON。' : e.message); }
    finally { setBusy(false); }
  }
  return <div className="dialog-backdrop connector-backdrop"><section ref={dialog} className="dialog connector-editor" role="dialog" aria-modal="true" aria-label={initial ? '编辑插件' : '添加插件'}>
    <header className="dialog-header"><h2>{initial ? '编辑插件' : '添加插件'}</h2><button className="icon-button" aria-label="关闭插件编辑" onClick={onClose}><X size={20} /></button></header>
    <form onSubmit={save} className="connector-form"><div className="connector-template-actions"><div className="connector-kind-options">{[['mcp', 'MCP 服务', Link], ['cli', 'CLI 工具', TerminalWindow], ...(!initial ? [['agent', 'Agent', Robot]] : [])].map(([type, label, Icon]) => <button key={type} type="button" aria-pressed={kind === type} onClick={() => { if (kind === type) return; setKind(type); if (type === 'agent') return; setManifest(JSON.stringify(blank(type, initial?.id || generatedId), null, 2)); setError(''); }}><Icon size={17} />{label}</button>)}</div>{kind !== 'agent' && <label className="connector-import-label">导入配置<input type="file" accept=".json,application/json" className="connector-import" onChange={importConfig} /></label>}</div>
      {kind === 'agent' ? <div className="plugin-agent-choices"><p>安装并登录本机 Agent 客户端后，重新检查即可使用。</p>{agents.map(client => <button className="settings-row" type="button" key={client.id} onClick={() => onAgent(client.id)}><strong>{client.name}</strong><span>{client.ready ? '可用' : client.installed ? '不可用' : '未安装'}</span><ArrowRight size={16} /></button>)}<button type="button" className="button button-secondary" onClick={onRefreshAgents}>重新检查</button></div> : <>
      {value && <div className="connector-basic-fields">{value.type === 'cli' && <label className="connector-field-wide">命令行工具<div className="connector-program-field"><input aria-label="命令行工具" value={value.command?.[0] || ''} placeholder="输入命令名或选择本机程序" onChange={e => program(e.target.value)} required />{native && <button type="button" className="button button-secondary" onClick={chooseProgram}>选择文件</button>}</div></label>}<label className="connector-field-wide">名称<input value={value.name || ''} placeholder="例如：团队知识库" onChange={e => field('name', e.target.value)} required /></label>{value.type === 'mcp' && value.transport !== 'stdio' && <label className="connector-field-wide">服务地址<input type="url" value={value.url || ''} placeholder="https://example.com/mcp" onChange={e => field('url', e.target.value)} required /></label>}{value.type === 'mcp' && <label className="connector-field-wide">授权方式<select aria-label="授权方式" value={value.auth?.mode || 'none'} onChange={e => field('auth', { ...value.auth, mode: e.target.value })}><option value="none">无需授权</option>{value.transport !== 'stdio' && <><option value="oauth">网页登录（OAuth）</option><option value="token">访问令牌</option></>}{['cli', 'auto'].includes(value.auth?.mode) && <option value={value.auth.mode} disabled>命令行登录（已有配置）</option>}</select></label>}</div>}
      {value?.type === 'cli' && <p className="connector-hint">连接时自动发现能力与授权方式。</p>}
      <details className="connector-config" open={!value || undefined}><summary><Code size={16} />Loops 配置</summary><p className="connector-hint">可导入或编辑完整插件定义。</p><textarea aria-label="插件定义" className="connector-manifest" value={manifest} onChange={e => setManifest(e.target.value)} rows={13} spellCheck={false} required /></details></>}
      {error && <p className="form-error" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button>{kind !== 'agent' && <button className="button button-primary" disabled={busy}>{busy ? '保存中…' : !initial ? '添加并连接' : '保存插件'}</button>}</div>
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

function PluginDetails({ item, busy, checking, error, action, onEdit, onClose }) {
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
      {!item.builtin && <div className="connector-custom-settings"><button className="auto-link" disabled={working} onClick={onEdit}>编辑插件</button></div>}
    </div><footer className="dialog-actions">{item.enabled && <button className="connector-disconnect" disabled={working} onClick={() => action('connector_disconnect', manifest.id)}>断开连接</button>}{item.credentialRequest ? <button className="button button-secondary" onClick={onClose}>稍后</button> : item.status === 'connecting' ? <button className="button button-secondary" disabled={working} onClick={() => action('connector_disconnect', manifest.id)}>取消连接</button> : <button className="button button-primary" disabled={working} onClick={() => action(item.enabled ? 'connector_check' : 'connector_connect', manifest.id)}>{working && <SpinnerGap className="spinning" size={16} />}{checking ? '检查中…' : item.enabled ? '检查连接' : '连接'}</button>}</footer>
  </section></div>;
}

export function Plugins({ onAgentsChange }) {
  const { agents, agentErrors, items, presets, loaded, pageError, refresh: refreshShared, setAgents } = useContext(PluginContext);
  const [selectedAgent, setSelectedAgent] = useState(null), [filter, setFilter] = useState('all');
  const [editor, setEditor] = useState(null), [selected, setSelected] = useState(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(''), [pendingAction, setPendingAction] = useState('');
  const refresh = () => refreshShared({ invalidate: true });
  const refreshAgents = refresh;
  useEffect(() => { onAgentsChange(agents); }, [agents, onAgentsChange]);
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
  return <main className="workspace connectors-workspace"><header className="topbar connectors-topbar"><h1>插件</h1><button className="button button-secondary" onClick={() => setEditor({})}><Plus size={17} />添加插件</button></header>
    <div className="connectors-body"><div className="connectors-content">{(error || pageError) && !detail && <p className="form-error" role="alert">{error || pageError}</p>}{!loaded && <p className="connector-loading"><SpinnerGap className="spinning" size={18} />正在读取插件…</p>}
      <div className="plugin-filters" role="group" aria-label="插件类型">{[['all', '全部'], ...Object.entries(pluginTypes)].map(([type, label]) => <button type="button" key={type} aria-pressed={filter === type} onClick={() => setFilter(type)}>{label}</button>)}</div>
      {agentErrors.map(item => <p key={item.plugin} className="form-error" role="alert">{item.plugin}：{item.message}</p>)}
      <div className="connector-grid">{agents.filter(() => filter === 'all' || filter === 'agent').map(client => <article className="connector-card" key={`agent:${client.id}`}><header><span className="connector-mark"><PluginIcon plugin={{ ...client, type: 'agent' }} size={29} /></span><span className={`connector-status ${client.ready ? 'connected' : 'disconnected'}`}><i />{client.ready ? '可用' : client.installed ? '不可用' : '未安装'}</span></header><h3>{client.name}</h3><p className="connector-description">{client.detail}</p><footer><span className="connector-account">Agent</span><button className="button button-secondary" onClick={() => setSelectedAgent(client.id)}>配置<ArrowRight size={14} /></button></footer></article>)}{[...items, ...presets.filter(p => !installed.has(p.id)).map(manifest => ({ manifest, status: 'disconnected', builtin: true }))].filter(item => filter === 'all' || item.manifest.type === filter).map(item => {
        const { manifest } = item, added = installed.has(manifest.id), connected = item.status === 'connected', working = busy === manifest.id;
        return <article className="connector-card" key={manifest.id}><header><Mark manifest={manifest} /><Status status={item.status} /></header><h3>{manifest.name}</h3><p className="connector-description">{description(manifest, item.generatedDescription, item.builtin)}</p><footer><span className="connector-account">{pluginTypes[manifest.type]}{connected && item.authorizationInfo?.renewable && <span className="connector-auto-renew" title="支持自动续期"><Check size={12} />自动续期</span>}</span><button className={`button ${connected || item.status === 'connecting' ? 'button-secondary' : 'button-primary'}`} disabled={working} aria-label={`${connected ? '管理' : item.status === 'connecting' ? '查看' : '连接'}${manifest.name}`} onClick={() => connected || item.status === 'connecting' || item.credentialRequest ? setSelected(manifest.id) : connect(manifest, added)}>{working && <SpinnerGap className="spinning" size={14} />}{connected ? '管理' : item.status === 'connecting' ? '查看' : '连接'}{!working && <ArrowRight size={14} />}</button></footer></article>;
      })}</div>
    </div></div>{selectedAgent && <AgentDetails client={agents.find(client => client.id === selectedAgent)} onClose={() => setSelectedAgent(null)} onRefresh={refreshAgents} onSave={async params => { const updated = await request('agent_model_default', params); const next = agents.map(client => client.id === updated.id ? updated : client); setAgents(next); onAgentsChange(next); }} />}{detail && !editor && <PluginDetails item={detail} error={error} busy={busy} checking={busy === selected && pendingAction === 'connector_check'} action={action} onClose={() => setSelected(null)} onEdit={() => { setEditor({ manifest: detail.manifest }); }} />}{editor && <PluginEditor agents={agents} onRefreshAgents={() => refreshAgents().catch(e => setError(e.message))} onAgent={id => { setEditor(null); setSelectedAgent(id); }} initial={editor.manifest} onClose={() => setEditor(null)} onSaved={async (id, connected) => { await refresh(); if (connected) setSelected(id); }} />}
  </main>;
}

function AgentDetails({ client, onClose, onRefresh, onSave }) {
  const dialog = useDialog(onClose), [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <div className="dialog-backdrop connector-backdrop"><section ref={dialog} className="dialog connector-details" role="dialog" aria-modal="true" aria-label={`${client.name} 插件设置`}><header className="dialog-header"><h2>{client.name}</h2><button className="icon-button" aria-label="关闭插件设置" onClick={onClose}><X size={20} /></button></header><div className="connector-detail-body"><AgentModel client={client} onSave={onSave} busy={busy} />{error && <p className="form-error" role="alert">{error}</p>}</div><footer className="dialog-actions"><button className="button button-secondary" disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await onRefresh(); } catch (e) { setError(e.message); } finally { setBusy(false); } }}>{busy ? '检查中…' : '重新检查'}</button><button className="button button-primary" onClick={onClose}>完成</button></footer></section></div>;
}
