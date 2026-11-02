import test from 'node:test';
import assert from 'node:assert/strict';
import { workflowOutputs } from '../src/workflow-outputs.js';

test('only explicit final outputs are shown, including selected files from earlier steps', () => {
  const summary = { id: 'data', label: '汇总数据', path: 'summary.json' };
  const report = { id: 'report', label: '经营简报', path: 'report.md' };
  const definition = {
    finalOutputs: ['${steps.report.outputs.report}', '${steps.prepare.outputs.data}'],
    steps: [
      { id: 'prepare', outputs: [summary, { id: 'temporary', label: '最终结果', path: 'final-result.json' }] },
      { id: 'report', outputs: [report, { id: 'debug', label: '调试文件', path: 'debug.txt' }] },
    ],
  };
  assert.deepEqual(workflowOutputs(definition), [report, summary]);
  definition.steps[0].outputs[0] = { ...summary, label: '新名称', path: 'data.json' };
  assert.equal(workflowOutputs(definition)[1].path, 'data.json');
});

test('an explicit empty final selection keeps node outputs out of the delivery list', () => {
  assert.deepEqual(workflowOutputs({ finalOutputs: [], steps: [{ outputs: [{ id: 'report', path: 'result.txt' }] }] }), []);
});
