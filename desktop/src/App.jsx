import { createRefreshTask } from './refresh-task.mjs';
import { StepModelSettings } from './StepModelSettings';
import { workflowPaths, shortPathText } from './workflow-paths.js';
import { StepDirectory } from './StepDirectory.jsx';
import { PluginsIcon } from './PluginIcon.jsx';
import { ConversationProgress } from './ConversationProgress.jsx';
import { StepAgentSettings } from './StepAgentSettings.jsx';
import { Sharing } from './Sharing.jsx';
import { Settings } from './Settings.jsx';
import { ModelContext } from './ModelContext.jsx';
import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ArrowCounterClockwise, CaretDown, CaretRight, CheckCircle, Circle, DotsThree, FileText, FloppyDisk, Globe, Gear, GitPullRequest, Link, Play, Plus, SpinnerGap, Stop, Trash, User, WarningCircle, X } from '@phosphor-icons/react';
import { native, request, subscribe } from './api.js';
import { workflowOutputs } from './workflow-outputs.js';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PanelDivider, ResizablePanels } from './ResizablePanels.jsx';
import { NewLoop } from './NewLoop.jsx';
import { Composer } from './Composer.jsx';
import { Automations } from './Automations.jsx';
import { Avatar } from './Avatar.jsx';
import { Plugins } from './Plugins.jsx';
import { PluginContext, PluginProvider, PluginMessage } from './PluginContext.jsx';

const activeStatuses = ['queued', 'running', 'correcting', 'stopping'];
const statuses = { queued: '准备中', running: '运行中', correcting: '自动纠正中', stopping: '停止中', stopped: '已停止', interrupted: '已中断', failed: '失败', success: '完成' };
const runName = (run) => `${run.mode === 'trial' ? '试跑' : '运行'} #${run.number}`;
const basename = (value = '') => value.split('/').filter(Boolean).at(-1) || '尚未选择';
const cx = (...values) => values.filter(Boolean).join(' ');
const date = (value) => new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function Dialog({ title, onClose, children }) {
  const panel = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    panel.current?.querySelector('button,input,select,textarea')?.focus();
    const key = (event) => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab') return;
      const items = [...panel.current.querySelectorAll('button:not(:disabled),input:not(:disabled),select,textarea')].filter((element) => element.getClientRects().length);
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
      if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
    };
    window.addEventListener('keydown', key);
    return () => { window.removeEventListener('keydown', key); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <div className="dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="dialog" role="dialog" aria-modal="true" aria-label={title} ref={panel}><header className="dialog-header"><h2>{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={21} /></button></header>{children}</section></div>;
}

function RenameLoopDialog({ item, act, onClose }) {
  const [title, setTitle] = useState(item.title), [error, setError] = useState(''), [working, setWorking] = useState(false);
  return <Dialog title="重命名 Loop" onClose={onClose}><form className="setup-form" onSubmit={async (event) => { event.preventDefault(); setWorking(true); try { await act('rename', { loopId: item.id, title }); onClose(); } catch (error) { setError(error.message); setWorking(false); } }}><div className="setup-fields"><div className="field"><label htmlFor="loop-name">名称</label><input id="loop-name" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} required /></div>{error && <p className="form-error" role="alert">{error}</p>}</div><div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={working || !title.trim()}>保存</button></div></form></Dialog>;
}

function DeleteLoopDialog({ item, act, onClose }) {
  const [working, setWorking] = useState(false), [error, setError] = useState('');
  return <Dialog title="删除 Loop" onClose={onClose}><div className="contract-details"><p>删除「{item.title}」？可在回收站恢复。</p>{error && <p className="form-error" role="alert">{error}</p>}</div><div className="dialog-actions artifact-actions"><button className="button button-secondary" onClick={onClose}>取消</button><button className="button button-danger" disabled={working} onClick={async () => { setWorking(true); try { await act('delete', { loopId: item.id }); onClose(); } catch (error) { setError(error.message); setWorking(false); } }}>{working ? '正在删除…' : '删除'}</button></div></Dialog>;
}

function LoopNavigation({ onSharing, sharingActive, sharingPending, loops, activeId, busy, onSelect, onNew, onSettings, act, onAutomations, automationsActive, onPlugins, pluginsActive, listError, listLoading, onRetryList }) {
  const [menu, setMenu] = useState(null), [dialog, setDialog] = useState(null);
  useEffect(() => {
    if (!menu) return;
    const dismiss = (event) => { if (!event.target.closest('[data-loop-menu]')) setMenu(null); };
    const key = (event) => { if (event.key === 'Escape') { setMenu(null); document.getElementById(`loop-more-${menu.item.id}`)?.focus(); } };
    const moved = () => setMenu(null);
    document.addEventListener('mousedown', dismiss); document.addEventListener('keydown', key);
    window.addEventListener('resize', moved); window.addEventListener('scroll', moved, true);
    return () => { document.removeEventListener('mousedown', dismiss); document.removeEventListener('keydown', key); window.removeEventListener('resize', moved); window.removeEventListener('scroll', moved, true); };
  }, [menu]);
  return <><aside id="loop-navigation" className="sidebar" aria-label="Loop 导航"><div className="traffic-lights" aria-hidden="true"><Circle weight="fill" /><Circle weight="fill" /><Circle weight="fill" /></div><div className="wordmark">Loops</div><button className="new-loop button-primary" onClick={onNew}><Plus size={21} />新建 Loop</button><button className={`loop-item automation-entry ${automationsActive ? 'active' : ''}`} aria-current={automationsActive ? 'page' : undefined} onClick={onAutomations}><ArrowCounterClockwise size={21} /><span>自动化任务</span></button><nav className="loop-list" aria-label="我的 Loops">{loops.map((item) => <div className={cx('loop-row', item.id === activeId && 'active')} key={item.id}><button className="loop-item" onClick={() => { setMenu(null); onSelect(item.id); }} aria-current={item.id === activeId ? 'page' : undefined}><span>{item.title}</span>{item.runs.some((run) => activeStatuses.includes(run.status)) && <SpinnerGap size={16} className="spinning" />}</button><button id={`loop-more-${item.id}`} className="icon-button loop-more" data-loop-menu aria-label={`更多操作：${item.title}`} aria-haspopup="menu" aria-expanded={menu?.item.id === item.id} onClick={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); setMenu(menu?.item.id === item.id ? null : { item, left: Math.min(bounds.left, window.innerWidth - 170), top: Math.min(bounds.bottom + 4, window.innerHeight - 60) }); }}><DotsThree size={24} weight="bold" /></button></div>)}</nav>{listError && <div className="loop-load-error" role="alert"><p>{listError}</p><button className="auto-link" disabled={listLoading} onClick={onRetryList}>{listLoading ? '正在重试…' : '重新加载流程'}</button></div>}<div className="sidebar-footer"><button className={`settings-entry ${pluginsActive ? 'active' : ''}`} aria-current={pluginsActive ? 'page' : undefined} onClick={onPlugins}><PluginsIcon size={20} />插件</button><button className={`settings-entry ${sharingActive ? 'active' : ''}`} aria-current={sharingActive ? 'page' : undefined} onClick={onSharing}><Globe size={20} />共享{!!sharingPending && <span className="sharing-nav-count">{sharingPending}</span>}</button><button className="settings-entry" onClick={onSettings}><Gear size={20} />设置</button></div></aside>{menu && <div className="loop-menu" data-loop-menu role="menu" aria-label={`${menu.item.title}的操作`} style={{ left: menu.left, top: menu.top }}><button role="menuitem" ref={(element) => element?.focus()} onClick={() => { setDialog({ type: 'rename', item: menu.item }); setMenu(null); }}>重命名</button><button role="menuitem" className="delete-menu-item" disabled={busy[menu.item.id] || menu.item.runs.some((run) => activeStatuses.includes(run.status))} title={busy[menu.item.id] ? '请等 Loops AI 回复结束' : menu.item.runs.some((run) => activeStatuses.includes(run.status)) ? '请先停止运行' : undefined} onClick={() => { setDialog({ type: 'delete', item: menu.item }); setMenu(null); }}><Trash size={18} />删除</button></div>}{dialog?.type === 'rename' && <RenameLoopDialog item={dialog.item} act={act} onClose={() => setDialog(null)} />}{dialog?.type === 'delete' && <DeleteLoopDialog item={dialog.item} act={act} onClose={() => setDialog(null)} />}</>;
}

function RunCard({ loop, run, artifact, selected }) {
  const [details, setDetails] = useState(false);
  const active = activeStatuses.includes(run.status);
  const Icon = active ? SpinnerGap : run.status === 'success' ? CheckCircle : run.status === 'stopped' ? Stop : WarningCircle;
  const current = run.definition.steps.find((step) => step.id === run.stage);
  const latestChecks = [...new Map(run.checks.map((check) => [`${check.stage}-${check.label}`, check])).values()];
  const passedChecks = latestChecks.filter((check) => check.passed).length;
  return <div id={run.id} className={cx('run-card', run.status, selected && 'selected-run')}><header className="run-card-header"><Icon size={32} weight={active ? 'regular' : 'fill'} className={active ? 'spinning' : ''} /><div className="run-card-title"><h3>{`${runName(run)} · ${statuses[run.status]}`}</h3><span className="run-version">版本 {run.revision}{run.mode === 'trial' ? ' · 草稿' : ''}</span></div></header><div className="run-card-body">
    <div className="run-input"><FileText size={16} />{run.input.name}<small>{date(run.startedAt)}</small></div>
    {active && <div className="run-current"><strong>{current?.label || '准备工作区'}</strong></div>}
    {run.reason && run.status !== 'success' && <p className="result-reason">{run.reason.trim().split(/\r?\n/, 1)[0]}</p>}
    <div className="real-artifacts">{run.artifacts.map((item) => <button key={item.path || item.url} onClick={() => artifact(item, run)}>{item.url ? <GitPullRequest size={19} /> : <FileText size={19} />}<span>{item.title}</span><ArrowUpRight size={16} /></button>)}</div>
    {!!latestChecks.length && <details className="run-checks" open={run.status === 'failed'}><summary><CaretRight size={15} />{passedChecks === latestChecks.length ? `${latestChecks.length} 项检查通过` : `${passedChecks}/${latestChecks.length} 项检查通过`}</summary>{latestChecks.map((check) => <div className="check-row" key={`${check.stage || check.kind}-${check.label || check.kind}`}>{check.passed ? <CheckCircle size={21} /> : <WarningCircle size={21} />}<span>{check.label}</span><span className="check-count">{check.passed ? '通过' : '未通过'}</span></div>)}</details>}
    <button className="disclosure" aria-expanded={details} onClick={() => setDetails(!details)}><CaretRight size={15} className={details ? 'rotate' : ''} />执行详情{!!run.corrections && ` · 按失败策略重跑 ${run.corrections} 次`}</button>
    {details && <div className="real-events">{!!run.intermediateArtifacts?.length && <div className="real-artifacts">{run.intermediateArtifacts.map((item) => <button key={item.path} onClick={() => artifact(item, run)}><FileText size={19} /><span>{item.title}</span><ArrowUpRight size={16} /></button>)}</div>}{run.reason?.includes('\n') && <pre>{run.reason}</pre>}{run.events.map((event, index) => <div key={index}><time>{date(event.at)}</time><pre>{event.text}</pre></div>)}</div>}
  </div></div>;
}

const toolLabels = { agent: 'Agent', codex: 'Codex', python: 'Python', command: '命令行', mcp: '插件' };
function WorkflowInput({ loop, onClose, act }) {
  const [values, setValues] = useState(Object.fromEntries(loop.definition.inputs.map((item) => [item.id, loop.config.inputDefaults[item.id] || ''])));
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const mode = loop.savedRevision === loop.revision ? '运行' : '试跑';
  return <Dialog title={`本次${mode}`} onClose={onClose}><form className="setup-form" onSubmit={async (event) => { event.preventDefault(); setBusy(true); try { await act('start', { loopId: loop.id, inputs: values, ...(mode === '运行' ? { revision: loop.revision } : { expectedRevision: loop.revision }) }); onClose(); } catch (error) { setError(error.message); } finally { setBusy(false); } }}><div className="setup-fields">{loop.definition.inputs.map((item) => <div className="field" key={item.id}><label htmlFor={`input-${item.id}`}>{item.label}</label><div className="input-with-action">{item.kind === 'text' ? <textarea id={`input-${item.id}`} rows={5} required={item.required} value={values[item.id]} onChange={(event) => setValues({ ...values, [item.id]: event.target.value })} placeholder="粘贴或输入本次内容" /> : <input id={`input-${item.id}`} required={item.required} value={values[item.id]} onChange={(event) => setValues({ ...values, [item.id]: event.target.value })} placeholder="选择本次输入文件" />}{native && item.kind === 'file' && <button type="button" className="button button-secondary" onClick={async () => { const path = await window.loops.choose('file'); if (path) setValues({ ...values, [item.id]: path }); }}>选择</button>}</div></div>)}{error && <p className="form-error" role="alert">{error}</p>}</div><div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={busy}><Play size={18} />开始{mode}</button></div></form></Dialog>;
}
function WorkflowOutputs({ definition }) {
  const outputs = workflowOutputs(definition);
  return outputs.length ? outputs.map((output, index) => <p key={index}>{output.label}{output.path && !output.label.includes(basename(output.path)) && `（${basename(output.path)}）`}</p>) : <p>无文件输出</p>;
}

function RollbackDialog({ loop, version, act, onClose, onRestored }) {
  const [working, setWorking] = useState(false), [error, setError] = useState('');
  return <Dialog title={`回退到版本 ${version}`} onClose={onClose}>
    <div className="contract-details">
      <p>{loop.revision !== loop.savedRevision ? '这会替换未保存的草稿。' : '这会以该版本创建草稿。'}试跑成功后可保存为新版本。</p>
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
    <div className="dialog-actions artifact-actions">
      <button className="button button-secondary" onClick={onClose}>取消</button>
      <button className="button button-primary" disabled={working} onClick={async () => {
        setWorking(true);
        try {
          await act('restore_version', { loopId: loop.id, revision: version, expectedRevision: loop.revision, discardDraft: true });
          onRestored();
        } catch (error) { setError(error.message); }
        finally { setWorking(false); }
      }}>回退</button>
    </div>
  </Dialog>;
}

function StepDetail({ loop, step, home, selectedRun, onDirectoryChange, onScript, onAgentChange, onModelChange, disabled }) {
  const execution = step.execution;
  const { agents, agentsLoaded, refreshAgents } = useContext(PluginContext);
  useEffect(() => {
    if (!agentsLoaded && ['agent', 'codex'].includes(execution.tool)) void refreshAgents();
  }, [execution.tool, agentsLoaded, refreshAgents]);
  const paths = workflowPaths(loop, selectedRun);
  const show = (value, file = false) => { const full = paths.resolve(value, file); return <span className="workflow-path" title={shortPathText(full, home)}>{shortPathText(full, home)}</span>; };
  const commandText = values => show(values.map(value => paths.resolve(value)).join(' '));
  const failure = step.onFailure;
  const executionDetails = <>
    <section><h3>执行方式</h3>{['agent', 'codex'].includes(execution.tool) && <StepAgentSettings execution={execution} config={loop.config} clients={agentsLoaded ? agents : null} disabled={disabled} onChange={onAgentChange} />}{!['agent', 'codex'].includes(execution.tool) && <p>{toolLabels[execution.tool]}{execution.tool === 'mcp' && ` · ${execution.connectorId} / ${execution.operation}`}</p>}{execution.command && <pre>{commandText(execution.command)}</pre>}{execution.script && <><button className="script-link" onClick={() => onScript(execution.script)}>查看脚本 · {show(`${paths.bundle}/${execution.script}`)}<ArrowUpRight size={13} /></button>{!!execution.args?.length && <pre>参数：{commandText(execution.args)}</pre>}</>}{['agent', 'codex'].includes(execution.tool) ? <StepDirectory key={step.id} home={home} defaultPath={paths.workspace} displayValue={paths.resolve(execution.cwd || '${workspace}', true)} value={execution.cwd || ''} disabled={disabled} onChange={onDirectoryChange} /> : execution.tool !== 'mcp' && <p>工作目录：{show(execution.cwd || '${workspace}', true)}</p>}</section>
    {execution.tool === 'command' && <section><h3>运行控制</h3><p>每次调用最多 {execution.timeoutSeconds ?? 180} 秒；{execution.effect === 'read' ? '只读，可按失败策略重试。' : '可能写入，发起后不自动重复执行。'}</p></section>}{execution.forEach && <section><h3>逐项处理</h3><p>{show(execution.forEach)}</p><p>按列表顺序执行；没有项目时跳过调用，继续后续步骤。</p></section>}
    {!!step.checks?.length && <section><h3>完成条件</h3>{step.checks.map((check, index) => <div key={index}>{check.kind === 'command' ? <><p>{check.label || '命令检查'} · 退出码 {check.expectedExitCode ?? 0}</p><pre>{commandText(check.command)}</pre></> : <p>文件存在：{show(check.path, true)}</p>}</div>)}</section>}
    <section><h3>失败处理</h3><p>{!failure || failure.action === 'stop' ? '结束本次运行，保留结果与失败原因。' : `${failure.action === 'retry' ? '重试本步骤' : `返回「${loop.definition.steps.find((item) => item.id === failure.target)?.label || failure.target}」`}，最多自动纠正 ${failure.maxAttempts} 次。`}</p></section>
  </>;
  return <div id={`step-${step.id}`} className="step-detail step-configuration">
    {execution.tool !== 'python' && executionDetails}
    <section><h3>任务要求</h3><p>{show(step.instructions)}</p></section>
    {!!Object.keys(step.inputs || {}).length && <section><h3>使用输入</h3>{Object.entries(step.inputs).map(([key, value]) => <p key={key}>{show(value)}</p>)}</section>}

    {!!step.outputs?.length && <section><h3>产出</h3>{step.outputs.map((item) => <p key={item.id}>{item.label}<small>{item.kind === 'value' ? '数据' : show(item.path, true)}</small></p>)}</section>}
    {execution.tool === 'python' && execution.args?.includes('--model') && <section><h3>模型资源</h3><StepModelSettings execution={execution} config={loop.config} disabled={disabled} onChange={onModelChange} /></section>}
    {execution.tool === 'python' && <details className="step-technical-details"><summary>执行详情</summary>{executionDetails}</details>}
  </div>;
}

function ReportSections({ record }) {
  return <><p className="report-summary">{record.summary}</p>{record.sections.map((section, index) => <section key={index}><h3>{section.heading}</h3>{section.body && <p className="message-text">{section.body}</p>}{!!section.columns?.length && <div className="report-table"><table><thead><tr>{section.columns.map((column, i) => <th key={i}>{column}</th>)}</tr></thead><tbody>{section.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody></table></div>}</section>)}</>;
}

function ArtifactContent({ content, filename = '', title = '', stepLabels = {} }) {
  if (/\.md$/i.test(filename)) return <div className="artifact-body markdown-artifact delivery-report"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ h1: ({ children }) => children === title ? null : <h1>{children}</h1>, table: ({ children }) => <div className="report-table"><table>{children}</table></div>, img: ({ alt }) => <span>{alt || ''}</span>, a: ({ children, href }) => <span>{children}{href ? ` (${href})` : ''}</span> }}>{content}</Markdown></div>;
  let record;
  try { record = JSON.parse(content); } catch { /* Text and patches retain their original formatting. */ }
  if (record && Array.isArray(record.sections)) return <div className="artifact-body delivery-report"><ReportSections record={record} /></div>;
  if (record && Array.isArray(record.steps) && Array.isArray(record.checks)) return <div className="artifact-body validation-summary"><p className="report-summary">{record.summary}</p><section><h3>步骤结果</h3>{record.steps.map((step, index) => <p key={index}>{stepLabels[step.stepId] || step.stepId} · {step.status === 'success' ? '完成' : '未通过'}</p>)}</section><section><h3>实际检查</h3>{record.checks.map((check, index) => <div key={index}><p>{check.label} · {check.passed ? '通过' : '未通过'} · 退出码 {check.exitCode}</p><pre>{check.command?.join(' ')}{'\n'}{check.output}</pre></div>)}</section></div>;
  if (record && typeof record.summary === 'string') return <div className="artifact-body"><p className="message-text">{record.summary}</p></div>;
  return <div className="artifact-body"><pre>{content}</pre></div>;
}

export function App() {
  const [loops, setLoops] = useState([]), [activeId, setActive] = useState(() => { const saved = sessionStorage.getItem('loops-active'); return saved === '__connectors__' ? '__plugins__' : saved || '__new__'; }), [drafts, setDrafts] = useState({});
  const [modelId, setModelId] = useState('');
  const [selectingAgent, setSelectingAgent] = useState(false);
  const [loaded, setLoaded] = useState(false), [creating, setCreating] = useState(false);
  const [listError, setListError] = useState(''), [listLoading, setListLoading] = useState(false);
  const [loopLoader] = useState(() => createRefreshTask(() => request('list'), value => {
    setLoops(value);
    setLoaded(true);
    setListError('');
  }));
  const loadLoops = useCallback(() => {
    setListLoading(true);
    return loopLoader.refresh().catch(error => setListError(error.message)).finally(() => setListLoading(false));
  }, [loopLoader]);
  const submitting = useRef(false), requestedRun = useRef(null);
  const [agentStatus, setAgentStatus] = useState({});
  const [busy, setBusy] = useState({}), [stream, setStream] = useState({}), [modal, setModal] = useState(null), [error, setError] = useState(''), [settings, setSettings] = useState(null), [openStep, setOpenStep] = useState(null), [selectedRun, selectRun] = useState(null);
  const bottom = useRef(null), follow = useRef(true), input = useRef(null);
  const [automationSeed, setAutomationSeed] = useState(null);
  const [automationDrafts, setAutomationDrafts] = useState({});
  const [settingsError, setSettingsError] = useState('');
  const closeSettings = useCallback(() => setModal(null), []);
  const loadSettings = useCallback(async () => {
    setSettingsError('');
    try {
      const value = await request('settings');
      setSettings(value);
      return value;
    } catch (error) { setSettingsError(error.message); }
  }, []);
  const openSettings = () => {
    setModal({ type: 'settings' });
    if (!settings && settingsError) void loadSettings();
  };
  function updateModels(value) {
    setSettings(previous => ({ ...previous, ...value }));
    setModelId(previous => value.models.some(item => item.id === previous && item.configured) ? previous : '');
  }
  const automationsActive = activeId === '__automations__';
  const pluginsActive = activeId === '__plugins__';
  const sharingActive = activeId === '__sharing__';
  const [sharingPending, setSharingPending] = useState(0);
  const storedLoop = activeId === '__new__' || automationsActive || pluginsActive || sharingActive ? null : loops.find((item) => item.id === activeId) || loops[0];
  const loop = storedLoop || { id: '__new__', title: '新建 Loop', definition: null, messages: [], runs: [], revision: 0, workdir: null };
  const localRuns = loop.runs.filter(run => !run.sharedTask);
  const pending = creating || Boolean(loop && (busy[loop.id] || loop.messages.some(m => m.process?.status === 'running'))), activeRun = localRuns.find((run) => activeStatuses.includes(run.status));
  const unsaved = loop?.definition && loop.savedRevision !== loop.revision;
  const canSave = unsaved && loop.runs.some(run => run.revision === loop.revision && run.mode === 'trial' && run.status === 'success');
  const [versionChoice, chooseVersion] = useState(null);
  const [versionResult, setVersionResult] = useState(null);
  const selectedVersion = versionChoice?.loopId === loop.id && versionChoice.workingRevision === loop.revision ? versionChoice.revision : loop.revision;
  const historical = selectedVersion !== loop.revision;
  const versionKey = historical ? `${loop.id}:${selectedVersion}` : null;
  const selectedSnapshot = versionResult?.key === versionKey ? versionResult?.snapshot : null;
  const viewLoop = historical ? selectedSnapshot && { ...loop, ...selectedSnapshot, savedRevision: selectedVersion } : loop;
  const flowDefinition = viewLoop?.definition;
  const viewingDraft = !historical && unsaved;
  useEffect(() => {
    if (!versionKey) return;
    let current = true;
    request('version', { loopId: loop.id, revision: selectedVersion }).then(
      snapshot => { if (current) setVersionResult({ key: versionKey, snapshot }); },
      error => { if (current) setVersionResult({ key: versionKey, error: error.message }); },
    );
    return () => { current = false; };
  }, [versionKey, loop.id, selectedVersion]);
  async function act(method, params) {
    const result = await request(method, params);
    if (method === 'new' || method === 'restore') setActive(result.id);
    if (method === 'delete') {
      setLoops((previous) => previous.filter((item) => item.id !== params.loopId));
      setActive((previous) => previous === params.loopId ? null : previous);
      setDrafts((previous) => { const next = { ...previous }; delete next[params.loopId]; return next; });
    }
    return result;
  }
  async function safe(method, params) { try { setError(''); return await act(method, params); } catch (error) { setError(error.message); } }
  useEffect(() => {
    let mounted = true;
    void loadLoops();
    request('sharing_state').then(value => mounted && setSharingPending(value.clients.filter(client => client.status === 'pending').length)).catch(() => {});
    loadSettings().then(value => {
      if (mounted && value && !value.defaultModelId) setModal(previous => previous || { type: 'settings' });
    });
    const unsubscribe = subscribe((event) => {
      if (event.event === 'state') { loopLoader.accept(event.loops); }
      if (event.event === 'sharing_state') request('sharing_state').then(value => mounted && setSharingPending(value.clients.filter(client => client.status === 'pending').length)).catch(() => {});
      if (event.event === 'connected' && event.reconnected) void loadLoops();
      if (event.event === 'agent_start') { setBusy((old) => ({ ...old, [event.loopId]: true })); setStream((old) => ({ ...old, [event.loopId]: '' })); }
      if (event.event === 'agent_status') { setAgentStatus(old => ({ ...old, [event.loopId]: event.text })); if (event.phase === 'waiting') setStream(old => ({ ...old, [event.loopId]: '' })); }
      if (event.event === 'agent_delta_reset') setStream(old => ({ ...old, [event.loopId]: '' }));
      if (event.event === 'agent_delta') setStream((old) => ({ ...old, [event.loopId]: (old[event.loopId] || '') + event.text }));
      if (event.event === 'agent_done') { setBusy((old) => ({ ...old, [event.loopId]: false })); setStream((old) => ({ ...old, [event.loopId]: '' })); }
      if (['engine_error', 'connection_error'].includes(event.event)) setError(event.message);
    });
    return () => { mounted = false; unsubscribe(); loopLoader.cancel(); };
  }, [loadSettings, loadLoops, loopLoader]);
  useEffect(() => { if (loop) sessionStorage.setItem('loops-active', sharingActive ? '__sharing__' : pluginsActive ? '__plugins__' : automationsActive ? '__automations__' : loop.id); selectRun(requestedRun.current); if (requestedRun.current) { follow.current = false; document.getElementById(requestedRun.current)?.scrollIntoView({ block: 'center' }); requestedRun.current = null; } }, [loop?.id, loop?.revision, automationsActive, pluginsActive, sharingActive]);
  useEffect(() => { setOpenStep(null); }, [loop?.id, selectedVersion, automationsActive, pluginsActive, sharingActive]);
  useEffect(() => { chooseVersion(null); }, [loop.id]);
  useEffect(() => { if (!modal) input.current?.focus(); }, [loop.id, loaded, modal]);
  const eventCount = loop?.runs.reduce((sum, run) => sum + run.events.length, 0) || 0;
  useEffect(() => { if (follow.current) bottom.current?.scrollIntoView({ block: 'end' }); }, [loop?.id, loop?.messages, eventCount, stream]);
  async function send(event, plugins = []) {
    event.preventDefault(); const text = drafts[loop.id]?.trim(); if (!text || historical || pending || submitting.current) return;
    submitting.current = true;
    const id = storedLoop?.id, draftId = loop.id;
    setCreating(!id); follow.current = true;
    try {
      setError('');
      const result = await request('chat', { ...(id ? { loopId: id } : {}), text, modelId, plugins });
      if (!id && result.loopId) setActive(result.loopId);
      setDrafts((old) => ({ ...old, [draftId]: '' }));
      return true;
    } catch (error) { setError(error.message); }
    finally { submitting.current = false; setCreating(false); input.current?.focus(); }
  }

  async function selectAgent(selection, stepId) {
    setSelectingAgent(true);
    try { await safe('agent_selection', { loopId: loop.id, stepId, ...selection }); }
    finally { setSelectingAgent(false); }
  }

  async function showArtifact(item, run) {
    try {
      if (item.url) { if (native) await window.loops.openUrl(item.url); else window.open(item.url, '_blank', 'noopener'); return; }
      const file = await act('artifact', { loopId: loop.id, runId: run.id, path: item.path });
      setModal({ type: 'artifact', ...file, filename: item.path, stepLabels: Object.fromEntries(run.definition.steps.map((step) => [step.id, step.label])) });
    } catch (error) { setError(error.message); }
  }
  if (!loaded) return <div className="startup">
    {listError && !listLoading ? <WarningCircle size={30} /> : <SpinnerGap className="spinning" size={30} />}
    <h1>Loops</h1>
    {listError ? <div className="loop-load-error" role="alert"><p>{listError}</p><button className="button button-primary" disabled={listLoading} onClick={loadLoops}>{listLoading ? '正在重试…' : '重新加载流程'}</button></div> : <p>{error || '正在连接本地工作台…'}</p>}
  </div>;
  const feed = loop ? [...loop.messages.map((item) => ({ ...item, kind: 'message', at: item.createdAt })), ...localRuns.map((run) => ({ ...run, kind: 'run', at: run.startedAt }))].sort((a, b) => a.at.localeCompare(b.at)) : [];
  return <PluginProvider warmup={loaded && Boolean(settings || settingsError)}><ModelContext.Provider value={{ settings, modelId, setModelId, updateModels, openSettings, settingsError }}><ResizablePanels className={cx('app-shell real-client', native && 'native-client')}><LoopNavigation onSharing={() => setActive('__sharing__')} sharingActive={sharingActive} sharingPending={sharingPending} listError={listError} listLoading={listLoading} onRetryList={loadLoops} onPlugins={() => setActive('__plugins__')} pluginsActive={pluginsActive} onAutomations={() => { setAutomationSeed(null); setActive('__automations__'); }} automationsActive={automationsActive} loops={loops} activeId={storedLoop?.id} busy={busy} act={act} onNew={() => { setActive('__new__'); setError(''); follow.current = true; input.current?.focus(); }} onSelect={(id) => { setActive(id); follow.current = true; }} onSettings={openSettings} /><PanelDivider panel="sidebar" />
    {sharingActive ? <Sharing loops={loops} /> : pluginsActive ? <Plugins /> : automationsActive ? <Automations chatDrafts={automationDrafts} setChatDrafts={setAutomationDrafts} key={automationSeed || 'all'} initialLoopId={automationSeed} onLoop={(id, runId) => { requestedRun.current = runId; setActive(id); }} /> : !storedLoop ? <NewLoop draftKey="loop:__new__" input={input} text={drafts.__new__ || ''} onChange={(text) => setDrafts((old) => ({ ...old, __new__: text }))} onSubmit={send} pending={creating} error={error} onDismissError={() => setError('')} /> : <main className="workspace"><header className={cx('topbar', !loop.definition && 'new-loop-topbar')}><div className="loop-context"><div className="title-line"><h1>{loop.title}</h1>{loop.definition && <span className={cx('save-state', viewingDraft ? 'unsaved' : 'saved')}>{viewingDraft ? '未保存' : `版本 ${selectedVersion}`}</span>}</div></div><div className="toolbar-actions">{loop.definition && <><button className="button button-secondary" disabled={!loop.savedRevision} onClick={() => { setAutomationSeed(loop.id); setActive('__automations__'); }}>自动化</button>{activeRun ? <button className="button button-secondary stop-button" onClick={() => safe('stop', { loopId: loop.id, runId: activeRun.id })} disabled={activeRun.status === 'stopping'}><Stop size={18} />停止</button> : <button className={cx('button', viewingDraft ? 'button-secondary' : 'button-primary')} disabled={!flowDefinition} onClick={() => setModal({ type: 'run', loop: viewLoop })}><Play size={19} />{viewingDraft ? '试跑' : '运行'}</button>}{viewingDraft && <button className="button button-primary" disabled={!canSave || pending || selectingAgent} title={!canSave ? '当前版本试跑成功后才能保存' : undefined} onClick={() => safe('save', { loopId: loop.id, expectedRevision: loop.revision })}><FloppyDisk size={19} />保存</button>}</>}</div></header>
    {error && <div className="service-error" role="alert"><WarningCircle size={18} /><span>{error}</span><button aria-label="关闭提示" onClick={() => setError('')}><X size={17} /></button></div>}
    <div className="workspace-body"><section className="conversation" aria-label="与 Loops AI 对话"><div className="messages" onScroll={(event) => { const e = event.currentTarget; follow.current = e.scrollHeight - e.scrollTop - e.clientHeight < 100; }}>
    {!feed.length && <div className="message"><Avatar /><div className="message-content"><div className="message-author">Loops AI</div><p className="welcome-copy">描述你希望自动完成的工作。</p></div></div>}
    {feed.map((item) => <div className="message" key={item.id}><Avatar role={item.role} /><div className="message-content">{item.kind !== 'run' && <div className="message-author">{item.role === 'user' ? '你' : 'Loops AI'}<span>{date(item.at)}</span></div>}{item.process ? <ConversationProgress process={item.process} stream={stream[loop.id]} /> : item.kind === 'run' ? <RunCard loop={loop} run={item} artifact={showArtifact} selected={selectedRun === item.id} /> : item.role === 'assistant' ? <div className="markdown-artifact conversation-answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt || ''}</span>, a: ({ children, href }) => <span>{children}{href ? ` (${href})` : ''}</span> }}>{item.text}</Markdown></div> : item.role === 'user' ? <PluginMessage text={item.text} plugins={item.plugins} /> : <p className={cx('message-text', item.role === 'error' && 'message-error')}>{item.text}</p>}</div></div>)}
    {pending && !loop.messages.some(m => m.process?.status === 'running') && <div className="message"><Avatar /><div className="message-content"><div className="message-author">Loops AI <SpinnerGap size={17} className="spinning" /></div><p className="message-text">{stream[loop.id] || agentStatus[loop.id] || '正在处理…'}</p></div></div>}<div ref={bottom} /></div>
    {!historical && <Composer key={loop.id} draftKey={`loop:${loop.id}`} input={input} text={drafts[loop.id] || ''} onChange={(text) => setDrafts((old) => ({ ...old, [loop.id]: text }))} onSubmit={send} pending={pending} onCancel={() => safe('cancel_chat', { loopId: loop.id })} placeholder={loop.definition ? '反馈哪里需要调整…' : '输入需求…'} rows={loop.definition ? 1 : 2} />}
    </section><PanelDivider panel="inspector" /><aside id="loop-inspector" className="inspector" aria-label="流程与运行记录"><section>{loop.definition && <div className="section-heading workflow-heading">
      <h2>流程</h2>
      <div className="workflow-version-actions">
        {historical && <button className="version-rollback" disabled={!selectedSnapshot || pending || selectingAgent} onClick={() => setModal({ type: 'rollback', loop, version: selectedVersion })}>回退</button>}
        <div className="workflow-version-field">
          <select className="workflow-version-select" aria-label="流程版本" value={selectedVersion} onChange={event => {
            setVersionResult(null);
            chooseVersion({ loopId: loop.id, workingRevision: loop.revision, revision: Number(event.target.value) });
          }}>
            {unsaved && <option value={loop.revision}>{canSave ? '未保存草稿' : '草稿 · 待试跑'}</option>}
            {[...(loop.versions || [])].reverse().map(version => <option key={version.revision} value={version.revision}>版本 {version.revision}</option>)}
          </select>
          <CaretDown size={14} aria-hidden="true" />
        </div>
      </div>
    </div>}
    {historical && !selectedSnapshot && (versionResult?.key === versionKey && versionResult.error
      ? <p className="form-error" role="alert">{versionResult.error}</p>
      : <p className="empty-copy">正在读取版本…</p>)}
    {flowDefinition && <><ol className="flow-list">{flowDefinition.steps.map((step, index) => <li key={step.id}><button className="flow-step" aria-expanded={openStep === step.id} aria-controls={`step-${step.id}`} onClick={() => setOpenStep(openStep === step.id ? null : step.id)}><span className="step-number">{index + 1}</span><span>{step.label}</span><CaretRight className={cx('step-chevron', openStep === step.id && 'rotate')} size={14} /></button>{openStep === step.id && <StepDetail loop={viewLoop} step={step} home={settings?.homeDirectory} selectedRun={selectedRun} onDirectoryChange={cwd => act('agent_directory', { loopId: loop.id, stepId: step.id, cwd })} disabled={historical || pending || selectingAgent} onAgentChange={selection => selectAgent(selection, step.id)} onModelChange={selection => act('step_model_selection', { loopId: loop.id, stepId: step.id, ...selection })} onScript={(path) => setModal({ type: 'artifact', title: path, content: flowDefinition.scripts[path] })} />}</li>)}</ol><button className="contract-summary" onClick={() => setModal({ type: 'contract', definition: flowDefinition })}><span>输入和输出</span><CaretRight size={17} /></button></>}
    </section>
    {!!localRuns.length && <section className="history-section"><h2>运行记录</h2><div className="run-history">{[...localRuns].reverse().map((run) => <button key={run.id} className={cx('history-row', run.status, selectedRun === run.id && 'active-history')} onClick={() => { selectRun(run.id); follow.current = false; document.getElementById(run.id)?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }}><span>{run.status === 'success' ? <CheckCircle size={25} weight="fill" /> : activeStatuses.includes(run.status) ? <SpinnerGap size={25} className="spinning" /> : <WarningCircle size={25} />}</span><span><b>{`${runName(run)} · ${statuses[run.status]}`}</b><small>版本 {run.revision}{run.mode === 'trial' ? ' · 草稿' : ''} · {run.input.name}</small></span><CaretRight size={16} /></button>)}</div></section>}</aside></div></main>}
    {modal?.type === 'rollback' && <RollbackDialog loop={modal.loop} version={modal.version} act={act} onClose={() => setModal(null)} onRestored={() => { chooseVersion(null); setModal(null); }} />}
    {modal?.type === 'run' && <WorkflowInput loop={modal.loop} onClose={() => setModal(null)} act={act} />}
    {modal?.type === 'contract' && <Dialog title="输入和输出" onClose={() => setModal(null)}><div className="contract-details"><section><h3>输入</h3><p>{modal.definition.inputs.map((item) => item.label).join('、') || '无需外部输入'}</p></section><section><h3>输出</h3><WorkflowOutputs definition={modal.definition} /></section></div><div className="dialog-actions artifact-actions"><button className="button button-primary" onClick={() => setModal(null)}>完成</button></div></Dialog>}
    {modal?.type === 'settings' && <Settings settings={settings} loadError={settingsError} onRetry={loadSettings} act={act} updateModels={updateModels} onClose={closeSettings} />}
    {modal?.type === 'artifact' && <Dialog title={modal.title} onClose={() => setModal(null)}><ArtifactContent content={modal.content} filename={modal.filename} title={modal.title} stepLabels={modal.stepLabels} /><div className="dialog-actions artifact-actions"><button className="button button-primary" onClick={() => setModal(null)}>完成</button></div></Dialog>}
  </ResizablePanels></ModelContext.Provider></PluginProvider>;
}
