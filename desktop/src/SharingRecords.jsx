import { useEffect, useId, useState } from 'react';
import { CaretRight, ClockCounterClockwise, FileText, SpinnerGap } from '@phosphor-icons/react';
import { request, subscribe } from './api.js';

const labels = { queued: '排队中', running: '运行中', correcting: '运行中', stopping: '停止中', success: '已完成', failed: '失败', stopped: '已取消', interrupted: '已中断', skipped: '已跳过' };
const active = status => ['queued', 'running', 'correcting', 'stopping'].includes(status);
const time = value => value ? new Date(value).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const text = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);

function useSharingRecord(method, params) {
  const key = JSON.stringify(params);
  const [value, setValue] = useState(null), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true, sequence = 0;
    setValue(null); setError('');
    async function refresh() {
      const version = ++sequence;
      try {
        const result = await request(method, JSON.parse(key));
        if (live && version === sequence) { setValue(result); setError(''); }
      } catch (error) { if (live && version === sequence) setError(error.message); }
    }
    refresh();
    const off = subscribe(event => { if (['state', 'sharing_state'].includes(event.event)) refresh(); });
    return () => { live = false; off(); };
  }, [method, key, retry]);
  return { value, error, refresh: () => setRetry(value => value + 1) };
}

function CallDetails({ taskId }) {
  const { value: task, error, refresh } = useSharingRecord('sharing_task_detail', { taskId });
  const [actionError, setActionError] = useState(''), [busy, setBusy] = useState(false), [preview, setPreview] = useState(null);
  return <div className="sharing-call-detail">
        {error && <p className="form-error" role="alert">{error} <button className="text-button" onClick={refresh}>重试</button></p>}
        {!task && !error && <p className="sharing-loading"><SpinnerGap size={16} className="spinning" />正在读取…</p>}
        {task && <>
          <div className="sharing-call-meta">{task.clientNote && <span>{task.clientName}</span>}<span>版本 {task.revision}</span>{task.endedAt && <span>完成于 {time(task.endedAt)}</span>}</div>
          {task.reason && task.status !== 'success' && <p className="form-error">{task.reason}</p>}
          <section><h3>输入</h3>{task.inputs.length ? task.inputs.map((item, index) => <div className="sharing-call-value" key={index}><span>{item.label}</span><pre>{'value' in item ? text(item.value) : item.name || '未提供'}</pre></div>) : <p className="sharing-note">无需输入</p>}</section>
          {(Object.keys(task.outputs).length > 0 || task.files.length > 0) && <section><h3>结果</h3>{Object.entries(task.outputs).map(([key, value]) => <pre className="sharing-call-output" key={key}>{text(value)}</pre>)}{task.files.map(file => <div key={file.index}><button className="sharing-call-file" disabled={busy} aria-expanded={preview?.index === file.index} onClick={async () => {
            if (preview?.index === file.index) { setPreview(null); return; }
            setBusy(true); setActionError('');
            try { setPreview({ ...await request('sharing_task_file', { taskId, index: file.index }), index: file.index }); }
            catch (error) { setActionError(error.message); } finally { setBusy(false); }
          }}><FileText size={18} /><span>{file.label}</span><CaretRight size={16} className={preview?.index === file.index ? 'rotate' : ''} /></button>{preview?.index === file.index && <pre className="sharing-call-output">{preview.content}</pre>}</div>)}</section>}
          <details className="sharing-call-execution" open={task.status === 'failed' || active(task.status)}><summary>执行详情</summary>
            {task.steps.map(step => {
              const attempts = task.stepResults.filter(result => result.stepId === step.id);
              const result = attempts.at(-1);
              const status = result?.status || (task.stage === step.id && active(task.status) ? 'running' : null);
              return <div className="sharing-call-step" key={step.id}><div><strong>{step.label}</strong><span>{labels[status] || '未执行'}</span></div>{result?.reason && <pre>{result.reason}</pre>}{attempts.length > 1 && <details><summary>历次尝试（{attempts.length}）</summary>{attempts.map((attempt, index) => <p key={index}>第 {index + 1} 次 · {labels[attempt.status] || attempt.status}{attempt.reason && ` · ${attempt.reason}`}</p>)}</details>}</div>;
            })}
            {task.checks.length > 0 && <div className="sharing-call-checks">{task.checks.map((check, index) => <p key={index}>{check.label} · {check.passed ? '通过' : '未通过'}</p>)}</div>}
          </details>
          {task.events.length > 0 && <details className="sharing-call-logs"><summary>运行日志</summary><div className="real-events">{task.events.map((event, index) => <div key={index}><time>{time(event.at)}</time><pre>{event.text}</pre></div>)}</div></details>}
        </>}
        {actionError && <p className="form-error" role="alert">{actionError}</p>}
      {task && active(task.status) && <div><button className="button button-secondary" disabled={busy || task.status === 'stopping'} onClick={async () => {
        setBusy(true); setActionError('');
        try { await request('sharing_cancel', { taskId }); refresh(); }
        catch (error) { setActionError(error.message); } finally { setBusy(false); }
      }}>停止任务</button></div>}
    </div>;
}

function CallRow({ task, expanded, onToggle }) {
  const id = useId();
  return <article className={`sharing-call-row ${expanded ? 'expanded' : ''}`}>
    <button className="sharing-call-toggle" aria-expanded={expanded} aria-controls={id} onClick={onToggle}>
      <time>{time(task.createdAt)}</time><span className="sharing-call-client" title={task.clientName}>{task.clientNote || task.clientName}</span>
      <span className={`sharing-status ${task.status === 'success' ? 'online' : ''}`}>{labels[task.status]}</span><CaretRight size={16} className={expanded ? 'rotate' : ''} />
    </button>
    {expanded && <div id={id}><CallDetails taskId={task.id} /></div>}
  </article>;
}

export function SharingRecords({ flow }) {
  const [offset, setOffset] = useState(0), [selected, setSelected] = useState(null);
  const { value: page, error, refresh } = useSharingRecord('sharing_history', { flowId: flow.id, offset });
  return <div className="sharing-records">
    <h3 className="sharing-records-title">调用记录</h3>
    {error && <p className="form-error" role="alert">{error} <button className="text-button" onClick={refresh}>重试</button></p>}
    {!page && !error && <p className="sharing-loading"><SpinnerGap size={16} className="spinning" />正在读取…</p>}
    {page && (page.items.length ? <div className="sharing-calls">
      {page.items.map(task => <CallRow key={task.id} task={task} expanded={selected === task.id} onToggle={() => setSelected(selected === task.id ? null : task.id)} />)}
    </div> : <div className="sharing-records-empty"><ClockCounterClockwise size={28} /><p>暂无调用记录</p></div>)}
    {(offset > 0 || page?.hasMore) && <div className="sharing-history-pages"><button className="button button-secondary" disabled={offset === 0} onClick={() => { setOffset(value => Math.max(0, value - 50)); setSelected(null); }}>上一页</button><span>第 {offset / 50 + 1} 页</span><button className="button button-secondary" disabled={!page?.hasMore} onClick={() => { setOffset(value => value + 50); setSelected(null); }}>下一页</button></div>}
  </div>;
}
