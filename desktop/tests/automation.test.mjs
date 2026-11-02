import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { sourceTemplate, finalOptions, newSpec } from '../src/automation-model.js';
const catalog = [{ id: 'a', title: '甲', versions: [{ revision: 2, inputs: [{ id: 'data', label: '资料', kind: 'file' }], outputs: [{ key: 'make.file', label: '结果' }] }] }, { id: 'b', title: '乙', versions: [{ revision: 1, inputs: [{ id: 'text', label: '文本', kind: 'text' }], outputs: [] }] }];
test('input templates accept real runtime entrypoints and valid Python for varied input kinds', () => {
  for (const type of ['once', 'interval', 'watch', 'webhook']) for (const kind of ['file', 'text']) {
    const source = sourceTemplate(type, 'payload', kind);
    const result = spawnSync('../runtime/.venv/bin/python', ['-c', 'import ast,sys; tree=ast.parse(sys.stdin.read()); assert {"run","sample"}.issubset({n.name for n in tree.body if isinstance(n,ast.FunctionDef)})'], { input: source.script, encoding: 'utf8' });
    assert.equal(result.status, 0, `${type}/${kind}: ${result.stderr}`);
  }
});
test('new automation uses the saved version and actual declared field identity', () => {
  const spec = newSpec(catalog, 'a');
  assert.equal(spec.nodes[0].revision, 2);
  assert.equal(spec.nodes[0].bindings.data, 'input.data');
  assert.ok(spec.source.script.includes('"data"'));
  assert.equal(finalOptions(catalog, spec.nodes)[0].value, `${spec.nodes[0].id}.make.file`);
  const other = newSpec(catalog, 'b');
  assert.ok('text' in other.source.config);
  assert.ok(other.source.script.includes('ctx.text'));
});
