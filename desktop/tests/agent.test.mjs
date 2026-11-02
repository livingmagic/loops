import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { Engine } from '../host/engine.mjs';
import { converse } from '../host/agent.mjs';
import { readConfig, publicConfig } from '../host/config.mjs';

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
      const names = ['inspect_project', 'update_loop', 'save_loop'];
      const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(),
        content: index < names.length ? [{ type: 'toolCall', id: `call-${index}`, name: names[index], arguments: index === 1 ? { definition } : {} }] : [{ type: 'text', text: '已保存流程。' }],
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, stopReason: index < names.length ? 'toolUse' : 'stop' };
      stream.push({ type: 'done', reason: message.stopReason, message }); stream.end(message); return stream;
    };
    await converse({ engine, loopId: loop.id, text: '搭建研发流程并保存', config: { model: 'deepseek-flash', key: 'test-only-not-a-key' }, emit() {}, streamFn });
    const saved = (await engine.call('list')).find((item) => item.id === loop.id);
    assert.equal(saved.savedRevision, 1);
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

test('credential configuration never exposes the key in public settings', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-config-test-'));
  try {
    await fs.writeFile(path.join(directory, '.env.local'), 'DEEPSEEK_API_KEY="fake-secret-for-test"\nDEEPSEEK_MODEL=deepseek-flash\n');
    const config = readConfig(directory), exposed = publicConfig(config);
    assert.equal(exposed.configured, true);
    assert.equal(JSON.stringify(exposed).includes('fake-secret-for-test'), false);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('development can read project-root configuration and retains explicit local precedence', async () => {
  const project = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-project-config-'));
  const desktop = path.join(project, 'desktop');
  try {
    await fs.mkdir(desktop);
    await fs.writeFile(path.join(project, '.env.local'), 'DEEPSEEK_API_KEY=root-test-key\n');
    const fromRoot = readConfig(desktop, project);
    assert.equal(fromRoot.key, 'root-test-key');
    assert.equal(fromRoot.configPath, path.join(project, '.env.local'));
    assert.equal(publicConfig(fromRoot).configured, true);
    await fs.writeFile(path.join(desktop, '.env.local'), 'DEEPSEEK_API_KEY=desktop-test-key\n');
    assert.equal(readConfig(desktop, project).key, 'desktop-test-key');
    assert.equal(readConfig(desktop).configPath, path.join(desktop, '.env.local'));
  } finally { await fs.rm(project, { recursive: true, force: true }); }
});
