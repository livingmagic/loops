import path from 'node:path';

export function clientDataPaths({ isPackaged, root, userData, home, env = process.env }) {
  const isolated = !isPackaged || Boolean(env.LOOPS_DATA_DIR);
  const dataDirectory = path.resolve(env.LOOPS_DATA_DIR || (isPackaged ? userData : path.join(root, '.local/desktop')));
  const workspaceRoot = path.resolve(env.LOOPS_WORKSPACE_ROOT || (isolated ? path.join(dataDirectory, 'workspaces') : path.join(home, 'Loops')));
  return { dataDirectory, workspaceRoot };
}
