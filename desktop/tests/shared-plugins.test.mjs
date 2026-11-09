import test from 'node:test';
import assert from 'node:assert/strict';
import { Host } from '../host/service.mjs';

function hostFixture({ status = 'approved', existing = null } = {}) {
  const host = Object.create(Host.prototype);
  const calls = [];
  let plugin = existing;
  host.sharedPluginJobs = new Map();
  host.engine = { async call(method) {
    calls.push(method);
    if (method === 'sharing_refresh_peer') return { peers: [{ id: 'peer-one', status }] };
    if (method === 'sharing_peer_config') return { id: 'shared-peer-one', type: 'mcp' };
    if (method === 'connector_list') return plugin ? [plugin] : [];
    if (method === 'connector_save') { plugin = { manifest: { id: 'shared-peer-one' }, enabled: false }; return plugin; }
    if (method === 'connector_connect') { plugin = { ...plugin, enabled: true, status: 'connected' }; return plugin; }
    throw new Error(`Unexpected call ${method}`);
  } };
  return { host, calls };
}

test('approved shared client becomes one plugin even when registration requests overlap', async () => {
  const { host, calls } = hostFixture();
  const params = { peerId: 'peer-one' };
  const results = await Promise.all([host.action('sharing_add_plugin', params), host.action('sharing_add_plugin', params)]);
  assert.equal(results[0].status, 'connected');
  assert.deepEqual(results[0], results[1]);
  assert.equal(calls.filter(method => method === 'connector_save').length, 1);
  await host.action('sharing_add_plugin', params);
  assert.equal(calls.filter(method => method === 'connector_save').length, 1);
  assert.equal(calls.filter(method => method === 'connector_connect').length, 1);
  assert.equal(host.sharedPluginJobs.size, 0);
});

test('pending or revoked authorization cannot register a shared plugin', async () => {
  for (const status of ['pending', 'revoked']) {
    const { host, calls } = hostFixture({ status });
    await assert.rejects(host.action('sharing_add_plugin', { peerId: 'peer-one' }), /同意连接/);
    assert.deepEqual(calls, ['connector_list', 'sharing_refresh_peer']);
    assert.equal(host.sharedPluginJobs.size, 0);
  }
});

test('reopening plugins preserves a shared plugin the user disabled', async () => {
  const existing = { manifest: { id: 'shared-peer-one' }, enabled: false, revision: 5 };
  const { host, calls } = hostFixture({ existing });
  assert.deepEqual(await host.action('sharing_add_plugin', { peerId: 'peer-one' }), existing);
  assert.ok(!calls.includes('sharing_refresh_peer'));
  assert.ok(!calls.includes('connector_connect'));
  assert.ok(!calls.includes('connector_save'));
});
