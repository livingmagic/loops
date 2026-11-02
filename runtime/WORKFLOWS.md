# 通用本地流程

正式流程统一使用 schemaVersion=2。步骤数量与标识由 Loops AI 根据目标生成，界面和执行器均读取同一份定义。工程修复与业务处理使用同一个执行器，不再提供固定七步流程、专属阶段 Prompt 或自动提交和创建 PR 的执行分支。开发期旧定义需要重新搭建，已有文件与运行记录不自动删除或迁移。

定义包含 title、goal、completion、inputs、finalOutputs、scripts、steps。每个步骤包含 id、label、instructions、inputs、execution、outputs、checks、onFailure。Python 脚本源码保存在 scripts 字典，键为 scripts/name.py；保存版本和启动运行时同时复制脚本。输入文件在启动时复制并记录哈希，运行中的配置不随员工反馈变化。

finalOutputs 是必填的最终交付清单，每项为 `${steps.步骤ID.outputs.产出ID}`，引用已经声明的节点产出。Loops AI 根据员工目标选择，可以引用任意步骤的产出；无文件交付时明确使用空数组。错误引用与重复引用会被拒绝，已存储但缺少清单的定义也不能启动。界面的“输入和输出”和交付文件都以此为准，中间文件与执行记录保留在执行详情。校验仍由各节点执行，不增加独立总体验收。

引用支持 `${inputs.ID}`、`${resources.ID}`、`${steps.ID.outputs.ID}`、`${workspace}`、`${failure}`、`${checks}`。前序输出必须先成功产生才能被引用；任务指令中的引用也会展开。固定资料统一使用 config.resources，默认输入使用 config.inputDefaults，启动时按输入 ID 提供 inputs 对象。普通流程使用独立空工作区，Git 资料通过独立 worktree 提供可修改的 workspace；resources 始终指向原始资料。

执行工具：

- codex：官方 Python SDK；相同 cwd 和 session 共享会话。mode 为 write 或 read-only。responseOutput 可指定一个本步骤产出 ID，由执行器把最终回复保存为该文件。
- python：运行本版本 scripts 中的脚本，args 可选。脚本从 LOOPS_CONTEXT 环境变量读取 JSON。
- command：直接运行程序和参数数组。cwd 默认本次工作区，必须位于该工作区内。

LOOPS_CONTEXT 包含 inputs（本步骤已映射输入）、outputs（本步骤产出 ID 到绝对路径）、workspace、resources、failure、checks（实际执行的命令、退出码和输出）。脚本不应自行猜测上一步输出或写死本机路径。

每个声明产出必须是工作区内的实际文件。checks 支持文件存在检查和命令检查，命令退出码默认要求 0，可设置 expectedExitCode=1 验证预期失败。产出缺失或检查不通过时不会标记步骤成功。

onFailure.action 可为 stop、retry、goto；goto 使用 target 指定步骤，maxAttempts 限制自动纠正次数。纠正携带真实失败信息，重新运行相应步骤并清理失效的下游结果；此前已完成步骤共享的文件保留。达到上限时终止并保存失败记录。进程取消会结束子进程组；重启只标记未完成运行中断，不自动重放。

本版本提供顺序步骤与有次数上限的失败回退。工具调用按定义执行；步骤完成条件应反映真实业务验收。模型生成的脚本仍需通过真实试跑验证，保存不等于已经验证。

Python、直接命令和完成检查仅继承 PATH、HOME、临时目录、语言、时区及基础系统变量，另加 LOOPS_CONTEXT；不自动传递主进程中的 API 密钥、代理设置、PYTHONPATH 或其他自定义环境变量。依赖这些变量的旧脚本需要调整。该限制不隔离文件访问或网络，Codex 仍按自身登录与沙箱机制运行。
