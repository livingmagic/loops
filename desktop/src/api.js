export const native = Boolean(window.loops);
export async function request(method, params = {}) {
  if (native) return window.loops.request(method, params);
  const response = await fetch('/api/action', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Loops-Client': 'desktop-development' }, body: JSON.stringify({ method, params }) });
  const value = await response.json();
  if (value.error) throw new Error(value.error);
  return value.result;
}
export function subscribe(callback) {
  if (native) return window.loops.subscribe(callback);
  const source = new EventSource('/api/events');
  let connected = false;
  source.onopen = () => {
    callback({ event: 'connected', reconnected: connected });
    connected = true;
  };
  source.onmessage = (event) => callback(JSON.parse(event.data));
  source.onerror = () => callback({ event: 'connection_error', message: '执行服务连接中断，正在重连。' });
  return () => source.close();
}
