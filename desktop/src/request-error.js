// Electron wraps rejected IPC calls; keep the original application message.
export function requestError(error) {
  return new Error(String(error?.message || error).replace(/^Error invoking remote method 'loops:request': (?:Error: )?/, ''));
}
