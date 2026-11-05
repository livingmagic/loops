import { useLayoutEffect, useRef } from 'react';
import { PluginIcon } from './PluginIcon.jsx';
import { pluginKey } from './plugin-selection.mjs';

// The editable DOM is owned here; React owns only its surrounding icon templates.
export function InlinePluginInput({ input, text, selected, plugins, onChange, onSelection, onKeyDown, onBlur, onCompositionStart, onCompositionEnd, placeholder, disabled, listId, expanded, activeId, caretRequest, draftKey }) {
  const templates = useRef(null);
  const known = new Map([...plugins, ...selected].map(item => [pluginKey(item), item]));
  function read(root) {
    let value = ''; const mentions = [];
    function visit(node) {
      if (node.nodeType === 3) { value += node.textContent; return; }
      const key = node.dataset?.plugin;
      if (key && known.has(key)) {
        const item = known.get(key), start = value.length;
        value += `/${item.name}`;
        mentions.push({ ...item, start, end: value.length }); return;
      }
      if (node.nodeName === 'BR') { value += '\n'; return; }
      const block = ['DIV', 'P'].includes(node.nodeName);
      if (block && value && !value.endsWith('\n')) value += '\n';
      for (const child of node.childNodes) visit(child);
    }
    for (const child of root.childNodes) visit(child);
    return { text: value, selected: mentions };
  }
  function position() {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !input.current.contains(selection.anchorNode)) return null;
    const range = selection.getRangeAt(0), before = range.cloneRange();
    before.selectNodeContents(input.current); before.setEnd(range.startContainer, range.startOffset);
    const start = read(before.cloneContents()).text.length;
    return { start, end: start + read(range.cloneContents()).text.length };
  }
  function notify() { const value = read(input.current); onChange(value.text, value.selected); onSelection(value.text, position()); }
  useLayoutEffect(() => {
    const editor = input.current;
    const current = read(editor);
    const signature = items => JSON.stringify(items.map(({ id, type, start, end }) => ({ id, type, start, end })));
    if (current.text !== text || signature(current.selected) !== signature(selected)) {
      editor.replaceChildren(); let offset = 0;
      for (const item of selected.filter(item => Number.isInteger(item.start)).sort((a,b) => a.start-b.start)) {
        if (item.start < offset || text.slice(item.start,item.end) !== `/${item.name}`) continue;
        editor.append(document.createTextNode(text.slice(offset,item.start)));
        const chip = document.createElement('span'); chip.className = 'inline-plugin'; chip.contentEditable = 'false'; chip.dataset.plugin = pluginKey(item); chip.setAttribute('aria-label', `${item.name} 插件`);
        const template = [...templates.current.children].find(el => el.dataset.key === pluginKey(item));
        if (template?.firstChild) chip.append(template.firstChild.cloneNode(true));
        chip.append(document.createTextNode(item.name)); editor.append(chip); offset=item.end;
      }
      editor.append(document.createTextNode(text.slice(offset)));
    }
    if (caretRequest.current !== null) {
      let remaining = caretRequest.current; const range = document.createRange();
      for (const node of editor.childNodes) {
        const length = node.nodeType === 3 ? node.textContent.length : read({childNodes:[node]}).text.length;
        if (remaining <= length) {
          if (node.nodeType === 3) range.setStart(node, remaining); else if (remaining === 0) range.setStartBefore(node); else range.setStartAfter(node);
          range.collapse(true); const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range); break;
        }
        remaining -= length;
      }
      editor.focus(); caretRequest.current = null;
    }
  }, [text, selected, draftKey]);
  return <><div hidden ref={templates}>{[...known.values()].map(item => <span key={pluginKey(item)} data-key={pluginKey(item)}><PluginIcon plugin={item} size={17} /></span>)}</div><div ref={input} className="composer-editor" role="textbox" aria-label="输入需求或反馈" aria-multiline="true" contentEditable={!disabled} suppressContentEditableWarning data-placeholder={placeholder} aria-autocomplete="list" aria-controls={expanded ? listId : undefined} aria-expanded={expanded} aria-activedescendant={activeId}
    onInput={notify} onClick={() => onSelection(read(input.current).text, position())} onKeyUp={() => onSelection(read(input.current).text, position())} onBlur={onBlur}
    onCopy={event => { const selection = window.getSelection(); if (!selection?.rangeCount) return; event.preventDefault(); event.clipboardData.setData('text/plain', read(selection.getRangeAt(0).cloneContents()).text); }}
    onPaste={event => { event.preventDefault(); document.execCommand('insertText', false, event.clipboardData.getData('text/plain')); }}
    onCompositionStart={onCompositionStart} onCompositionEnd={event => { notify(); onCompositionEnd(event); }} onKeyDown={onKeyDown} /></>;
}
