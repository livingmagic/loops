import { displayPath } from './path-display.js';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FolderSimple } from '@phosphor-icons/react';
import { native } from './api.js';

const defaultDirectory = value => !value || ['${workspace}', '${workspace}/', '.', './'].includes(value);

export function StepDirectory({ value = '', defaultPath, displayValue, home, disabled, onChange }) {
  const tooltipId = useId(), control = useRef(null), tooltip = useRef(null);
  const [anchor, setAnchor] = useState(null), [position, setPosition] = useState({});
  const revealPath = () => {
    const rect = control.current.getBoundingClientRect();
    setAnchor({ left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom });
  };
  useLayoutEffect(() => {
    if (!anchor || !tooltip.current) return;
    const width = tooltip.current.offsetWidth, height = tooltip.current.offsetHeight;
    setPosition({ left: Math.max(12, Math.min(anchor.right - width, window.innerWidth - width - 12)), top: anchor.bottom + height + 20 <= window.innerHeight ? anchor.bottom + 8 : Math.max(12, anchor.top - height - 8) });
  }, [anchor]);
  useEffect(() => {
    if (!anchor) return;
    const hide = () => setAnchor(null);
    const key = event => { if (event.key === 'Escape') hide(); };
    window.addEventListener('scroll', hide, true); window.addEventListener('resize', hide); window.addEventListener('keydown', key);
    return () => { window.removeEventListener('scroll', hide, true); window.removeEventListener('resize', hide); window.removeEventListener('keydown', key); };
  }, [anchor]);
  const current = defaultDirectory(value) ? '' : value;
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => { setError(''); }, [current]);
  const apply = async (path) => {
    setBusy(true); setError('');
    try { await onChange(path || null); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const choose = async () => {
    try { const path = await window.loops.choose('directory'); if (path) await apply(path); }
    catch (e) { setError(e.message); }
  };
  const label = displayValue || current || defaultPath;
  return <div className="step-directory">
    <div className="step-directory-heading"><span>工作目录</span>{current && <button type="button" disabled={disabled || busy} onClick={() => apply('')}>恢复默认</button>}</div>
    <div ref={control} className="step-directory-control" onMouseEnter={revealPath} onMouseLeave={() => setAnchor(null)} onFocus={revealPath} onBlur={() => setAnchor(null)}>
      <button type="button" className="step-directory-choose" aria-label={`选择工作目录：${label}`} aria-describedby={anchor ? tooltipId : undefined} disabled={disabled || busy || !native} onClick={choose}>
        <FolderSimple size={18} /><span>{displayPath(label, home)}</span><small>{busy ? '保存中…' : '选择文件夹'}</small>
      </button>
    </div>
    {anchor && createPortal(<div ref={tooltip} id={tooltipId} role="tooltip" className="step-directory-tooltip" style={position}>{displayPath(label, home)}</div>, document.body)}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}
