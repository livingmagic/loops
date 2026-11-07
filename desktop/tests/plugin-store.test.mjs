import test from 'node:test';
import assert from 'node:assert/strict';
import { createPluginStore } from '../src/plugin-store.mjs';
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = method => ({
  connector_list: [{ manifest: {id:'mcp',type:'mcp',name:'Example'}, enabled:true, status:'connected' }],
  connector_presets: [],
  agent_plugins: { agents: [{id:'codex',name:'Codex',ready:true}] },
})[method];

test('MCP becomes visible while Agent discovery is still pending', async () => {
  let finishAgent;
  const calls = [];
  const store = createPluginStore(async method => {
    calls.push(method);
    if (method === 'agent_plugins') return new Promise(resolve => { finishAgent = resolve; });
    return response(method);
  });
  const pending = store.ensureLoaded();
  await tick();
  assert.deepEqual(store.getSnapshot().plugins.map(p => p.id), ['mcp']);
  assert.equal(store.getSnapshot().loading, true);
  const reuse = store.ensureLoaded();
  await tick();
  assert.equal(calls.length, 3);
  finishAgent(response('agent_plugins'));
  await Promise.all([pending,reuse]);
  assert.equal(store.getSnapshot().loaded, true);
  assert.deepEqual(store.getSnapshot().plugins.map(p => p.id), ['mcp','codex']);
});

test('connector events only refresh connections; explicit Agent checks bypass the cache', async () => {
  const calls = [];
  const store = createPluginStore(async (method, params) => { calls.push({method,params}); return response(method); });
  await store.ensureLoaded();
  assert.deepEqual(calls.find(call => call.method === 'agent_plugins').params, {refresh:false});
  calls.length = 0;
  store.handleEvent({event:'connector_state'});
  await tick();
  assert.deepEqual(calls.map(call => call.method), ['connector_list']);
  calls.length = 0;
  store.handleEvent({event:'agent_plugins_changed'});
  await tick();
  assert.deepEqual(calls, [{method:'agent_plugins',params:{refresh:false}}]);
  await store.refreshAgents({force:true});
  assert.deepEqual(calls.at(-1), {method:'agent_plugins',params:{refresh:true}});
});

test('failed refreshes keep successful data and a retry clears the error', async () => {
  let fail = false;
  const store = createPluginStore(async method => {
    if (fail && method === 'connector_list') throw Error('offline');
    return response(method);
  });
  await store.ensureLoaded();
  const items = store.getSnapshot().items;
  fail = true;
  await store.refreshConnectors();
  assert.equal(store.getSnapshot().items, items);
  assert.match(store.getSnapshot().pageError, /offline/);
  fail = false;
  await store.refreshConnectors();
  assert.equal(store.getSnapshot().pageError, '');
});

test('a failed preset read is retried without repeating a successful Agent probe', async () => {
  let fail = true;
  const calls = [];
  const store = createPluginStore(async method => {
    calls.push(method);
    if (method === 'connector_presets' && fail) throw Error('presets unavailable');
    return response(method);
  });
  await store.ensureLoaded();
  assert.match(store.getSnapshot().pageError, /presets unavailable/);
  fail = false;
  calls.length = 0;
  await store.refreshConnectors();
  assert.deepEqual(calls, ['connector_list','connector_presets']);
  assert.equal(store.getSnapshot().pageError, '');
});
