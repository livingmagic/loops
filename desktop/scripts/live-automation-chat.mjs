import { modelHarnessOptions, closeModelHarness } from './model-harness.mjs';
const modelOptions = await modelHarnessOptions();
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { Host } from '../host/service.mjs';
const desktop = path.resolve(new URL('..', import.meta.url).pathname);
const root = path.resolve(process.env.LOOPS_CHAT_QA_DIR || path.join(desktop, '.local/automation-chat-' + Date.now()));
const qa = path.join(desktop, 'qa/artifacts/live-automation-chat');
await fs.mkdir(root, { recursive: true }); await fs.mkdir(qa, { recursive: true });
const options = { ...modelOptions, dataDirectory: root, workspaceRoot: path.join(root, 'workspaces'), configDirectory: desktop, fallbackConfigDirectory: path.dirname(desktop) };
let host = new Host(options);
const evidence = { dataDirectory: root, at: new Date().toISOString(), model: 'real Pi + DeepSeek, no model double', loopDefinitions: 'hand-authored Python fixtures', turns: [] };
function definition(title, suffix = '') {
  return { schemaVersion: 2, title, goal: title, completion: '实际文件存在', inputs: [{ id: 'source', label: '原文', kind: 'file', required: true }], finalOutputs: ['${steps.convert.outputs.report}'],
    scripts: { 'scripts/process.py': `import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT']); Path(c['outputs']['report']).write_text(Path(c['inputs']['source']).read_text().upper()+${JSON.stringify(suffix)})` },
    steps: [{ id: 'convert', label: title, instructions: title, inputs: { source: '${inputs.source}' }, execution: { tool: 'python', script: 'scripts/process.py' }, outputs: [{ id: 'report', label: '报告', path: 'report.txt' }], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }] };
}
async function waitFor(check, timeout = 180000) {
  return new Promise((resolve, reject) => {
    let pending = false, again = false, ended = false;
    const end = () => { ended = true; clearTimeout(timer); host.off('event', onEvent); };
    const timer = setTimeout(() => { end(); reject(new Error('Live conversation timeout')); }, timeout);
    const onEvent = event => { if (['automation_agent_done', 'automation_state', 'state'].includes(event.event)) void tick(); };
    async function tick() {
      if (ended) return;
      if (pending) { again = true; return; }
      pending = true;
      try { const value = await check(); if (value) { end(); resolve(value); } }
      catch (error) { end(); reject(error); }
      finally { pending = false; if (again && !ended) { again = false; void tick(); } }
    }
    host.on('event', onEvent); void tick();
  });
}
let taskId;
async function turn(text) {
  const accepted = await host.action('automation_chat', { taskId, text });
  assert.equal(accepted.accepted, true, JSON.stringify(accepted)); taskId = accepted.taskId;
  const task = await waitFor(async () => { const t = await host.action('automation_get', { taskId }); return !t.chatBusy && t; });
  const answers = task.messages.filter(m => m.role !== 'user').slice(evidence.turns.reduce((n, t) => n + t.answers.length, 0));
  assert.equal(task.messages.at(-1)?.role, 'assistant', JSON.stringify(task.messages.at(-1)));
  evidence.turns.push({ text, answers, revision: task.revision, draftRevision: task.draftRevision, enabled: task.enabled, runCount: task.runs.length });
  await fs.writeFile(path.join(qa, 'live-chat-results.json'), JSON.stringify(evidence, null, 2));
  console.log('TURN', evidence.turns.length, task.messages.at(-1).text);
  return task;
}
try {
  const source = path.join(root, 'source.txt'), target = path.join(root, 'delivered');
  await fs.writeFile(source, '对话式输入 beta 23');
  for (const [title, suffix] of [['内容整理', ''], ['报告编写', '\n报告生成完成。']]) {
    const loop = await host.engine.call('new');
    await host.engine.call('definition', { loopId: loop.id, definition: definition(title, suffix) });
    await host.action('start', { loopId: loop.id, inputs: { source } });
    const trial = await waitFor(async () => {
      const run = (await host.action('list')).find(item => item.id === loop.id).runs.at(-1);
      return ['success', 'failed', 'stopped', 'interrupted'].includes(run.status) && run;
    });
    assert.equal(trial.status, 'success', trial.reason);
    await host.action('save', { loopId: loop.id });
  }
  let task = await turn('我想用“内容整理”接“报告编写”，每天自动处理数据。输入和交付位置稍后再告诉你，先别运行。');
  assert.equal(task.revision, 0); assert.equal(task.draftSpec, null); assert.equal(task.runs.length, 0);
  task = await turn(`输入文件是 ${source}，结果保存到 ${target}。每天上午9点执行，先把流程搭出来，不保存也不启用。`);
  assert.ok(task.draftSpec); assert.equal(task.revision, 0); assert.equal(task.enabled, false); assert.equal(task.runs.length, 0);
  const firstDraft = structuredClone(task.draftSpec);
  task = await turn('改成每小时执行，其他不变。仍然只修改草稿。');
  assert.ok(task.draftRevision > 1); assert.notDeepEqual(task.draftSpec.source, firstDraft.source);
  assert.deepEqual(task.draftSpec.nodes.map(n => n.loopId), firstDraft.nodes.map(n => n.loopId));
  assert.ok(JSON.stringify(task.draftSpec).includes(source)); assert.ok(JSON.stringify(task.draftSpec).includes(target));
  task = await turn('保存这个流程，不要启用。');
  assert.equal(task.revision, 1); assert.equal(task.draftSpec, null); assert.equal(task.enabled, false);
  task = await turn('用当前输入试跑一次，检查交付结果，不要启用自动运行。');
  task = await waitFor(async () => { const t = await host.action('automation_get', { taskId }); const run = t.runs.filter(r => r.status !== 'sample').at(-1); if (run && ['failed', 'unknown'].includes(run.status)) throw new Error(JSON.stringify(run)); return run?.status === 'success' && t; }, 30000);
  const run = task.runs.filter(r => r.status !== 'sample').at(-1);
  const receipt = Object.values(run.nodes).find(n => n.receipt).receipt;
  const actual = await fs.readFile(receipt.target, 'utf8'); assert.equal(actual.trim(), '对话式输入 BETA 23\n报告生成完成。');
  assert.equal(task.enabled, false);
  const before = task.messages.length, savedRevision = task.revision;
  await host.close(); host = new Host(options);
  task = await host.action('automation_get', { taskId }); assert.equal(task.messages.length, before); assert.equal(task.runs.filter(r => r.status === 'success').length, 1);
  task = await turn('沿用之前的输入和交付位置，只把任务名称改成“每小时报告”，不要保存。');
  assert.equal(task.draftSpec.title, '每小时报告'); assert.equal(task.revision, savedRevision); assert.ok(task.messages.length > before); assert.ok(JSON.stringify(task.draftSpec).includes(source));
  evidence.passed = true; evidence.taskId = taskId; evidence.runId = run.id; evidence.actual = actual; evidence.receipt = receipt; evidence.messageCount = task.messages.length; evidence.restoredConversation = true;
  await fs.writeFile(path.join(qa, 'delivered-report.txt'), actual);
  await fs.writeFile(path.join(qa, 'live-chat-results.json'), JSON.stringify(evidence, null, 2));
  console.log('PASS multi-turn clarification, persistent draft, adjustment, save, real execution, delivery, restart and continued conversation');
} catch (error) {
  evidence.error = error.message; evidence.taskId = taskId; await fs.writeFile(path.join(qa, 'live-chat-results.json'), JSON.stringify(evidence, null, 2)); console.error(error.message); process.exitCode = 1;
} finally { await host.close(); closeModelHarness(); }
