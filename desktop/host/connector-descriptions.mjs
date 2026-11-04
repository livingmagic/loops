import { modelConnection } from './models.mjs';
import fs from 'node:fs';

const systemPrompt = fs.readFileSync(new URL('../prompts/connector-description.md', import.meta.url), 'utf8');

export function descriptionInput(context) {
  // Bound metadata, never include connector URLs, accounts, credentials or calls.
  const tools = []; let size = 0;
  for (const tool of context.tools) {
    const next = JSON.stringify(tool).length;
    if (size + next > 40000) break;
    tools.push(tool); size += next;
  }
  return { tools, totalTools: context.tools.length, truncated: tools.length < context.tools.length };
}

export async function generateDescription(context, config, signal) {
  const { collection, model } = modelConnection(config);
  const response = await collection.completeSimple(model, { systemPrompt, messages: [
    { role: 'user', content: JSON.stringify(descriptionInput(context)), timestamp: Date.now() }
  ] }, { apiKey: config.key, signal, maxTokens: 256 });
  if (response.stopReason === 'error' || response.stopReason === 'aborted') throw new Error('连接器简介生成未完成。');
  const raw = response.content.filter(part => part.type === 'text').map(part => part.text).join('');
  const value = JSON.parse(raw).description;
  if (typeof value !== 'string' || Array.from(value.trim()).length > 60 || /[\r\n]/.test(value)) throw new Error('连接器简介格式无效。');
  return value.trim();
}

export class ConnectorDescriptions {
  constructor(engine, getConfig, generate = generateDescription) {
    this.engine = engine; this.getConfig = getConfig; this.generate = generate;
    this.attempts = new Map(); this.controller = new AbortController();
    this.job = null; this.pending = false; this.closed = false;
  }
  schedule() {
    if (this.closed) return;
    this.pending = true;
    if (!this.job) this.job = this.run().finally(() => { this.job = null; });
  }
  async run() {
    while (this.pending && !this.closed) {
      this.pending = false;
      try {
        const config = this.getConfig();
        if (!config.key) continue;
        const contexts = await this.engine.call('connector_description_contexts');
        for (const context of contexts) {
          if (this.closed) break;
          const attempt = this.attempts.get(context.connectorId);
          if (attempt?.hash === context.sourceHash && attempt.after > Date.now()) continue;
          // Failure remains nonblocking; retry on a later discovery/list refresh.
          this.attempts.set(context.connectorId, { hash: context.sourceHash, after: Date.now() + 300000 });
          try {
            const signal = AbortSignal.any([this.controller.signal, AbortSignal.timeout(25000)]);
            const text = await this.generate(context, config, signal);
            if (text) await this.engine.call('connector_set_description', {
              connectorId: context.connectorId, revision: context.revision, sourceHash: context.sourceHash, text
            });
          } catch { /* Keep the existing description; no credential-bearing errors reach the UI. */ }
        }
      } catch { /* A disconnected engine will report its own lifecycle event. */ }
    }
  }
  async close() {
    this.closed = true; this.controller.abort();
    await this.job;
  }
}
