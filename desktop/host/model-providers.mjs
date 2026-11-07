import { createProvider } from '@earendil-works/pi-ai';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { zaiCodingCnProvider } from '@earendil-works/pi-ai/providers/zai-coding-cn';
import { moonshotaiCnProvider } from '@earendil-works/pi-ai/providers/moonshotai-cn';
import { xiaomiProvider } from '@earendil-works/pi-ai/providers/xiaomi';
import { qwenTokenPlanCnProvider } from '@earendil-works/pi-ai/providers/qwen-token-plan-cn';

// Reuse Pi's model capabilities and wire adapters, with standard API endpoints.
// Coding/Token Plan endpoints have separate credentials and billing.
function preset(source, { id = source.id, name, baseUrl = source.baseUrl, description, include = () => true }) {
  const provider = createProvider({ id, name, baseUrl,
    auth: { apiKey: { name: 'API Key', resolve: ({ credential }) => credential?.key ? { auth: { apiKey: credential.key } } : undefined } },
    models: source.getModels().filter(include).map(model => ({ ...model, provider: id, baseUrl })),
    api: openAICompletionsApi(),
  });
  return { ...provider, description };
}

export const additionalModelProviders = {
  glm: () => preset(zaiCodingCnProvider(), {
    id: 'glm', name: 'GLM（智谱）', baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    description: '使用智谱开放平台的模型 API Key，按模型 API 额度计费。',
    include: model => ['glm-5.3', 'glm-5.3-flash'].includes(model.id),
  }),
  'moonshotai-cn': () => preset(moonshotaiCnProvider(), {
    name: 'Kimi（月之暗面）',
    description: '使用 Kimi 开放平台 API Key，与 Kimi Code 订阅额度独立。',
  }),
  xiaomi: () => preset(xiaomiProvider(), {
    name: 'MiMo（小米）',
    description: '使用小米 MiMo 开放平台的模型 API Key。',
  }),
  qwen: () => preset(qwenTokenPlanCnProvider(), {
    id: 'qwen', name: 'Qwen（通义千问）', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    description: '使用阿里云百炼中国内地（北京）的模型 API Key，与 Token Plan 额度独立。',
    include: model => model.id.startsWith('qwen'),
  }),
};
