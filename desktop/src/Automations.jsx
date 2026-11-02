import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, CaretRight, CheckCircle, DotsThree, Pause, Play, Plus, SpinnerGap, Trash, X } from '@phosphor-icons/react';
import { request, subscribe } from './api.js';
import { Composer } from './Composer.jsx';
import { NewLoop } from './NewLoop.jsx';
import { Avatar } from './Avatar.jsx';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const labels = { queued: '排队中', waiting: '等待 Loop 空闲', running: '运行中', success: '完成', failed: '失败', unknown: '待核对', interrupted: '已中断', stopped: '已停止', sample: '输入样本' };
const time = value => new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const blank = value => JSON.stringify(value || {}, null, 2);
function TaskStatus({ task }) {
  const [text, tone] = !task.revision ? ['搭建中', 'pending'] : task.draftSpec ? ['未保存修改', 'draft'] : !task.enabled ? ['已暂停', 'paused'] : task.sourceStatus === 'error' ? ['异常', 'error'] : ['已启用', 'enabled'];
  return <span className={`auto-status state-${tone}`}>{text}</span>;
}
function TaskMenu({ busy, onDelete }) {
  const menu = useRef(null);
  useEffect(() => {
    const close = event => { if (!menu.current?.contains(event.target)) menu.current?.removeAttribute('open'); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);
  return <details className="auto-task-menu" ref={menu} onKeyDown={event => {
    if (event.key === 'Escape') { menu.current.removeAttribute('open'); menu.current.querySelector('summary').focus(); }
  }}><summary className="icon-button" aria-label="任务更多操作"><DotsThree size={24} weight="bold" /></summary><div className="auto-menu-items"><button disabled={busy} onClick={() => { menu.current.removeAttribute('open'); menu.current.querySelector('summary').focus(); onDelete(); }}><Trash size={18} />删除任务</button></div></details>;
}
function NodeConfiguration({ value, label, bindings }) {
  return <details className="auto-flow-node"><summary aria-label={label}><span><b>{value.label || value.title || (value.lifecycle ? '获取输入' : '交付结果')}</b>{value.lifecycle && <small>{value.lifecycle === 'service' ? '持续采集' : '采集一次'}</small>}</span><CaretRight size={17} /></summary><div className="auto-node-details">
    {Object.keys(value.config || {}).length > 0 && <section><h3>参数</h3><dl>{Object.entries(value.config).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{typeof item === 'string' ? item || '（空）' : JSON.stringify(item)}</dd></div>)}</dl></section>}
    {Object.keys(value.bindings || {}).length > 0 && <section><h3>输入映射</h3><dl>{Object.entries(value.bindings).map(([key, ref]) => <div key={key}><dt>{key}</dt><dd>{bindings[ref] || ref}</dd></div>)}</dl></section>}
    <details className="auto-node-script"><summary>Python 脚本</summary><pre><code>{value.script}</code></pre></details>
  </div></details>;
}
function InputRunNode({ run, showFile }) {
  const input = run.inputNode;
  return <section className="auto-run-node" aria-label="输入节点记录"><div><b>获取输入</b><span>完成</span>{input?.kind === 'reused' && <span>复用输入</span>}{input?.kind === 'snapshot' && <span>输入快照</span>}</div>
    {input?.label && input.label !== '获取输入' && <p className="auto-input-source">{input.label}</p>}
    <p className="auto-node-time">{input?.collectedAt ? `采集于 ${time(input.collectedAt)}` : '原采集时间未记录'}</p>
    <div className="auto-run-files">{Object.entries(run.inputs).map(([key, value]) => <div className="auto-input-file" key={key}><span>{run.inputLabels?.[key] || key}</span>{value.kind === 'file' ? <button className="auto-link" onClick={() => showFile(run, value.path)}>{value.name || value.path.split('/').at(-1)}</button> : <pre>{value.value}</pre>}</div>)}</div>
  </section>;
}
function AutomationRuns({detail, changed, busy, action, setConfirm, showFile, onLoop}) { return <div className="auto-runs">{!detail?.runs.length && <p className="empty-copy">还没有运行记录。可通过对话要求试跑，或启用自动运行。</p>}{[...(detail?.runs || [])].reverse().map(run => <article className={`auto-run ${run.status}`} key={run.id}><header><div><b>{run.testOutputId ? '投递测试 · ' : ''}{labels[run.status]}</b><span>{time(run.createdAt)} · 版本 {run.revision}</span></div><div className="auto-actions">{run.status === 'sample' && <button className="button button-secondary" disabled={Boolean(busy) || changed} onClick={() => setConfirm({ kind: 'run', run })}><Play size={16} />运行样本</button>}{['queued', 'running', 'waiting'].includes(run.status) && <button className="button button-secondary" disabled={Boolean(busy)} onClick={() => action('automation_stop', { runId: run.id })}>停止</button>}{['failed', 'unknown', 'interrupted', 'stopped'].includes(run.status) && <button className="button button-secondary" disabled={Boolean(busy)} onClick={() => setConfirm({ kind: 'retry', run })}>从失败处继续</button>}{run.status === 'success' && <button className="auto-link" disabled={Boolean(busy) || changed} onClick={() => setConfirm({ kind: run.testOutputId ? 'output' : 'run', nodeId: run.testOutputId, run })}>{run.testOutputId ? '再次投递' : '再次运行'}</button>}</div></header>{run.error && <p className="form-error">{run.error}</p>}<InputRunNode run={run} showFile={showFile} />{Object.entries(run.nodes).map(([key, node]) => <div className="auto-run-node" key={key}><div><b>{run.nodeLabels?.[key] || key}</b><span>{labels[node.status]}{node.attempts > 1 ? ` · 第 ${node.attempts} 次尝试` : ''}</span>{node.loopId && node.runId && <button className="auto-link" onClick={() => onLoop(node.loopId, node.runId)}>查看 Loop 运行</button>}</div>{node.startedAt && <p className="auto-node-time">{time(node.startedAt)}{node.endedAt ? ` — ${time(node.endedAt)}` : ''}</p>}<div className="auto-run-files">{Object.entries(node.outputs || {}).map(([name, value]) => <button key={name} className="auto-link" onClick={() => showFile(run, value.path)}>{run.outputLabels?.[`${key}.${name}`] || name}</button>)}</div>{!!node.history?.length && <details><summary>历次尝试</summary>{node.history.map(attempt => <div key={attempt.attempts}><p>第 {attempt.attempts} 次 · {labels[attempt.status]}</p>{attempt.error && <pre>{attempt.error}</pre>}</div>)}</details>}{node.receipt && <details open><summary><CheckCircle size={15} />{node.receipt.status === 'accepted' ? '目标已接收' : '已送达'}</summary><p className="auto-receipt">{node.receipt.target}</p><pre>{blank(node.receipt)}</pre></details>}</div>)}</article>)}{detail?.logs.length > 0 && <details className="auto-logs"><summary>采集与脚本日志</summary><pre>{detail.logs.map(l => `${time(l.at)} ${l.text}`).join('\n')}</pre></details>}</div>;
}

function AutomationDialogs({ preview, confirm, trash, setPreview, setConfirm, setTrash, action, busy, setView }) { return <>{preview && <div className="dialog-backdrop"><section className="dialog" role="dialog" aria-modal="true" aria-label={preview.title}><header className="dialog-header"><h2>{preview.title}</h2><button className="icon-button" aria-label="关闭文件预览" onClick={() => setPreview(null)}><X size={20} /></button></header><pre className="auto-file-preview">{preview.content}</pre></section></div>}{confirm && <div className="dialog-backdrop"><section className="dialog" role="dialog" aria-modal="true" aria-label="确认操作"><header className="dialog-header"><h2>{confirm.kind === 'delete' ? '删除自动化任务' : confirm.kind === 'retry' ? '从失败处继续' : confirm.kind === 'output' ? '试投递输出' : '运行输入'}</h2></header><div className="contract-details"><p>{confirm.kind === 'delete' ? '删除后停止采集，保留队列与历史记录，可在回收站恢复。' : confirm.kind === 'retry' ? '已完成的节点会保留。请先核对失败节点是否已产生外部操作，继续执行可能再次写入外部系统。' : confirm.kind === 'output' ? '将使用已有 Loop 产出，实际投递到此输出节点配置的目标，不重新执行 Loop。' : '将使用这份输入和当前已保存任务版本执行所有 Loop，并实际投递到配置的输出位置。'}</p></div><div className="dialog-actions"><button className="button button-secondary" onClick={() => setConfirm(null)}>取消</button><button className="button button-primary" disabled={Boolean(busy)} onClick={async () => { if (confirm.kind === 'delete') { const result = await action('automation_delete');  } else if (confirm.kind === 'retry') await action('automation_retry', { runId: confirm.run.id, confirm: true }); else if (confirm.kind === 'output') { await action('automation_test_output', { runId: confirm.run.id, nodeId: confirm.nodeId }); setView('runs'); } else await action('automation_run', { runId: confirm.run.id }); setConfirm(null); }}>确认{confirm.kind === 'delete' ? '删除' : confirm.kind === 'retry' ? '继续' : confirm.kind === 'output' ? '投递' : '运行'}</button></div></section></div>}{trash && <div className="dialog-backdrop"><section className="dialog" role="dialog" aria-modal="true" aria-label="自动化回收站"><header className="dialog-header"><h2>自动化回收站</h2><button className="icon-button" aria-label="关闭回收站" onClick={() => setTrash(null)}><X size={20} /></button></header><div className="contract-details">{!trash.length && <p>回收站为空</p>}{trash.map(t => <div className="trash-row" key={t.id}><span>{t.title}</span><button className="button button-secondary" onClick={async () => { const result = await action('automation_restore', { taskId: t.id }); if (result) setTrash(trash.filter(i => i.id !== t.id)); }}>恢复</button></div>)}</div></section></div>}</>;
}

export function Automations({ initialLoopId, onLoop, configured }) {
  const remembered = () => { try { return sessionStorage.getItem('loops-automation-active'); } catch { return null; } };
  const [items, setItems] = useState([]), [catalog, setCatalog] = useState([]), [selected, select] = useState(initialLoopId ? null : remembered);
  const [screen, setScreen] = useState(initialLoopId ? 'new' : remembered() ? 'task' : 'list');
  const [detail, setDetail] = useState(null), [spec, setSpec] = useState(null), [loaded, setLoaded] = useState(false);
  const [view, setView] = useState('conversation'), [error, setError] = useState(''), [busy, setBusy] = useState('');
  const [chatBusy, setChatBusy] = useState(false), [stream, setStream] = useState(''), [chatDrafts, setChatDrafts] = useState({}), [submitting, setSubmitting] = useState(false);
  const [preview, setPreview] = useState(null), [confirm, setConfirm] = useState(null), [trash, setTrash] = useState(null);
  const selectedRef = useRef(selected), sequence = useRef(0), sending = useRef(false), input = useRef(null), bottom = useRef(null), follow = useRef(true);
  function remember(id) { try { if (id) sessionStorage.setItem('loops-automation-active', id); else sessionStorage.removeItem('loops-automation-active'); } catch {} }
  async function refresh(id = selectedRef.current) {
    const [tasks, loops] = await Promise.all([request('automation_list'), request('automation_catalog')]);
    setItems(tasks); setCatalog(loops);
    if (id) {
      const number = ++sequence.current;
      const value = await request('automation_get', { taskId: id });
      if (number !== sequence.current || selectedRef.current !== id) return;
      setDetail(value); setChatBusy(Boolean(value.chatBusy)); setStream(value.chatText || '');
      setSpec(value.draftSpec || value.spec);
    }
    return tasks;
  }
  useEffect(() => {
    let alive = true;
    refresh().then(tasks => { if (alive) { setLoaded(true); if (!tasks.length && !selectedRef.current) setScreen('new'); } }).catch(e => { if (alive) { setError(e.message); setLoaded(true); } });
    const off = subscribe(event => {
      const current = event.taskId === selectedRef.current;
      if (event.event === 'automation_agent_start' && current) { setChatBusy(true); setStream(''); }
      if (event.event === 'automation_agent_delta' && current) setStream(old => old + event.text);
      if (event.event === 'automation_agent_done' && current) { setChatBusy(false); setStream(''); }
      if (['automation_state', 'automation_agent_done', 'automation_draft_updated', 'connected'].includes(event.event)) refresh().catch(e => alive && setError(e.message));
      if (['engine_error', 'connection_error'].includes(event.event)) { setError(event.message); setChatBusy(false); }
    });
    return () => { alive = false; off(); };
  }, []);
  useEffect(() => { input.current?.focus(); follow.current = true; }, [screen, selected]);
  useEffect(() => { if (follow.current) bottom.current?.scrollIntoView({ block: 'end' }); }, [detail?.messages?.length, detail?.runs?.length, stream]);
  useEffect(() => {
    if (!confirm && !preview && !trash) return;
    const previous = document.activeElement;
    const dialog = document.querySelector('.automation-workspace [role=dialog]');
    const controls = () => [...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)')].filter(e => e.getClientRects().length);
    controls()[0]?.focus();
    const key = event => {
      if (event.key === 'Escape') { setConfirm(null); setPreview(null); setTrash(null); }
      if (event.key === 'Tab') { const nodes = controls(); if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1)?.focus(); } else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0]?.focus(); } }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); if (previous?.isConnected) previous.focus(); };
  }, [Boolean(confirm), Boolean(preview), Boolean(trash)]);
  const changed = Boolean(spec && (detail?.draftSpec || !detail?.revision));
  const working = Boolean(busy || submitting || chatBusy);
  const locked = Boolean(detail?.enabled || working);
  function clearSelection(nextScreen) {
    selectedRef.current = null; select(null); remember(null); setDetail(null); setSpec(null); setScreen(nextScreen); setError(''); setStream(''); setChatBusy(false); setView('conversation');
  }
  function leave(nextScreen) {
    clearSelection(nextScreen);
  }
  async function choose(id) {
    selectedRef.current = id; select(id); remember(id); setSpec(null); setDetail(null); setScreen('task'); setView('conversation'); setError('');
    try { await refresh(id); } catch (e) { setError(e.message); }
  }
  async function action(name, params = {}) {
    setBusy(name); setError('');
    try {
      const result = await request(name, { taskId: selectedRef.current, ...params });
      if (name === 'automation_delete') clearSelection('list');
      if (name === 'automation_save') { setDetail(result); setSpec(result.spec); }
      await refresh(); return result;
    } catch (e) { setError(e.message); return null; }
    finally { setBusy(''); }
  }
  async function send(event) {
    event.preventDefault();
    const draftId = selectedRef.current || '__new__';
    const text = chatDrafts[draftId]?.trim();
    if (!text || working || sending.current) return;
    sending.current = true; setSubmitting(true); setError(''); follow.current = true;
    try {
      const result = await request('automation_chat', { ...(selectedRef.current ? { taskId: selectedRef.current } : {}), seedLoopId: initialLoopId, text });
      if (result.taskId) { selectedRef.current = result.taskId; select(result.taskId); remember(result.taskId); setScreen('task'); }
      setChatDrafts(old => ({ ...old, [draftId]: '' }));
      await refresh(result.taskId);
    } catch (e) { setError(e.message); }
    finally { sending.current = false; setSubmitting(false); input.current?.focus(); }
  }
  async function showFile(run, path) {
    const result = await action('automation_file', { runId: run.id, path });
    if (result) setPreview({ title: path.split('/').at(-1), content: result.content });
  }
  const bindingLabels = Object.fromEntries((spec?.nodes || []).flatMap(node => {
    const loop = catalog.find(item => item.id === node.loopId);
    const version = loop?.versions.find(item => item.revision === node.revision);
    return (version?.outputs || []).map(output => [`${node.id}.${output.key}`, `${loop.title} · ${output.label}`]);
  }));
  const draftId = selected || '__new__';
  const text = chatDrafts[draftId] || '';
  const feed = [...(detail?.messages || []).map(m => ({ ...m, kind: 'message', at: m.createdAt })), ...(detail?.runs || []).map(r => ({ ...r, kind: 'run', at: r.createdAt }))].sort((a, b) => a.at.localeCompare(b.at));
  return <main className="automation-workspace auto-conversational">
    <header className="auto-topbar"><div className="auto-title">{screen !== 'list' && <button className="icon-button" aria-label="返回自动化任务列表" onClick={() => leave('list')}><ArrowLeft size={21} /></button>}<h1>{screen === 'task' ? detail?.title || '自动化任务' : '自动化任务'}</h1>{screen === 'task' && <span className={`save-state ${changed ? 'unsaved' : 'saved'}`}>{changed ? '未保存' : detail?.revision ? '已保存' : '搭建中'}</span>}</div><div className="auto-actions">
      {screen === 'task' && spec && <>{changed && <button className="button button-primary" disabled={locked} onClick={() => action('automation_save', { spec })}>保存</button>}{detail?.revision > 0 && <button className="button button-secondary" disabled={working || changed} onClick={() => action('automation_enable', { enabled: !detail.enabled })}>{detail.enabled ? <Pause size={17} /> : <Play size={17} />}{detail.enabled ? '暂停' : '启用'}</button>}</>}
      {screen === 'task' && <TaskMenu key={selected} busy={working} onDelete={() => setConfirm({ kind: 'delete' })} />}
      {screen === 'list' && <button className="button button-secondary" onClick={() => leave('new')} disabled={submitting}><Plus size={18} />新建任务</button>}
    </div></header>
    {screen === 'list' ? <section className="auto-dashboard" aria-label="自动化任务列表">{error && <p className="form-error" role="alert">{error}</p>}{!loaded ? <p>正在加载…</p> : !items.length ? <div className="auto-empty"><h2>描述你希望自动完成的工作</h2><button className="button button-primary" onClick={() => leave('new')}>创建自动化任务</button></div> : items.map(item => <button className="auto-task-row" key={item.id} onClick={() => choose(item.id)}><b>{item.title}</b><TaskStatus task={item} /><span className={`auto-latest-run state-${item.latestRun?.status || 'empty'}`}>{item.latestRun ? labels[item.latestRun.status] : '尚未运行'}</span><CaretRight size={17} /></button>)}<button className="auto-trash" onClick={async () => setTrash(await action('automation_trash'))}>回收站</button></section>
    : screen === 'new' ? <NewLoop input={input} text={text} onChange={value => setChatDrafts(old => ({ ...old, [draftId]: value }))} onSubmit={send} pending={submitting} configured={configured} title="你希望如何自动运行 Loop？" pageLabel="新建自动化任务" placeholder="描述何时获取哪些数据、使用哪些 Loop，以及把结果送到哪里…" pendingLabel="正在创建自动化任务" error={error} onDismissError={() => setError('')} />
    : <><div className="auto-tabs auto-main-tabs" role="tablist" aria-label="任务视图"><button role="tab" aria-selected={view === 'conversation'} onClick={() => setView('conversation')}>对话</button><button role="tab" aria-selected={view === 'runs'} onClick={() => setView('runs')}>运行记录 ({detail?.runs.length || 0})</button></div>
      {error && <div className="service-error" role="alert"><span>{error}</span><button aria-label="关闭自动化错误" onClick={() => setError('')}><X size={18} /></button></div>}
      {view === 'conversation' ? <div className="auto-chat-body"><section className="conversation" aria-label="与 自动化 AI 搭建自动化任务"><div className="messages auto-messages" onScroll={e => { const node = e.currentTarget; follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100; }}>
        {!feed.length && !submitting && <div className="message"><Avatar /><div className="message-content"><div className="message-author">自动化 AI</div><p className="message-text">描述你希望怎样获取数据、运行 Loop 和交付结果。</p></div></div>}
        {feed.map(item => <div className="message" key={item.id}><Avatar role={item.role} /><div className="message-content">{item.kind === 'run' ? <button className={`auto-chat-run ${item.status}`} onClick={() => setView('runs')}><b>{labels[item.status]}</b><span>{time(item.at)}</span><CaretRight size={17} /></button> : <><div className="message-author">{item.role === 'user' ? '你' : '自动化 AI'}<span>{time(item.at)}</span></div><div className={`message-text auto-message-text ${item.role === 'error' ? 'message-error' : ''}`}><Markdown remarkPlugins={[remarkGfm]}>{item.text}</Markdown></div></>}</div></div>)}
        {(chatBusy || submitting) && <div className="message"><Avatar /><div className="message-content"><div className="message-author">自动化 AI <SpinnerGap size={17} className="spinning" /></div><p className="message-text">{stream && detail?.messages?.at(-1)?.text !== stream ? stream : '正在处理…'}</p></div></div>}<div ref={bottom} />
      </div><Composer input={input} text={text} onChange={value => setChatDrafts(old => ({ ...old, [draftId]: value }))} onSubmit={send} pending={chatBusy || submitting} onCancel={selected ? () => request('automation_cancel_chat', { taskId: selected }).catch(e => setError(e.message)) : undefined} placeholder="继续补充需求，或告诉 自动化 AI 哪里需要调整…" rows={1} /></section>
      <aside className="inspector auto-chat-inspector" aria-label="自动化流程与状态"><h2>流程</h2>{!spec ? <p className="empty-copy">随着对话补齐输入、Loop 和交付位置。</p> : <ol className="auto-summary-flow"><li><NodeConfiguration value={spec.source} label="输入节点配置" bindings={bindingLabels} /></li>{spec.nodes.map(node => <li key={node.id}><button className="auto-link" onClick={() => onLoop(node.loopId)}>{catalog.find(l => l.id === node.loopId)?.title || node.id}</button><small>版本 {node.revision}</small></li>)}{spec.outputs.map(sink => <li key={sink.id}><NodeConfiguration value={sink} label={`输出节点配置：${sink.title || '交付结果'}`} bindings={bindingLabels} /></li>)}</ol>}{detail?.sourceError && <p className="form-error">{detail.sourceError}</p>}</aside></div>
      : <section className="auto-content"><AutomationRuns {...{ detail, changed, busy: working, action, setConfirm, showFile, onLoop }} /></section>}
    </>}
    <AutomationDialogs {...{ preview, confirm, trash, setPreview, setConfirm, setTrash, action, busy: working, setView }} />
  </main>;
}
