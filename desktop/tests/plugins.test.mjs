import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { Host } from '../host/service.mjs';
import { converse, builderTools } from '../host/agent.mjs';
import { converseAutomation } from '../host/automation-agent.mjs';
import { pluginCatalog, resolvePlugins } from '../host/plugins.mjs';
import { slashQuery, filterPlugins, pluginKey } from '../src/plugin-selection.mjs';

const catalogEngine = { call: async method => ({
  connector_list: [
    { manifest: { id: 'same', name: '同名工具', type: 'cli', command: ['secret-argument'] }, enabled: true, status: 'connected', authorization: { code: 'secret-code' }, tools: [{ name: 'read' }] },
    { manifest: { id: 'remote', name: '知识库', type: 'mcp', url: 'https://private.example/mcp' }, status: 'disconnected' },
  ],
  connector_presets: [{ id: 'same', type: 'cli' }, { id: 'preset', name: '预设服务', type: 'mcp' }],
  agent_clients: [{ id: 'same', name: '同名工具', ready: true, modes: ['read-only'], binary: '/private/binary', nativeSecret: 'hidden-secret' }],
})[method] };

test('catalog and selections distinguish types, resolve server identity and omit private configuration', async () => {
  const catalog = await pluginCatalog(catalogEngine);
  assert.equal(catalog.length, 4);
  assert.equal(new Set(catalog.map(pluginKey)).size, 4);
  assert.doesNotMatch(JSON.stringify(catalog), /secret|private|binary/);
  const chosen = await resolvePlugins(catalogEngine, [{ id: 'same', type: 'agent', name: 'forged' }, { id: 'same', type: 'cli' }, { id: 'same', type: 'agent' }, { id: 'preset', type: 'mcp' }]);
  assert.deepEqual(chosen.map(pluginKey), ['agent:same', 'cli:same', 'mcp:preset']);
  assert.equal(chosen[0].name, '同名工具');
  assert.equal(chosen[2].ready, false);
  await assert.rejects(resolvePlugins(catalogEngine, [{ id: 'same', type: 'mcp' }]), /重新选择/);
  await assert.rejects(resolvePlugins(catalogEngine, null), /选择无效/);
  const tools = builderTools(catalogEngine, 'loop');
  assert.equal((await tools.find(t => t.name === 'list_connectors').execute('id', {})).details.length, 2);
  assert.ok(tools.some(t => t.name === 'read_connector_skill'));
});

test('slash search handles cursor position, Chinese, type search and ordinary URLs or paths', async () => {
  assert.deepEqual(slashQuery('整理 /知识', 6), { start: 3, end: 6, query: '知识' });
  assert.deepEqual(slashQuery('/mi 后面的文字', 3), { start: 0, end: 3, query: 'mi' });
  for (const text of ['https://example.com', '/Users/pc/report', '这个/那个', '日期 11/04', '\\/cli']) assert.equal(slashQuery(text, text.length), null);
  assert.equal(slashQuery('/cli', 4, 2), null);
  const catalog = await pluginCatalog(catalogEngine);
  assert.deepEqual(filterPlugins(catalog, 'AGENT').map(pluginKey), ['agent:same']);
  assert.deepEqual(filterPlugins(catalog, '同名', [catalog[0]]).map(pluginKey), ['agent:same']);
  assert.deepEqual(filterPlugins(catalog, '知识').map(pluginKey), []);
  assert.deepEqual(filterPlugins(catalog, '').map(pluginKey), ['cli:same', 'agent:same']);
  assert.deepEqual(filterPlugins([{ id: 'off', type: 'agent', name: '离线', ready: false }], ''), []);
});

function reply(model) {
  const stream = createAssistantMessageEventStream();
  const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(), content: [{ type: 'text', text: '已收到选择。' }],
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, stopReason: 'stop' };
  stream.push({ type: 'done', reason: 'stop', message }); stream.end(message); return stream;
}

test('both chat routes persist exact selections and Pi receives them in the user turn (provider double)', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-plugins-'));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory });
  try {
    const all = await host.action('plugin_list');
    const selections = ['agent', 'cli', 'mcp'].map(type => all.find(item => item.type === type)).filter(Boolean);
    assert.equal(selections.length, 3);
    const refs = selections.map(({ id, type }) => ({ id, type }));
    for (const [route, read, converseFn] of [['chat', 'list', converse], ['automation_chat', 'automation_get', converseAutomation]]) {
      const created = await host.action(route, { text: '用选中的插件整理资料', plugins: refs });
      assert.equal(created.reason, 'missing_credentials');
      const identity = route === 'chat' ? { loopId: created.loopId } : { taskId: created.taskId };
      const current = route === 'chat' ? (await host.action(read)).find(item => item.id === created.loopId) : await host.action(read, identity);
      assert.deepEqual(current.messages[0].plugins.map(pluginKey), refs.map(pluginKey));
      let checked = false;
      await converseFn({ engine: host.engine, ...identity, text: current.messages[0].text, plugins: await resolvePlugins(host.engine, refs), config: { model: 'deepseek-flash', key: 'test-not-a-secret' }, emit() {}, streamFn(model, context) {
        const user = context.messages.filter(message => message.role === 'user').at(-1);
        const content = typeof user.content === 'string' ? user.content : user.content.map(part => part.text).join('');
        assert.match(content, /用户通过 \/ 选择的插件/);
        for (const ref of refs) { assert.ok(content.includes(`"id":"${ref.id}"`)); assert.ok(content.includes(`"type":"${ref.type}"`)); }
        assert.doesNotMatch(content, /test-not-a-secret/);
        checked = true; return reply(model);
      } });
      assert.ok(checked);
      const session = await host.engine.call(route === 'chat' ? 'session_get' : 'automation_session_get', identity);
      assert.match(JSON.stringify(session.filter(message => message.role === 'user')), /用户通过/);
    }
    const before = (await host.action('list')).length;
    await assert.rejects(host.action('chat', { text: '选择已删除插件', plugins: [{ id: 'deleted', type: 'mcp' }] }), /已不存在/);
    assert.equal((await host.action('list')).length, before);
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});

test('message chips preserve exact positions, including identical names and trimmed input', async () => {
  const { pluginReferences } = await import('../host/plugins.mjs');
  const { messagePluginParts } = await import('../src/plugin-selection.mjs');
  const text = '  用 /工具 读取，再用 /工具 处理';
  const plugins = [{id:'one',type:'mcp',name:'工具'},{id:'two',type:'cli',name:'工具'}];
  const refs = plugins.map((p,i) => ({...p,start:i ? text.lastIndexOf('/工具') : text.indexOf('/工具'),end:(i ? text.lastIndexOf('/工具') : text.indexOf('/工具'))+3}));
  const saved = pluginReferences(plugins,text,refs);
  const rendered = messagePluginParts(text.trim(),saved);
  assert.deepEqual(rendered.parts.filter(p=>typeof p!=='string').map(pluginKey),['mcp:one','cli:two']);
  assert.equal(rendered.remaining.length,0);
  assert.equal(rendered.parts.map(p=>typeof p==='string'?p:`/${p.name}`).join(''),text.trim());
  assert.equal(messagePluginParts('用 /工具 读取',[plugins[0]]).remaining.length,0);
  assert.equal(messagePluginParts('用 /工具 读取，再用 /工具 处理',plugins).remaining.length,2);
});

test('MCP-only selections never discover Agents; Agent-only selections only request their ids', async () => {
  const calls = [];
  const engine = { call: async (method, params) => {
    calls.push({method,params});
    return catalogEngine.call(method);
  }};
  await resolvePlugins(engine, [{id:'remote',type:'mcp'}]);
  assert.deepEqual(calls.map(call => call.method), ['connector_list','connector_presets']);
  calls.length = 0;
  await resolvePlugins(engine, [{id:'same',type:'agent'}]);
  assert.deepEqual(calls, [{method:'agent_clients',params:{ids:['same']}}]);
});
