import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { builderTools } from '../host/agent.mjs';
import { Host } from '../host/service.mjs';

test('fresh startup installs the greeting once; only a nonempty requirement creates a user Loop', async () => {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'loops-create-test-')));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory });
  try {
    const [greeting] = await host.action('list');
    assert.equal(greeting.title, '打个招呼');
    assert.equal(greeting.builtin, 'greeting');
    assert.equal(greeting.savedRevision, 1);
    assert.deepEqual(greeting.runs, []);
    assert.equal(greeting.workdir, path.join(directory, 'workspaces', greeting.id));
    await assert.rejects(host.action('chat', { text: '  ' }), /有效的需求/);
    assert.deepEqual(await host.action('list'), [greeting]);
    const result = await host.action('chat', { text: '把销售明细生成经营简报' });
    const [loop] = await host.action('list');
    assert.equal(loop.id, result.loopId);
    assert.equal(loop.messages[0].text, '把销售明细生成经营简报');
    assert.equal(loop.messages[0].role, 'user');
    assert.equal((await fs.stat(loop.workdir)).isDirectory(), true);
    assert.equal(loop.workdir, path.join(directory, 'workspaces', loop.id));
    await host.action('rename', { loopId: loop.id, title: '每周经营简报' });
    assert.equal((await host.action('list'))[0].title, '每周经营简报');
    await assert.rejects(host.action('rename', { loopId: loop.id, title: '  ' }), /名称/);
    const selected = path.join(directory, 'selected-project');
    const next = await host.action('chat', { text: '创建另一个流程', workspace: selected });
    assert.equal((await host.action('list')).find((item) => item.id === next.loopId).workdir, path.join(directory, 'workspaces', next.loopId));
    const direct = await host.action('new', { workspace: selected });
    assert.equal(direct.workdir, path.join(directory, 'workspaces', direct.id));
    assert.equal(await fs.stat(selected).then(() => true, () => false), false);
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});

test('host deletes and restores a Loop through the real service without losing its workspace', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-trash-test-'));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory });
  try {
    const loop = await host.action('new');
    const file = path.join(loop.workdir, 'keep.txt');
    await fs.writeFile(file, 'preserved');
    await host.action('delete', { loopId: loop.id });
    assert.equal((await host.action('list')).some(item => item.id === loop.id), false);
    assert.equal((await host.action('trash'))[0].id, loop.id);
    const restored = await host.action('restore', { loopId: loop.id });
    assert.deepEqual(restored, loop);
    assert.equal(await fs.readFile(file, 'utf8'), 'preserved');
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});

test('version APIs cross the host boundary and block changes during a conversation', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-versions-host-'));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory });
  try {
    const loop = await host.action('new');
    const definition = { schemaVersion: 2, title: '报告', goal: '第一版', completion: '文件存在', inputs: [], scripts: { 'scripts/report.py': "import json,os\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['file']).write_text('report')" }, finalOutputs: ['${steps.report.outputs.file}'],
      steps: [{ id: 'report', label: '生成报告', instructions: '生成报告', inputs: {}, execution: { tool: 'python', script: 'scripts/report.py' }, outputs: [{ id: 'file', label: '报告', path: 'report.txt' }], checks: [] }] };
    async function trialAndSave() {
      await host.action('start', { loopId: loop.id });
      const result = await builderTools(host.engine, loop.id).find(tool => tool.name === 'get_run_result').execute('trial', {});
      assert.equal(result.details.status, 'success', result.details.reason);
      await host.action('save', { loopId: loop.id });
    }
    await host.engine.call('definition', { loopId: loop.id, definition });
    await trialAndSave();
    await host.engine.call('definition', { loopId: loop.id, definition: { ...definition, goal: '第二版' } });
    await trialAndSave();
    const original = await host.action('version', { loopId: loop.id, revision: 1 });
    assert.equal(original.definition.goal, '第一版');
    const task = await host.action('automation_save', { spec: { title: '版本测试', source: { lifecycle: 'once', script: 'def sample(ctx):\n    ctx.emit("one", {})\ndef run(ctx):\n    sample(ctx)\n', config: {} }, nodes: [{ id: 'report', loopId: loop.id, revision: 1, bindings: {} }], outputs: [] } });
    host.busy.set(loop.id, new AbortController());
    await assert.rejects(host.action('restore_version', { loopId: loop.id, revision: 1 }), /回复结束/);
    await assert.rejects(host.action('save', { loopId: loop.id }), /回复结束/);
    host.busy.delete(loop.id);
    host.busy.set(`automation:${task.id}`, new AbortController());
    await assert.rejects(host.action('automation_set_loop_version', { taskId: task.id, nodeId: 'report', revision: 2 }), /回复结束/);
    host.busy.delete(`automation:${task.id}`);
    const draft = await host.action('automation_set_loop_version', { taskId: task.id, nodeId: 'report', revision: 2 });
    assert.equal(draft.spec.nodes[0].revision, 1);
    assert.equal(draft.draftSpec.nodes[0].revision, 2);
    const restored = await host.action('restore_version', { loopId: loop.id, revision: 1, expectedRevision: 2 });
    assert.equal(restored.revision, 3);
    assert.equal(restored.savedRevision, 2);
    assert.equal(restored.definition.goal, '第一版');
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});

test('chat reserves its Loop before the first engine response, and deletion blocks concurrent chat', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-trash-chat-test-'));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory });
  try {
    const loop = await host.action('new');
    const chat = host.action('chat', { loopId: loop.id, text: 'test only, no credentials configured' });
    await assert.rejects(host.action('delete', { loopId: loop.id }), /Loops AI 正在处理/);
    assert.equal((await chat).reason, 'missing_credentials');
    const deletion = host.action('delete', { loopId: loop.id });
    await assert.rejects(host.action('chat', { loopId: loop.id, text: 'should not be sent' }), /正在删除/);
    await deletion;
    await assert.rejects(host.action('chat', { loopId: loop.id, text: 'should not be stored' }), /回收站/);
    assert.equal(host.busy.has(loop.id), false);
    const restored = await host.action('restore', { loopId: loop.id });
    assert.equal(restored.messages.filter((item) => item.role === 'user').length, 1);
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});
