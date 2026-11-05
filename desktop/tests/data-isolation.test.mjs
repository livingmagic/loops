import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { clientDataPaths } from '../host/data-paths.mjs';
import { Engine } from '../host/engine.mjs';

test('source and acceptance remain isolated; installed app keeps all managed data under Loops', () => {
  const common = { root: '/checkout/desktop', appData: '/home/user/Application Support', env: {} };
  assert.deepEqual(clientDataPaths({ ...common, isPackaged: false }), {
    dataDirectory: '/checkout/desktop/.local/desktop', workspaceRoot: '/checkout/desktop/.local/desktop/workspaces',
  });
  assert.deepEqual(clientDataPaths({ ...common, isPackaged: true }), {
    dataDirectory: '/home/user/Application Support/Loops', workspaceRoot: '/home/user/Application Support/Loops/workspaces',
  });
  assert.deepEqual(clientDataPaths({ ...common, isPackaged: true, env: { LOOPS_DATA_DIR: '/qa/session' } }), {
    dataDirectory: '/qa/session', workspaceRoot: '/qa/session/workspaces',
  });
  assert.equal(clientDataPaths({ ...common, isPackaged: false, env: { LOOPS_WORKSPACE_ROOT: '/qa/custom' } }).workspaceRoot, '/qa/custom');
});

test('a direct runtime host with an isolated database also creates its Loop files there', async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'loops-isolation-')));
  const previous = process.env.LOOPS_WORKSPACE_ROOT;
  delete process.env.LOOPS_WORKSPACE_ROOT;
  const engine = new Engine({ dataDirectory: root });
  if (previous !== undefined) process.env.LOOPS_WORKSPACE_ROOT = previous;
  try {
    const loop = await engine.call('new');
    assert.equal(loop.workdir, path.join(root, 'workspaces', loop.id));
    assert.equal((await fs.stat(loop.workdir)).isDirectory(), true);
  } finally { await engine.close(); await fs.rm(root, { recursive: true, force: true }); }
});

test('packaged preview launcher overrides inherited production paths even in a checkout with spaces', { skip: process.platform !== 'darwin' }, async () => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'loops preview ')));
  const script = path.join(root, 'desktop/scripts/open-latest.command');
  const binary = path.join(root, 'desktop/out/Loops-darwin-arm64/Loops.app/Contents/MacOS/Loops');
  try {
    await fs.mkdir(path.dirname(script), { recursive: true });
    await fs.mkdir(path.dirname(binary), { recursive: true });
    await fs.copyFile(new URL('../scripts/open-latest.command', import.meta.url), script);
    await fs.writeFile(binary, '#!/bin/sh\nprintf "%s\\n" "$LOOPS_DATA_DIR" "$LOOPS_WORKSPACE_ROOT" "$LOOPS_CONFIG_DIR" "${LOOPS_ELECTRON_DEV-unset}"\n', { mode: 0o755 });
    const result = execFileSync('/bin/zsh', [script], { encoding: 'utf8', env: { ...process.env, LOOPS_DATA_DIR: '/production', LOOPS_WORKSPACE_ROOT: '/production/files', LOOPS_ELECTRON_DEV: '1' } });
    const actualRoot = await fs.realpath(root);
    assert.deepEqual(result.trim().split('\n'), [path.join(actualRoot, 'qa/desktop-client'), path.join(actualRoot, 'qa/desktop-client/workspaces'), actualRoot, 'unset']);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
