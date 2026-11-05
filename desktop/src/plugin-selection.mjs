export const pluginTypes = { mcp: 'MCP 服务', cli: 'CLI 工具', agent: 'Agent' };
export const pluginKey = item => `${item.type}:${item.id}`;
export const pluginStatus = item => item.ready ? '可用' : ({ connecting: '连接中', authorization_required: '需要授权', error: '连接异常', unavailable: '不可用', not_installed: '未安装' }[item.status] || '未连接');

export function slashQuery(text, caret, selectionEnd = caret) {
  if (caret !== selectionEnd) return null;
  const before = text.slice(0, caret);
  const match = before.match(/(?:^|\s)\/([^\s/]*)$/u);
  return match ? { start: caret - match[1].length - 1, end: caret, query: match[1] } : null;
}

export function filterPlugins(plugins, query, selected = []) {
  const keys = new Set(selected.map(pluginKey)), needle = query.toLocaleLowerCase();
  return plugins.filter(item => item.ready === true && !keys.has(pluginKey(item)) && [item.name, item.id, pluginTypes[item.type]].some(value => value?.toLocaleLowerCase().includes(needle)));
}

// Older messages have references but no positions. Only infer an unambiguous label.
export function messagePluginParts(text, plugins = []) {
  const spans = [], remaining = [];
  for (const plugin of plugins) {
    const label = `/${plugin.name}`;
    let { start, end } = plugin;
    if (!Number.isInteger(start) || !Number.isInteger(end)) {
      start = text.indexOf(label); end = start + label.length;
      if (start < 0 || text.indexOf(label, end) >= 0 || plugins.filter(p => p.name === plugin.name).length !== 1) { remaining.push(plugin); continue; }
    }
    if (start < 0 || text.slice(start,end) !== label || spans.some(s => start < s.end && end > s.start)) { remaining.push(plugin); continue; }
    spans.push({ ...plugin, start, end });
  }
  const parts = []; let cursor = 0;
  for (const plugin of spans.sort((a,b) => a.start-b.start)) {
    if (plugin.start > cursor) parts.push(text.slice(cursor,plugin.start));
    parts.push(plugin); cursor = plugin.end;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return { parts, remaining };
}
