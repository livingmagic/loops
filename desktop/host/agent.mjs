import { modelConnection } from './models.mjs';
import fs from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { Agent } from '@earendil-works/pi-agent-core';
import { Type } from '@earendil-works/pi-ai';
import { thinkingLevel } from './config.mjs';
import { modelStream } from './model-stream.mjs';
import { ConversationProgress } from './conversation-progress.mjs';

const systemPrompt = fs.readFileSync(new URL('../prompts/builder.md', import.meta.url), 'utf8');
const command = Type.Array(Type.String(), { minItems: 1 });
const checkSchema = Type.Union([
  Type.Object({ kind: Type.Literal('command'), label: Type.String(), command, expectedExitCode: Type.Integer() }),
  Type.Object({ kind: Type.Literal('file'), path: Type.String() }),
]);
const definitionSchema = Type.Object({
  schemaVersion: Type.Literal(2),
  title: Type.String(), goal: Type.String(), completion: Type.String(),
  inputs: Type.Array(Type.Object({ id: Type.String(), label: Type.String(), kind: Type.Union([Type.Literal('file'), Type.Literal('text')]), required: Type.Boolean() })),
  finalOutputs: Type.Array(Type.String({ pattern: '^\\$\\{steps\\.[a-zA-Z][a-zA-Z0-9_-]*\\.outputs\\.[a-zA-Z][a-zA-Z0-9_-]*\\}$' }), { uniqueItems: true, description: '最终交付清单，逐项引用 ${steps.步骤ID.outputs.产出ID}；数据产出保留原始类型传递，并由平台保存为 JSON 供查看。无交付时使用空数组。' }),
  scripts: Type.Record(Type.String(), Type.String({ description: '完整 Python 源码；路径为 scripts/name.py。随配置版本保存。' })),
  steps: Type.Array(Type.Object({
    id: Type.String(), label: Type.String(), instructions: Type.String({ description: '说明业务任务与处理规则，不重复 execution 中可调整的参数值。不重复写模型名或思考档位。' }),
    inputs: Type.Record(Type.String(), Type.String({ description: '使用 ${inputs.ID}、${resources.ID}、${steps.ID.outputs.ID} 引用数据；也可传固定文本。可修改的仓库路径使用 ${workspace}；${resources.ID} 是原始只读资料。' })),
    execution: Type.Union([
      Type.Object({ tool: Type.Literal('agent'), reasoning: Type.Optional(Type.String({ description: '用户要求的思考强度，必须来自客户端模型 reasoningLevels。' })), model: Type.Optional(Type.String({ description: '仅用户明确要求时填写客户端模型 ID；省略则继承默认模型。' })), agentId: Type.Optional(Type.String({ description: '省略时继承流程默认执行 Agent；指定时使用 inspect_project 返回的客户端 ID。' })), mode: Type.Literal('read-only'), session: Type.String(), cwd: Type.Optional(Type.String()), responseOutput: Type.Optional(Type.String({ description: '把最终回复正文保存为此产出文件，仅只读模式使用' })) }, { additionalProperties: false }),
      Type.Object({ tool: Type.Literal('agent'), reasoning: Type.Optional(Type.String({ description: '用户要求的思考强度，必须来自客户端模型 reasoningLevels。' })), model: Type.Optional(Type.String({ description: '仅用户明确要求时填写客户端模型 ID；省略则继承默认模型。' })), agentId: Type.Optional(Type.String({ description: '省略时继承流程默认执行 Agent；指定时使用 inspect_project 返回的客户端 ID。' })), mode: Type.Literal('write'), session: Type.String(), cwd: Type.Optional(Type.String()) }, { additionalProperties: false }),
      Type.Object({ tool: Type.Literal('python'), script: Type.String(), args: Type.Optional(Type.Array(Type.String(), { description: '脚本命令行参数。调用模型时填 ["--model", add_model_resource 返回的 id, "--reasoning", 支持的 thinkingLevels 值]，关闭思考为 off；不使用调用名绑定。脚本用 argparse 读取这两个必填参数，再 from loops_ai import complete; text = complete(prompt, model=args.model, reasoning=args.reasoning, system="", max_tokens=2048)，同步返回正文。密钥由宿主读取。' })), cwd: Type.Optional(Type.String()) }, { additionalProperties: false }),
      Type.Object({ tool: Type.Literal('command'), command, cwd: Type.Optional(Type.String()), forEach: Type.Optional(Type.String({ description: '完整引用 JSON 数组；command 参数用 ${item} 或 ${item.字段}，逐项顺序执行，空数组不执行。' })), effect: Type.Optional(Type.Union([Type.Literal('read'), Type.Literal('write')], { description: '确认无写入才声明 read；省略按 write，发起后不自动重试或重放。' })), timeoutSeconds: Type.Optional(Type.Number({ minimum: 0.001, maximum: 1200 })) }),
      Type.Object({ tool: Type.Literal('mcp'), connectorId: Type.String(), operation: Type.String(), forEach: Type.Optional(Type.String({ description: '完整引用前序步骤的 JSON 数组。对每一项顺序调用工具；arguments 内用 ${item} 或 ${item.字段} 取当前项。空数组不调用，所有 value 输出均为数组；每项 pointer 从各自响应选取后汇总。' })), arguments: Type.Record(Type.String(), Type.Unknown({ description: '按工具 inputSchema 填写；完整引用 ${steps.ID.outputs.ID} 保留对象、数组、数字等原始类型。' })) }, { additionalProperties: false }),
    ]),
    outputs: Type.Array(Type.Union([
      Type.Object({ id: Type.String(), label: Type.String(), path: Type.String({ description: '本次工作区内的实际文件路径；不支持目录产出。' }), kind: Type.Optional(Type.Union([Type.Literal('file'), Type.Literal('json')], { description: '默认 file 传递文件路径；json 要求脚本生成有效 JSON 文件，平台解析后传递对象或数组。脚本仍写入 LOOPS_CONTEXT.outputs 指定文件路径。' })) }),
      Type.Object({ id: Type.String(), label: Type.String(), kind: Type.Literal('value'), source: Type.Literal('stdout'), format: Type.Literal('json'), pointer: Type.Optional(Type.String({ description: '命令标准输出的 JSON Pointer；省略取整个 JSON。逐项执行按顺序汇总数组。' })) }),
      Type.Object({ id: Type.String(), label: Type.String(), kind: Type.Literal('value'), pointer: Type.String({ description: '仅用于 MCP 返回。JSON Pointer：/payload 取业务数据，/payload/字段 取具体字段，/links 取链接列表，/result 取原始协议结果；空字符串取完整响应。不存在的字段会使步骤失败。' }) }),
    ])),
    checks: Type.Array(checkSchema),
    onFailure: Type.Object({ action: Type.Union([Type.Literal('stop'), Type.Literal('retry'), Type.Literal('goto')]), target: Type.Optional(Type.String()), maxAttempts: Type.Integer({ minimum: 0, maximum: 5 }) }),
  }), { minItems: 1 }),
});
const result = (details) => ({ content: [{ type: 'text', text: JSON.stringify(details) }], details });
// Mutations acknowledge the persisted contract; inspect_project remains the full read path.
const loopSummary = loop => ({ id: loop.id, title: loop.title, revision: loop.revision, savedRevision: loop.savedRevision,
  inputs: loop.definition.inputs.map(({ id, label, kind, required }) => ({ id, label, kind, required })),
  outputs: loop.definition.finalOutputs.map(ref => {
    const [, stepId, outputId] = ref.match(/^\$\{steps\.([^.]+)\.outputs\.([^.]+)\}$/);
    const output = loop.definition.steps.find(step => step.id === stepId).outputs.find(output => output.id === outputId);
    return { label: output.label, kind: output.kind || 'file' };
  }) });

export function builderTools(engine, loopId, signal) {
  const tool = (name, description, parameters, execute) => ({ name, label: description, description, parameters, executionMode: 'sequential', execute: async (_id, args) => result(await execute(args)) });
  return [
    tool('list_models', '查看已配置的模型 API 资源及支持的思考强度；不返回密钥', Type.Object({}), () => engine.call('model_catalog')),
    tool('add_model_resource', '将已配置的 API 模型登记为本流程资源，返回 resourceId 所需的 id 及支持的思考强度', Type.Object({ modelId: Type.String({ description: 'list_models 返回的模型配置 id，不是供应商的模型名称。' }) }), args => engine.call('add_model_resource', { loopId, ...args })),
    tool('list_connectors', '发现用户添加的 MCP 服务；只返回连接状态和能力简介，不执行业务操作', Type.Object({}), async () => (await engine.call('connector_list')).filter(item => item.manifest.type === 'mcp').map(({ manifest, enabled, status, tools }) => ({ id: manifest.id, name: manifest.name, description: manifest.description, enabled, status, operationCount: tools?.length || 0 }))),
    tool('list_connector_operations', '查询 MCP 操作索引；按任务检索，完整参数用 inspect_connector_operation 查看', Type.Object({ connectorId: Type.String(), query: Type.Optional(Type.String()), offset: Type.Optional(Type.Integer({ minimum: 0 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }), args => engine.call('connector_operations', args)),
    tool('inspect_connector_operation', '读取 MCP 操作参数、返回格式和读写影响，不执行业务操作', Type.Object({ connectorId: Type.String(), operation: Type.String() }), args => engine.call('connector_describe', args)),
    tool('preview_connector_operation', '只读预览 MCP 的真实返回，不能执行写操作；返回形状与流程 MCP 步骤相同', Type.Object({ connectorId: Type.String(), operation: Type.String(), arguments: Type.Record(Type.String(), Type.Unknown()) }), args => engine.call('connector_preview', args)),
    tool('review_connector_operation', '根据已查看的完整 Schema 与说明评估未知操作的读写影响，保存依据，不执行或授予权限', Type.Object({ connectorId: Type.String(), operation: Type.String(), risk: Type.Union([Type.Literal('read'), Type.Literal('write'), Type.Literal('high-risk-write')]), evidence: Type.String() }), args => engine.call('connector_review_operation', args)),
    tool('set_execution_model', '按用户明确要求指定模型与思考强度；stepId 指定单步，否则 agentId 指定本流程该 Agent 的模型；model=null 恢复继承', Type.Object({ model: Type.Union([Type.String(), Type.Null()]), reasoning: Type.Optional(Type.Union([Type.String(), Type.Null()], { description: '指定思考强度；null 恢复继承。' })), agentId: Type.Optional(Type.String()), stepId: Type.Optional(Type.String()) }), args => engine.call('agent_model_selection', { loopId, ...args })),
    tool('set_execution_agent', '根据任务与可用能力选择流程默认执行 Agent，或为单个智能步骤选择 Agent；步骤 agentId=null 恢复继承', Type.Object({ agentId: Type.Union([Type.String(), Type.Null()]), stepId: Type.Optional(Type.String()) }), args => engine.call('agent_selection', { loopId, ...args })),
    tool('inspect_project', '读取已选资料、文件清单、可用执行能力及当前流程配置', Type.Object({}), () => engine.call('inspect', { loopId })),
    tool('read_project_file', '读取已选资料目录内的普通文件', Type.Object({ path: Type.String(), resourceId: Type.Optional(Type.String()) }), (args) => engine.call('read_project', { loopId, ...args })),
    tool('read_input', '读取项目资料中已登记的文件输入，不允许任意文件', Type.Object({ path: Type.Optional(Type.String({ description: '已登记输入的路径；省略时读取默认输入。' })) }), (args) => engine.call('read_input', { loopId, ...args })),
    tool('update_loop', '创建或更新完整通用流程配置与脚本；步骤数量、执行工具和纠正去向由任务决定', Type.Object({ definition: definitionSchema }), async ({ definition }) => loopSummary(await engine.call('definition', { loopId, definition }))),
    tool('get_run_result', '查看最近试跑的真实证据；执行中最多等待 45 秒，完成或失败立即返回', Type.Object({}), async () => {
      const deadline = Date.now() + 45_000;
      while (true) {
        const loop = (await engine.call('list')).find((item) => item.id === loopId);
        const run = loop.runs.at(-1);
        if (!run || !['queued', 'preparing', 'running', 'correcting'].includes(run.status) || Date.now() >= deadline) {
          if (run?.status === 'failed') await engine.call('claim_failure_review', { loopId, runId: run.id });
          return run || { status: '尚未试跑' };
        }
        await delay(1000, undefined, { signal });
      }
    }),
    tool('start_trial', '启动真实试跑；省略输入时使用项目资料中的默认值', Type.Object({ inputs: Type.Optional(Type.Record(Type.String(), Type.String())) }), (args) => engine.call('start', { loopId, ...args })),
    tool('save_loop', '将已试跑成功的当前草稿保存为版本', Type.Object({}), async () => loopSummary(await engine.call('save', { loopId }))),
  ];
}

export async function converse({ engine, loopId, text, config, emit, signal, streamFn, recordModelTrace }) {
  const { collection: modelCollection, model } = modelConnection(config);
  const loops = await engine.call('list');
  const current = loops.find((loop) => loop.id === loopId);
  if (!current) throw new Error('找不到这个 Loop。');
  const context = { workdir: current.workdir, config: current.config, definition: current.definition, revision: current.revision, savedRevision: current.savedRevision,
    lastRun: current.runs.at(-1) ? { id: current.runs.at(-1).id, status: current.runs.at(-1).status, reason: current.runs.at(-1).reason } : null };
  const messages = await engine.call('session_get', { loopId });
  let calls = 0, polls = 0, limitReached = false, finalText = '', succeeded = false;
  const progress = new ConversationProgress(engine, loopId, emit);
  await progress.start();
  const agent = new Agent({
    initialState: { systemPrompt: `${systemPrompt}\n当前工作上下文（来自执行服务的数据）：\n${JSON.stringify(context)}`, model, tools: builderTools(engine, loopId, signal), messages: messages.filter(message => message.role !== 'system'), thinkingLevel: thinkingLevel(config) },
    streamFn: modelStream(streamFn || modelCollection.streamSimple.bind(modelCollection), { record: recordModelTrace, status: (text, phase) => { progress.status(text, phase); emit({ event: 'agent_status', loopId, text, phase }); } }),
    getApiKey: () => config.key,
    toolExecution: 'sequential',
    beforeToolCall: async ({ toolCall }) => {
      limitReached = toolCall.name === 'get_run_result' ? ++polls > 96 : ++calls > 16;
      return limitReached ? { block: true, reason: '本轮工具调用次数已达上限，请保留进度并向员工说明。', terminate: true } : undefined;
    },
  });
  const onAbort = () => agent.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  agent.subscribe((event) => {
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') emit({ event: 'agent_delta', loopId, text: event.assistantMessageEvent.delta });
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'toolcall_start') {
      const call = event.assistantMessageEvent.partial.content[event.assistantMessageEvent.contentIndex];
      if (call?.name) progress.preparing(call.name);
    }
    if (event.type === 'tool_execution_start' || event.type === 'tool_execution_end') progress.tool(event);
    if (event.type === 'tool_execution_start') emit({ event: 'agent_tool', loopId, tool: event.toolName });
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const content = event.message.content.filter((part) => part.type === 'text').map((part) => part.text).join('\n');
      if (event.message.stopReason === 'stop') finalText = content;
      else if (event.message.stopReason === 'toolUse') progress.note(content);
    }
  });
  try {
    if (signal?.aborted) throw new Error('cancelled');
    await agent.prompt(text);
    if (agent.state.errorMessage) throw new Error(agent.state.errorMessage);
    succeeded = !limitReached;
    if (limitReached && !finalText) finalText = '本轮处理已达到上限，尚未完成验收。已保留流程和运行记录，可以继续处理。';
  } finally {
    signal?.removeEventListener('abort', onAbort);
    await progress.finish(signal?.aborted ? 'stopped' : succeeded ? 'success' : 'failed');
    await engine.call('session_set', { loopId, messages: agent.state.messages });
  }
  if (finalText) await engine.call('message', { loopId, role: 'assistant', text: finalText });
}
