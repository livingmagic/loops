// One credential-free catalog for the picker and model discovery.
export async function pluginCatalog(engine) {
  const [items, presets, agents] = await Promise.all([
    engine.call('connector_list'), engine.call('connector_presets'), engine.call('agent_clients'),
  ]);
  const installed = new Set(items.map(item => item.manifest.id));
  return [
    ...items.concat(presets.filter(item => !installed.has(item.id)).map(manifest => ({ manifest, status: 'not_added' }))).map(item => ({
      id: item.manifest.id, type: item.manifest.type, name: item.manifest.name, provider: item.manifest.provider,
      description: item.generatedDescription?.text || item.manifest.description || '',
      status: item.status, ready: Boolean(item.enabled && item.status === 'connected'),
      operationCount: item.capabilities?.operationCount ?? item.tools?.length ?? 0,
    })),
    ...agents.map(item => ({ id: item.id, type: 'agent', name: item.name, description: item.detail,
      status: item.ready ? 'connected' : item.installed ? 'unavailable' : 'not_installed', ready: Boolean(item.ready),
      modes: item.modes, defaultModel: item.defaultModel, defaultReasoning: item.defaultReasoning,
    })),
  ];
}

export async function resolvePlugins(engine, references = []) {
  if (!Array.isArray(references) || references.length > 100) throw new Error('插件选择无效，请重新选择。');
  if (!references.length) return [];
  const catalog = await pluginCatalog(engine), seen = new Set();
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
