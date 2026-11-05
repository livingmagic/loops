import path from 'node:path';

export function clientDataPaths({ isPackaged, root, appData, env = process.env }) {
  const dataDirectory = path.resolve(env.LOOPS_DATA_DIR || (isPackaged ? path.join(appData, 'Loops') : path.join(root, '.local/desktop')));
  const workspaceRoot = path.resolve(env.LOOPS_WORKSPACE_ROOT || path.join(dataDirectory, 'workspaces'));
  return { dataDirectory, workspaceRoot };
}
