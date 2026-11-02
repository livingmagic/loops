import { packager } from '@electron/packager';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { writeNpmNotices } from './third-party-notices.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = path.resolve(root, '../runtime');
const pythonInstalls = (await fs.readdir(path.join(runtime, '.python'))).filter((name) => name.startsWith('cpython-'));
if (!pythonInstalls.length) throw new Error('请先用 uv python install 3.13 --install-dir runtime/.python --no-bin 安装可打包的 Python。');
const sourcePython = path.join(runtime, '.python', pythonInstalls.sort().at(-1));
await fs.mkdir(path.join(root, '.local'), { recursive: true });
const staging = await fs.mkdtemp(path.join(root, '.local/package-'));
const target = path.join(staging, 'runtime');
try {
await fs.mkdir(target, { recursive: true });
await fs.cp(sourcePython, path.join(target, 'python'), { recursive: true,
  filter: (source) => !['site-packages', '__pycache__'].includes(path.basename(source)) });
async function relocateSymlinks(directory, sources = [sourcePython], destination = path.join(target, 'python')) {
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) await relocateSymlinks(filename, sources, destination);
    else if (entry.isSymbolicLink()) {
      const original = await fs.readlink(filename);
      const source = sources.find((source) => original === source || original.startsWith(source + path.sep));
      if (path.isAbsolute(original) && source) {
        const mapped = path.join(destination, path.relative(source, original));
        await fs.unlink(filename);
        await fs.symlink(path.relative(path.dirname(filename), mapped), filename);
      }
    }
  }
}
await relocateSymlinks(path.join(target, 'python'));
const sitePackages = path.join(target, 'python/lib/python3.13/site-packages');
// Install exactly the locked runtime; never copy the developer's virtual environment.
execFileSync(path.join(runtime, '.venv/bin/python'), ['-m', 'pip', 'install', '--index-url',
  process.env.LOOPS_PIP_INDEX_URL || 'https://pypi.org/simple', '--no-deps', '--no-compile',
  '--target', sitePackages, '-r', path.join(runtime, 'requirements.lock')], { stdio: 'inherit' });
await fs.cp(path.join(runtime, 'loops_runtime'), path.join(target, 'loops_runtime'), { recursive: true,
  filter: (source) => path.basename(source) !== '__pycache__' });
const legal = path.join(staging, 'legal');
await fs.cp(path.join(root, 'licenses'), legal, { recursive: true });
for (const filename of ['LICENSE', 'NOTICE', 'THIRD_PARTY.md']) {
  await fs.copyFile(path.join(root, '..', filename), path.join(legal, filename));
}
await writeNpmNotices(root, path.join(legal, 'npm-notices.txt'));
await fs.copyFile(path.join(root, 'node_modules/electron/dist/LICENSE'), path.join(legal, 'electron-LICENSE'));
await fs.copyFile(path.join(root, 'node_modules/electron/dist/LICENSES.chromium.html'), path.join(legal, 'LICENSES.chromium.html'));
const included = ['dist', 'electron', 'host', 'prompts', 'public', 'assets', 'node_modules', 'package.json', 'package-lock.json'];
const outputs = await packager({ dir: root, name: 'Loops', appBundleId: 'com.loops.desktop', platform: 'darwin', arch: process.arch,
  icon: path.join(root, 'assets/loops.icns'),
  out: path.resolve(root, process.env.LOOPS_PACKAGE_OUT || 'out'), overwrite: true, prune: true, asar: false, extraResource: [target, legal],
  ignore: (filename) => Boolean(filename) && !included.some((name) => filename === `/${name}` || filename.startsWith(`/${name}/`)),
});
for (const output of outputs) {
  const bundledPython = path.join(output, 'Loops.app/Contents/Resources/runtime/python');
  await relocateSymlinks(bundledPython, [sourcePython, path.join(target, 'python')], bundledPython);
  const prefix = execFileSync(path.join(bundledPython, 'bin/python3'), ['-c', 'import sys,openai_codex; print(sys.prefix)'], { encoding: 'utf8' }).trim();
  if (prefix !== bundledPython) throw new Error('Python 打包路径未正确重定位。');
}
console.log(outputs.map((directory) => path.join(directory, 'Loops.app')).join('\n'));
} finally {
  // Only remove this invocation's generated staging directory.
  await fs.rm(staging, { recursive: true, force: true });
}
