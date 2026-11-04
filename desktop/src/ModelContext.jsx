import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { CaretDown, Check } from '@phosphor-icons/react';
import { request } from './api.js';
export const ModelContext = createContext({});
export const thinkingLabels = { off: '关闭', minimal: '最低', low: '低', medium: '中', high: '高', xhigh: '更高', max: '最高' };
export function ModelPicker({ disabled }) {
  const { settings, modelId, setModelId, updateModels } = useContext(ModelContext);
  const [open, setOpen] = useState(false), [saving, setSaving] = useState(false), [error, setError] = useState('');
  const root = useRef(null), trigger = useRef(null);
  const current = settings?.models?.find(item => item.id === (modelId || settings.defaultModelId));
  useEffect(() => {
    if (!open) return;
    const dismiss = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    const keyboard = event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', dismiss); document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', keyboard); };
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  if (!current) return null;
  return <div className="model-control" ref={root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button type="button" ref={trigger} className="model-trigger" aria-label={`模型与思考强度：${current.name}，${thinkingLabels[current.thinkingLevel] || '关闭'}`} aria-expanded={open} aria-haspopup="dialog" disabled={disabled || saving} onClick={() => { setOpen(!open); setError(''); }}><span>{current.name}</span>{current.thinkingLevels?.length > 1 && <span className="thinking-summary">{thinkingLabels[current.thinkingLevel]}</span>}<CaretDown size={13} /></button>
    {open && <div className="model-popover" role="dialog" aria-label="选择模型与思考强度">
      <div className="model-options" role="group" aria-label="模型">{settings.models.map(item => <button type="button" key={item.id} disabled={!item.configured || saving} aria-pressed={item.id === current.id} className={item.id === current.id ? 'selected' : ''} onClick={() => { setModelId(item.id === settings.defaultModelId ? '' : item.id); setError(''); }}><span>{item.name}</span>{item.id === settings.defaultModelId && <small>默认</small>}{!item.configured && <small>未配置</small>}<Check size={16} className={item.id === current.id ? '' : 'invisible'} /></button>)}</div>
      {current.thinkingLevels?.length > 1 && <div className="thinking-options"><span>思考强度</span><div role="group" aria-label="思考强度">{current.thinkingLevels.map(level => <button type="button" key={level} disabled={saving} aria-pressed={current.thinkingLevel === level} onClick={async () => { setSaving(true); setError(''); try { updateModels(await request('model_thinking', { id: current.id, level })); } catch (e) { setError(e.message); } finally { setSaving(false); } }}>{thinkingLabels[level] || level}</button>)}</div></div>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>}
  </div>;
}
