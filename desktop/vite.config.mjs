import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Host } from './host/service.mjs';

const directory = path.dirname(fileURLToPath(import.meta.url));
export default defineConfig({
  base: './', plugins: [react(), {
    name: 'loops-production-content-policy', apply: 'build',
    transformIndexHtml(html) { return html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; connect-src \'self\'; object-src \'none\'; base-uri \'none\'">'); },
  }, {
    name: 'loops-local-development-bridge',
    configureServer(server) {
      if (process.env.LOOPS_ELECTRON_DEV) return;
      const host = new Host({ dataDirectory: process.env.LOOPS_DATA_DIR || path.join(directory, '.local/browser'), configDirectory: directory, fallbackConfigDirectory: path.resolve(directory, '..') });
      const clients = new Set();
      host.on('event', (event) => { for (const response of clients) response.write(`data: ${JSON.stringify(event)}\n\n`); });
      server.middlewares.use('/api', async (request, response) => {
        response.setHeader('Cache-Control', 'no-store');
        if (request.headers.origin && request.headers.origin !== 'http://127.0.0.1:4174') { response.writeHead(403).end(); return; }
        if (request.method === 'GET' && request.url === '/events') {
          response.writeHead(200, { 'Content-Type': 'text/event-stream', Connection: 'keep-alive' });
          response.write(': connected\n\n'); clients.add(response);
          request.on('close', () => clients.delete(response)); return;
        }
        if (request.method !== 'POST' || request.headers['x-loops-client'] !== 'desktop-development') { response.writeHead(403).end(); return; }
        try {
          let body = '';
          for await (const chunk of request) { body += chunk; if (body.length > 100000) throw new Error('请求过大。'); }
          const { method, params } = JSON.parse(body);
          const result = await host.action(method, params);
          response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ result }));
        } catch (error) { response.writeHead(400, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ error: error.message })); }
      });
      server.httpServer?.on('close', () => { for (const client of clients) client.end(); void host.close(); });
    },
  }],
  server: { host: '127.0.0.1', port: 4174, strictPort: true, watch: { usePolling: true } },
});
