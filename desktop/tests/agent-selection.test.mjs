import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Host } from '../host/service.mjs';
import { builderTools } from '../host/agent.mjs';

test('first chat persists execution client independently of builder model; step override can be cleared', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-agent-selection-'));
  const host = new Host({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces'), configDirectory: directory,
    encryptSecret: value => value, decryptSecret: value => value });
  try {
    const created = await host.action('chat', { text: '整理资料', agentId: 'mimo' });
    const loop = (await host.action('list')).find(item => item.id === created.loopId);
    assert.equal(loop.config.defaultAgentId, 'mimo');
    assert.equal(created.reason, 'missing_credentials');
    const definition = { schemaVersion: 2, title: '分析', goal: '生成报告', completion: '文件存在', inputs: [], scripts: {}, finalOutputs: ['${steps.analyze.outputs.report}'],
      steps: [{ id: 'analyze', label: '分析', instructions: '分析资料', inputs: {}, execution: { tool: 'agent', mode: 'read-only', session: 'analysis', responseOutput: 'report' }, outputs: [{ id: 'report', label: '报告', path: 'report.txt' }], checks: [] }] };
    await host.engine.call('definition', { loopId: loop.id, definition });
    await host.action('save', { loopId: loop.id });
    let changed = await host.action('agent_selection', { loopId: loop.id, stepId: 'analyze', agentId: 'codex' });
    assert.equal(changed.definition.steps[0].execution.agentId, 'codex');
    assert.notEqual(changed.revision, changed.savedRevision);
    changed = await host.action('agent_selection', { loopId: loop.id, stepId: 'analyze', agentId: null });
    assert.equal(changed.definition.steps[0].execution.agentId, undefined);
    const tools = builderTools(host.engine, loop.id);
    assert.ok(tools.some(tool => tool.name === 'set_execution_agent'));
    const selectModel = tools.find(tool => tool.name === 'set_execution_model');
    assert.ok(selectModel);
    await selectModel.execute('model-choice', { agentId: 'mimo', model: 'xiaomi/chosen' });
    changed = (await host.action('list')).find(item => item.id === loop.id);
    assert.equal(changed.config.agentModels.mimo, 'xiaomi/chosen');
    changed = await host.action('agent_model_selection', { loopId: loop.id, stepId: 'analyze', model: 'xiaomi/step' });
    assert.equal(changed.definition.steps[0].execution.model, 'xiaomi/step');
    const schema = JSON.stringify(tools.find(tool => tool.name === 'update_loop').parameters);
    assert.match(schema, /agentId/);
    assert.doesNotMatch(schema, /"const":"codex"/);
    await assert.rejects(host.action('new', { agentId: 'unregistered' }), /请选择/);
  } finally { await host.close(); await fs.rm(directory, { recursive: true, force: true }); }
});
