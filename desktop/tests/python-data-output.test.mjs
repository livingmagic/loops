import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { validateToolCall } from '@earendil-works/pi-ai';
import { Engine } from '../host/engine.mjs';
import { builderTools } from '../host/agent.mjs';

// Hand-authored contracts test the AI tool schema and real runtime, not model generation.
test('builder tools accept Python data outputs and preserve types without intermediate files', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-python-data-tools-'));
  const engine = new Engine({ dataDirectory: directory, workspaceRoot: path.join(directory, 'workspaces') });
  try {
    const loop = await engine.call('new');
    const tools = builderTools(engine, loop.id);
    const inspect = (await tools.find(tool => tool.name === 'inspect_project').execute('inspect', {})).details;
    assert.match(inspect.capabilities.mcp, /source=result/);
    const cases = [
      { expression: "{'count': 3, 'valid': True, 'missing': None}", dataType: 'object', expected: { count: 3, valid: true, missing: null } },
      { expression: "[{'name': '甲'}, {'name': '乙'}]", dataType: 'array', expected: [{ name: '甲' }, { name: '乙' }] },
      { expression: "'普通文本结果'", dataType: 'string', expected: '普通文本结果' },
    ];
    for (const { expression, expected, dataType } of cases) {
      const definition = { schemaVersion: 2, title: '通用数据处理', goal: '返回结构化结果', completion: '保持原始数据类型',
        inputs: [], scripts: { 'scripts/process.py': `print('日志不会进入结果')\nresult = ${expression}` },
        steps: [{ id: 'process', label: '处理数据', instructions: '返回处理结果', inputs: {},
          execution: { tool: 'python', script: 'scripts/process.py' },
          outputs: [{ id: 'data', label: '处理结果', kind: 'value', source: 'result', dataType }], checks: [], onFailure: { action: 'stop', maxAttempts: 0 } }],
        finalOutputs: ['${steps.process.outputs.data}'] };
      const args = validateToolCall(tools, { type: 'toolCall', id: 'definition', name: 'update_loop', arguments: { definition } });
      await tools.find(tool => tool.name === 'update_loop').execute('definition', args);
      await tools.find(tool => tool.name === 'start_trial').execute('start', {});
      const result = (await tools.find(tool => tool.name === 'get_run_result').execute('result', {})).details;
      assert.equal(result.status, 'success', result.reason);
      assert.deepEqual(result.stepResults[0].outputs.data, expected);
      assert.deepEqual(result.stepResults[0].outputFiles, {});
      assert.deepEqual(result.artifacts[0].value, expected);
      assert.equal(result.artifacts[0].path, undefined);
      assert.deepEqual(await fs.readdir(path.join(result.directory, 'workspace')), []);
    }
  } finally { await engine.close(); await fs.rm(directory, { recursive: true, force: true }); }
});
