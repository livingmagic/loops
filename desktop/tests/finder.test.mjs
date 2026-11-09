import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { openInFinder } from '../electron/finder.mjs';

test('Finder opens directories and reveals files without executing them', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-finder-'));
  const calls = [];
  const shell = { openPath: async value => { calls.push(['open', value]); return ''; }, showItemInFolder: value => calls.push(['reveal', value]) };
  try {
    const file = path.join(root, '中文 file.command');
    await fs.writeFile(file, 'exit 1');
    await openInFinder(root, shell);
    await openInFinder(file, shell);
    await openInFinder('~', shell);
    assert.deepEqual(calls, [['open', root], ['reveal', file], ['open', os.homedir()]]);
    for (const invalid of ['https://example.com', 'relative/file', '～/file', '/tmp/{runId}/file', '/tmp/${workspace}', null, '/tmp/a\0b']) {
      await assert.rejects(openInFinder(invalid, shell), /有效的本机路径/);
    }
    await assert.rejects(openInFinder(path.join(root, 'missing'), shell), /尚不存在/);
    assert.equal(calls.length, 3);
    await assert.rejects(openInFinder(root, { ...shell, openPath: async () => 'OS error' }), /无法在 Finder/);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
