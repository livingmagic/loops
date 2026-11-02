import { app, BrowserWindow, ipcMain, dialog, shell } from 'electron';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Host } from '../host/service.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (process.env.LOOPS_DATA_DIR) {
  const dataDirectory = path.resolve(process.env.LOOPS_DATA_DIR);
  fs.mkdirSync(dataDirectory, { recursive: true });
  app.setPath('userData', dataDirectory);
}
if (!app.requestSingleInstanceLock()) { app.quit(); process.exit(0); }
let host, window, quitting = false;
function trusted(event) {
  if (event.senderFrame !== window?.webContents.mainFrame) throw new Error('无效的窗口请求。');
}
function createWindow() {
  window = new BrowserWindow({ width: 1487, height: 1058, minWidth: 1000, minHeight: 680, title: 'Loops', backgroundColor: '#ffffff',
    titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 18, y: 18 },
    webPreferences: { preload: path.join(root, 'electron/preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event) => event.preventDefault());
  if (process.env.LOOPS_ELECTRON_DEV) window.loadURL('http://127.0.0.1:4174');
  else window.loadFile(path.join(root, 'dist/index.html'));
}
app.whenReady().then(() => {
app.setName('Loops');
app.dock?.setIcon(path.join(root, 'public/assets/loops-icon.png'));
const dataDirectory = process.env.LOOPS_DATA_DIR || app.getPath('userData');
try {
  host = new Host({ dataDirectory, configDirectory: process.env.LOOPS_CONFIG_DIR || (app.isPackaged ? dataDirectory : root),
    fallbackConfigDirectory: app.isPackaged ? undefined : path.resolve(root, '..'),
    runtimeDirectory: app.isPackaged ? path.join(process.resourcesPath, 'runtime') : path.resolve(root, '../runtime') });
} catch (error) { dialog.showErrorBox('Loops 启动失败', error.message); app.quit(); }
if (host) {
  ipcMain.handle('loops:request', (event, method, params) => { trusted(event); return host.action(method, params); });
  ipcMain.handle('loops:choose', async (event, kind) => {
    trusted(event);
    if (!['directory', 'file'].includes(kind)) throw new Error('无效的文件选择。');
    const result = await dialog.showOpenDialog(window, { properties: kind === 'directory' ? ['openDirectory'] : ['openFile'] });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle('loops:reveal', async (event, loopId) => {
    trusted(event);
    const loop = (await host.action('list')).find((item) => item.id === loopId);
    if (!loop) throw new Error('找不到工作目录。');
    return shell.openPath(loop.workdir);
  });
  ipcMain.handle('loops:url', (event, url) => { trusted(event); if (!/^https:\/\/github\.com\//.test(url)) throw new Error('只能打开交付记录中的 GitHub 地址。'); return shell.openExternal(url); });
  host.on('event', (event) => { if (!window?.isDestroyed()) window?.webContents.send('loops:event', event); });
  createWindow();
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow(); });
  app.on('before-quit', (event) => {
    if (quitting) return;
    event.preventDefault(); quitting = true;
    host.close().finally(() => app.quit());
  });
}
}).catch((error) => { dialog.showErrorBox('Loops 启动失败', '客户端初始化未完成，请检查本地执行环境。'); console.error(error.name); app.quit(); });
