import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { ModelSettings, modelConnection } from '../host/models.mjs';

test('model settings persist defaults, keep secrets out of public state and reject credential reuse across endpoints', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-models-'));
  const secrets = new Map();
  const options = { dataDirectory: root, configDirectory: root, encryptSecret: key => { const id = `encrypted-${secrets.size}`; secrets.set(id, key); return id; }, decryptSecret: id => secrets.get(id) };
  try {
    const manager = new ModelSettings(options);
    assert.deepEqual(manager.public().models, []);
    assert.equal(manager.public().defaultModelId, null);
    const response = manager.save({ name: '工作模型', provider: 'compatible', model: 'my-model', baseUrl: 'https://example.com/v1', apiKey: 'test-only-secret' });
    const id = response.models.at(-1).id;
    assert.ok(!JSON.stringify(response).includes('test-only-secret'));
    assert.ok(!fs.readFileSync(manager.file, 'utf8').includes('test-only-secret'));
    assert.equal(fs.statSync(manager.file).mode & 0o777, 0o600);
    assert.equal(response.defaultModelId, id);
    const restarted = new ModelSettings(options);
    assert.equal(restarted.config().model, 'my-model');
    assert.equal(restarted.config().key, 'test-only-secret');
    assert.throws(() => manager.remove(id), /默认/);
    assert.throws(() => manager.save({ id, name: '换地址', provider: 'compatible', model: 'my-model', baseUrl: 'https://other.example/v1' }), /API Key/);
    assert.throws(() => manager.save({ name: 'bad', provider: 'compatible', model: 'x', baseUrl: 'https://example.com/?key=secret', apiKey: 'x' }), /地址/);
    const other = manager.save({ name: '其他模型', provider: 'compatible', model: 'other-model', baseUrl: 'https://example.com/v1' });
    assert.equal(other.defaultModelId, id);
    manager.default(other.models.at(-1).id); manager.remove(id);
    assert.throws(() => manager.config(id), /移除/);
    assert.throws(() => new ModelSettings({ ...options, encryptSecret: undefined }).save({ name: 'x', provider: 'compatible', model: 'x', baseUrl: 'https://example.com/v1', apiKey: 'x' }), /安全存储/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('all bundled providers resolve selected catalog models', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-catalog-'));
  const manager = new ModelSettings({ dataDirectory: root, configDirectory: root });
  for (const provider of manager.public().providers) {
    const { model } = modelConnection({ provider: provider.id, model: provider.models[0].id });
    assert.equal(model.provider, provider.id);
    assert.equal(model.id, provider.models[0].id);
  }
  fs.rmSync(root, { recursive: true, force: true });
});

test('compatible service sends selected model and credential through a real HTTP transport (local test service)', async () => {
  let received;
  const server = http.createServer(async (req, res) => {
    let body = ''; for await (const part of req) body += part;
    received = { url: req.url, authorization: req.headers.authorization, body: JSON.parse(body) };
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end('data: '+JSON.stringify({ id: 'test', choices: [{ index: 0, delta: { role: 'assistant', content: 'ok' }, finish_reason: null }] })+'\n\ndata: '+JSON.stringify({ id: 'test', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })+'\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const { collection, model } = modelConnection({ provider: 'compatible', model: 'selected-model', baseUrl: `http://127.0.0.1:${server.address().port}/v1` });
    const reply = await collection.completeSimple(model, { messages: [{ role: 'user', content: 'test', timestamp: Date.now() }] }, { apiKey: 'test-key' });
    assert.equal(reply.stopReason, 'stop');
    assert.equal(received.url, '/v1/chat/completions');
    assert.equal(received.body.model, 'selected-model');
    assert.equal(received.authorization, 'Bearer test-key');
    assert.ok(reply.content.some(part => part.text === 'ok'));
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('legacy config migrates once, models share provider keys, and later env edits cannot override settings', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-migration-'));
  const secrets = new Map();
  const options = { dataDirectory: root, configDirectory: root,
    encryptSecret: key => { const id = `cipher-${secrets.size}`; secrets.set(id, key); return id; }, decryptSecret: id => secrets.get(id) };
  const env = path.join(root, '.env.local');
  try {
    fs.writeFileSync(env, 'DEEPSEEK_API_KEY=old-test-key\nDEEPSEEK_MODEL=deepseek-flash\nDEEPSEEK_THINKING_LEVEL=high\n');
    const manager = new ModelSettings(options);
    const migrated = manager.public();
    assert.equal(migrated.models[0].configured, true);
    assert.equal(manager.config().key, 'old-test-key');
    assert.equal(manager.config().thinkingLevel, 'high');
    const added = manager.save({ provider: 'deepseek', name: 'Pro', model: 'deepseek-v4-pro' }).models.at(-1);
    assert.equal(manager.config(added.id).key, 'old-test-key');
    manager.save({ id: added.id, provider: 'deepseek', name: 'Pro', model: 'deepseek-v4-pro', apiKey: 'new-test-key' });
    assert.equal(manager.config('legacy').key, 'new-test-key');
    fs.writeFileSync(env, 'DEEPSEEK_API_KEY=must-not-be-used\nDEEPSEEK_MODEL=invalid\n');
    const reloaded = new ModelSettings(options);
    assert.equal(reloaded.config('legacy').key, 'new-test-key');
    assert.equal(reloaded.config('legacy').model, 'deepseek-flash');
    assert.ok(!JSON.stringify(reloaded.public()).includes('test-key'));
    assert.ok(!fs.readFileSync(manager.file, 'utf8').includes('test-key'));
    assert.throws(() => manager.save({ provider: 'openai', name: 'Other', model: manager.public().providers.find(p => p.id === 'openai').models[0].id }), /API Key/);
    manager.default(added.id); manager.remove('legacy');
    assert.equal(manager.config().key, 'new-test-key');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('migration failure preserves old data and retries without recording completion', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-migration-fail-'));
  try {
    fs.writeFileSync(path.join(root, '.env.local'), 'DEEPSEEK_API_KEY=test-only\n');
    const manager = new ModelSettings({ dataDirectory: root, configDirectory: root, encryptSecret() { throw new Error('locked'); } });
    assert.throws(() => manager.public(), /安全存储/);
    assert.equal(fs.existsSync(manager.file), false);
    assert.equal(fs.readFileSync(path.join(root, '.env.local'), 'utf8'), 'DEEPSEEK_API_KEY=test-only\n');
    manager.options.encryptSecret = () => 'encrypted';
    assert.equal(manager.public().models[0].configured, true);
    assert.equal(JSON.parse(fs.readFileSync(manager.file)).version, 2);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('existing encrypted model records migrate without exposing or losing their selected credential', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-model-upgrade-'));
  try {
    fs.writeFileSync(path.join(root, 'models.json'), JSON.stringify({ defaultId: 'chosen', models: [{ id: 'chosen', provider: 'deepseek', name: 'Chosen', model: 'deepseek-v4-pro', secret: 'old-cipher' }] }));
    const manager = new ModelSettings({ dataDirectory: root, configDirectory: root, decryptSecret: value => value === 'old-cipher' ? 'test-key' : undefined });
    assert.equal(manager.config().key, 'test-key');
    assert.equal(manager.public().defaultModelId, 'chosen');
    assert.ok(!JSON.stringify(manager.public()).includes('old-cipher'));
    assert.ok(!JSON.parse(fs.readFileSync(manager.file)).models.some(model => 'secret' in model));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('thinking controls expose supported levels and persist independently per model', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-thinking-'));
  try {
    const options = { dataDirectory: root, configDirectory: root, encryptSecret: () => 'cipher', decryptSecret: () => 'test-key' };
    const settings = new ModelSettings(options);
    const flash = settings.save({ name: 'Flash', provider: 'deepseek', model: 'deepseek-flash', apiKey: 'test-key' }).models.at(-1);
    const pro = settings.save({ name: 'Pro', provider: 'deepseek', model: 'deepseek-v4-pro', apiKey: 'test-key' }).models.at(-1);
    assert.deepEqual(pro.thinkingLevels, ['off', 'high', 'max']);
    assert.deepEqual(settings.public().models[0].thinkingLevels, ['off', 'low', 'high', 'max']);
    assert.equal(flash.thinkingLevel, 'low');
    assert.equal(pro.thinkingLevel, 'high');
    assert.equal(new ModelSettings(options).config(flash.id).thinkingLevel, 'low');
    settings.thinking(flash.id, 'off');
    assert.equal(new ModelSettings(options).config(flash.id).thinkingLevel, 'off');
    settings.save({ id: flash.id, name: 'Flash renamed', provider: 'deepseek', model: 'deepseek-flash' });
    assert.equal(new ModelSettings(options).config(flash.id).thinkingLevel, 'off');
    settings.thinking(pro.id, 'max');
    assert.equal(new ModelSettings(options).config(pro.id).thinkingLevel, 'max');
    assert.notEqual(settings.config(flash.id).thinkingLevel, 'max');
    assert.throws(() => settings.thinking(pro.id, 'low'), /不支持/);
    assert.equal(settings.config(pro.id).thinkingLevel, 'max');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('old default off upgrades while explicit choices and non-reasoning models survive restart', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-thinking-upgrade-'));
  try {
    const options = { dataDirectory: root, decryptSecret: () => 'test-key' };
    const settings = new ModelSettings(options);
    settings.write({ version: 2, defaultId: 'old', credentials: { deepseek: 'cipher' }, models: [
      { id: 'old', name: 'Old', provider: 'deepseek', model: 'deepseek-flash', thinkingLevel: 'off' },
      { id: 'chosen', name: 'Chosen', provider: 'deepseek', model: 'deepseek-flash', thinkingLevel: 'off', thinkingLevelExplicit: true },
      { id: 'high', name: 'High', provider: 'deepseek', model: 'deepseek-flash', thinkingLevel: 'high' },
      { id: 'plain', name: 'Plain', provider: 'compatible', model: 'custom', baseUrl: 'https://example.com/v1', thinkingLevel: 'off' },
    ] });
    assert.equal(settings.config().thinkingLevel, 'low');
    const restarted = new ModelSettings(options);
    assert.equal(restarted.read().models[0].thinkingLevel, 'low');
    assert.equal(restarted.config('chosen').thinkingLevel, 'off');
    assert.equal(restarted.config('high').thinkingLevel, 'high');
    assert.equal(restarted.config('plain').thinkingLevel, 'off');
    restarted.save({ name: 'Explicit off', provider: 'deepseek', model: 'deepseek-flash', thinkingLevel: 'off' });
    assert.equal(new ModelSettings(options).public().models.at(-1).thinkingLevel, 'off');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('an old unconfigured placeholder does not override an existing configured model', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-placeholder-'));
  try {
    const manager = new ModelSettings({ dataDirectory: root });
    manager.write({ version: 2, defaultId: 'legacy', credentials: { 'compatible:https://example.com/v1': 'cipher' }, models: [
      { id: 'legacy', name: 'DeepSeek', provider: 'deepseek', model: 'deepseek-flash' },
      { id: 'chosen', name: 'Chosen', provider: 'compatible', model: 'custom', baseUrl: 'https://example.com/v1' },
    ] });
    const state = manager.public();
    assert.equal(state.defaultModelId, 'chosen');
    assert.deepEqual(state.models.map(item => item.id), ['chosen']);
    assert.equal(manager.read().defaultId, 'chosen');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('legacy entries edited to another provider survive placeholder cleanup', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-edited-legacy-'));
  try {
    const manager = new ModelSettings({ dataDirectory: root, decryptSecret: () => 'test-key' });
    manager.write({ version: 2, defaultId: 'legacy', credentials: { 'compatible:https://example.com/v1': 'cipher' }, models: [
      { id: 'legacy', name: 'Edited model', provider: 'compatible', model: 'custom', baseUrl: 'https://example.com/v1' },
    ] });
    assert.equal(manager.public().defaultModelId, 'legacy');
    assert.equal(manager.config().model, 'custom');
    assert.equal(manager.config().key, 'test-key');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
