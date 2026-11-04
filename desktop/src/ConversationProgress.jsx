import { useEffect, useState } from 'react';
import { CaretRight, SpinnerGap, CheckCircle, WarningCircle } from '@phosphor-icons/react';

export function elapsedLabel(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}
export function ConversationProgress({ process, stream }) {
  const running = process.status === 'running';
  const [expanded, setExpanded] = useState(running), [now, setNow] = useState(Date.now());
  useEffect(() => { if (!running) setExpanded(false); }, [running]);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const elapsed = process.durationMs ?? now - Date.parse(process.startedAt);
  const events = running && !stream && process.events.at(-1)?.type === 'status' && process.events.at(-1).text === process.current ? process.events.slice(0, -1) : process.events;
  const label = running ? '处理中' : process.status === 'success' ? '处理完成' : process.status === 'stopped' ? '已停止' : '处理未完成';
  return <div className="conversation-progress">
    <button className="progress-summary" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {running ? <SpinnerGap size={16} className="spinning" /> : <CaretRight size={16} className={expanded ? 'rotate' : ''} />}
      <span>{label} · {elapsedLabel(elapsed)}</span>
    </button>
    {expanded && <div className="progress-events">
      {events.map((item, index) => <div className={`progress-event ${item.type}`} key={item.id || index}>
        {item.type === 'tool' && (item.status === 'running' ? <SpinnerGap size={15} className="spinning" /> : item.status === 'success' ? <CheckCircle size={15} /> : <WarningCircle size={15} />)}
        <span>{item.text}{item.type === 'tool' && <small>{item.status === 'success' ? '已完成' : item.status === 'failed' ? '失败' : item.status === 'interrupted' ? '已中断' : '执行中'}</small>}</span>
      </div>)}
      {running && <p className="progress-current" role="status">{stream || process.current}</p>}
    </div>}
  </div>;
}
