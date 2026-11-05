import { modelConnection } from './models.mjs';

// Only this host holds API credentials. Runtime messages contain public bindings.
export class WorkflowModels {
  constructor(settings) { this.settings = settings; }
  catalog() { const { models, defaultModelId } = this.settings.public(); return { models, defaultModelId }; }
  describe(modelId) {
    const entry = this.catalog().models.find(item => item.id === modelId);
    if (!entry?.configured) throw new Error('模型尚未配置或已移除，请在设置中配置模型。');
    return { modelId: entry.id, provider: entry.provider, model: entry.model,
      ...(entry.baseUrl ? { baseUrl: entry.baseUrl } : {}), name: entry.name,
      thinkingLevels: entry.thinkingLevels, thinkingLevel: entry.thinkingLevel };
  }
  check(binding) {
    const current = this.describe(binding.modelId);
    if (['provider', 'model', 'baseUrl'].some(key => current[key] !== binding[key])) throw new Error('模型资源对应的 API 配置已改变，请重新选择节点模型并保存流程。');
    if (!current.thinkingLevels.includes(binding.reasoning)) throw new Error('此模型不支持节点声明的思考强度，请重新选择。');
    return current;
  }
  async handle(method, params, signal) {
    if (method === 'catalog') return this.catalog();
    if (method === 'describe') return this.describe(params.modelId);
    if (method === 'validate') { for (const binding of params.bindings) this.check(binding); return true; }
    if (method !== 'complete') throw new Error('不支持的模型操作。');
    const { binding, prompt, system = '', maxTokens = 2048 } = params;
    this.check(binding);
    if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 100000 || typeof system !== 'string' || system.length > 20000 || !Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 32768) throw new Error('模型调用需要有效的文本和输出长度（1–32768）。');
    const config = this.settings.config(binding.modelId);
    if (!config.key) throw new Error('模型密钥尚未配置，请在设置中配置模型。');
    try {
      const { collection, model } = modelConnection(config);
      const response = await collection.completeSimple(model, { systemPrompt: system, messages: [
        { role: 'user', content: prompt, timestamp: Date.now() }
      ] }, { apiKey: config.key, reasoning: binding.reasoning, maxTokens, signal, maxRetries: 0 });
      if (response.stopReason !== 'stop') {
        if (response.stopReason === 'length') throw Object.assign(new Error(), { truncated: true });
        throw new Error(response.errorMessage || '模型没有正常完成');
      }
      const text = response.content.filter(part => part.type === 'text').map(part => part.text).join('');
      if (!text.trim()) throw new Error('empty_response');
      // A provider can echo request headers in failures or content; never relay a key.
      if (text.includes(config.key)) throw new Error('credential_in_response');
      const usage = Object.fromEntries(['input', 'output', 'totalTokens'].filter(key => Number.isFinite(response.usage?.[key])).map(key => [key, response.usage[key]]));
      return { text, usage };
    } catch (error) {
      if (signal?.aborted) throw new Error('模型调用已停止或超时。');
      if (error.truncated) throw new Error('模型输出达到长度上限，结果不完整；请调整 max_tokens。');
      const status = Number(error?.status || String(error?.message || '').match(/^(\d{3})\b/)?.[1]);
      const message = { 401: '模型身份验证失败，请检查设置中的密钥。', 402: '模型账户额度不足。', 429: '模型请求频率受限，请稍后重试。' }[status];
      throw new Error(message || '模型 API 调用失败，请检查模型配置与网络后重试。');
    }
  }
}
