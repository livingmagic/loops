import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const asarPath = require.resolve('@electron/asar', { paths: [path.dirname(require.resolve('@electron/packager'))] });
const asar = await import(asarPath);

test('patched packaging dependencies preserve files, hidden files, symlinks and unpack patterns', async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'loops-asar-'));
  try {
    const source = path.join(temp, 'source');
    await fs.mkdir(path.join(source, 'nested'), { recursive: true });
    await fs.writeFile(path.join(source, '.hidden'), 'hidden');
    await fs.writeFile(path.join(source, 'nested/data.txt'), 'data');
    await fs.writeFile(path.join(source, 'native.node'), 'binary-fixture');
    await fs.symlink('nested/data.txt', path.join(source, 'link'));
    const archive = path.join(temp, 'app.asar');
    await asar.createPackageWithOptions(source, archive, { unpack: '*.node' });
    assert.equal(asar.extractFile(archive, '.hidden').toString(), 'hidden');
    assert.equal(asar.extractFile(archive, 'nested/data.txt').toString(), 'data');
    assert.equal(asar.extractFile(archive, 'link').toString(), 'data');
    assert.equal(await fs.readFile(`${archive}.unpacked/native.node`, 'utf8'), 'binary-fixture');
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});
