import { ModelPicker } from './ModelContext.jsx';
import { ArrowUpRight, SpinnerGap, Stop } from '@phosphor-icons/react';

export function Composer({ input, text, onChange, onSubmit, pending, onCancel, placeholder, rows = 2, className = '', note, pendingLabel = '正在创建 Loop' }) {
  return <form className={`composer ${className}`} onSubmit={onSubmit}>
    <textarea ref={input} aria-label="输入需求或反馈" placeholder={placeholder} rows={rows} value={text} disabled={pending && !onCancel} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit(event); } }} />
    {note && <span className="provider-note composer-note">{note}</span>}
    <div className="composer-actions"><ModelPicker disabled={pending} />{pending && onCancel ? <button type="button" className="send-button cancel-agent" aria-label="停止回复" onClick={onCancel}><Stop size={21} /></button> : <button className="send-button" aria-label={pending ? pendingLabel : '发送'} disabled={pending || !text.trim()}>{pending ? <SpinnerGap size={21} className="spinning" /> : <ArrowUpRight size={24} />}</button>}</div>
  </form>;
}
