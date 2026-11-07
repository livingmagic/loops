// Public picker metadata only; never include connection configuration.
export function connectorPlugins(items, presets) {
  const installed = new Set(items.map(item => item.manifest.id));
  return items.concat(presets.filter(item => !installed.has(item.id)).map(manifest => ({ manifest, status: 'not_added' }))).map(item => ({
      id: item.manifest.id, type: item.manifest.type, name: item.manifest.name, provider: item.manifest.provider,
      description: item.generatedDescription?.text || item.manifest.description || '',
      status: item.status, ready: Boolean(item.enabled && item.status === 'connected'),
      operationCount: item.capabilities?.operationCount ?? item.tools?.length ?? 0,
    }));
}
export function agentPlugins(agents) {
  return agents.map(item => ({ id: item.id, type: 'agent', name: item.name, description: item.detail,
      status: item.ready ? 'connected' : item.installed ? 'unavailable' : 'not_installed', ready: Boolean(item.ready),
      modes: item.modes, defaultModel: item.defaultModel, defaultReasoning: item.defaultReasoning,
    }));
}
