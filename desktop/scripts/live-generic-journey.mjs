import { modelHarnessOptions, closeModelHarness } from './model-harness.mjs';
const modelOptions = await modelHarnessOptions();
// Real Pi/DeepSeek and local execution; this harness never supplies assistant replies.
import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Host } from '../host/service.mjs';
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const kind = process.argv[2] || 'sales';
if (!['sales', 'engineering'].includes(kind)) throw new Error('Unknown journey');
const options = { ...modelOptions, dataDirectory: path.join(desktop, '.local/generic-acceptance-20261030'), configDirectory: path.resolve(desktop, '..') };
await fs.mkdir(options.dataDirectory, { recursive: true });
const reportPath = path.join(options.dataDirectory, `${kind}-journey.json`);
let state;
try { state = JSON.parse(await fs.readFile(reportPath, 'utf8')); } catch { state = { kind, runs: [] }; }
const host = new Host(options);
const persist = () => fs.writeFile(reportPath, JSON.stringify(state, null, 2));
const get = async () => (await host.action('list')).find((loop) => loop.id === state.loopId);
let stage;
host.on('event', (event) => { if (event.event === 'state') { const run = event.loops.find((loop) => loop.id === state.loopId)?.runs.at(-1); if (run && `${run.id}:${run.stage}:${run.status}` !== stage) { stage = `${run.id}:${run.stage}:${run.status}`; console.log(JSON.stringify({ kind, run: run.number, stage: run.stage, status: run.status })); } } });
async function chat(text) {
  await host.action('chat', { loopId: state.loopId, text });
  if (host.busy.has(state.loopId)) await new Promise((resolve) => { const done = (event) => { if (event.event === 'agent_done' && event.loopId === state.loopId) { host.off('event', done); resolve(); } }; host.on('event', done); });
  const loop = await get();
  await fs.writeFile(path.join(options.dataDirectory, `${kind}-conversation.json`), JSON.stringify(loop.messages, null, 2));
  if (loop.messages.at(-1)?.role === 'error') throw new Error(loop.messages.at(-1).text);
  return loop;
}
async function trial(input, repairs = 0) {
  let run = await host.action('start', { loopId: state.loopId, inputs: Object.fromEntries((await get()).definition.inputs.map((item) => [item.id, input])) });
  if (['queued', 'running', 'correcting', 'stopping'].includes(run.status)) await new Promise((resolve) => { const update = (event) => { const current = event.event === 'state' && event.loops.find((loop) => loop.id === state.loopId)?.runs.find((item) => item.id === run.id); if (current && !['queued', 'running', 'correcting', 'stopping'].includes(current.status)) { run = current; host.off('event', update); resolve(); } }; host.on('event', update); });
  state.lastRunId = run.id; await persist();
  if (run.status !== 'success') {
    state.failedRuns = [...(state.failedRuns || []), run.id]; await persist();
    if (repairs >= 2) throw new Error(run.reason || '试跑失败');
    const fixed = await chat('这次真实试跑失败了。请通过 get_run_result 查看失败证据，核对步骤配置和脚本的输入输出契约，修正可复用方法。保留运行记录，不写死当前输入或结果，不自动试跑或保存。');
    state.repairRevisions = [...(state.repairRevisions || []), fixed.revision]; await persist();
    return trial(input, repairs + 1);
  }
  return run;
}
try {
  if (!state.loopId) { state.loopId = (await host.action('new')).id; await persist(); }
  if (!state.fixture) {
    const fixture = await host.action('fixture', { loopId: state.loopId, kind });
    const config = { resources: fixture.resources, inputDefaults: fixture.inputDefaults };
    await host.action('configure', { loopId: state.loopId, config });
    state.fixture = fixture; await persist();
  }
  if (!state.builtRevision) {
    const text = kind === 'sales'
      ? '请读取销售资料约定和默认 CSV，搭建一个可换销售明细重复运行的经营简报 Loop。用两个步骤完成：汇总数据、生成简报；确定性计算用 Python，不调用 Codex。仅统计已付款订单，汇总总额、总数量和按商品明细，并检查汇总一致性。每次输入一个文件，标识 source。脚本与配置一起保存，不能写死样例数值。先搭建，不试跑、不保存。'
      : '请检查故障仓库约定和默认错误日志，搭建可换日志重复运行的研发 Loop，缩短为三个步骤：分析与复现、修复与验证、交付。前两步用同一 Codex 会话；第三步用 Python 交付本地补丁、问题分析和带真实命令退出码的验证记录，不推送远程。每次文件输入标识 source。先新增回归测试证明修复前失败，再修复；保留已有测试，修复后通过外部测试检查。失败时根据检查结果自动纠正。固定测试命令保存在结构化 checks，必要脚本保存到 scripts。不要把本次路径或日志数值写死在可复用方法中。先搭建，不试跑、不保存。';
    const built = await chat(text);
    if (built.definition?.schemaVersion !== 2) throw new Error('Loops AI 未生成通用流程，请查看对话');
    state.builtRevision = built.revision; await persist();
    console.log(JSON.stringify({ kind, built: built.revision, steps: built.definition.steps.map((s) => ({ id: s.id, tool: s.execution.tool })) }));
  }
  if (!state.runs.length) {
    if (state.lastRunId) {
      const previous = (await get()).runs.find((run) => run.id === state.lastRunId);
      if (previous && previous.status === 'failed') {
        state.failedRuns = [...new Set([...(state.failedRuns || []), previous.id])];
        const fixed = await chat('这次真实试跑失败了。请通过 get_run_result 查看失败证据，核对步骤配置和脚本的输入输出契约，修正可复用方法。保留运行记录，不写死当前输入或结果，不自动试跑或保存。');
        state.repairRevisions = [...(state.repairRevisions || []), fixed.revision]; await persist();
      }
    }
    const run = await trial(state.fixture.inputDefaults.source);
    state.runs.push(run); await persist();
  }
  if (!state.correctedRevision) {
    const text = kind === 'sales'
      ? '请根据这次真实结果完善以后复用的方法：简报增加各商品销售额占总额的比例，并核对各商品金额之和等于总额。以实际输入计算，不写死商品名称、数值或比例。保留已有统计口径和前一次运行，只更新配置与脚本，暂不运行、不保存。'
      : '请根据刚才真实试跑完善后续交付：报告逐项列出本轮新增用例、输入与预期依据、修复前失败和修复后结果，并附真实测试命令及退出码。复现测试以后同时保留一个有关的正常或边界场景，依据项目约定确定预期。仍保持三个步骤，只改通用方法与必要脚本，保留前一次运行，不运行、不保存。';
    const corrected = await chat(text);
    if (corrected.revision <= state.builtRevision) throw new Error('反馈未产生新版本');
    state.correctedRevision = corrected.revision; await persist();
  }
  if (state.runs.length < 2) {
    const run = await trial(state.fixture.secondInputPath);
    state.runs.push(run); await persist();
  }
  if (!state.savedRevision) {
    const saved = await chat('请保存当前流程和相关脚本，供以后换输入直接运行。');
    const verifiedRevision = state.runs.at(-1).revision;
    if (saved.savedRevision !== verifiedRevision) throw new Error('最后一次成功试跑的版本未保存');
    if (JSON.stringify(saved.runs.find((run) => run.id === state.runs[0].id)) !== JSON.stringify(state.runs[0])) throw new Error('旧运行记录改变');
    state.correctedRevision = verifiedRevision; state.savedRevision = saved.savedRevision; state.firstRunUnchanged = true; await persist();
  }
  console.log(JSON.stringify({ kind, loopId: state.loopId, built: state.builtRevision, corrected: state.correctedRevision, saved: state.savedRevision, runs: state.runs.map((run) => ({ id: run.id, status: run.status })) }));
} finally { await host.close(); closeModelHarness(); }
