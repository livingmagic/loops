import { connectorPlugins, agentPlugins } from './plugin-catalog.mjs';

// Callers may request only the plugin types they actually need.
export async function pluginCatalog(engine, references) {
  const types = references && new Set(references.map(item => item?.type));
  const connectorsNeeded = !types || types.has('mcp') || types.has('cli');
  const agentsNeeded = !types || types.has('agent');
  const [items, presets, agents] = await Promise.all([
    connectorsNeeded ? engine.call('connector_list') : [],
    connectorsNeeded ? engine.call('connector_presets') : [],
    agentsNeeded ? engine.call('agent_clients', references ? { ids: references.filter(item => item?.type === 'agent').map(item => item.id) } : {}) : [],
  ]);
  return [...connectorPlugins(items, presets), ...agentPlugins(agents)];
}

export async function resolvePlugins(engine, references = []) {
  if (!Array.isArray(references) || references.length > 100) throw new Error('插件选择无效，请重新选择。');
  if (!references.length) return [];
  const catalog = await pluginCatalog(engine, references), seen = new Set();
  return references.flatMap(reference => {
    const item = catalog.find(item => item.id === reference?.id && item.type === reference?.type);
    if (!item) throw new Error('所选插件已不存在或类型已变更，请重新选择。');
    const key = `${item.type}:${item.id}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [item];
  });
}

export const pluginReferences = (plugins, text = '', references = []) => plugins.map(({ id, type, name, provider }) => {
  const reference = references.find(item => item.id === id && item.type === type);
  const start = reference?.start, end = reference?.end;
  const offset = text.length - text.trimStart().length;
  const position = Number.isInteger(start) && Number.isInteger(end) && start >= offset && text.slice(start, end) === `/${name}`
    ? { start: start - offset, end: end - offset } : {};
  return { id, type, name, ...(provider ? { provider } : {}), ...position };
});

// Keep the exact selection with its user turn, including in the durable model session.
export function pluginPrompt(text, plugins = []) {
  return plugins.length ? `${text}\n\n用户通过 / 选择的插件（服务端核实的数据）：\n${JSON.stringify(plugins)}` : text;
}
