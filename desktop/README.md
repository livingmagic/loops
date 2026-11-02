# Loops Mac 客户端

正式代码位于本目录与 `../runtime/`；历史原型不包含在公开源码中。界面保持左侧 Loop 导航、中间对话与单次执行、右侧流程和运行记录。

## 当前能力与边界

- Electron + React 实际客户端，提供本机输入文件选择、试跑、停止、保存、历史结果与交付文件查看。
- 左右栏可拖动边缘调整宽度，双击恢复该栏默认宽度。切换 Loop 和重启后保留调整；缩小窗口时自动给对话区保留空间。聚焦分隔线后可用左右方向键微调，Shift 加快，Home/End 到达宽度边界，Enter 恢复默认。
- 新建使用独立创建页：居中引导、底部需求输入，输入与发送共用工作台组件；首次发送非空需求后才创建 Loop 和工作目录并进入对话与流程工作台，启动或反复点击新建不创建记录。工作目录统一默认在 ~/Loops/ 下创建，每个 Loop 使用独立子目录，不提供手动选择入口；历史目录不迁移。已有 Loop 在标题左侧显示目录图标，移入标题即时显示工作目录，移开即隐藏；键盘聚焦同样可查看，Esc 可关闭提示。点击标题打开目录。
- 左侧 Loop 的更多菜单支持重命名和删除，回收站支持恢复。重命名不改历史版本，后续 AI 更新保留员工指定的名称。删除保留定义、脚本、对话、版本、历史运行和交付文件，不清理工作目录或原始输入；运行中或 Loops AI 处理时不能删除。
- Loops AI 使用 Pi Agent Core 1.4.0 和 DeepSeek。通过工具检查项目、读取文件、生成或修改流程、读取失败证据、启动试跑和保存。
- 新流程使用通用结构化配置，自由决定步骤数量。每步保存任务、输入映射、执行配置、产出、完成条件和失败处理。展开步骤查看固定配置，运行命令在对应运行记录中查看。
- 支持 Codex、Python 脚本和直接命令。Codex 使用官方 `openai-codex==0.163.0`，相同工作目录与 session 可共享会话；Python 从统一上下文接收本步骤输入与产出路径。脚本源码与配置一起保存，详见 `../runtime/WORKFLOWS.md`。
- 每次运行固定配置、脚本和输入副本，使用独立工作目录。选用 Git 资料时建立独立 Git 工作区；普通流程不要求仓库。完成条件依据实际文件与命令退出码检查，失败按配置重试或返回指定步骤，达到上限保留失败证据。工程与业务流程统一使用通用执行器，旧七步定义不再支持，需重新搭建；历史文件和记录不自动删除。
- 保存与执行成功独立。保存时归档定义、资源配置、脚本与版本；历史运行继续保留原版本。重启后未完成执行标为中断，不自动重放外部提交。
- 结果来自真实 SDK、进程退出码、测试日志与文件，没有前端定时器推进或预设成功结果。

历史验证已覆盖本机 Codex 修复、真实 Pi + DeepSeek 自然语言搭建、通用流程试跑与保存恢复。手工流程、模型替身和真实服务结果分别记录；公开摘要见 [验收结论](qa/acceptance.md)，详细运行证据仅在本机保留。

**远程交付**：通用流程可通过明确配置的脚本和命令执行外部操作，不再内置工程专用的提交、GitHub 草稿 PR 与同输入 PR 复验路径。历史样例仅交付本地文件，没有创建或验收真实 PR。自动化任务现已支持 Python 定时与监听、多个已保存 Loop 串联及输出投递，仍没有云端常驻调度或发布使用端。

## 自动化任务

左侧“自动化任务”可以新建任务，也可从已保存 Loop 顶部的“自动化”进入。新建页与 Loop 共用对话输入组件；首次发送需求后创建独立任务，Loops AI 持续追问、搭建和调整，右侧同步展示流程。对话、草稿和模型会话均持久化，刷新或重启后可继续。输入映射和 Python 脚本放在可选的“流程配置”中查看和修改。定时、目录监听、webhook 都由输入脚本实现；平台管理生命周期、持久化队列、版本快照与回执。

可以通过对话要求保存、采样、试跑、启用或暂停，也可使用对应按钮。仅搭建或调整时不自动保存、启用或投递；试跑使用固定版本和真实输入。保存后也可先“测试输入”，再“运行样本”验证整个链路与真实投递；已有 Loop 产出后可单独试投递指定输出。支持启用、暂停、停止单次运行、核对后继续、删除与恢复。失败或不确定的投递不会声称成功；成功节点与目标在重试中保留。重启恢复来源和未开始队列，不自动重放中断的外部操作。

脚本接口、版本及本机连接配置见 [执行约定](../runtime/AUTOMATIONS.md)。每版脚本使用独立标准库 venv，不自动安装第三方依赖。关闭窗口继续后台运行，彻底退出或电脑休眠期间不执行；外部 webhook 需要可达入口。

自动化验证范围见 [验收结论](qa/acceptance.md)。`node scripts/live-automation-chat.mjs` 验证真实多轮对话、修改、保存、试跑、交付与重启继续，会消耗模型额度。`node scripts/live-automation-journey.mjs` 使用隔离目录、真实 Python 和本机真实 Pi + DeepSeek 验证采集、串联、投递与重启，会消耗模型额度。示例中的两个 Python Loop 为手工测试定义，不代表 Codex 或 AI 业务分析能力的新增验收。

双击 `scripts/open-latest.command` 可打开当前打包客户端，使用正常 Application Support 数据与项目现有本机模型配置，不进入验收数据库。

## 模型与本机配置

Loops AI 使用 DeepSeek 密钥，Codex 使用本机 Codex 登录，两者独立。当前验收使用项目根目录 `.env.local` 的配置，不复制密钥。

开发版先读取 `desktop/.env.local`；该文件不存在时兼容项目根目录 `.env.local`。字段参照 `.env.example`：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`（默认 `deepseek-flash`）。每次对话重新读取配置；不用把密钥发到聊天或写入源码。`.env.local` 已被忽略；设置页显示连接状态，密钥未配置时显示配置文件位置。

打包应用默认从其 Application Support 数据目录读取 `.env.local`，设置页显示实际位置。开发调试可通过 `LOOPS_CONFIG_DIR` 指向配置目录。配置不会通过客户端状态或执行子进程传出。

## 本地开发

开发需要 Node.js 22.19+、Python 3.13。Git 资料需要 Git，使用 Codex 的步骤需要本机已登录 Codex。测试样例只使用 Python 标准库。正式项目需要自行准备其项目依赖；执行器不会自动安装任意依赖。

从 `runtime/` 执行：

```sh
sh setup.sh
.venv/bin/python -m unittest discover -s tests -v
```

从 `desktop/` 执行：

```sh
npm ci
npm run dev
```

`npm run dev` 启动原生 Mac 窗口。`npm run dev:browser` 单独启动用于界面检查的本地开发入口（4174端口）；浏览器与 Mac 默认数据目录不同。浏览器入口不提供原生文件选择与 Finder 打开目录。

开发运行数据在 `desktop/.local/`，正式应用默认使用 macOS Application Support。每个 Loop 自动创建独立工作目录：`draft/`、`versions/<版本>/`、`runs/<运行ID>/`。每次运行保留输入副本、定义、脚本、工作区、事件、步骤结果和实际检查记录，具体交付文件由流程定义决定。运行目录应保留，以便复查和更新已有 PR。

## 检查和真实试跑

```sh
# desktop/
npm test
npm run build
node scripts/live-generic-journey.mjs sales
node scripts/live-generic-journey.mjs engineering
```

`live-generic-journey.mjs` 使用真实 Pi、DeepSeek、Codex 与本地样例检查搭建、执行、失败反馈、业务反馈、换输入再执行和保存，会消耗模型额度。它可从保存的阶段继续，当前记录在 `.local/generic-acceptance-20261030`，不复用旧格式的验收状态。`sales` 仅运行 Python，`engineering` 调用真实 Codex，需在具备本机执行权限的环境中运行。两种样例统一使用 resources、inputDefaults 和按 ID 映射的运行输入。

可复用脚本与证据目录见 [验收说明](qa/README.md)。依赖旧验收数据库的一次性启动、修复和复查脚本已移入本机归档，不作为公开执行入口。

## 打包 Mac 应用

在 `runtime/` 下用 uv 准备可随应用携带的 Python：

```sh
uv python install 3.13 --install-dir .python --no-bin
```

然后从 `desktop/` 执行：

```sh
npm run build
npm run package:mac
```

输出为 `out/Loops-darwin-arm64/Loops.app`（当前机器为 Apple Silicon）。应用携带 Python、SDK 与固定 Codex 运行时。打包脚本从 requirements.lock 重新安装 Python 依赖，不复制开发虚拟环境；仅纳入客户端运行目录，并生成第三方许可清单。`Contents/Resources/legal/` 保存项目与依赖声明。当前为本机开发构建，尚未做开发者签名、公证、安装器与自动更新；第三方二进制声明核对尚未完成，不能作为公开安装包分发，见 [第三方组件清单](../THIRD_PARTY.md)。

可用 `LOOPS_PACKAGE_OUT` 指定隔离输出目录。打包后在 `desktop/` 执行 `node qa/check-release-package.mjs /absolute/path/to/Loops.app`，可核对包内依赖与许可文件，并使用手工定义的 Python 流程验证执行、保存和重启恢复；不调用模型，不代表真实 AI 对话验收。

应用图标采用蓝底白色立体带状双回环，Dock、对话头像与网页标签共用 `public/assets/loops-icon.png`，Mac 打包使用 `assets/loops.icns`。设计源稿保存在 `design/app-icon/`；修改源稿后，在本目录执行 `swift scripts/export-icon.swift` 重新导出资源，再重新构建、打包。`design/drafts/icon-v2/` 是未采用的扁平几何尝试。

本版本依赖本机登录及项目授权，未承诺对不可信仓库提供完整的进程、网络和凭证隔离；Codex 使用其 workspace-write 沙箱和自动审批审查，项目测试命令由本地执行器按约定启动。面向组织部署前还需要完善权限配置和外部操作恢复。
