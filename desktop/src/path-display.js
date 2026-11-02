export function displayPath(value, home) {
  if (!value) return '';
  const root = home?.replace(/\/+$/, '');
  return root && (value === root || value.startsWith(root + '/')) ? '～' + value.slice(root.length) : value;
}
