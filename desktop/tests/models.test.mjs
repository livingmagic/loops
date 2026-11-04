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
    const response = manager.save({ name: '工作模型', provider: 'compatible', model: 'my-model', baseUrl: 'https://example.com/v1', apiKey: 'test-only-secret' });
    const id = response.models.at(-1).id;
    assert.ok(!JSON.stringify(response).includes('test-only-secret'));
    assert.ok(!fs.readFileSync(manager.file, 'utf8').includes('test-only-secret'));
    assert.equal(fs.statSync(manager.file).mode & 0o777, 0o600);
    manager.default(id);
    const restarted = new ModelSettings(options);
    assert.equal(restarted.config().model, 'my-model');
    assert.equal(restarted.config().key, 'test-only-secret');
    assert.throws(() => manager.remove(id), /默认/);
    assert.throws(() => manager.save({ id, name: '换地址', provider: 'compatible', model: 'my-model', baseUrl: 'https://other.example/v1' }), /API Key/);
    assert.throws(() => manager.save({ name: 'bad', provider: 'compatible', model: 'x', baseUrl: 'https://example.com/?key=secret', apiKey: 'x' }), /地址/);
    manager.default('legacy'); manager.remove(id);
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
    const pro = settings.save({ name: 'Pro', provider: 'deepseek', model: 'deepseek-v4-pro', apiKey: 'test-key' }).models.at(-1);
    assert.deepEqual(pro.thinkingLevels, ['off', 'high', 'max']);
    assert.deepEqual(settings.public().models[0].thinkingLevels, ['off', 'low', 'high', 'max']);
    settings.thinking(pro.id, 'max');
    assert.equal(new ModelSettings(options).config(pro.id).thinkingLevel, 'max');
    assert.notEqual(settings.config('legacy').thinkingLevel, 'max');
    assert.throws(() => settings.thinking(pro.id, 'low'), /不支持/);
    assert.equal(settings.config(pro.id).thinkingLevel, 'max');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
