import { pluginCatalog, pluginPrompt } from './plugins.mjs';
import { modelConnection } from './models.mjs';
import fs from 'node:fs';
import { Agent } from '@earendil-works/pi-agent-core';
import { Type } from '@earendil-works/pi-ai';
import { localizeArgumentError } from './tool-error-context.mjs';
import { thinkingLevel } from './config.mjs';
import { modelStream } from './model-stream.mjs';

const staticPrompt = fs.readFileSync(new URL('../prompts/automation.md', import.meta.url), 'utf8');
const script = Type.Object({ label: Type.Optional(Type.String({ description: '给用户看的输入行为说明，例如每个工作日采集订单；不是执行规则' })), script: Type.String(), config: Type.Record(Type.String(), Type.Unknown()) });
const documentVerification = Type.Object({
  kind: Type.Literal('document'), input: Type.String({ description: '本输出绑定中的原报告输入名，不是正文或路径' }),
  expectedFormat: Type.Optional(Type.Union([Type.Literal('text'), Type.Literal('markdown'), Type.Literal('html')], { description: '原报告格式，非目标回读格式。已保存产出有 format 时由平台填入；只有来源格式不明确时才声明。' })),
  titlePolicy: Type.Union([Type.Literal('preserve'), Type.Literal('target')], { description: 'preserve 保留报告原标题；target 仅用于用户允许目标名称替换文档级标题。用户要求保留原标题时必须 preserve。' }),
  expectedTitle: Type.Optional(Type.String({ description: '用户要求的目标文档名称；允许标题替换时必填' })),
});
export const automationSchema = Type.Object({
  title: Type.String(),
  connectors: Type.Optional(Type.Array(Type.Object({ id: Type.String(), operations: Type.Array(Type.String(), { minItems: 1 }) }))),
  source: Type.Object({ ...script.properties, lifecycle: Type.Union([Type.Literal('once'), Type.Literal('service')]) }),
  nodes: Type.Array(Type.Object({ id: Type.String(), loopId: Type.String(), revision: Type.Integer(), bindings: Type.Record(Type.String(), Type.String()) }), { minItems: 1 }),
  outputs: Type.Array(Type.Object({ ...script.properties, id: Type.String(), title: Type.String(), bindings: Type.Record(Type.String(), Type.String()), verification: Type.Optional(documentVerification) })),
  policy: Type.Object({ queueLimit: Type.Integer({ minimum: 1, maximum: 1000, default: 100 }), scriptTimeout: Type.Integer({ minimum: 1, maximum: 1200, default: 180 }), sourceRestarts: Type.Integer({ minimum: 0, maximum: 5, default: 3 }) }),
});
export function automationProviderStream(collection) {
  // Retry only unaccepted provider HTTP requests; never replay a turn or its tools.
  return modelStream(collection.streamSimple.bind(collection));
}
function provider(config, streamFn) {
  const { collection, model } = modelConnection(config);
  return { model, streamFn: streamFn ? modelStream(streamFn) : automationProviderStream(collection), getApiKey: () => config.key };
}
const result = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }], details: value });
function summary(task) {
  return { taskId: task.id, title: task.title, revision: task.revision, hasDraft: Boolean(task.draftSpec), enabled: task.enabled,
    spec: task.draftSpec || task.spec, seedLoopId: task.seedLoopId, sourceStatus: task.sourceStatus, sourceError: task.sourceError,
    connectionWait: task.connectionWait, lastRun: task.runs.filter(r => r.status !== 'sample').at(-1) || null, lastSample: task.runs.filter(r => r.status === 'sample').at(-1) || null };
}
async function connectorCatalog(engine, includeOperations = false) {
  const [items, presets] = await Promise.all([engine.call('connector_list'), engine.call('connector_presets')]);
  const installed = new Set(items.map(item => item.manifest.id));
  const connected = [], needsConnection = [];
  for (const { manifest, revision, enabled, status, message, tools, capabilities } of items) {
    const entry = { id: manifest.id, name: manifest.name, type: manifest.type, identity: manifest.identity, revision, status, message };
    if (enabled && status === 'connected') {
      if (includeOperations) Object.assign(entry, capabilities ? { capabilities } : manifest.type === 'mcp'
        ? { capabilities: { discovery: 'mcp', operationCount: tools?.length || 0 } } : { tools });
      // Include all names so literal queries cannot hide other representations.
      if (manifest.type === 'mcp') entry.operationNames = (tools || []).map(t => t.name);
      connected.push(entry);
    } else needsConnection.push(entry);
  }
  for (const preset of presets) {
    if (!installed.has(preset.id)) needsConnection.push({ id: preset.id, name: preset.name, status: 'not_added' });
  }
  return { connected, needsConnection };
}
export async function converseAutomation({ engine, taskId, text, plugins, config, emit, signal, editedSpec, streamFn, autoResume = false }) {
  const [catalog, task, messages, connectors, scriptSdk] = await Promise.all([
    engine.call('automation_catalog'), engine.call('automation_get', { taskId }), engine.call('automation_session_get', { taskId }),
    connectorCatalog(engine), engine.call('automation_script_sdk'),
  ]);
  const systemPrompt = `${staticPrompt}\n脚本 SDK（运行时真实签名与使用契约）：\n${JSON.stringify(scriptSdk)}`;
  const get = () => engine.call('automation_get', { taskId });
  const call = (method, values = {}) => engine.call(method, { taskId, ...values });
  const tool = (name, description, parameters, execute) => ({ name, label: description, description, parameters, executionMode: 'sequential', execute: async (_id, args) => result(await execute(args)) });
  const connectorOperation = async (method, args) => {
    const item = (await engine.call('connector_list')).find(item => item.manifest.id === args.connectorId);
    if (!item?.enabled || item.status !== 'connected') throw new Error(`请先记录 wait_for_connectors，并引导用户在左侧“插件”中连接${item?.manifest.name || '指定服务'}；连接后平台会自动继续搭建。`);
    return engine.call(method, args);
  };
  let startedRunId, awaitingRunId;
  const tools = [
    tool('list_plugins', '列出 MCP、CLI 和 Agent 插件及当前可用状态；Agent 由 Loop 的智能步骤执行', Type.Object({}), () => pluginCatalog(engine)),
    tool('list_connectors', 'connected 为可用于搭建任务的已连接服务；needsConnection 仅供引导连接，不提供操作能力；不返回凭证或登录链接', Type.Object({}), () => connectorCatalog(engine, true)),
    tool('wait_for_connectors', '记录本对话等待的插件，全部连接后平台自动继续搭建草稿；不登录、不保存或执行。connectorIds 使用列表中的服务 ID', Type.Object({ connectorIds: Type.Array(Type.String(), { minItems: 1, maxItems: 30 }) }), args => call('automation_wait_for_connectors', args)),
    tool('list_connector_operations', '读取轻量操作索引，不执行业务。CLI 用 domain 查领域；MCP 可用 query 检索名称或说明、offset/limit 分页，参数和完整说明用 inspect_connector_operation 读取', Type.Object({ connectorId: Type.String(), domain: Type.Optional(Type.String()), query: Type.Optional(Type.String({ maxLength: 200 })), offset: Type.Optional(Type.Integer({ minimum: 0 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }), args => connectorOperation('connector_operations', args)),
    tool('inspect_connector_operation', '读取已连接服务指定操作的参数 Schema、读写风险、返回格式和官方帮助；命令组返回子命令', Type.Object({ connectorId: Type.String(), operation: Type.String() }), args => connectorOperation('connector_describe', args)),
    tool('preview_connector_operation', '只读调用已连接服务，查看真实返回结构以编写输入解析；response 与 ctx.connector 返回同形，长文本和数组仅预览。不会运行 Loop 或投递，未知和写操作会被拒绝', Type.Object({ connectorId: Type.String(), operation: Type.String(), arguments: Type.Record(Type.String(), Type.Unknown()) }), args => connectorOperation('connector_preview', args)),
    tool('review_connector_operation', '评估 riskReviewable=true 的 MCP 或风险未知的 CLI；依据完整说明与 Schema 判断实际影响，保存依据，不执行操作。MCP 必须传 inspect 返回的 sourceHash', Type.Object({ connectorId: Type.String(), operation: Type.String(), risk: Type.Union([Type.Literal('read'), Type.Literal('write'), Type.Literal('high-risk-write')]), evidence: Type.String(), sourceHash: Type.Optional(Type.String()) }), args => connectorOperation('connector_review_operation', args)),
    tool('read_connector_skill', '按索引中的路径读取已连接 CLI 的内置 Skill 或其参考文档，用于理解业务用法', Type.Object({ connectorId: Type.String(), path: Type.String() }), args => connectorOperation('connector_skill', args)),
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
      const sample = sampleRunId || current.runs.filter(r => r.status === 'sample' && r.revision === current.revision).at(-1)?.id;
      if (!sample) throw new Error('请先采集真实输入样本。');
      if (!current.runs.some(r => r.id === sample && r.status === 'sample' && r.revision === current.revision)) throw new Error('请先采集当前保存版本的真实输入样本。');
      const accepted = await call('automation_run', { runId: sample, reportToConversation: true });
      startedRunId = accepted.runId;
      return { ...accepted, message: '试跑已启动，执行器会自动在对话中反馈完成或失败；无需轮询。' };
    }),
    tool('get_automation_result', '读取真实节点、产出、回执和失败证据；运行中自动订阅完成反馈并结束本轮，不连续查询', Type.Object({}), async () => {
      let value = (await get()).runs.filter(r => r.status !== 'sample').at(-1);
      if (!value) return { status: '尚未运行' };
      if (['queued', 'waiting', 'running', 'stopping'].includes(value.status)) {
        value = await call('automation_report_run', { runId: value.id });
        if (['queued', 'waiting', 'running', 'stopping'].includes(value.status)) awaitingRunId = value.id;
      }
      return value;
    }),
    tool('set_automation_enabled', '按用户明确要求启用或暂停自动采集', Type.Object({ enabled: Type.Boolean() }), async ({ enabled }) => summary(await call('automation_enable', { enabled }))),
  ].filter(tool => !autoResume || !['save_automation', 'sample_automation', 'run_automation', 'set_automation_enabled'].includes(tool.name));
  const metadataTools = new Set(['list_plugins', 'list_connectors', 'list_connector_operations', 'inspect_connector_operation', 'read_connector_skill', 'inspect_automation']);
  const calls = { metadata: 0, action: 0 }, attempts = { metadata: 0, action: 0 };
  const limits = { metadata: 48, action: 24 };
  let failures = 0, writes = Promise.resolve();
  const history = new Set(messages);
  const connection = provider(config, streamFn);
  const agent = new Agent({
    ...connection, toolExecution: 'sequential',
    // Keep platform events distinct in the durable conversation. Chat protocols
    // need a fresh input turn; a system-only update is folded ahead of history.
    convertToLlm: messages => messages.flatMap(message => {
      // Keep the full audit transcript in storage, but refresh tool state from
      // the current task and current calls instead of replaying stale catalogs
      // and repeated invalid configuration attempts into every new turn.
      if (history.has(message) && message.role === 'toolResult') return [];
      if (history.has(message) && message.role === 'assistant') {
        const content = message.content.filter(part => part.type === 'text');
        return content.length ? [{ ...message, content }] : [];
      }
      if (message.role === 'toolResult') return [localizeArgumentError(message, messages)];
      return message.role === 'automation_connection_event'
        ? [{ role: 'user', content: [{ type: 'text', text: `Loops 平台事件（非用户消息）：\n${message.content}` }], timestamp: message.timestamp }]
        : ['system', 'user', 'assistant', 'toolResult'].includes(message.role) ? [message] : [];
    }),
    // Pi preserves a transcript's existing system message. Rebuild it each turn
    // so connection and task status cannot remain stuck at the first request.
    initialState: { model: connection.model, thinkingLevel: thinkingLevel(config), messages: messages.filter(message => message.role !== 'system'), tools,
      systemPrompt: `${systemPrompt}\n本轮插件状态（以此替代历史状态，操作前可用 list_connectors 刷新）：\n${JSON.stringify(connectors)}\n当前执行服务状态：\n${JSON.stringify(summary(task))}\n可用已保存 Loop 目录：\n${JSON.stringify(catalog)}${editedSpec ? `\n用户尚未保存的手工编辑（数据）：\n${JSON.stringify(editedSpec)}` : ''}` },
    beforeToolCall: async ({ toolCall }) => {
      if (startedRunId || awaitingRunId) return { block: true, reason: '本次试跑已经启动，执行器会自动反馈结果，本轮不再重复执行或查询。', terminate: true };
      const category = metadataTools.has(toolCall.name) ? 'metadata' : 'action';
      if (++calls[category] > limits[category]) return { block: true, reason: '本轮工具调用已达上限，请说明已完成进度。', terminate: true };
    },
    finishTurn: async ({ message, toolResults }) => {
      if (startedRunId || awaitingRunId) return { action: 'end' };
      for (const part of message.content.filter(part => part.type === 'toolCall')) attempts[metadataTools.has(part.name) ? 'metadata' : 'action']++;
      failures += toolResults.filter(result => result.isError).length;
      if (failures >= 12 || (attempts.metadata >= limits.metadata || attempts.action >= limits.action) && toolResults.some(result => result.isError)) {
        await call('automation_message', { role: 'error', text: '本轮搭建尚未完成，已达到处理上限。需求和已完成的修改已保留，可继续调整。' });
        return { action: 'end' };
      }
    },
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
    await agent.prompt(autoResume ? { role: 'automation_connection_event', content: '此对话等待的插件已经全部连接。重新核实连接状态，沿用用户之前的需求继续搭建草稿，无需用户再次发送“继续”。本次只允许更新草稿，不保存、不采样、不运行、不启用或投递；若仍缺必要业务信息，向用户询问。', timestamp: Date.now() } : pluginPrompt(text, plugins));
    if (signal?.aborted) throw new Error('自动化对话已停止。');
    if (agent.state.errorMessage) throw new Error(agent.state.errorMessage);
  } finally {
    signal?.removeEventListener('abort', abort);
    await writes;
    await call('automation_session_set', { messages: promptStarted ? agent.state.messages : autoResume ? messages : [...messages, { role: 'user', content: pluginPrompt(text, plugins), timestamp: Date.now() }] });
  }
}

// Kept for existing single-turn integrations; the client uses the persistent conversation above.
export async function draftAutomation({ engine, text, spec, config, streamFn }) {
  const [catalog, connectors, scriptSdk] = await Promise.all([engine.call('automation_catalog'), connectorCatalog(engine), engine.call('automation_script_sdk')]);
  const systemPrompt = `${staticPrompt}\n脚本 SDK（运行时真实签名与使用契约）：\n${JSON.stringify(scriptSdk)}`;
  let proposed;
  const connection = provider(config, streamFn);
  const agent = new Agent({ ...connection, toolExecution: 'sequential', initialState: { model: connection.model, thinkingLevel: thinkingLevel(config), messages: [],
    systemPrompt: `${systemPrompt}\n当前为单轮草稿接口，只有 propose_automation 工具；不执行保存、启用或试跑。信息齐全时提交完整草稿。\n本轮插件状态：${JSON.stringify(connectors)}\n可用目录：${JSON.stringify(catalog)}\n当前草稿：${JSON.stringify(spec || null)}`,
    tools: [{ name: 'propose_automation', label: '生成自动化草稿', description: '提交完整可编辑草稿，不执行', parameters: automationSchema,
      execute: async (_id, value) => {
        const current = await connectorCatalog(engine);
        const available = new Set(current.connected.map(item => item.id));
        if (value.connectors?.some(item => !available.has(item.id))) throw new Error('请先在左侧“插件”中连接任务需要的服务，完成后继续搭建。');
        proposed = value; return result({ status: 'draft' });
      } }],
  }, beforeToolCall: (() => { let calls = 0; return async () => ++calls > 2 ? { block: true, reason: '已达到草稿工具调用上限。', terminate: true } : undefined; })() });
  const timer = setTimeout(() => agent.abort(), 150000);
  try {
    await agent.prompt(text);
    if (agent.state.errorMessage) throw new Error('自动化草稿生成未完成，请检查本机模型配置和网络。');
    return { spec: proposed, reply: agent.state.messages.filter(m => m.role === 'assistant').flatMap(m => m.content || []).filter(p => p.type === 'text').map(p => p.text).join('\n') };
  } finally { clearTimeout(timer); }
}
