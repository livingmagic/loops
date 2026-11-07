import test from 'node:test';
import assert from 'node:assert/strict';
import { createAutomationRefresh } from '../src/automation-refresh.mjs';
const tick = () => new Promise(resolve => setImmediate(resolve));
const noop = () => {};

test('task detail and list render without waiting for the version catalog', async () => {
  let finishCatalog;
  const updates = [];
  const loader = createAutomationRefresh(async (method, params) => {
    if (method === 'automation_catalog') return new Promise(resolve => { finishCatalog = resolve; });
    return method === 'automation_get' ? { id: params.taskId } : [];
  }, { onItems: () => updates.push('items'), onCatalog: () => updates.push('catalog'), onDetail: () => updates.push('detail') });
  const pending = loader.refresh('task', { includeCatalog: true });
  await tick();
  assert.deepEqual(updates, ['items', 'detail']);
  finishCatalog([]);
  await pending;
  loader.close();
});

test('run progress skips version reads and unrelated tasks skip current detail', async () => {
  const calls = [];
  const loader = createAutomationRefresh(async method => { calls.push(method); return []; }, { onItems: noop, onCatalog: noop, onDetail: noop });
  const loop = { id: 'loop', title: 'Example', savedRevision: 1, versions: [{ revision: 1 }], runs: [] };
  await loader.refreshEvent({ event: 'state', loops: [loop] }, 'current');
  calls.length = 0;
  await loader.refreshEvent({ event: 'state', loops: [{ ...loop, runs: [{status:'running'}] }] }, 'current');
  await loader.refreshEvent({ event: 'automation_state', taskId: 'other' }, 'current');
  assert.deepEqual(calls, ['automation_list']);
  calls.length = 0;
  await loader.refreshEvent({ event: 'state', loops: [{ ...loop, savedRevision: 2 }] }, 'current');
  assert.deepEqual(calls, ['automation_catalog']);
  loader.close();
});

test('event bursts discard old lists and merge into one follow-up read', async () => {
  const reads = [], published = [];
  const loader = createAutomationRefresh(() => new Promise(resolve => reads.push(resolve)), { onItems: value => published.push(value), onCatalog: noop, onDetail: noop });
  const pending = loader.refresh(null);
  await tick();
  const events = Array.from({ length: 5 }, () => loader.refreshEvent({ event: 'automation_state' }, null));
  reads[0]('old');
  await tick();
  assert.equal(reads.length, 2);
  assert.deepEqual(published, []);
  reads[1]('new');
  await Promise.all([pending, ...events]);
  assert.deepEqual(published, ['new']);
  loader.close();
});

test('a detail snapshot cannot erase newer streamed chat text', async () => {
  let finish;
  const updates = [];
  const loader = createAutomationRefresh(async method => {
    if (method === 'automation_get') return new Promise(resolve => { finish = resolve; });
    return [];
  }, { onItems: noop, onCatalog: noop, onDetail: (value, id, chatChanged) => updates.push({id,chatChanged}) });
  const pending = loader.refresh('task');
  await tick();
  await loader.refreshEvent({event:'automation_agent_delta',taskId:'task',text:'new text'}, 'task');
  finish({chatText:'old text'});
  await pending;
  assert.deepEqual(updates, [{id:'task',chatChanged:true}]);
  loader.close();
});
