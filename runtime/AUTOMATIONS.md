# 自动化任务

自动化任务独立于 Loop。输入 Python 产生事件，执行器保存输入并排队，按顺序执行一个或多个已保存的 Loop，最后逐个调用输出 Python。没有平台定时节点；定时、目录监听和 webhook 都是输入脚本。执行状态以 SQLite 与运行文件为准。

## 对话与草稿

首次发送非空需求后，Host 创建 revision=0 的对话任务。打开新建页或反复点击新建不创建记录。用户消息、助手回复、模型工具会话与 draftSpec 分别由执行层持久化；缺少来源或目标时可以先继续对话，不要求先提交完整配置。

`automation_update_draft` 校验后保存完整草稿，保留现有保存版本及历史记录；`automation_save` 将草稿固定为新版本并清理待保存标记。未保存或有新草稿时，采样、运行及启用会被代码拒绝，避免错误地执行旧版本。输入脚本在草稿校验时要求 run(ctx) 与 sample(ctx)。sample(ctx) 必须调用 emit 提交真实样本。

Pi Agent 根据完整历史与动态当前状态使用查看、更新草稿、保存、采样、启动运行、查询结果、启停工具。工具状态和副作用由执行器负责，对话权限依据用户本轮语义决定，不通过自然语言关键词分支判断。启用或投递不由草稿更新触发。停止回复会中止模型请求，保留已有消息和已完成修改；模型回复期间禁止并发发送、手工保存或删除同一任务。

## 定义与版本

通过 `automation_catalog` 读取可选 Loop 的实际保存版本及输入、最终产出。`automation_save` 创建任务或在暂停时保存新版，立即校验 Python 语法、节点顺序、引用及输入类型。每版保存完整脚本、参数和 Loop 快照。运行按接收时的任务版本执行，不使用后来编辑的 Loop 草稿。

任务右侧每个 Loop 可选择已保存版本。启用期间先暂停；选择后生成待保存草稿，保存任务才应用到新输入。新保存的 Loop 版本不会自动升级已有任务。`automation_set_loop_version` 接收 `{taskId, nodeId, revision}`，检查完整链路；若输入或下游产出映射不兼容，保留原配置并返回原因，可通过对话同步调整。已排队或执行中的运行、失败后继续的运行仍使用接收时快照。运行记录分别显示任务版本与各 Loop 的实际版本。

```json
{
  "title": "报告交付",
  "source": {"lifecycle": "once", "script": "完整 Python 源码", "config": {"path": "/path/input.txt"}},
  "nodes": [
    {"id": "prepare", "loopId": "保存的 Loop ID", "revision": 1, "bindings": {"source": "input.source"}},
    {"id": "report", "loopId": "另一 Loop ID", "revision": 1, "bindings": {"source": "prepare.convert.report"}}
  ],
  "outputs": [
    {"id": "local", "title": "本地报告", "script": "完整 Python 源码", "config": {"directory": "/path/output"}, "bindings": {"report": "report.convert.report"}}
  ],
  "policy": {"queueLimit": 100, "scriptTimeout": 180, "sourceRestarts": 3}
}
```

`input.FIELD` 引用输入脚本字段；`NODE.STEP.OUTPUT` 只能引用前序 Loop 在 finalOutputs 中声明的文件。名称由界面显示，引用使用稳定 ID。Loop 文件产出映射到文件输入；结构转换使用 Loop 内的 Python 步骤。第一版为单输入脚本、多 Loop 顺序链路、多输出目标，不包含条件分支、并行汇合或循环连线。

## Python 接口

定义 `run(ctx)`。输入另定义 `sample(ctx)`，一次采集一份真实样本，不等待定时或监听事件。接口为同步调用，也可在 async 函数内使用；不要 await 同步 ctx 方法。普通 print 只记日志，不会被解释为输入或投递成功。

- `ctx.config`：本版参数，不含凭证明文。
- `ctx.workspace`：当前脚本工作目录。
- `ctx.checkpoint`：来源最后已确认接收的进度。
- `ctx.file(path)`、`ctx.text(value)`：构造文件或文本输入。
- `ctx.emit(event_key, inputs, checkpoint=None)`：输入持久化并入队后返回 `{runId, duplicate}`。不是整条任务执行完成的确认。
- `ctx.inputs`：输出脚本已映射的值；文件类型为绝对路径，文本类型为正文。
- `ctx.inputEvidence`：输入的 kind、路径或文本、文件哈希等信息。
- `ctx.read_text(name)`、`ctx.read_bytes(name)`：根据输入 kind 读取真实内容，避免将文件路径当正文。
- `ctx.delivery_key`：当前运行和输出目标的稳定标识，同次运行重试不变。独立试投递和新运行会产生新标识。
- `ctx.receipt(target=..., status='delivered'|'accepted', **details)`：保存真实投递回执；成功退出却无回执时记为待核对。
- `ctx.log(message)`：记录日志。
- `ctx.request(url, connection=None, method='GET', body=None, headers={})`：主进程代为发起 HTTP(S) 请求，返回 status 和 body。脚本检查 HTTP 与业务结果后才确认回执。

输入示例：

```python
from pathlib import Path
import hashlib

def sample(ctx):
    path = Path(ctx.config['path'])
    key = hashlib.sha256(path.read_bytes()).hexdigest()
    ctx.emit(key, {'source': ctx.file(path)}, checkpoint={'last': key})

def run(ctx):
    sample(ctx)
```

输出示例：

```python
from pathlib import Path
import hashlib

def run(ctx):
    directory = Path(ctx.config['directory'])
    directory.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(ctx.delivery_key.encode()).hexdigest() + '.txt'
    target = directory / filename
    target.write_text(ctx.read_text('report'))
    ctx.receipt(target=str(target.resolve()))
```

来源配置 lifecycle 为 once 或 service。once 正常退出表示采集结束；service 退出后按次数上限和退避策略重启，失败信息保留。交互式 sample 最多 20 秒；长期 service 不套用普通脚本超时。原有 Loop 执行仍沿用其现有时长限制。

每版脚本使用独立 venv，不继承系统或用户第三方包，环境清单记录 Python 版本。第一版提供 Python 标准库及上述上下文接口，不自动安装第三方依赖。脚本是用户授权的本机代码；venv 和最小环境变量用于依赖、凭证隔离，不是操作系统文件权限沙箱。

## 接收与执行

输入文件最多 10MB，文本最多 10 万字符；拒绝将凭证文件作为输入。输入文件及未显式映射的默认输入先复制、计算哈希，再将事件、运行和采集进度一并提交 SQLite。事件标识在同一任务内唯一；重复且内容相同返回原 runId，内容不同则拒绝并解释冲突。队列满时不确认接收、不推进进度。无效或拒收输入不遗留临时快照。

任务内顺序排队。不同任务可以并行，同一 Loop 的手动运行和自动化运行互斥，占用时等待而非报失败。暂停后不启动新的排队运行，已执行的任务继续。手动运行样本可以在暂停状态执行。运行前及恢复时检查输入和缓存产出的文件哈希，防止使用已修改或已删除的证据。

每个 Loop 节点链接独立真实 Loop run；上下游只传数据，不共享可修改工作区。停止自动化运行会取消它创建的子运行和脚本进程组，不取消占用中的其他手动运行。脚本监测父执行服务退出，避免孤儿监听进程长期占用端口。

## 失败和管理

- 任务：启用、暂停、保存新版、删除与恢复。任务被引用的 Loop 不能直接删除。
- 输入测试：只采样，不执行 Loop 或输出。样本可预览，随后明确发起真实运行。
- 输出测试：已有完整 Loop 产出后，可以仅试投递指定目标；不重复执行 Loop，也不触碰其他输出。链路变化后先重新试跑。
- 节点失败：保留成功节点、子运行 ID、尝试次数和所有回执。继续前要求用户核对已发生的外部操作。
- 多目标投递：成功目标跳过，失败目标单独继续；不重新执行成功的上游。
- 结果不确定：输出无回执、网络中断或进程停止时不声称成功。回执已持久化后脚本再异常退出，不重放已经确认的投递。
- 重启：恢复已启用来源和未开始队列；在途运行标为中断，不自动重放外部写入。恢复监听不等于补齐离线时的全部事件，取决于脚本与来源的补查能力。
- 删除：先停止来源；运行或采样中不允许删除。移入回收站，保留版本、队列、记录和文件，恢复后保持暂停。

关闭 Mac 窗口保留后台 Host；彻底退出、关机或休眠期间不执行。webhook 模板默认绑定 127.0.0.1，外部请求需要可达入口；不会自行开启公网通道。

## 本机连接

在执行服务数据目录的 `connections.local.json` 配置连接。例如：

```json
{"docs":{"hosts":["docs.example.com"],"headers":{"Authorization":"Bearer 在本机填写"}}}
```

此文件不得入库。脚本只传连接名，主进程核对完整目标 host（含非默认端口）并附加鉴权；不将明文注入子进程、模型、页面状态或上下文。响应和日志中匹配本机连接凭证的内容会隐藏。禁止带用户名密码的 URL，不携带鉴权跟随跳转。请求直接连接目标，不继承系统代理；超时 30 秒、响应体最多 10MB。

自然语言搭建使用现有 Pi + DeepSeek 的持久多轮会话；缺少关键来源或目标信息时询问。搭建和调整默认只更新草稿，用户明确要求后才保存、试跑或启用。仍须用真实样本验证业务内容，回执只能证明目标接收，不能自动证明业务内容正确。
