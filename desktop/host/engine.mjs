import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import { EventEmitter } from 'node:events';

export class Engine extends EventEmitter {
  constructor({ dataDirectory, runtimeDirectory = fileURLToPath(new URL('../../runtime/', import.meta.url)), python, workspaceRoot, modelHost } = {}) {
    super();
    this.pending = new Map();
    this.hostRequests = new Map();
    this.modelHost = modelHost;
    this.sequence = 0;
    const bundled = path.join(runtimeDirectory, 'python/bin/python3');
    const interpreter = python || process.env.LOOPS_PYTHON || (fs.existsSync(bundled) ? bundled : path.join(runtimeDirectory, '.venv/bin/python'));
    if (!fs.existsSync(interpreter)) throw new Error('尚未安装本地执行环境，请运行 runtime/setup.sh。');
    const env = { ...process.env, PYTHONPATH: runtimeDirectory, PYTHONUNBUFFERED: '1', LOOPS_DATA_DIR: dataDirectory };
    env.LOOPS_WORKSPACE_ROOT = workspaceRoot || process.env.LOOPS_WORKSPACE_ROOT || path.join(dataDirectory, 'workspaces');
    for (const name of Object.keys(env)) if (name.startsWith('DEEPSEEK_')) delete env[name];
    this.process = spawn(interpreter, ['-m', 'loops_runtime.bridge'], { cwd: runtimeDirectory, env, stdio: ['pipe', 'pipe', 'pipe'] });
    this.process.stderr.on('data', () => {}); // SDK diagnostics stay out of the product and credentials never enter UI logs.
    createInterface({ input: this.process.stdout }).on('line', (line) => {
      let payload;
      try { payload = JSON.parse(line); } catch { return; }
      if (payload.hostRequest) { void this.hostRequest(payload); return; }
      if (payload.hostCancel) { this.hostRequests.get(payload.hostCancel)?.abort(); return; }
      if (payload.event) { this.emit('event', payload); return; }
      const pending = this.pending.get(payload.id);
      if (!pending) return;
      clearTimeout(pending.timer); this.pending.delete(payload.id);
      if (payload.error) pending.reject(new Error(payload.error)); else pending.resolve(payload.result);
    });
    this.process.on('error', (error) => this.fail(error));
    this.process.on('exit', (code) => { this.fail(new Error(`本地执行服务已退出（${code}）。请重新打开客户端。`)); this.emit('event', { event: 'engine_error', message: '本地执行服务已退出，请重新打开客户端。' }); });
  }
  async hostRequest({ hostRequest, method, params }) {
    const controller = new AbortController();
    this.hostRequests.set(hostRequest, controller);
    const timer = setTimeout(() => controller.abort(), 180000);
    let reply;
    try {
      if (!this.modelHost) throw new Error('模型 API 服务未连接。');
      const cancelled = new Promise((_, reject) => controller.signal.addEventListener('abort', () => reject(new Error('模型调用已停止或超时。')), { once: true }));
      reply = { result: await Promise.race([this.modelHost.handle(method, params, controller.signal), cancelled]) };
    } catch (error) { reply = { error: error.message }; }
    finally { clearTimeout(timer); this.hostRequests.delete(hostRequest); }
    if (this.process.stdin.writable && !controller.signal.aborted) this.process.stdin.write(JSON.stringify({ hostResponse: hostRequest, ...reply }) + '\n');
    else if (this.process.stdin.writable) this.process.stdin.write(JSON.stringify({ hostResponse: hostRequest, error: '模型调用已停止或超时。' }) + '\n');
  }
  fail(error) { for (const controller of this.hostRequests.values()) controller.abort(); for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); } this.pending.clear(); }
  call(method, params = {}) {
    return new Promise((resolve, reject) => {
      if (!this.process.stdin.writable) { reject(new Error('本地执行服务未连接。')); return; }
      const id = ++this.sequence;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('本地执行服务响应超时。')); }, 30000);
      this.pending.set(id, { resolve, reject, timer });
      this.process.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    });
  }
  async close() { try { await this.call('shutdown'); } finally { this.process.stdin.end(); setTimeout(() => this.process.kill(), 3000).unref(); } }
}
