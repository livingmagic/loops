import { InlinePluginInput } from './InlinePluginInput.jsx';
import { useContext, useEffect, useId, useRef, useState } from 'react';
import { ModelPicker } from './ModelContext.jsx';
import { ArrowUpRight, SpinnerGap, Stop } from '@phosphor-icons/react';
import { PluginIcon } from './PluginIcon.jsx';
import { PluginContext } from './PluginContext.jsx';
import { filterPlugins, pluginKey, pluginStatus, pluginTypes, slashQuery } from './plugin-selection.mjs';

export function Composer({ input, text, onChange, onSubmit, pending, onCancel, placeholder, rows = 2, className = '', note, pendingLabel = '正在创建 Loop', draftKey }) {
  const { plugins, selections, setSelection, loading, error, refresh } = useContext(PluginContext);
  const selected = selections[draftKey] || [];
  const [query, setQuery] = useState(null), [active, setActive] = useState(0);
  const listId = useId(), composing = useRef(false), caretRequest = useRef(null);
  const options = query ? filterPlugins(plugins, query.query, selected) : [];
  const index = Math.min(active, Math.max(0, options.length - 1));
  useEffect(() => { setQuery(null); setActive(0); }, [draftKey]);
  useEffect(() => { document.getElementById(`${listId}-${index}`)?.scrollIntoView({ block: 'nearest' }); }, [index, listId, query?.query]);
  function updateQuery(value, caret) {
    if (composing.current || !caret) return;
    const next = slashQuery(value, caret.start, caret.end);
    if (next?.start === query?.start && next?.end === query?.end && next?.query === query?.query) return;
    setQuery(next); setActive(0);
  }
  function choose(item) {
    if (!query) return;
    const label = `/${item.name}`, replacement = label + ' ';
    const delta = replacement.length - (query.end - query.start);
    const mentions = selected.map(mention => mention.start >= query.end ? { ...mention, start: mention.start + delta, end: mention.end + delta } : mention);
    setSelection(draftKey, [...mentions, { ...item, start: query.start, end: query.start + label.length }].sort((a,b) => a.start-b.start));
    caretRequest.current = query.start + replacement.length;
    onChange(text.slice(0, query.start) + replacement + text.slice(query.end));
    setQuery(null);
  }
  async function submit(event) {
    event.preventDefault();
    if (pending || !text.trim()) return;
    const sent = await onSubmit(event, selected.map(({ id, type, start, end }) => ({ id, type, start, end })));
    if (sent) { setSelection(draftKey, []); setQuery(null); }
  }
  return <form className={`composer ${className}`} onSubmit={submit}>
    {query && <div className="plugin-picker">
      <div id={listId} role="listbox" aria-label="选择插件">{options.map((item, i) => <button id={`${listId}-${i}`} type="button" role="option" aria-selected={i === index} key={pluginKey(item)} tabIndex={-1} onMouseDown={e => e.preventDefault()} onClick={() => choose(item)}><PluginIcon plugin={item} size={22} /><span className="plugin-option-label"><b>{item.name}</b><small>{pluginTypes[item.type]}</small></span><small className={item.ready ? 'plugin-ready' : ''}>{pluginStatus(item)}</small></button>)}</div>
      {!options.length && <p role="status">{loading ? '正在读取插件…' : error || '没有匹配的可用插件'}</p>}
      {error && <button className="auto-link" type="button" onMouseDown={e => e.preventDefault()} onClick={refresh}>重新加载</button>}
    </div>}
    <InlinePluginInput input={input} text={text} selected={selected} plugins={plugins} draftKey={draftKey} caretRequest={caretRequest}
      placeholder={`${placeholder} 输入 / 选择插件`} disabled={pending && !onCancel} listId={listId} expanded={Boolean(query)} activeId={query && options.length ? `${listId}-${index}` : undefined}
      onChange={(value, mentions) => { onChange(value); setSelection(draftKey, mentions); }} onSelection={updateQuery}
      onBlur={() => setQuery(null)} onCompositionStart={() => { composing.current = true; setQuery(null); }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || composing.current || event.keyCode === 229) return;
        if (query && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setQuery(null); return; }
        if (query && ['ArrowDown', 'ArrowUp'].includes(event.key) && options.length) { event.preventDefault(); setActive((index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length); return; }
        if (query && ['Enter', 'Tab'].includes(event.key) && !event.shiftKey) { if (options[index]) { event.preventDefault(); choose(options[index]); } else if (event.key === 'Tab') setQuery(null); else event.preventDefault(); return; }
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(event); }
      }} />
    {note && <span className="provider-note composer-note">{note}</span>}
    <div className="composer-actions"><ModelPicker disabled={pending} />{pending && onCancel ? <button type="button" className="send-button cancel-agent" aria-label="停止回复" onClick={onCancel}><Stop size={21} /></button> : <button className="send-button" aria-label={pending ? pendingLabel : '发送'} disabled={pending || !text.trim()}>{pending ? <SpinnerGap size={21} className="spinning" /> : <ArrowUpRight size={24} />}</button>}</div>
  </form>;
}
