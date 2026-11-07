import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Type } from '@earendil-works/pi-ai';
import { ModelSettings, modelConnection } from '../host/models.mjs';
import { WorkflowModels } from '../host/workflow-models.mjs';

const cases = [
  { provider: 'glm', model: 'glm-5.3', level: 'low', endpoint: 'https://open.bigmodel.cn/api/paas/v4/chat/completions', effort: 'low', thinking: { type: 'enabled', clear_thinking: false } },
  { provider: 'moonshotai-cn', model: 'kimi-k3', level: 'low', endpoint: 'https://api.moonshot.cn/v1/chat/completions', effort: 'low' },
  { provider: 'xiaomi', model: 'mimo-v2.6-pro', level: 'high', endpoint: 'https://api.xiaomimimo.com/v1/chat/completions', thinking: { type: 'enabled' } },
  { provider: 'qwen', model: 'qwen3.8-max', level: 'low', endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', effort: 'low', enableThinking: true },
];
function fixture() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-provider-test-'));
  const secrets = new Map();
  const options = { dataDirectory: directory, configDirectory: directory,
    encryptSecret: key => { const cipher = `cipher-${secrets.size}`; secrets.set(cipher, key); return cipher; },
    decryptSecret: cipher => secrets.get(cipher),
  };
  return { directory, options, settings: new ModelSettings(options) };
}
function response(deltas) {
  return new Response(deltas.map(delta => `data: ${JSON.stringify({ id: 'test', choices: [{ index: 0, ...delta }] })}\n\n`).join('') + 'data: [DONE]\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
}

test('four named providers save, share keys only within a provider, and restore supported thinking defaults', () => {
  const { directory, options, settings } = fixture();
  try {
    for (const entry of cases) {
      const catalog = settings.public().providers.find(provider => provider.id === entry.provider);
      assert.ok(catalog?.models.some(model => model.id === entry.model));
      assert.ok(catalog.description);
      assert.throws(() => settings.save({ ...entry, name: entry.provider }), /API Key/);
      const added = settings.save({ ...entry, name: entry.provider, apiKey: `${entry.provider}-private-test-key` }).models.at(-1);
      assert.equal(added.thinkingLevel, entry.level);
      assert.ok(added.thinkingLevels.includes(entry.level));
      const shared = settings.save({ ...entry, name: 'Same provider' }).models.at(-1);
      const restarted = new ModelSettings(options);
      assert.equal(restarted.config(shared.id).key, `${entry.provider}-private-test-key`);
      assert.equal(restarted.config(added.id).thinkingLevel, entry.level);
      assert.ok(!JSON.stringify(restarted.public()).includes('private-test-key'));
      assert.ok(!fs.readFileSync(settings.file, 'utf8').includes('private-test-key'));
    }
    assert.ok(settings.public().providers.find(p => p.id === 'qwen').models.every(m => m.id.startsWith('qwen')));
    assert.ok(!settings.public().providers.find(p => p.id === 'glm').models.find(m => m.id === 'glm-5.3').thinkingLevels.includes('off'));
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

for (const entry of cases) {
  test(`${entry.provider} serializes endpoint, auth, reasoning and tool-call replay through Pi (fetch double)`, async t => {
    const { directory, settings } = fixture();
    const requests = [];
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      requests.push({ url: String(url), headers: new Headers(options.headers), body: JSON.parse(options.body) });
      if (requests.length === 1) return response([
        { delta: { role: 'assistant', reasoning_content: 'Inspect the input first.' }, finish_reason: null },
        { delta: { tool_calls: [{ index: 0, id: 'call_inspect', type: 'function', function: { name: 'inspect', arguments: '{}' } }] }, finish_reason: null },
        { delta: {}, finish_reason: 'tool_calls' },
      ]);
      return response([{ delta: { role: 'assistant', content: 'done' }, finish_reason: null }, { delta: {}, finish_reason: 'stop' }]);
    });
    try {
      const added = settings.save({ ...entry, name: entry.provider, apiKey: 'private-test-key' }).models.at(-1);
      const config = settings.config(added.id);
      const { collection, model } = modelConnection(config);
      const context = { messages: [{ role: 'user', content: 'Inspect the input', timestamp: Date.now() }], tools: [{ name: 'inspect', description: 'Read input', parameters: Type.Object({}) }] };
      const options = { apiKey: config.key, reasoning: config.thinkingLevel, maxTokens: 2048, maxRetries: 0 };
      const reply = await collection.completeSimple(model, context, options);
      assert.equal(reply.stopReason, 'toolUse', reply.errorMessage);
      assert.equal(reply.content.find(part => part.type === 'toolCall')?.name, 'inspect');
      context.messages.push(reply, { role: 'toolResult', toolCallId: 'call_inspect', toolName: 'inspect', content: [{ type: 'text', text: 'input' }], isError: false, timestamp: Date.now() });
      assert.equal((await collection.completeSimple(model, context, options)).stopReason, 'stop');
      for (const request of requests) {
        assert.equal(request.url, entry.endpoint);
        assert.equal(request.headers.get('authorization'), 'Bearer private-test-key');
        assert.equal(request.body.model, entry.model);
        assert.equal(request.body.reasoning_effort, entry.effort);
        assert.equal(request.body.enable_thinking, entry.enableThinking);
        assert.deepEqual(request.body.thinking, entry.thinking);
        assert.equal(request.body.tools[0].function.name, 'inspect');
      }
      const replayed = requests[1].body.messages.find(message => message.role === 'assistant');
      assert.equal(replayed.reasoning_content, 'Inspect the input first.');
      assert.equal(replayed.tool_calls[0].id, 'call_inspect');
      const host = new WorkflowModels(settings);
      assert.equal((await host.handle('complete', { binding: { ...host.describe(added.id), reasoning: entry.level }, prompt: 'Check workflow model access' })).text, 'done');
    } finally { fs.rmSync(directory, { recursive: true, force: true }); }
  });
}
