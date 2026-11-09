import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createAssistantMessageEventStream, validateToolCall } from '@earendil-works/pi-ai';
import { Engine } from '../host/engine.mjs';
import { converse, builderTools } from '../host/agent.mjs';

test('builder accepts default sessions and explicit independent contexts in both Agent modes', () => {
  const tools = builderTools({}, 'test-loop');
  for (const mode of ['read-only', 'write']) {
    for (const session of [undefined, 'independent-review']) {
      const execution = { tool: 'agent', mode, ...(session === undefined ? {} : { session }) };
      const definition = { schemaVersion: 2, title: '复核', goal: '复核结果', completion: '完成复核',
        inputs: [], scripts: {}, finalOutputs: [], steps: [{ id: 'review', label: '复核', instructions: '复核结果',
          inputs: {}, execution, outputs: [], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }] };
      const args = validateToolCall(tools, { type: 'toolCall', id: 'definition', name: 'update_loop', arguments: { definition } });
      assert.deepEqual(args.definition.steps[0].execution, execution);
    }
  }
});

test('Pi tool calling constructs, corrects and saves a versioned flow through the real service (provider test double)', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-agent-test-'));
  const engine = new Engine({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces') });
  try {
    const loop = await engine.call('new');
    const fixture = await engine.call('fixture', { loopId: loop.id });
    await engine.call('configure', { loopId: loop.id, config: fixture });
    const definition = { schemaVersion: 2, title: '资料整理', goal: '根据输入生成文件', completion: '交付文件存在', inputs: [], scripts: { 'scripts/report.py': "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text('result')" }, steps: [{ id: 'report', label: '整理资料', instructions: '生成结果', inputs: {}, execution: { tool: 'python', script: 'scripts/report.py' }, outputs: [{ id: 'report', label: '报告', path: 'report.txt' }], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }] };

    definition.finalOutputs = ['${steps.report.outputs.report}'];
    let calls = 0;
    const streamFn = (model) => {
      const index = calls++;
      const stream = createAssistantMessageEventStream();
      const names = ['inspect_project', 'update_loop', 'start_trial', 'get_run_result', 'save_loop'];
      const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(),
        content: index < names.length ? [{ type: 'toolCall', id: `call-${index}`, name: names[index], arguments: index === 1 ? { definition } : {} }] : [{ type: 'text', text: '已保存流程。' }],
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, stopReason: index < names.length ? 'toolUse' : 'stop' };
      stream.push({ type: 'done', reason: message.stopReason, message }); stream.end(message); return stream;
    };
    await converse({ engine, loopId: loop.id, text: '搭建研发流程并保存', config: { model: 'deepseek-flash', key: 'test-only-not-a-key' }, emit() {}, streamFn });
    const saved = (await engine.call('list')).find((item) => item.id === loop.id);
    assert.equal(saved.savedRevision, 1);
    const process = saved.messages.find(m => m.role === 'process');
    assert.equal(process.process.status, 'success');
    assert(process.process.durationMs >= 0);
    assert.deepEqual(process.process.events.filter(e => e.type === 'tool').map(e => e.status), Array(5).fill('success'));
    assert.equal(saved.messages.at(-1).text, '已保存流程。');
    assert.equal(saved.messages.filter(m => m.role === 'assistant').length, 1);
    assert(!JSON.stringify(process).includes('test-only-not-a-key'));
    assert.equal(saved.definition.title, '资料整理');
    assert.deepEqual(saved.definition.finalOutputs, ['${steps.report.outputs.report}']);
    assert.match(await fs.readFile(path.join(saved.versions[0].path, 'workflow.py'), 'utf8'), /run_workflow/);
    const corrected = structuredClone(saved.definition);
    corrected.steps[0].instructions = '根据实际触发条件复现，保留原始证据。';
    await engine.call('definition', { loopId: loop.id, definition: corrected });
    const next = (await engine.call('list')).find((item) => item.id === loop.id);
    assert.equal(next.revision, 2); assert.equal(next.savedRevision, 1);
    assert.equal(JSON.parse(await fs.readFile(path.join(saved.versions[0].path, 'loop.json'), 'utf8')).definition.steps[0].instructions, saved.definition.steps[0].instructions);
    assert.ok((await engine.call('session_get', { loopId: loop.id })).some((message) => message.role === 'toolResult'));
  } finally { await engine.close(); await fs.rm(directory, { recursive: true, force: true }); }
});

test('run result waits for completion and can be cancelled (service test double)', async () => {
  const { builderTools } = await import('../host/agent.mjs');
  let calls = 0;
  const engine = { call: async () => [{ id: 'loop', runs: [{ id: 'run', status: ++calls === 1 ? 'running' : 'success' }] }] };
  const tool = builderTools(engine, 'loop').find(tool => tool.name === 'get_run_result');
  assert.equal((await tool.execute('wait', {})).details.status, 'success');
  assert.equal(calls, 2);
  const controller = new AbortController();
  const pending = builderTools({ call: async () => [{ id: 'loop', runs: [{ status: 'running' }] }] }, 'loop', controller.signal)
    .find(tool => tool.name === 'get_run_result').execute('cancel', {});
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
});
