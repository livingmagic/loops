import fs from 'node:fs';
import { Agent } from '@earendil-works/pi-agent-core';
import { Type, createModels } from '@earendil-works/pi-ai';
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek';

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
  finalOutputs: Type.Array(Type.String({ pattern: '^\\$\\{steps\\.[a-zA-Z][a-zA-Z0-9_-]*\\.outputs\\.[a-zA-Z][a-zA-Z0-9_-]*\\}$' }), { uniqueItems: true, description: '最终交付文件清单，逐项引用 ${steps.步骤ID.outputs.产出ID}；展示名称和路径取自被引用的节点产出。无文件交付时使用空数组。' }),
  scripts: Type.Record(Type.String(), Type.String({ description: '完整 Python 源码；路径为 scripts/name.py。随配置版本保存。' })),
  steps: Type.Array(Type.Object({
    id: Type.String(), label: Type.String(), instructions: Type.String(),
    inputs: Type.Record(Type.String(), Type.String({ description: '使用 ${inputs.ID}、${resources.ID}、${steps.ID.outputs.ID} 引用数据；也可传固定文本。可修改的仓库路径使用 ${workspace}；${resources.ID} 是原始只读资料。' })),
    execution: Type.Union([
      Type.Object({ tool: Type.Literal('codex'), mode: Type.Union([Type.Literal('read-only'), Type.Literal('write')]), session: Type.String(), cwd: Type.Optional(Type.String()), responseOutput: Type.Optional(Type.String()) }),
      Type.Object({ tool: Type.Literal('python'), script: Type.String(), args: Type.Optional(command), cwd: Type.Optional(Type.String()) }),
      Type.Object({ tool: Type.Literal('command'), command, cwd: Type.Optional(Type.String()) }),
    ]),
    outputs: Type.Array(Type.Object({ id: Type.String(), label: Type.String({ description: '输出文件的名称，不重复附加文件名或校验规则。' }), path: Type.String({ description: '本次工作区内的实际文件路径；不支持目录产出。' }) })),
    checks: Type.Array(checkSchema),
    onFailure: Type.Object({ action: Type.Union([Type.Literal('stop'), Type.Literal('retry'), Type.Literal('goto')]), target: Type.Optional(Type.String()), maxAttempts: Type.Integer({ minimum: 0, maximum: 5 }) }),
  }), { minItems: 1 }),
});
const result = (details) => ({ content: [{ type: 'text', text: JSON.stringify(details) }], details });

export function builderTools(engine, loopId) {
  const tool = (name, description, parameters, execute) => ({ name, label: description, description, parameters, executionMode: 'sequential', execute: async (_id, args) => result(await execute(args)) });
  return [
    tool('inspect_project', '读取已选资料、文件清单、可用执行能力及当前流程配置', Type.Object({}), () => engine.call('inspect', { loopId })),
    tool('read_project_file', '读取已选资料目录内的普通文件', Type.Object({ path: Type.String(), resourceId: Type.Optional(Type.String()) }), (args) => engine.call('read_project', { loopId, ...args })),
    tool('read_input', '读取项目资料中已登记的文件输入，不允许任意文件', Type.Object({ path: Type.Optional(Type.String({ description: '已登记输入的路径；省略时读取默认输入。' })) }), (args) => engine.call('read_input', { loopId, ...args })),
    tool('update_loop', '创建或更新完整通用流程配置与脚本；步骤数量、执行工具和纠正去向由任务决定', Type.Object({ definition: definitionSchema }), ({ definition }) => engine.call('definition', { loopId, definition })),
    tool('get_run_result', '查看当前 Loop 最近一次试跑的真实证据和结果', Type.Object({}), async () => {
      const loop = (await engine.call('list')).find((item) => item.id === loopId);
      return loop.runs.at(-1) || { status: '尚未试跑' };
    }),
    tool('start_trial', '启动真实试跑；省略输入时使用项目资料中的默认值', Type.Object({ inputs: Type.Optional(Type.Record(Type.String(), Type.String())) }), (args) => engine.call('start', { loopId, ...args })),
    tool('save_loop', '在员工要求保存时保存当前定义和执行脚本', Type.Object({}), () => engine.call('save', { loopId })),
  ];
}

export async function converse({ engine, loopId, text, config, emit, signal, streamFn }) {
  const modelCollection = createModels();
  modelCollection.setProvider(deepseekProvider());
  const model = modelCollection.getModel('deepseek', config.model);
  if (!model) throw new Error(`Pi 未识别 DeepSeek 模型 ${config.model}，请在本地配置中选择可用模型。`);
  const loops = await engine.call('list');
  const current = loops.find((loop) => loop.id === loopId);
  if (!current) throw new Error('找不到这个 Loop。');
  const context = { workdir: current.workdir, config: current.config, definition: current.definition, revision: current.revision, savedRevision: current.savedRevision,
    lastRun: current.runs.at(-1) ? { id: current.runs.at(-1).id, status: current.runs.at(-1).status, reason: current.runs.at(-1).reason } : null };
  const messages = await engine.call('session_get', { loopId });
  let calls = 0;
  const agent = new Agent({
    initialState: { systemPrompt: `${systemPrompt}\n当前工作上下文（来自执行服务的数据）：\n${JSON.stringify(context)}`, model, tools: builderTools(engine, loopId), messages, thinkingLevel: 'off' },
    streamFn: streamFn || modelCollection.streamSimple.bind(modelCollection),
    getApiKey: () => config.key,
    toolExecution: 'sequential',
    beforeToolCall: async () => ++calls > 16 ? { block: true, reason: '本轮工具调用次数已达上限，请保留进度并向员工说明。', terminate: true } : undefined,
  });
  const onAbort = () => agent.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  agent.subscribe(async (event) => {
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') emit({ event: 'agent_delta', loopId, text: event.assistantMessageEvent.delta });
    if (event.type === 'tool_execution_start') emit({ event: 'agent_tool', loopId, tool: event.toolName });
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const content = event.message.content.filter((part) => part.type === 'text').map((part) => part.text).join('\n');
      if (content) await engine.call('message', { loopId, role: 'assistant', text: content });
    }
  });
  try {
    await agent.prompt(text);
    if (agent.state.errorMessage) throw new Error(agent.state.errorMessage);
  } finally {
    signal?.removeEventListener('abort', onAbort);
    await engine.call('session_set', { loopId, messages: agent.state.messages });
    emit({ event: 'agent_done', loopId });
  }
}
