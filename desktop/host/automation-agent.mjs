import fs from 'node:fs';
import { Agent } from '@earendil-works/pi-agent-core';
import { Type, createModels } from '@earendil-works/pi-ai';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';

const systemPrompt = fs.readFileSync(new URL('../prompts/automation.md', import.meta.url), 'utf8');
const script = Type.Object({ label: Type.Optional(Type.String({ description: '给用户看的输入行为说明，例如每个工作日采集订单；不是执行规则' })), script: Type.String(), config: Type.Record(Type.String(), Type.Unknown()) });
export const automationSchema = Type.Object({
  title: Type.String(),
  source: Type.Object({ ...script.properties, lifecycle: Type.Union([Type.Literal('once'), Type.Literal('service')]) }),
  nodes: Type.Array(Type.Object({ id: Type.String(), loopId: Type.String(), revision: Type.Integer(), bindings: Type.Record(Type.String(), Type.String()) }), { minItems: 1 }),
  outputs: Type.Array(Type.Object({ ...script.properties, id: Type.String(), title: Type.String(), bindings: Type.Record(Type.String(), Type.String()) })),
  policy: Type.Object({ queueLimit: Type.Integer({ minimum: 1, maximum: 1000, default: 100 }), scriptTimeout: Type.Integer({ minimum: 1, maximum: 1200, default: 180 }), sourceRestarts: Type.Integer({ minimum: 0, maximum: 5, default: 3 }) }),
});
export function automationProviderStream(collection) {
  // Retry only unaccepted provider HTTP requests; never replay a turn or its tools.
  return (model, context, options) => collection.streamSimple(model, context, { ...options, maxRetries: 2, maxRetryDelayMs: 5000 });
}
function provider(config, streamFn) {
  const collection = createModels(); collection.setProvider(deepseekProvider());
  const model = collection.getModel('deepseek', config.model);
  if (!model) throw new Error('本机 DeepSeek 模型配置不可用。');
  return { model, streamFn: streamFn || automationProviderStream(collection), getApiKey: () => config.key };
}
const result = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }], details: value });
function summary(task) {
  return { taskId: task.id, title: task.title, revision: task.revision, hasDraft: Boolean(task.draftSpec), enabled: task.enabled,
    spec: task.draftSpec || task.spec, seedLoopId: task.seedLoopId, sourceStatus: task.sourceStatus, sourceError: task.sourceError,
    lastRun: task.runs.filter(r => r.status !== 'sample').at(-1) || null, lastSample: task.runs.filter(r => r.status === 'sample').at(-1) || null };
}
export async function converseAutomation({ engine, taskId, text, config, emit, signal, editedSpec, streamFn }) {
  const [catalog, task, messages] = await Promise.all([
    engine.call('automation_catalog'), engine.call('automation_get', { taskId }), engine.call('automation_session_get', { taskId }),
  ]);
  const get = () => engine.call('automation_get', { taskId });
  const call = (method, values = {}) => engine.call(method, { taskId, ...values });
  const tool = (name, description, parameters, execute) => ({ name, label: description, description, parameters, executionMode: 'sequential', execute: async (_id, args) => result(await execute(args)) });
  const tools = [
    tool('inspect_automation', '读取自动化草稿、保存版本、状态和最近真实运行', Type.Object({}), async () => summary(await get())),
    tool('update_automation', '校验并更新完整草稿，保留对话，不保存版本、不执行', Type.Object({ spec: automationSchema }), async ({ spec }) => {
      const value = await call('automation_update_draft', { spec });
      emit({ event: 'automation_draft_updated', taskId });
      return { taskId, title: value.title, draftRevision: value.draftRevision, savedRevision: value.revision, status: 'draft' };
    }),
    tool('save_automation', '保存当前草稿为固定版本，不启用或投递', Type.Object({}), async () => {
      const current = await get();
      return summary(current.draftSpec ? await call('automation_save') : current);
    }),
    tool('sample_automation', '采集真实输入样本，不执行 Loop 或投递；需要已保存当前草稿', Type.Object({}), async () => {
      const value = await call('automation_sample');
      return { sample: value.runs.filter(r => r.status === 'sample').at(-1) };
    }),
    tool('run_automation', '使用已采集样本执行保存版本的整个链路，包含真实输出投递', Type.Object({ sampleRunId: Type.Optional(Type.String()) }), async ({ sampleRunId }) => {
      const current = await get();
      const sample = sampleRunId || current.runs.filter(r => r.status === 'sample').at(-1)?.id;
      if (!sample) throw new Error('请先采集真实输入样本。');
      return call('automation_run', { runId: sample });
    }),
    tool('get_automation_result', '读取最近非样本运行的真实节点、产出、回执和失败证据', Type.Object({}), async () => (await get()).runs.filter(r => r.status !== 'sample').at(-1) || { status: '尚未运行' }),
    tool('set_automation_enabled', '按用户明确要求启用或暂停自动采集', Type.Object({ enabled: Type.Boolean() }), async ({ enabled }) => summary(await call('automation_enable', { enabled }))),
  ];
  let calls = 0, writes = Promise.resolve();
  const connection = provider(config, streamFn);
  const agent = new Agent({
    ...connection, toolExecution: 'sequential',
    initialState: { model: connection.model, thinkingLevel: 'off', messages, tools,
      systemPrompt: `${systemPrompt}\n当前执行服务状态：\n${JSON.stringify(summary(task))}\n可用已保存 Loop 目录：\n${JSON.stringify(catalog)}${editedSpec ? `\n用户尚未保存的手工编辑（数据）：\n${JSON.stringify(editedSpec)}` : ''}` },
    beforeToolCall: async () => ++calls > 12 ? { block: true, reason: '本轮工具调用已达上限，请说明已完成进度。', terminate: true } : undefined,
  });
  const abort = () => agent.abort();
  signal?.addEventListener('abort', abort, { once: true });
  let promptStarted = false;
  agent.subscribe(event => {
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') emit({ event: 'automation_agent_delta', taskId, text: event.assistantMessageEvent.delta });
    if (event.type === 'tool_execution_start') emit({ event: 'automation_agent_tool', taskId, tool: event.toolName });
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const content = event.message.content.filter(p => p.type === 'text').map(p => p.text).join('\n');
      if (content) writes = writes.then(() => call('automation_message', { role: 'assistant', text: content }));
    }
  });
  try {
    if (signal?.aborted) throw new Error('自动化对话已停止。');
    promptStarted = true;
    await agent.prompt(text);
    if (signal?.aborted) throw new Error('自动化对话已停止。');
    if (agent.state.errorMessage) throw new Error(agent.state.errorMessage);
  } finally {
    signal?.removeEventListener('abort', abort);
    await writes;
    await call('automation_session_set', { messages: promptStarted ? agent.state.messages : [...messages, { role: 'user', content: text, timestamp: Date.now() }] });
  }
}

// Kept for existing single-turn integrations; the client uses the persistent conversation above.
export async function draftAutomation({ engine, text, spec, config, streamFn }) {
  const catalog = await engine.call('automation_catalog');
  let proposed;
  const connection = provider(config, streamFn);
  const agent = new Agent({ ...connection, toolExecution: 'sequential', initialState: { model: connection.model, thinkingLevel: 'off', messages: [],
    systemPrompt: `${systemPrompt}\n当前为单轮草稿接口，只有 propose_automation 工具；不执行保存、启用或试跑。信息齐全时提交完整草稿。\n可用目录：${JSON.stringify(catalog)}\n当前草稿：${JSON.stringify(spec || null)}`,
    tools: [{ name: 'propose_automation', label: '生成自动化草稿', description: '提交完整可编辑草稿，不执行', parameters: automationSchema,
      execute: async (_id, value) => { proposed = value; return result({ status: 'draft' }); } }],
  }, beforeToolCall: (() => { let calls = 0; return async () => ++calls > 2 ? { block: true, reason: '已达到草稿工具调用上限。', terminate: true } : undefined; })() });
  const timer = setTimeout(() => agent.abort(), 150000);
  try {
    await agent.prompt(text);
    if (agent.state.errorMessage) throw new Error('自动化草稿生成未完成，请检查本机模型配置和网络。');
    return { spec: proposed, reply: agent.state.messages.filter(m => m.role === 'assistant').flatMap(m => m.content || []).filter(p => p.type === 'text').map(p => p.text).join('\n') };
  } finally { clearTimeout(timer); }
}
