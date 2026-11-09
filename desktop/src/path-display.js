export { isLocalPath } from '../host/local-path.mjs';

export function displayPath(value, home) {
  if (!value) return '';
  const root = home?.replace(/\/+$/, '');
  return root && (value === root || value.startsWith(root + '/')) ? '~' + value.slice(root.length) : value;
}

export function pathName(value, home, levels = 1) {
  const full = displayPath(value, home);
  const parts = full.split('/').filter(Boolean);
  return parts.slice(-levels).join('/') || full;
}
