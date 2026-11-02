import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Host } from '../host/service.mjs';

test('fresh startup creates no Loop or workspace; only a nonempty submitted requirement creates one', async () => {
  const directory = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'loops-create-test-')));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory });
  try {
    assert.deepEqual(await host.action('list'), []);
    assert.equal(await fs.stat(path.join(directory, 'workspaces')).then(() => true, () => false), false);
    await assert.rejects(host.action('chat', { text: '  ' }), /有效的需求/);
    assert.deepEqual(await host.action('list'), []);
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
    assert.deepEqual(await host.action('list'), []);
    assert.equal((await host.action('trash'))[0].id, loop.id);
    const restored = await host.action('restore', { loopId: loop.id });
    assert.deepEqual(restored, loop);
    assert.equal(await fs.readFile(file, 'utf8'), 'preserved');
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
