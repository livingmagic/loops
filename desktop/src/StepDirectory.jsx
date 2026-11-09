import { PathLink } from './PathLink.jsx';
import { displayPath, pathName } from './path-display.js';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FolderSimple } from '@phosphor-icons/react';
import { native } from './api.js';

export const isDefaultDirectory = value => !value || ['${workspace}', '${workspace}/', '.', './'].includes(value);

export function StepDirectory({ value = '', defaultPath, displayValue, home, disabled, onChange }) {
  const tooltipId = useId(), control = useRef(null), tooltip = useRef(null);
  const [anchor, setAnchor] = useState(null), [position, setPosition] = useState({});
  const revealPath = () => {
    if (isDefaultDirectory(value)) return;
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
  const current = isDefaultDirectory(value) ? '' : value;
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
      {current && <PathLink path={label} home={home} className="step-directory-open" tooltip={false} describedBy={anchor ? tooltipId : undefined}>
        <FolderSimple size={18} /><span>{pathName(label, home, 2)}</span>
      </PathLink>}
      <button type="button" className="step-directory-choose" aria-label="选择工作目录" disabled={disabled || busy || !native} onClick={choose}>{busy ? '保存中…' : '选择文件夹'}</button>
    </div>
    {anchor && createPortal(<div ref={tooltip} id={tooltipId} role="tooltip" className="step-directory-tooltip" style={position}>{displayPath(label, home)}</div>, document.body)}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}
