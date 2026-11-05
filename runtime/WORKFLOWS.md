# 通用本地流程

正式流程统一使用 schemaVersion=2。步骤数量与标识由 Loops AI 根据目标生成，界面和执行器均读取同一份定义。工程修复与业务处理使用同一个执行器，不再提供固定七步流程、专属阶段 Prompt 或自动提交和创建 PR 的执行分支。开发期旧定义需要重新搭建，已有文件与运行记录不自动删除或迁移。

定义包含 title、goal、completion、inputs、finalOutputs、scripts、steps。每个步骤包含 id、label、instructions、inputs、execution、outputs、checks、onFailure。Python 脚本源码保存在 scripts 字典，键为 scripts/name.py；保存版本和启动运行时同时复制脚本。输入文件在启动时复制并记录哈希，运行中的配置不随员工反馈变化。

finalOutputs 是必填的最终交付清单，每项为 `${steps.步骤ID.outputs.产出ID}`，引用已经声明的节点产出。Loops AI 根据员工目标选择，可以引用任意步骤的产出；无文件交付时明确使用空数组。错误引用与重复引用会被拒绝，已存储但缺少清单的定义也不能启动。界面的“输入和输出”和交付文件都以此为准，中间文件与执行记录保留在执行详情。校验仍由各节点执行，不增加独立总体验收。

引用支持 `${inputs.ID}`、`${resources.ID}`、`${steps.ID.outputs.ID}`、`${workspace}`、`${failure}`、`${checks}`。前序输出必须先成功产生才能被引用；任务指令中的引用也会展开。固定资料统一使用 config.resources，默认输入使用 config.inputDefaults，启动时按输入 ID 提供 inputs 对象。普通流程使用独立空工作区，Git 资料通过独立 worktree 提供可修改的 workspace；resources 的文件资源始终指向原始资料，model 资源提供不含凭证的模型元数据。

执行工具：

- agent：智能任务，省略 execution.agentId 时继承 config.defaultAgentId；指定 agentId 时覆盖该步骤。内置 codex、mimo。mode 为 write 或 read-only；responseOutput 仅用于只读模式，由平台把最后回复保存为指定产出 ID。
- codex：旧定义兼容入口，固定使用 Codex，不随流程默认 Agent 改变；在步骤选择器重新选择后转为 agent 定义。
- python：运行本版本 scripts 中的脚本，args 可选。脚本从 LOOPS_CONTEXT 环境变量读取 JSON。
- command：直接运行程序和参数数组。cwd 默认本次工作区，必须位于该工作区内。

LOOPS_CONTEXT 包含 inputs（本步骤已映射输入）、outputs（本步骤产出 ID 到绝对路径）、workspace、resources、failure、checks（实际执行的命令、退出码和输出）。脚本不应自行猜测上一步输出或写死本机路径。

每个声明产出必须是工作区内的实际文件。checks 支持文件存在检查和命令检查，命令退出码默认要求 0，可设置 expectedExitCode=1 验证预期失败。产出缺失或检查不通过时不会标记步骤成功。

onFailure.action 可为 stop、retry、goto；goto 使用 target 指定步骤，maxAttempts 限制自动纠正次数。纠正携带真实失败信息，重新运行相应步骤并清理失效的下游结果；此前已完成步骤共享的文件保留。达到上限时终止并保存失败记录。进程取消会结束子进程组；重启只标记未完成运行中断，不自动重放。

本版本提供顺序步骤与有次数上限的失败回退。工具调用按定义执行；步骤完成条件应反映真实业务验收。模型生成的脚本仍需通过真实试跑验证，保存不等于已经验证。

Python、直接命令和完成检查仅继承 PATH、HOME、临时目录、语言、时区及基础系统变量，另加 LOOPS_CONTEXT；不自动传递主进程中的 API 密钥、代理设置、PYTHONPATH 或其他自定义环境变量。依赖这些变量的旧脚本需要调整。该限制不隔离文件访问或网络，Codex 仍按自身登录与沙箱机制运行。

## Python 节点使用模型 API

简单的分类、提取、摘要和改写可以直接由 Python 调用模型 API。先在设置中配置模型；搭建工具 `list_models` 返回实际模型目录、配置状态和支持的思考强度，`add_model_resource(modelId)` 将模型登记到当前 Loop 的 `config.resources`。资源类型为 `model`，固定模型配置 ID、供应商、API 模型名及自定义服务地址，不保存凭证。API 模型资源与 Agent 客户端的执行模型分别配置。

Python 直接通过命令行参数选择模型，不使用调用名绑定。`execution.args` 是唯一的参数来源：`--model` 为已登记模型资源 ID，`--reasoning` 为该模型支持的思考强度，关闭思考也需明确 `off`。两个参数各出现一次，参数名和值使用独立数组项。

```json
{
  "tool": "python",
  "script": "scripts/extract.py",
  "args": ["--model", "model-a1b2c3d4e5f6", "--reasoning", "off"]
}
```

```python
import argparse
import json
import os
from pathlib import Path
from loops_ai import complete

parser = argparse.ArgumentParser()
parser.add_argument("--model", required=True)
parser.add_argument("--reasoning", required=True)
args = parser.parse_args()
context = json.loads(os.environ["LOOPS_CONTEXT"])
text = complete(context["inputs"]["text"], model=args.model,
                reasoning=args.reasoning, system="提取文本要点，保留原意。", max_tokens=2048)
Path(context["outputs"]["result"]).write_text(text)
```

`complete(prompt, *, model, reasoning, system='', max_tokens=2048)` 同步返回正文。不支持工具调用；空响应、长度截断及 API 失败会报错。输出长度为 1–32768，仍受供应商上限约束。节点沿用 180 秒进程超时，整个运行上限 20 分钟；取消或超时会结束子进程并取消 API 请求。SDK 由带模型参数的 Python 节点注入，无需额外安装；普通命令和完成检查没有模型通道。调用的模型和强度必须与启动参数一致。

节点详情的“模型资源”直接修改 `execution.args` 中的这两个参数，生成未保存的新版本，其他脚本参数与源码保持不变。脚本预览与运行使用同一份源码，不自动添加配置注释。运行记录保留 `modelSelections`、每次调用的 `modelCalls` 状态与实际 token 用量；不记录思考过程、密钥或请求正文。

模型资源、参数和脚本随版本与运行快照固定。修改节点不影响历史运行或自动化任务已绑定版本。模型配置被移除或供应商、模型名、API 地址已改变时明确失败，要求重新选择模型，不自动切换。密钥仅由客户端从本机安全配置读取，不进入 Python 参数或环境变量。开发期旧 `execution.models` 定义需重新搭建；历史文件和运行记录保留。

## 本机 Agent 客户端

创建页选择流程默认执行 Agent，步骤详情可覆盖或恢复“跟随流程”。选择保存在流程配置和版本快照中；修改产生未保存版本，不改变旧运行或自动化任务已经绑定的版本。搭建对话仍使用现有 Pi Agent，与执行客户端选择独立。

`agent_clients.py` 通过目录插件加载器统一调度客户端；流程引擎仅使用异步会话接口（进入/关闭、ask、事件）。新增客户端只增加插件目录，不修改注册表、流程引擎或界面，详见 [Agent 插件协议](AGENT_PLUGINS.md)。首次加载自动发现；设置的“通用 → 重新检查”刷新状态。检查读取本机客户端安装、登录状态，以及首次初始化所需的默认模型配置，不读取模型历史，不发起模型任务。

Codex 使用官方 Python SDK 配套的本地运行时及已有登录，保留只读/工作区写入沙箱与自动权限审查。MiMo 使用本机 `mimo run --format json`、stdin 输入和显式会话 ID；配置了模型时传 --model，否则由 MiMo Code 自己选择模型；不读取历史模型、不据此禁用客户端，也不修改本机 MiMo 配置。登录凭证存在不等于服务端调用已验证；实际调用失败按真实结果报告。

每次运行在执行任何步骤前检查所需客户端，将实际客户端路径和版本写入 agentBindings。会话按客户端、模型、工作目录、逻辑会话名和权限模式隔离；agentSessions 记录各个真实会话 ID。取消 MiMo 时终止进程组，取消 Codex 时中断 turn 并关闭 SDK；异常、缺少结束事件或产出校验失败不会算作成功。

MiMo 的 read-only 使用原生工具权限，仅开放读取与搜索，关闭 shell、写入、MCP 和子 Agent 工具；平台已登记输入、脚本和资料可读取。write 保留客户端权限检查，原始资料对文件编辑工具只读，不使用跳过权限参数。MiMo 的工具权限不是操作系统沙箱；写模式中的 shell 等工具仍受 MiMo 自身权限机制管理。Loops 的完成检查和产出验证仍独立执行。

Agent 默认模型在 Loops 数据目录的 `agent-models.json` 保存。首次成功读取时使用客户端明确配置的模型（Codex config.toml 的当前 profile/model，MiMo debug config 的 model）；仅首次初始化读取默认模型和强度并保存为明确值；读取不到默认模型时取客户端列表第一个模型；读取不到默认强度时选 low，没有 low 时选支持的最低档位。列表尚不可用时保留未配置，之后补齐。之后不再读取客户端默认配置，刷新只更新可选模型和强度。优先级：步骤 `execution.model` > 流程 `config.agentModels[agentId]` > Loops 的 Agent 默认模型；运行绑定保存本次默认模型，步骤覆盖保存在流程版本中。明确指定的模型调用失败时不自动回退。

Agent 模型目录通过注册项的 catalog 回调读取：Codex 原生 model/list、MiMo models --verbose --pure。只返回模型名称、ID 和支持的思考档位，不返回供应商 headers/options。设置保存后自动生效；agent-models.json 兼容旧字符串，新增记录为 {model, reasoning}。思考强度传入 Codex turn.effort / MiMo --variant，并固定在运行绑定中；覆盖为其他模型时清除默认模型的强度覆盖。目录读取失败保留客户端状态及原配置，提示重新检查，不伪造档位。

自然语言明确指定思考强度时，搭建工具可保存流程级 `config.agentReasoning[agentId]` 或步骤级 `execution.reasoning`；执行前按该步骤最终选定模型校验支持档位。不同模型或强度使用独立会话。

## CLI 逐项执行与 JSON 输出

`command` 仍使用程序和参数数组；不自动经过 Shell。不设置 `forEach` 就执行一次，原有文件产出与检查继续支持。可选 `forEach` 完整引用数组，参数中的 `${item}` 或 `${item.字段}` 绑定当前元素；数字、布尔值转为 JSON 文本参数，对象与数组请通过步骤 inputs / LOOPS_CONTEXT 传入。所有项的参数在首次启动前验证；不允许列表项选择可执行程序。逐项命令的产出使用 value，结果顺序汇总；零项不启动子进程，输出 `[]`。

```json
{
  "execution": {
    "tool": "command",
    "command": ["order-cli", "create-ticket", "--order-id", "${item.id}"],
    "forEach": "${steps.orders.outputs.data}",
    "effect": "write",
    "timeoutSeconds": 60
  },
  "outputs": [
    {"id": "tickets", "label": "工单", "kind": "value", "source": "stdout", "format": "json"}
  ]
}
```

value 输出解析完整 stdout JSON，`pointer` 可选，按 JSON Pointer 取字段；省略表示完整 JSON。stderr 单独保留末尾 12000 字节，不参与解析；stdout 上限 8 MiB，超过后停止且报错，不截断为貌似有效的 JSON。非零退出码、JSON 格式错误或缺失字段均失败。value 输出同时保存为 JSON 文件，兼容自动化交付；后续步骤的完整引用保留原始类型。

每次调用默认超时 180 秒，可用 timeoutSeconds 设置（大于 0，不超过 1200 秒），超时和取消终止整个进程组。commandCalls 持久化逐项序号、命令、退出码、stdout、stderr 和状态；部分已完成的调用在失败或取消后仍保留。运行整体仍受 20 分钟限制。

命令默认 effect=write，包括旧定义中未声明影响的命令。启动后，本次运行的自动重试或 goto 不再重放该步骤；这是对旧自动纠正行为的有意收紧，原有配置仍可加载并执行。只有确认无写入时才声明 effect=read，沿用失败策略重试。这是流程作者的影响声明，不是操作系统权限隔离。写入后失败、超时或取消的外部结果可能未知，需要核对；不能承诺跨系统 exactly-once。服务重启不恢复执行；用户主动发起新运行仍可能重复写入，需自行核对或由 CLI 提供幂等键。
