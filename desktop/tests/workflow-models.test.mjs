import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { ModelSettings } from '../host/models.mjs';
import { WorkflowModels } from '../host/workflow-models.mjs';
import { Engine } from '../host/engine.mjs';
import { builderTools } from '../host/agent.mjs';

const stream = text => 'data: ' + JSON.stringify({ id: 'fixture', choices: [{ index: 0, delta: { role: 'assistant', content: text }, finish_reason: null }] }) + '\n\ndata: ' + JSON.stringify({ id: 'fixture', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }) + '\n\ndata: [DONE]\n\n';
async function settings() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-workflow-models-'));
  const keys = new Map();
  const manager = new ModelSettings({ dataDirectory: root, configDirectory: root,
    encryptSecret: key => { const id = `test-cipher-${keys.size}`; keys.set(id, key); return id; }, decryptSecret: id => keys.get(id) });
  return { root, manager };
}
function completion(engine, loopId) {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); engine.off('event', listener); };
    const listener = event => {
      const run = event.loops?.find(loop => loop.id === loopId)?.runs.at(-1);
      if (run && ['success', 'failed', 'stopped'].includes(run.status)) { cleanup(); resolve(run); }
    };
    const timer = setTimeout(() => { cleanup(); reject(new Error('test run timed out')); }, 10000);
    engine.on('event', listener);
  });
}

test('Python model resource invokes a local HTTP provider through the real engine, records results, and hides credentials', async () => {
  const { root, manager } = await settings();
  const requests = [];
  let waitingStarted;
  const waiting = new Promise(resolve => { waitingStarted = resolve; });
  const server = http.createServer(async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk;
    const value = JSON.parse(body);
    requests.push({ value, key: request.headers.authorization });
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    if (value.messages.at(-1).content === 'wait for cancellation') { response.flushHeaders(); waitingStarted(); return; }
    response.end(stream('本地 HTTP 测试返回：' + value.messages.at(-1).content));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let engine;
  try {
    const entry = manager.save({ name: '测试资源', provider: 'compatible', model: 'test-text-model', baseUrl: `http://127.0.0.1:${server.address().port}/v1`, apiKey: 'test-private-key' }).models.at(-1);
    engine = new Engine({ dataDirectory: root, workspaceRoot: path.join(root, 'workspaces'), modelHost: new WorkflowModels(manager) });
    const publicEvents = [];
    engine.on('event', event => publicEvents.push(event));
    const loop = await engine.call('new');
    const tools = builderTools(engine, loop.id);
    const catalog = (await tools.find(tool => tool.name === 'list_models').execute('catalog', {})).details;
    assert.ok(catalog.models.some(item => item.id === entry.id));
    const resource = (await tools.find(tool => tool.name === 'add_model_resource').execute('resource', { modelId: entry.id })).details;
    const definition = { schemaVersion: 2, title: '文本提取', goal: '提取输入', completion: '文件存在',
      inputs: [{ id: 'text', label: '文本', kind: 'text', required: true }], finalOutputs: ['${steps.extract.outputs.text}'],
      scripts: { 'scripts/extract.py': "import os,json\nfrom pathlib import Path\nfrom loops_ai import complete\nimport argparse\np=argparse.ArgumentParser()\np.add_argument('--model',required=True)\np.add_argument('--reasoning',required=True)\nargs=p.parse_args()\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nassert not any(k.endswith('API_KEY') for k in os.environ)\nPath(c['outputs']['text']).write_text(complete(c['inputs']['text'], model=args.model, reasoning=args.reasoning))\n" },
      steps: [{ id: 'extract', label: '提取', instructions: '按输入提取', inputs: { text: '${inputs.text}' },
        execution: { tool: 'python', script: 'scripts/extract.py', args: ['--model', resource.id, '--reasoning', 'off'] },
        outputs: [{ id: 'text', label: '结果', path: 'result.txt' }], checks: [], onFailure: { action: 'stop' } }] };
    await tools.find(tool => tool.name === 'update_loop').execute('define', { definition });
    await engine.call('save', { loopId: loop.id });
    const done = completion(engine, loop.id);
    await engine.call('start', { loopId: loop.id, inputs: { text: '这一段是输入' } });
    const run = await done;
    assert.equal(run.status, 'success', run.reason);
    assert.equal(await fs.readFile(run.artifacts[0].path, 'utf8'), '本地 HTTP 测试返回：这一段是输入');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].key, 'Bearer test-private-key');
    assert.equal(requests[0].value.model, 'test-text-model');
    assert.equal(run.modelCalls[0].model, 'test-text-model');
    assert.equal(run.modelCalls[0].reasoning, 'off');
    assert.ok(!JSON.stringify({ publicEvents, run, catalog, resource }).includes('test-private-key'));
    assert.ok(!publicEvents.some(event => event.hostRequest || event.hostResponse));
    assert.equal(await fs.readFile(path.join(run.directory, 'scripts/extract.py'), 'utf8'), run.definition.scripts['scripts/extract.py']);
    assert.deepEqual(run.definition.steps[0].execution.args, ['--model', resource.id, '--reasoning', 'off']);
    const stopped = completion(engine, loop.id);
    const pending = await engine.call('start', { loopId: loop.id, inputs: { text: 'wait for cancellation' } });
    await waiting;
    await engine.call('stop', { loopId: loop.id, runId: pending.id });
    const cancelled = await stopped;
    assert.equal(cancelled.status, 'stopped');
    assert.equal(cancelled.modelCalls[0].status, 'stopped');
    assert.equal(engine.hostRequests.size, 0);
  } finally {
    await engine?.close();
    await new Promise(resolve => server.close(resolve));
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('model host sends the node reasoning level, rejects drift and unsupported efforts, and sanitizes provider errors (fetch double)', async t => {
  const { root, manager } = await settings();
  try {
    const entry = manager.save({ name: 'DeepSeek 测试', provider: 'deepseek', model: 'deepseek-flash', apiKey: 'private-provider-key' }).models.at(-1);
    const host = new WorkflowModels(manager);
    const binding = { ...host.describe(entry.id), reasoning: 'high' };
    const requests = [];
    t.mock.method(globalThis, 'fetch', async (_url, options) => {
      requests.push(JSON.parse(options.body));
      return new Response(stream('answer'), { headers: { 'Content-Type': 'text/event-stream' } });
    });
    assert.equal((await host.handle('complete', { binding, prompt: 'test' })).text, 'answer');
    assert.equal(requests[0].thinking.type, 'enabled');
    assert.equal(requests[0].reasoning_effort, 'high');
    await host.handle('complete', { binding: { ...binding, reasoning: 'off' }, prompt: 'test' });
    assert.equal(requests[1].thinking.type, 'disabled');
    await assert.rejects(host.handle('complete', { binding: { ...binding, reasoning: 'invalid' }, prompt: 'test' }), /思考强度/);
    await assert.rejects(host.handle('complete', { binding: { ...binding, model: 'changed' }, prompt: 'test' }), /配置已改变/);
    await assert.rejects(host.handle('complete', { binding, prompt: '', maxTokens: 0 }), /有效的文本/);
    t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ error: { message: 'private-provider-key leaked upstream' } }), { status: 401, headers: { 'Content-Type': 'application/json' } }));
    await assert.rejects(host.handle('complete', { binding, prompt: 'test' }), error => !error.message.includes('private-provider-key') && /验证失败/.test(error.message));
    t.mock.method(globalThis, 'fetch', async () => new Response(stream('private-provider-key'), { headers: { 'Content-Type': 'text/event-stream' } }));
    await assert.rejects(host.handle('complete', { binding, prompt: 'test' }), error => !error.message.includes('private-provider-key'));
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
