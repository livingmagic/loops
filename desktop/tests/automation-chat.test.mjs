import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { builderTools } from '../host/agent.mjs';
import { Engine } from '../host/engine.mjs';
import { Host } from '../host/service.mjs';
import { converseAutomation } from '../host/automation-agent.mjs';
import { sourceTemplate } from '../src/automation-model.js';

function messageStream(model, content) {
  const stream = createAssistantMessageEventStream();
  const tools = content.some(p => p.type === 'toolCall');
  const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(), content,
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, stopReason: tools ? 'toolUse' : 'stop' };
  stream.push({ type: 'done', reason: message.stopReason, message }); stream.end(message); return stream;
}
async function loopFixture(engine) {
  const loop = await engine.call('new');
  const definition = { schemaVersion: 2, title: '文本处理', goal: '读取文本', completion: '文件存在', inputs: [{ id: 'source', label: '原文', kind: 'file', required: true }], finalOutputs: ['${steps.convert.outputs.report}'],
    scripts: { 'scripts/process.py': "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT']); Path(c['outputs']['report']).write_text(Path(c['inputs']['source']).read_text().upper())" },
    steps: [{ id: 'convert', label: '转换', instructions: '读取文本', inputs: { source: '${inputs.source}' }, execution: { tool: 'python', script: 'scripts/process.py' }, outputs: [{ id: 'report', label: '报告', path: 'report.txt' }], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }] };
  const source = path.join(loop.workdir, 'trial.txt');
  await fs.writeFile(source, 'trial input');
  await engine.call('definition', { loopId: loop.id, definition });
  await engine.call('start', { loopId: loop.id, inputs: { source } });
  const result = await builderTools(engine, loop.id).find(tool => tool.name === 'get_run_result').execute('trial', {});
  assert.equal(result.details.status, 'success', result.details.reason);
  await engine.call('save', { loopId: loop.id });
  return loop;
}
test('multi-turn Pi conversation retains clarification, validates draft and saves only on explicit later turn (provider double)', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-auto-chat-'));
  const engine = new Engine({ dataDirectory: root, workspaceRoot: path.join(root, 'workspaces') });
  try {
    const loop = await loopFixture(engine);
    const task = await engine.call('automation_new_conversation', { seedLoopId: loop.id });
    const config = { model: 'deepseek-flash', key: 'test-only-not-a-key' };
    const first = '每天处理数据';
    await engine.call('automation_message', { taskId: task.id, role: 'user', text: first });
    await converseAutomation({ engine, taskId: task.id, text: first, config, emit() {}, streamFn: model => messageStream(model, [{ type: 'text', text: '数据从哪里读取？' }]) });
    let current = await engine.call('automation_get', { taskId: task.id });
    assert.equal(current.revision, 0); assert.equal(current.draftSpec, null); assert.equal(current.messages.length, 2);
    const cancelled = new AbortController(); cancelled.abort();
    let called = false;
    await assert.rejects(converseAutomation({ engine, taskId: task.id, text: '已取消的请求', config, emit() {}, signal: cancelled.signal, streamFn: () => { called = true; throw new Error('must not call provider'); } }), /停止/);
    assert.equal(called, false);
    assert.ok((await engine.call('automation_session_get', { taskId: task.id })).some(m => m.role === 'user' && m.content === '已取消的请求'));
    const spec = { title: '每天处理', source: sourceTemplate('once'), nodes: [{ id: 'process', loopId: loop.id, revision: 1, bindings: { source: 'input.source' } }], outputs: [], policy: { queueLimit: 100, scriptTimeout: 180, sourceRestarts: 3 } };
    spec.source.config.path = path.join(root, 'source.txt');
    let phase = 0;
    await engine.call('automation_message', { taskId: task.id, role: 'user', text: '用这个文件，先不要保存' });
    await converseAutomation({ engine, taskId: task.id, text: '用这个文件，先不要保存', config, emit() {}, streamFn: (model, context) => {
      assert.ok(context.messages.some(m => m.role === 'assistant' && m.content.some(p => p.text === '数据从哪里读取？')));
      return messageStream(model, phase++ === 0 ? [{ type: 'toolCall', id: 'draft', name: 'update_automation', arguments: { spec } }] : [{ type: 'text', text: '已搭建草稿。' }]);
    } });
    current = await engine.call('automation_get', { taskId: task.id });
    assert.equal(current.revision, 0); assert.equal(current.draftSpec.title, '每天处理'); assert.equal(current.enabled, false); assert.equal(current.runs.length, 0);
    await engine.call('automation_message', { taskId: task.id, role: 'user', text: '保存这个流程' });
    phase = 0;
    await converseAutomation({ engine, taskId: task.id, text: '保存这个流程', config, emit() {}, streamFn: model => messageStream(model, phase++ === 0 ? [{ type: 'toolCall', id: 'save', name: 'save_automation', arguments: {} }] : [{ type: 'text', text: '已保存，尚未启用。' }]) });
    current = await engine.call('automation_get', { taskId: task.id });
    assert.equal(current.revision, 1); assert.equal(current.draftSpec, null); assert.equal(current.messages.length, 6); assert.equal(current.enabled, false);
    assert.ok((await engine.call('automation_session_get', { taskId: task.id })).some(m => m.role === 'toolResult'));
  } finally { await engine.close(); await fs.rm(root, { recursive: true, force: true }); }
});

test('chat preserves unconfigured requests, reserves a busy task and refuses empty creations', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-auto-chat-host-'));
  const previous = process.env.DEEPSEEK_API_KEY; delete process.env.DEEPSEEK_API_KEY;
  const host = new Host({ dataDirectory: root, configDirectory: root, workspaceRoot: path.join(root, 'workspaces') });
  try {
    await assert.rejects(host.action('automation_chat', { text: '   ' }), /请输入/);
    assert.equal((await host.action('automation_list')).length, 0);
    const result = await host.action('automation_chat', { text: '每天整理文件' });
    assert.equal(result.reason, 'missing_credentials');
    const current = await host.action('automation_get', { taskId: result.taskId });
    assert.equal(current.messages[0].text, '每天整理文件'); assert.equal(current.messages[1].role, 'error'); assert.equal(current.chatBusy, false);
    const original = host.engine.call.bind(host.engine);
    let release, entered;
    const ready = new Promise(resolve => { entered = resolve; });
    host.engine.call = async (method, params) => { if (method === 'automation_message' && params.role === 'user') { entered(); await new Promise(resolve => { release = resolve; }); } return original(method, params); };
    const pending = host.action('automation_chat', { taskId: result.taskId, text: '继续整理' });
    await ready;
    await assert.rejects(host.action('automation_chat', { taskId: result.taskId, text: '重复发送' }), /上一条消息/);
    await assert.rejects(host.action('automation_delete', { taskId: result.taskId }), /正在处理/);
    release(); await pending;
    assert.equal((await host.action('automation_list')).length, 1);
  } finally { await host.close(); if (previous !== undefined) process.env.DEEPSEEK_API_KEY = previous; await fs.rm(root, { recursive: true, force: true }); }
});
