// Run real-service harnesses with the same macOS secure storage as the client.
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
let application;
export async function modelHarnessOptions() {
  const electron = await import('electron');
  if (!process.versions.electron) {
    const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(electron.default, [fileURLToPath(new URL('./model-harness-entry.mjs', import.meta.url)), ...process.argv.slice(1)], { stdio: 'inherit', env });
    child.on('error', () => process.exit(1));
    child.on('exit', code => process.exit(code ?? 1));
    await new Promise(() => {});
  }
  const { app, safeStorage } = electron.default;
  application = app;
  app.setName('Loops');
  await app.whenReady();
  if (!process.env.LOOPS_MODEL_CONFIG_DIR) {
    console.error('请用 LOOPS_MODEL_CONFIG_DIR 指向已在客户端设置模型的数据目录。');
    app.exit(1);
    throw new Error('尚未选择模型配置目录。');
  }
  return {
    modelConfigDirectory: path.resolve(process.env.LOOPS_MODEL_CONFIG_DIR),
    encryptSecret: value => { if (!safeStorage.isEncryptionAvailable()) throw new Error(); return safeStorage.encryptString(value).toString('base64'); },
    decryptSecret: value => safeStorage.decryptString(Buffer.from(value, 'base64')),
  };
}
export function closeModelHarness() { application?.exit(process.exitCode || 0); }
