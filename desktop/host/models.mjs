import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { createModels, createProvider, getSupportedThinkingLevels } from '@earendil-works/pi-ai';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { anthropicProvider } from '@earendil-works/pi-ai/providers/anthropic';
import { googleProvider } from '@earendil-works/pi-ai/providers/google';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { thinkingLevel } from './config.mjs';
import { readLegacyModelConfig } from './legacy-model-config.mjs';
import { additionalModelProviders } from './model-providers.mjs';

const factories = { deepseek: deepseekProvider, ...additionalModelProviders, openai: openaiProvider, anthropic: anthropicProvider, google: googleProvider };
export function modelConnection(config) {
  const collection = createModels();
  const provider = config.provider || 'deepseek';
  if (provider === 'compatible') {
    collection.setProvider(createProvider({ id: provider, baseUrl: config.baseUrl, auth: { apiKey: { name: 'API Key', resolve: () => ({ auth: { apiKey: config.key } }) } },
      models: [{ id: config.model, name: config.model, provider, api: 'openai-completions', baseUrl: config.baseUrl, reasoning: false, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32768, maxTokens: 4096 }], api: openAICompletionsApi() }));
  } else {
    if (!factories[provider]) throw new Error('不支持的模型服务。');
    collection.setProvider(factories[provider]());
  }
  const model = collection.getModel(provider, config.model);
  if (!model) throw new Error('请选择当前服务支持的模型。');
  return { collection, model };
}

export function modelThinking(config) {
  const levels = getSupportedThinkingLevels(modelConnection(config).model);
  const configured = thinkingLevel(config);
  const fallback = levels.includes('low') ? 'low' : levels.find(level => level !== 'off') || levels[0];
  return { thinkingLevels: levels, thinkingLevel: levels.includes(configured) ? configured : fallback };
}

export class ModelSettings {
  constructor(options) { this.options = options; this.file = path.join(options.modelConfigDirectory || options.dataDirectory, 'models.json'); }
  scope(entry) { return entry.provider === 'compatible' ? `compatible:${entry.baseUrl}` : entry.provider; }
  encrypt(key) {
    try { return this.options.encryptSecret(key); } catch { throw new Error('系统安全存储不可用，密钥未保存。'); }
  }
  read() {
    let state = { models: [], defaultId: null };
    if (fs.existsSync(this.file)) {
      try { state = JSON.parse(fs.readFileSync(this.file, 'utf8')); if (!Array.isArray(state.models)) throw new Error(); }
      catch { throw new Error('模型配置无法读取，请检查本地 models.json 文件。'); }
    }
    if (state.version === 2) {
      // Earlier versions created a DeepSeek placeholder even without a key.
      let thinkingChanged = false;
      const models = state.models.filter(entry => entry.id !== 'legacy' || entry.provider !== 'deepseek' || state.credentials.deepseek).map(entry => {
        // Older builds persisted off as the default without tracking user choice.
        if (entry.thinkingLevel !== 'off' || entry.thinkingLevelExplicit) return entry;
        const level = modelThinking({ ...entry, thinkingLevel: undefined }).thinkingLevel;
        if (level === entry.thinkingLevel) return entry;
        thinkingChanged = true;
        return { ...entry, thinkingLevel: level };
      });
      const defaultId = this.configuredDefault({ ...state, models });
      if (thinkingChanged || models.length !== state.models.length || defaultId !== state.defaultId) {
        state = { ...state, models, defaultId };
        this.write(state);
      }
      return state;
    }
    // Write the complete upgrade atomically. If secure storage fails, preserve
    // the old configuration and retry next time, never mark migration complete.
    const legacy = readLegacyModelConfig(this.options.configDirectory || this.options.dataDirectory, this.options.fallbackConfigDirectory);
    const migrated = { version: 2, defaultId: state.defaultId, models: [], credentials: {} };
    const initial = { id: 'legacy', name: 'DeepSeek', provider: 'deepseek', model: legacy.model, thinkingLevel: legacy.thinkingLevel };
    if (legacy.key) migrated.credentials.deepseek = this.encrypt(legacy.key);
    if (legacy.key) migrated.models.push(initial);
    // Existing explicit model credentials take precedence over old environment
    // configuration; the default model's credential wins within a service.
    for (const entry of [...state.models].sort((a, b) => Number(a.id === state.defaultId) - Number(b.id === state.defaultId))) {
      const { secret, ...metadata } = entry;
      migrated.models.push(metadata);
      if (secret) migrated.credentials[this.scope(entry)] = secret;
    }
    migrated.defaultId = this.configuredDefault(migrated);
    this.write(migrated);
    return migrated;
  }
  configuredDefault(state) {
    const configured = state.models.filter(entry => state.credentials[this.scope(entry)]);
    return (configured.find(entry => entry.id === state.defaultId) || configured[0])?.id ?? null;
  }
  config(id) {
    const state = this.read(); id ||= state.defaultId;
    if (!id) return { key: '' };
    const entry = state.models.find(item => item.id === id);
    if (!entry) throw new Error('模型已移除，请重新选择。');
    const secret = state.credentials[this.scope(entry)];
    let key = '';
    if (secret) {
      try { key = this.options.decryptSecret(secret); } catch { throw new Error('无法读取模型密钥，请在设置中重新保存。'); }
    }
    return { ...entry, ...modelThinking(entry), key };
  }
  public() {
    const state = this.read();
    const models = state.models.map(entry => ({ ...entry, ...modelThinking(entry), configured: Boolean(state.credentials[this.scope(entry)]) }));
    const services = [...new Map(state.models.map(entry => [this.scope(entry), { provider: entry.provider, baseUrl: entry.baseUrl, configured: Boolean(state.credentials[this.scope(entry)]) }])).values()];
    const providers = Object.entries(factories).map(([id, factory]) => {
      const provider = factory();
      return { id, name: provider.name, description: provider.description, models: provider.getModels().map(model => ({ id: model.id, name: model.name, thinkingLevels: getSupportedThinkingLevels(model) })) };
    });
    return { models, services, defaultModelId: state.defaultId, providers };
  }
  write(state) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temporary = `${this.file}.${randomUUID()}.tmp`;
    try { fs.writeFileSync(temporary, JSON.stringify(state), { mode: 0o600 }); fs.renameSync(temporary, this.file); }
    finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  }
  save(params) {
    const state = this.read();
    const existing = params.id && state.models.find(item => item.id === params.id);
    if (params.id && !existing) throw new Error('找不到待编辑的模型。');
    const entry = { id: existing?.id || randomUUID(), name: String(params.name || '').trim(), provider: params.provider, model: String(params.model || '').trim(), thinkingLevel: existing?.thinkingLevel,
      thinkingLevelExplicit: existing?.thinkingLevelExplicit || params.thinkingLevel !== undefined };
    if (!entry.name || entry.name.length > 80 || !entry.model || entry.model.length > 200) throw new Error('请填写模型名称和模型 ID。');
    if (entry.provider === 'compatible') {
      let url; try { url = new URL(params.baseUrl); } catch { throw new Error('请输入有效的 API 地址。'); }
      if (url.username || url.password || url.search || url.hash || !(url.protocol === 'https:' || url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) throw new Error('API 地址需要 HTTPS，本机服务可以使用 HTTP；请勿在地址中包含密钥。');
      entry.baseUrl = url.href.replace(/\/$/, '');
    }
    const supported = modelThinking(entry);
    if (params.thinkingLevel !== undefined && !supported.thinkingLevels.includes(params.thinkingLevel)) throw new Error('此模型不支持所选思考强度。');
    entry.thinkingLevel = params.thinkingLevel ?? supported.thinkingLevel;
    const key = typeof params.apiKey === 'string' ? params.apiKey.trim() : '';
    const scope = this.scope(entry);
    if (!key && !state.credentials[scope]) throw new Error('请填写此服务的 API Key。');
    if (key) state.credentials[scope] = this.encrypt(key);
    state.models = [...state.models.filter(item => item.id !== entry.id), entry];
    state.defaultId ||= entry.id;
    this.write(state);
    return this.public();
  }
  thinking(id, level) {
    const state = this.read();
    const entry = state.models.find(item => item.id === id);
    if (!entry) throw new Error('模型已移除，请重新选择。');
    if (!modelThinking(entry).thinkingLevels.includes(level)) throw new Error('此模型不支持所选思考强度。');
    entry.thinkingLevel = level;
    entry.thinkingLevelExplicit = true;
    this.write(state);
    return this.public();
  }
  default(id) { if (typeof id !== 'string' || !id) throw new Error('请选择默认模型。'); const state = this.read(); if (!this.config(id).key) throw new Error('请先配置此模型。'); state.defaultId = id; this.write(state); return this.public(); }
  remove(id) {
    const state = this.read();
    if (state.defaultId === id) throw new Error('请先选择其他默认模型。');
    state.models = state.models.filter(item => item.id !== id);
    const used = new Set(state.models.map(entry => this.scope(entry)));
    for (const scope of Object.keys(state.credentials)) if (!used.has(scope)) delete state.credentials[scope];
    this.write(state); return this.public();
  }
}
