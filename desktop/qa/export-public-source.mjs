// Export the reviewed working tree, without private archives or Git history.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const root = fileURLToPath(new URL('../../', import.meta.url));
const artifacts = path.join(root, 'desktop/qa/artifacts/open-source');
await fs.mkdir(artifacts, { recursive: true });
const destination = await fs.mkdtemp(path.join(artifacts, 'public-source-'));
const entries = [...new Set(execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', '-z'], {
  cwd: root, encoding: 'utf8',
}).split('\0').filter(Boolean))].sort();
const manifest = [];
for (const relative of entries) {
  if (relative === 'prototype' || relative.startsWith('prototype/') || path.basename(relative) === 'loops-ai.png') continue;
  if (relative.split('/').some((part) => ['.git', '.local', 'node_modules', '.venv', '.python', 'artifacts', 'out', 'dist'].includes(part))) {
    throw new Error(`不可发布的路径已被跟踪：${relative}`);
  }
  const source = path.join(root, relative);
  const stat = await fs.lstat(source).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
  if (!stat) continue; // Working-tree deletions are part of the exported snapshot.
  if (!stat.isFile()) throw new Error(`仅允许普通源码文件：${relative}`);
  const target = path.join(destination, relative);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.copyFile(source, target);
  await fs.chmod(target, stat.mode & 0o777);
  manifest.push({ path: relative, sha256: createHash('sha256').update(await fs.readFile(target)).digest('hex') });
}
for (const name of ['.git', 'prototype', 'desktop/public/assets/loops-ai.png']) {
  if (await fs.stat(path.join(destination, name)).then(() => true, () => false)) throw new Error(`导出包含禁止项：${name}`);
}
await fs.writeFile(`${destination}.manifest.json`, JSON.stringify({ source: root, destination, files: manifest }, null, 2) + '\n');
console.log(destination);
