import { AgentPicker } from './AgentPicker.jsx';
import { effortNames, modelReasoning, stepSelection } from './agent-selection.mjs';

export function StepAgentSettings({ execution, config, clients, disabled, onChange }) {
  const { agentId, model, reasoning, client, entry } = stepSelection(execution, config, clients || []);
  const models = client?.models || [], levels = entry?.reasoningLevels || [];
  const save = (id, model, reasoning) => onChange({ agentId: id, model: model || null, reasoning: reasoning || null });
  return <div className="step-agent-settings">
    <AgentPicker clients={clients} value={agentId} label="Agent" disabled={disabled} onChange={id => {
      const next = clients.find(c => c.id === id), selected = next?.models?.find(m => m.id === next.defaultModel) || next?.models?.[0];
      save(id, selected?.id, next?.defaultReasoning && selected?.reasoningLevels.includes(next.defaultReasoning) ? next.defaultReasoning : modelReasoning(selected));
    }} />
    <label>模型<select aria-label="节点模型" value={model} disabled={disabled || !client?.ready || !models.length} onChange={event => {
      const selected = models.find(m => m.id === event.target.value);
      save(agentId, selected.id, selected.reasoningLevels.includes(reasoning) ? reasoning : modelReasoning(selected));
    }}>
      {!model && <option value="">{clients ? '暂无可用模型' : '正在检测…'}</option>}
      {model && !models.some(m => m.id === model) && <option value={model} disabled>{model} · 当前不可用</option>}
      {models.map(m => <option key={m.id} value={m.id}>{m.name}{m.provider && models.filter(other => other.name === m.name).length > 1 ? ` · ${m.provider}` : ''}</option>)}
    </select></label>
    <label>思考强度<select aria-label="节点思考强度" value={reasoning} disabled={disabled || !client?.ready || !levels.length} onChange={event => save(agentId, model, event.target.value)}>
      {!reasoning && <option value="">{clients ? '无可选档位' : '正在检测…'}</option>}
      {reasoning && !levels.includes(reasoning) && <option value={reasoning} disabled>{effortNames[reasoning] || reasoning} · 当前不可用</option>}
      {levels.map(level => <option key={level} value={level}>{effortNames[level] || level}</option>)}
    </select></label>
    {client?.catalogError && <p className="form-error" role="alert">{client.catalogError}</p>}
  </div>;
}
