import { useContext, useState } from 'react';
import { ModelContext, thinkingLabels } from './ModelContext';

export function StepModelSettings({ execution, config, disabled, onChange }) {
  const { settings } = useContext(ModelContext);
  const [saving, setSaving] = useState(false), [error, setError] = useState('');
  const models = settings?.models || [];
  async function save(modelId, reasoning) {
    setSaving(true); setError('');
    try { await onChange({ modelId, reasoning }); }
    catch (error) { setError(error.message); }
    finally { setSaving(false); }
  }
  const args = execution.args || [];
  const binding = { resourceId: args[args.indexOf('--model') + 1], reasoning: args[args.indexOf('--reasoning') + 1] };
  const resource = config.resources.find(item => item.id === binding.resourceId);
  const current = models.find(item => item.id === resource?.modelId);
  const matches = current && ['model', 'provider', 'baseUrl'].every(key => current[key] === resource[key]);
  const levels = matches ? current.thinkingLevels || [] : [];
  return <><fieldset className="step-agent-settings step-model-settings">
      <label>模型 API<select aria-label={'模型 API'} value={matches ? current.id : ''} disabled={disabled || saving || !settings} onChange={event => {
        const selected = models.find(item => item.id === event.target.value);
        save(selected.id, selected.thinkingLevels.includes(binding.reasoning) ? binding.reasoning : selected.thinkingLevel);
      }}>
        {!matches && <option value="" disabled>{resource?.model || '模型不可用'} · 请重新选择</option>}
        {models.map(item => <option key={item.id} value={item.id} disabled={!item.configured}>{item.name} · {item.model}{!item.configured ? '（未配置）' : ''}</option>)}
      </select></label>
      <label>思考强度<select aria-label={'思考强度'} value={binding.reasoning} disabled={disabled || saving || !current?.configured || !levels.length} onChange={event => save(current.id, event.target.value)}>
        {!levels.includes(binding.reasoning) && <option value={binding.reasoning} disabled>{thinkingLabels[binding.reasoning] || binding.reasoning} · 不可用</option>}
        {levels.map(level => <option key={level} value={level}>{thinkingLabels[level] || level}</option>)}
      </select></label>
    </fieldset>{error && <p className="form-error" role="alert">{error}</p>}</>;
}
