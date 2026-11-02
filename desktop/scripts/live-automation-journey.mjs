import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { Host } from '../host/service.mjs';
import { sourceTemplate, outputScript } from '../src/automation-model.js';
const desktop = path.resolve(new URL('..', import.meta.url).pathname);
const root = path.resolve(process.env.LOOPS_AUTOMATION_QA_DIR || path.join(desktop, '.local/automation-real-' + Date.now()));
const evidence = path.join(desktop, 'qa/artifacts/live-automation');
await fs.mkdir(root, { recursive: true }); await fs.mkdir(evidence, { recursive: true });
let host;
const options = { dataDirectory: root, workspaceRoot: path.join(root, 'workspaces'), configDirectory: desktop, fallbackConfigDirectory: path.dirname(desktop) };
function open() { host = new Host(options); }
async function waitFor(check, timeout = 15000) {
  return new Promise((resolve, reject) => {
    let checking = false, again = false, ended = false;
    const timer = setTimeout(() => { end(); reject(new Error('Acceptance condition timed out')); }, timeout);
    const end = () => { ended = true; clearTimeout(timer); host.engine.off('event', tick); };
    async function tick() {
      if (ended) return;
      if (checking) { again = true; return; }
      checking = true;
      try { const value = await check(); if (value) { end(); resolve(value); } }
      catch (error) { end(); reject(error); }
      finally { checking = false; if (again && !ended) { again = false; void tick(); } }
    }
    host.engine.on('event', tick); void tick();
  });
}
async function finished(taskId, count = 1) {
  return waitFor(async () => {
    const task = await host.action('automation_get', { taskId });
    const runs = task.runs.filter(r => r.status !== 'sample');
    if (runs.some(r => ['failed', 'unknown'].includes(r.status))) throw new Error(JSON.stringify(runs));
    return runs.filter(r => r.status === 'success').length >= count && task;
  });
}
function definition(title, suffix = '') {
  return { schemaVersion: 2, title, goal: title, completion: '实际结果文件存在', inputs: [{ id: 'source', label: '原文', kind: 'file', required: true }], finalOutputs: ['${steps.process.outputs.report}'],
    scripts: { 'scripts/process.py': `import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text(Path(c['inputs']['source']).read_text().upper()+${JSON.stringify(suffix)})\n` },
    steps: [{ id: 'process', label: title, instructions: title, inputs: { source: '${inputs.source}' }, execution: { tool: 'python', script: 'scripts/process.py' }, outputs: [{ id: 'report', label: '报告', path: 'report.txt' }], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }] };
}
const results = { startedAt: new Date().toISOString(), dataDirectory: root, definitionOrigin: 'hand-authored Python-only Loop fixtures; production Host and real subprocesses; no model doubles', checks: [] };
const record = (name, extra = {}) => { results.checks.push({ name, passed: true, ...extra }); console.log('PASS', name); };
let receiver;
try {
  open();
  const loops = [];
  for (const [title, suffix] of [['内容整理', ''], ['报告编写', '\n报告生成完成。']]) {
    const loop = await host.engine.call('new'); await host.engine.call('definition', { loopId: loop.id, definition: definition(title, suffix) }); loops.push(await host.action('save', { loopId: loop.id }));
  }
  const source = path.join(root, 'source.txt'); await fs.writeFile(source, '真实输入 alpha 17');
  function specFor(title, input) { return { title, source: input, nodes: [{ id: 'prepare', loopId: loops[0].id, revision: 1, bindings: { source: 'input.source' } }, { id: 'report', loopId: loops[1].id, revision: 1, bindings: { source: 'prepare.process.report' } }], outputs: [{ id: 'local', title: '本地报告', config: { directory: path.join(root, 'delivered', title) }, script: outputScript, bindings: { report: 'report.process.report' } }], policy: { queueLimit: 20, scriptTimeout: 10, sourceRestarts: 0 } }; }
  const once = sourceTemplate('once'); once.config.path = source;
  let task = await host.action('automation_save', { spec: specFor('单次采集', once) });
  const taskId = task.id;
  task = await host.action('automation_sample', { taskId }); assert.equal(task.runs.length, 1); assert.equal(task.runs[0].status, 'sample');
  record('sample does not execute Loops or deliveries');
  await host.action('automation_run', { taskId, runId: task.runs[0].id }); task = await finished(taskId);
  const run = task.runs.find(r => r.status === 'success');
  const report = await fs.readFile(run.nodes.local.receipt.target, 'utf8'); assert.equal(report, '真实输入 ALPHA 17\n报告生成完成。');
  await fs.writeFile(path.join(evidence, 'real-delivered-report.txt'), report);
  record('two real Python Loops chain and deliver exact file content', { runId: run.id, receipt: run.nodes.local.receipt });
  await host.action('automation_test_output', { taskId, runId: run.id, nodeId: 'local' }); task = await finished(taskId, 2);
  assert.equal(task.runs.at(-1).nodes.prepare.runId, run.nodes.prepare.runId);
  record('standalone output test reuses successful Loop results');

  const interval = sourceTemplate('interval'); interval.config = { path: source, interval: 1 };
  task = await host.action('automation_save', { spec: specFor('定时采集', interval) });
  await host.action('automation_enable', { taskId: task.id, enabled: true });
  task = await finished(task.id, 2); await host.action('automation_enable', { taskId: task.id, enabled: false });
  record('Python timer emits multiple real events', { eventKeys: task.runs.map(r => r.eventKey) });

  const watched = path.join(root, 'watched'); await fs.mkdir(watched); await fs.writeFile(path.join(watched, 'one.txt'), 'watch one');
  const watcher = sourceTemplate('watch'); watcher.config = { directory: watched, path: source, interval: 1 };
  task = await host.action('automation_save', { spec: specFor('目录监听', watcher) });
  await host.action('automation_enable', { taskId: task.id, enabled: true }); await finished(task.id);
  await fs.writeFile(path.join(watched, 'two.txt'), 'watch two'); task = await finished(task.id, 2); await host.action('automation_enable', { taskId: task.id, enabled: false });
  assert.equal(new Set(task.runs.map(r => r.eventKey)).size, 2);
  record('directory listener handles new files without duplicating unchanged files');

  const reserve = http.createServer(); await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve)); const port = reserve.address().port; await new Promise(resolve => reserve.close(resolve));
  const webhook = sourceTemplate('webhook'); webhook.config = { port, path: source };
  task = await host.action('automation_save', { spec: specFor('Webhook', webhook) });
  await host.action('automation_enable', { taskId: task.id, enabled: true });
  await waitFor(async () => (await host.action('automation_get', { taskId: task.id })).logs.some(l => l.text.includes('Webhook listening')));
  const send = () => fetch(`http://127.0.0.1:${port}`, { method: 'POST', headers: { 'X-Event-ID': 'real-webhook-1' }, body: 'webhook actual content' });
  const first = await send(); assert.equal(first.status, 202); const firstAck = await first.json();
  const second = await send(); assert.equal(second.status, 202); const secondAck = await second.json(); assert.equal(firstAck.runId, secondAck.runId); assert.equal(secondAck.duplicate, true);
  task = await finished(task.id); assert.equal(task.runs.length, 1); assert.equal(await fs.readFile(task.runs[0].nodes.local.receipt.target, 'utf8'), 'WEBHOOK ACTUAL CONTENT\n报告生成完成。');
  await host.action('automation_enable', { taskId: task.id, enabled: false });
  record('real webhook acknowledges durable acceptance and deduplicates retries');

  const received = [];
  receiver = http.createServer(async (req, res) => { let raw = ''; for await (const chunk of req) raw += chunk; received.push({ body: JSON.parse(raw), auth: req.headers.authorization, key: req.headers['idempotency-key'] }); res.writeHead(201, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ id: 'local-record-1' })); });
  await new Promise(resolve => receiver.listen(0, '127.0.0.1', resolve)); const remote = `127.0.0.1:${receiver.address().port}`;
  await fs.writeFile(path.join(root, 'connections.local.json'), JSON.stringify({ acceptance: { hosts: [remote], headers: { Authorization: 'Bearer acceptance-only-token' } } }));
  let httpSpec = specFor('接口交付', once);
  httpSpec.outputs = [{ id: 'remote', title: '本地接口', config: { url: `http://${remote}` }, bindings: { report: 'report.process.report' }, script: `import json\ndef run(ctx):\n    response = ctx.request(ctx.config['url'], connection='acceptance', method='POST', headers={'Idempotency-Key':ctx.delivery_key}, body={'report':ctx.read_text('report')})\n    assert response['status'] == 201\n    ctx.receipt(target=ctx.config['url'],status='accepted',remoteId=json.loads(response['body'])['id'])\n` }];
  task = await host.action('automation_save', { spec: httpSpec }); await host.action('automation_run', { taskId: task.id, inputs: { source: { kind: 'file', path: source } } }); task = await finished(task.id);
  assert.equal(received[0].body.report, report); assert.equal(received[0].auth, 'Bearer acceptance-only-token'); assert.equal(task.runs[0].nodes.remote.receipt.remoteId, 'local-record-1');
  assert.ok(!JSON.stringify(task).includes('acceptance-only-token'));
  record('authenticated local HTTP delivery transmits real content; credentials stay out of task state');

  await host.close(); open();
  const restored = await host.action('automation_get', { taskId }); assert.equal(restored.runs[1].nodes.local.receipt.target, run.nodes.local.receipt.target);
  record('production Host restart preserves versions, input samples, child run links and receipts');
  const draft = await host.action('automation_draft', { text: `创建一个单次任务，读取本地文件 ${source}，先运行内容整理，再运行报告编写，把最终报告的真实正文保存到本地目录 ${path.join(root, 'ai-delivered')}。选择目录中的已保存版本；输入测试读取同一个真实文件。输出文件必须包含生成的报告正文。`, spec: null });
  assert.ok(draft.spec, draft.reply);
  task = await host.action('automation_save', { spec: draft.spec }); await host.action('automation_sample', { taskId: task.id });
  task = await host.action('automation_get', { taskId: task.id }); await host.action('automation_run', { taskId: task.id, runId: task.runs[0].id }); task = await finished(task.id);
  const aiRun = task.runs.at(-1); const aiReceipt = aiRun.nodes[draft.spec.outputs[0].id].receipt;
  assert.equal(await fs.readFile(aiReceipt.target, 'utf8'), report);
  await fs.writeFile(path.join(evidence, 'ai-delivered-report.txt'), await fs.readFile(aiReceipt.target));
  record('real Pi + DeepSeek drafts, saves, samples, chains and delivers actual report content', { taskId: task.id, runId: aiRun.id, receipt: aiReceipt });
  results.endedAt = new Date().toISOString(); await fs.writeFile(path.join(evidence, 'live-results.json'), JSON.stringify(results, null, 2));
} catch (error) {
  results.error = error.message; await fs.writeFile(path.join(evidence, 'live-results.json'), JSON.stringify(results, null, 2)); console.error(error.message); process.exitCode = 1;
} finally { if (receiver) await new Promise(resolve => receiver.close(resolve)); if (host) await host.close(); }
