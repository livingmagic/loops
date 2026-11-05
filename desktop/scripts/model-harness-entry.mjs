import { app } from 'electron';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
app.setName('Loops');
const dataDirectory = fileURLToPath(new URL('../.local/model-harness/', import.meta.url));
fs.mkdirSync(dataDirectory, { recursive: true });
app.setPath('userData', dataDirectory);
const target = path.resolve(process.argv[2]);
process.argv = [process.argv[0], target, ...process.argv.slice(3)];
app.whenReady().then(() => import(pathToFileURL(target).href)).catch(() => {
  console.error('真实服务检查启动失败，请检查客户端模型配置。');
  app.exit(1);
});
