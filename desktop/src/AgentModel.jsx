import { useState } from 'react';

const effortNames = { none: '关闭', minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高', ultra: '超高' };

export function AgentModel({ client, onSave, busy }) {
  const [saving, setSaving] = useState(false), [error, setError] = useState('');
  const model = client.defaultModel || '', reasoning = client.defaultReasoning || '';
  const models = client.models || [];
  const selected = models.find(item => item.id === model);
  const levels = selected?.reasoningLevels || [];
  async function save(nextModel, nextReasoning) {
    setSaving(true); setError('');
    try { await onSave({ agentId: client.id, model: nextModel || null, reasoning: nextReasoning || null }); }
    catch (e) { setError(e.message); } finally { setSaving(false); }
  }
  return <section className="settings-card agent-settings-card" aria-label={`${client.name} 执行设置`}>
    <header className="agent-settings-heading"><div><strong>{client.name}</strong><small>{client.version ? `v${client.version}` : client.installed ? '已安装' : '未安装'}</small></div><span className="agent-settings-status" role="status">{saving ? '保存中…' : client.detail}</span></header>
    <div className="agent-settings-fields">
      <label>模型<select aria-label={`${client.name} 模型`} value={model} disabled={busy || saving || !client.ready || !models.length} onChange={event => { const next = models.find(m => m.id === event.target.value); save(event.target.value, next?.reasoningLevels.includes(reasoning) ? reasoning : next?.defaultReasoning || (next?.reasoningLevels.includes('low') ? 'low' : next?.reasoningLevels[0]) || ''); }}>
        {!model && <option value="" disabled>请选择模型</option>}
        {model && !models.some(m => m.id === model) && <option value={model} disabled>{model} · 当前不可用</option>}
        {models.map(item => <option key={item.id} value={item.id}>{item.name}{item.provider && models.filter(m => m.name === item.name).length > 1 ? ` · ${item.provider}` : ''}</option>)}
      </select></label>
      <label>思考强度<select aria-label={`${client.name} 思考强度`} value={reasoning} disabled={busy || saving || !levels.length} onChange={event => save(model, event.target.value)}>
        {!reasoning && <option value="" disabled>{levels.length ? '请选择思考强度' : '无可选档位'}</option>}
        {reasoning && !levels.includes(reasoning) && <option value={reasoning} disabled>{effortNames[reasoning] || reasoning} · 当前不可用</option>}
        {levels.map(level => <option key={level} value={level}>{effortNames[level] || level}</option>)}
      </select></label>
    </div>
    {(error || client.catalogError) && <p className="form-error" role="alert">{error || client.catalogError}</p>}
  </section>;
}
