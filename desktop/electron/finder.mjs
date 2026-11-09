import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { isLocalPath } from '../host/local-path.mjs';

// Files are revealed, never launched with their associated application.
export async function openInFinder(value, shell) {
  if (!isLocalPath(value)) throw new Error('请选择有效的本机路径。');
  const filename = value === '~' ? os.homedir() : value.startsWith('~/') ? path.join(os.homedir(), value.slice(2)) : value;
  const info = await fs.stat(filename).catch(error => {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') throw new Error('文件或目录尚不存在。');
    throw new Error('无法访问此路径，请检查权限。');
  });
  if (info.isDirectory()) {
    const error = await shell.openPath(filename);
    if (error) throw new Error('无法在 Finder 中打开此目录。');
  } else {
    shell.showItemInFolder(filename);
  }
}
