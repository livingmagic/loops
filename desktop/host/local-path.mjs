export function isLocalPath(value) {
  return typeof value === 'string' && (value.startsWith('/') || value === '~' || value.startsWith('~/')) && !/[\0\r\n]|\$\{|\{runId\}/.test(value);
}
