import { connectorPlugins, agentPlugins } from '../host/plugin-catalog.mjs';
import { createRefreshTask } from './refresh-task.mjs';

// Each source publishes independently. A slow Agent never holds up MCP/CLI.
export function createPluginStore(request) {
  let state = { plugins: [], items: [], presets: [], agents: [], agentErrors: [] };
  const settled = { items: false, presets: false, agents: false };
  const pending = { items: false, presets: false, agents: false };
  const errors = { items: '', presets: '', agents: '' };
  const listeners = new Set();
  let started = false, forceAgents = false;
  function publish(patch = {}) {
    state = {
      ...state, ...patch,
      loaded: Object.values(settled).every(Boolean),
      agentsLoaded: settled.agents,
      loading: Object.keys(pending).some(key => pending[key] && !settled[key]),
      error: Object.values(errors).filter(Boolean).join(' '),
      pageError: Object.values(errors).filter(Boolean).join(' '),
    };
    state.plugins = [...connectorPlugins(state.items, state.presets), ...agentPlugins(state.agents)];
    listeners.forEach(listener => listener(state));
  }
  const readers = {
    items: () => request('connector_list'),
    presets: () => request('connector_presets'),
    agents: () => {
      const refresh = forceAgents;
      forceAgents = false;
      return request('agent_plugins', { refresh });
    },
  };
  const jobs = Object.fromEntries(Object.keys(readers).map(key => [key, createRefreshTask(readers[key], value => {
    settled[key] = true;
    errors[key] = '';
    publish(key === 'agents' ? { agents: value.agents || [], agentErrors: value.agentPluginErrors || [] } : { [key]: value });
  })]));
  function load(key, invalidate = false) {
    started = true;
    pending[key] = true;
    publish();
    return jobs[key].refresh({ invalidate }).catch(error => {
      settled[key] = true;
      errors[key] = error.message || '插件状态暂时无法更新。';
      publish();
    }).finally(() => { pending[key] = false; publish(); });
  }
  const ensureLoaded = () => Promise.all(Object.keys(readers).filter(key => !settled[key]).map(key => load(key)));
  const refreshConnectors = ({ invalidate = false } = {}) => Promise.all([
    load('items', invalidate),
    ...(!settled.presets || errors.presets ? [load('presets', invalidate)] : []),
  ]);
  const refreshAgents = ({ invalidate = false, force = false } = {}) => {
    forceAgents ||= force;
    return load('agents', invalidate || force);
  };
  const refresh = ({ invalidate = false } = {}) => Promise.all([refreshConnectors({ invalidate }), refreshAgents({ invalidate })]);
  publish();
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    ensureLoaded, refresh, refreshConnectors, refreshAgents,
    handleEvent(event) {
      if (!started) return;
      if (event.event === 'connector_state') void refreshConnectors({ invalidate: true });
      if (event.event === 'agent_plugins_changed') void refreshAgents({ invalidate: true });
      if (event.event === 'connected') void refresh({ invalidate: true });
    },
    setAgents(agents) { jobs.agents.accept({ agents, agentPluginErrors: state.agentErrors }); },
  };
}
