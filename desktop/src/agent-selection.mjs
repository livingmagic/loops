export const effortNames = { none: '关闭', minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高', ultra: '超高' };
export function modelReasoning(model) {
  const levels = model?.reasoningLevels || [];
  return model?.defaultReasoning || (levels.includes('low') ? 'low' : levels[0]) || '';
}
export function stepSelection(execution, config, clients = []) {
  const agentId = execution.tool === 'codex' ? 'codex' : execution.agentId || config.defaultAgentId || 'codex';
  const client = clients.find(c => c.id === agentId);
  const inheritedModel = config.agentModels?.[agentId] ?? client?.defaultModel ?? '';
  const model = execution.model ?? inheritedModel;
  const entry = client?.models?.find(m => m.id === model);
  const inheritedReasoning = config.agentReasoning?.[agentId] ?? (inheritedModel === client?.defaultModel ? client?.defaultReasoning : '');
  const reasoning = (execution.reasoning ?? (model === inheritedModel ? inheritedReasoning : '')) || modelReasoning(entry);
  return { agentId, model, reasoning, client, entry };
}
