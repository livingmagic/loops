import { app } from 'electron';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
app.setName('loops-desktop');
const target = path.resolve(process.argv[2]);
process.argv = [process.argv[0], target, ...process.argv.slice(3)];
app.whenReady().then(() => import(pathToFileURL(target).href)).catch(() => {
  console.error('真实服务检查启动失败，请检查客户端模型配置。');
  app.exit(1);
});
