import test from 'node:test';
import assert from 'node:assert/strict';
import { FailureReviews } from '../host/failure-reviews.mjs';
const state = (status, extra = {}) => [{ id: 'loop', runs: [{ id: 'run', status, ...extra }] }];
const tick = () => new Promise(resolve => setImmediate(resolve));

test('live failure triggers one review; historical failures and duplicate events do not', async () => {
  const calls = [];
  const watcher = new FailureReviews({ review: async (...args) => calls.push(args), busy: () => false, onError: assert.fail });
  watcher.observe(state('failed')); await tick(); assert.equal(calls.length, 0);
  watcher.observe(state('running')); watcher.observe(state('failed')); watcher.observe(state('failed'));
  await tick(); assert.deepEqual(calls, [['loop', 'run']]);
});

test('busy chat defers failure review; consumed failures and cancellation suppress restart', async () => {
  const calls = []; let busy = true;
  const watcher = new FailureReviews({ review: async (...args) => calls.push(args), busy: () => busy, onError: assert.fail });
  watcher.observe(state('correcting')); watcher.observe(state('failed')); await tick(); assert.equal(calls.length, 0);
  busy = false; await watcher.drain(); assert.equal(calls.length, 1);
  busy = true; watcher.observe(state('running')); watcher.observe(state('failed'));
  watcher.observe(state('failed', { failureReview: { status: 'claimed' } }));
  busy = false; await watcher.drain(); assert.equal(calls.length, 1);
  busy = true; watcher.observe(state('running')); watcher.observe(state('failed')); watcher.cancel('loop');
  busy = false; await watcher.drain(); assert.equal(calls.length, 1);
  watcher.observe(state('running')); watcher.observe(state('stopped')); await tick(); assert.equal(calls.length, 1);
  watcher.close(); watcher.observe(state('running')); watcher.observe(state('failed')); await tick(); assert.equal(calls.length, 1);
});

import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Host } from '../host/service.mjs';
import { converse } from '../host/agent.mjs';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';

function response(model, content) {
  const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(), content,
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: content.some(p => p.type === 'toolCall') ? 'toolUse' : 'stop' };
  const stream = createAssistantMessageEventStream(); stream.push({ type: 'done', reason: message.stopReason, message }); stream.end(message); return stream;
}
for (const repairable of [true, false]) test(`failed real Python run automatically invokes AI: ${repairable ? 'repair and verify' : 'ask user'} (provider double)`, async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-failure-review-'));
  const host = new Host({ dataDirectory: directory, configDirectory: directory, workspaceRoot: path.join(directory, 'workspaces') });
  const done = Promise.withResolvers();
  try {
    host.models.config = () => ({ model: 'deepseek-flash', key: 'test-only' });
    const loop = await host.action('new');
    const definition = { schemaVersion: 2, title: '恢复测试', goal: '生成报告', completion: '文件存在', inputs: [], scripts: { 'scripts/report.py': 'raise ValueError("test failure")' }, finalOutputs: ['${steps.report.outputs.report}'],
      steps: [{ id: 'report', label: '生成报告', instructions: '生成报告', inputs: {}, execution: { tool: 'python', script: 'scripts/report.py' }, outputs: [{ id: 'report', kind: 'file', label: '报告', path: 'report.txt' }], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }] };
    await host.engine.call('definition', { loopId: loop.id, definition });
    const repaired = structuredClone(definition);
    repaired.scripts['scripts/report.py'] = "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text('verified real Python')";
    let reviews = 0;
    host.startLoopConversation = ({ loopId, text, config, controller }) => {
      reviews++;
      assert.match(text, /平台运行事件/);
      let index = 0;
      const names = repairable ? ['get_run_result', 'update_loop', 'start_trial', 'get_run_result', 'save_loop'] : ['get_run_result'];
      const job = converse({ engine: host.engine, loopId, text, config, signal: controller.signal, emit() {}, streamFn: (model, context) => {
        assert.match(JSON.stringify(context), /运行失败后先读取真实运行记录/);
        const name = names[index++];
        return response(model, name ? [{ type: 'toolCall', id: `review-${index}`, name, arguments: name === 'update_loop' ? { definition: repaired } : {} }] : [{ type: 'text', text: repairable ? '已修复，真实检查通过。' : '无法读取业务资料，需要你提供文件后才能继续；未重跑。' }]);
      } }).then(done.resolve, done.reject).finally(() => { host.busy.delete(loopId); host.loopJobs.delete(loopId); host.failureReviews.drain(); });
      host.loopJobs.set(loopId, job);
    };
    await host.action('start', { loopId: loop.id });
    await done.promise;
    const current = (await host.action('list')).find(l => l.id === loop.id);
    assert.equal(reviews, 1);
    assert.equal(current.runs[0].status, 'failed');
    assert.equal(current.runs[0].failureReview.status, 'claimed');
    assert.equal(await host.engine.call('claim_failure_review', { loopId: loop.id, runId: current.runs[0].id }), false);
    assert.equal(current.runs.length, repairable ? 2 : 1);
    assert.equal(current.runs.at(-1).status, repairable ? 'success' : 'failed', current.runs.at(-1).reason);
    assert.equal(current.messages.filter(m => m.role === 'user').length, 0);
    assert.match(current.messages.at(-1).text, repairable ? /真实检查通过/ : /提供文件/);
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});
