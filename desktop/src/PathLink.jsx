import { useEffect, useState } from 'react';
import { native } from './api.js';
import { isLocalPath, pathName } from './path-display.js';
import { shortPathText } from './workflow-paths.js';

export function PathLink({ path, home, children, levels = 1, className = '', describedBy, tooltip = true }) {
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [path]);
  const label = shortPathText(path, home);
  const open = async () => {
    setError('');
    try { await window.loops.openInFinder(path); }
    catch { setError('无法在 Finder 中打开，请确认路径存在且有访问权限。'); }
  };
  return <><button type="button" className={`path-link ${className}`} title={tooltip ? label : undefined} aria-label={`在 Finder 中打开：${label}`} aria-describedby={describedBy} disabled={!native || !isLocalPath(path)} onClick={open}>{children || pathName(path, home, levels)}</button>{error && <span className="path-error" role="alert">{error}</span>}</>;
}
