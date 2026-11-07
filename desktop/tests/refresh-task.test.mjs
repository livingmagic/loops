import test from 'node:test';
import assert from 'node:assert/strict';
import { createRefreshTask } from '../src/refresh-task.mjs';
const tick = () => new Promise(resolve => setImmediate(resolve));

test('overlapping invalidations coalesce and only the final snapshot is published', async () => {
  const reads = [], published = [];
  const task = createRefreshTask(() => new Promise(resolve => reads.push(resolve)), value => published.push(value));
  const first = task.refresh();
  assert.equal(task.refresh(), first);
  await tick();
  task.refresh({ invalidate: true });
  task.refresh({ invalidate: true });
  reads[0]('stale');
  await tick();
  assert.deepEqual(published, []);
  assert.equal(reads.length, 2);
  reads[1]('latest');
  assert.equal(await first, 'latest');
  assert.deepEqual(published, ['latest']);
});

test('switching resources starts immediately and discards a late response', async () => {
  const reads = new Map(), published = [];
  const task = createRefreshTask(key => new Promise(resolve => reads.set(key, resolve)), value => published.push(value));
  const old = task.refresh({ key: 'a' });
  await tick();
  const next = task.refresh({ key: 'b' });
  await tick();
  reads.get('b')('new task');
  await next;
  reads.get('a')('old task');
  await old;
  assert.deepEqual(published, ['new task']);
});

test('an initial failure can retry, while live recovery supersedes an outstanding read', async () => {
  const published = [];
  let fail = true, finish;
  const task = createRefreshTask(() => {
    if (fail) throw Error('offline');
    return new Promise(resolve => { finish = resolve; });
  }, value => published.push(value));
  await assert.rejects(task.refresh(), /offline/);
  fail = false;
  const retry = task.refresh();
  await tick();
  task.accept('live recovery');
  finish('old initial list');
  await retry;
  assert.deepEqual(published, ['live recovery']);
});

test('cancelling suppresses late results and errors', async () => {
  let reject;
  const task = createRefreshTask(() => new Promise((_, fail) => { reject = fail; }), () => assert.fail('cancelled update'));
  const pending = task.refresh();
  await tick();
  task.cancel();
  reject(Error('late failure'));
  await pending;
});
