// Application-lifetime cache. Navigation never starts requests or clears data.
export function createPluginStore(request) {
  let state = { plugins: [], items: [], presets: [], agents: [], agentErrors: [], loaded: false, loading: true, error: '', pageError: '' };
  let pending = null, dirty = false;
  const listeners = new Set();
  const methods = ['plugin_list', 'connector_list', 'connector_presets', 'agent_plugins'];
  const publish = patch => { state = { ...state, ...patch }; listeners.forEach(listener => listener(state)); };
  const refresh = ({ invalidate = false } = {}) => {
    if (pending) { dirty ||= invalidate; return pending; }
    pending = (async () => {
      do {
        dirty = false;
        const results = await Promise.allSettled(methods.map(method => request(method)));
        const patch = { loaded: true, loading: false, error: '', pageError: '' };
        results.forEach((result, index) => {
          if (result.status === 'rejected') {
            patch[index === 0 ? 'error' : 'pageError'] = result.reason?.message || '插件状态暂时无法更新。';
            return; // Keep the last successful snapshot on transient failure.
          }
          const value = result.value;
          if (index === 0) patch.plugins = value;
          if (index === 1) patch.items = value;
          if (index === 2) patch.presets = value;
          if (index === 3) { patch.agents = value.agents || []; patch.agentErrors = value.agentPluginErrors || []; }
        });
        publish(patch);
      } while (dirty);
    })().finally(() => { pending = null; });
    return pending;
  };
  return { getSnapshot: () => state, refresh, subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); }, setAgents: agents => publish({ agents }) };
}
