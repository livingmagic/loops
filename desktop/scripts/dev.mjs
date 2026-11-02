import { spawn } from 'node:child_process';
import { createServer } from 'vite';
import electron from 'electron';
process.env.LOOPS_ELECTRON_DEV = '1';
const server = await createServer();
await server.listen();
const child = spawn(electron, ['.'], { stdio: 'inherit', env: { ...process.env, LOOPS_DATA_DIR: process.env.LOOPS_DATA_DIR || new URL('../.local/desktop', import.meta.url).pathname } });
child.on('exit', async (code) => { await server.close(); process.exit(code || 0); });
process.on('SIGINT', () => child.kill());
