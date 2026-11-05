# Loops

用自然语言搭建、试跑和复用本机工作流。

Loops 是一个早期 Mac 桌面客户端：描述任务，让 AI 生成流程，查看真实运行结果，再通过对话调整并保存。自动化任务可以定时或监听输入，串联已保存的 Loop 并投递结果。

项目采用 [Apache-2.0](LICENSE) 许可证；第三方组件遵循各自许可证。Copyright 2026 livingmagic，见 [NOTICE](NOTICE)。首版仅提供源码；安装包的第三方声明核对仍待完成。

## 能做什么

- 使用 Pi Agent + DeepSeek，通过对话创建和修改流程。
- 运行 Python、直接命令或官方 Codex Python SDK，保留实际退出码、文件和历史记录。
- 固定每次运行的流程版本与输入，保存后复用，重启后查看历史。
- 用自动化任务采集输入、执行多个 Loop、投递结果；彻底退出应用或电脑休眠时不执行。

公开源码包含 `desktop/` 正式客户端和 `runtime/` 本地执行层。历史交互原型仅在本机保留，不随源码发布。当前验收包含本机真实模型及 Codex 运行，但没有验收真实 GitHub PR 交付。

## 快速开始

当前开发与打包面向 Apple Silicon Mac，需要 Node.js 22.19+、Python 3.13 和 Git。下载 [源码预览版](https://github.com/livingmagic/loops/releases) 或克隆后安装：

```sh
git clone https://github.com/livingmagic/loops.git
cd loops/runtime
sh setup.sh
cd ../desktop
npm ci
```

在 `desktop/` 启动客户端：

```sh
npm run dev
```

打开“设置 → 模型”，添加模型并填写服务商密钥，再选择默认模型。同一服务商的模型共用密钥，自定义兼容服务按 API 地址区分。密钥由 macOS 安全存储加密，仅保存在本机。模型对话需要自己的服务额度。使用 Codex 步骤还需提前完成本机 Codex 登录及相应服务授权，两者独立；仅运行 Python 的流程不需要 Codex 步骤。不要把密钥输入对话或提交到仓库。

第一次可以输入：“创建一个文本处理流程，每次读取我提供的文本文件，转成大写并输出新文件，用 Python 执行。”准备一份不含敏感信息的文本，按界面提示搭建和试跑，检查输出后再保存。该示例会调用真实 DeepSeek，不保证每次生成完全相同的流程。

若提示本地执行环境未安装，检查 `runtime/setup.sh` 是否成功；若模型未连接，在“设置 → 模型”检查服务商密钥。进一步说明见 [客户端文档](desktop/README.md)。

安装脚本默认从官方 PyPI 安装锁定依赖，避免本机镜像缺少 Codex SDK；如需使用组织内完整镜像，可在安装时设置 `LOOPS_PIP_INDEX_URL`。

## 数据与执行权限

开发数据及工作目录保存在 `desktop/.local/`；打包验收通过 `desktop/scripts/open-latest.command` 使用 `qa/desktop-client/` 及其 `workspaces/`。已安装的正式应用统一使用 `~/Library/Application Support/Loops/`，数据库、配置和自动化文件位于此目录，流程工作文件位于其 `workspaces/` 下。运行会保留输入副本、生成脚本、模型输出和日志；分享验收材料前请先脱敏。

输入及工具读取的内容可能发送给 DeepSeek 或 Codex 服务。只使用有权提供的资料，并自行确认服务使用要求与费用。

Python 和直接命令以当前用户权限执行，可以访问本机文件及网络；独立工作目录、venv 和环境变量过滤都不等于系统沙箱。Codex 步骤使用其自身沙箱与审批机制。只运行可信流程，启用自动化和真实外部投递前先检查脚本及目标。详见 [安全说明](SECURITY.md)。

## 开发与验证

```sh
# 仓库根目录；无需真实模型密钥
(cd runtime && .venv/bin/python -m unittest discover -s tests -v)
(cd desktop && npm test && npm run build)
```

真实模型验收需单独运行，会消耗额度，见 [客户端验收说明](desktop/README.md#检查和真实试跑)。自动测试通过不代表真实模型、安装包或远程交付已通过验收。

## 项目导航

- [贡献指南](CONTRIBUTING.md)：开发检查、目录约定和提交流程。
- [第三方组件与素材](THIRD_PARTY.md)：许可来源及随包声明。
- [流程接口](runtime/WORKFLOWS.md) · [自动化接口](runtime/AUTOMATIONS.md)。
- [客户端与打包](desktop/README.md)。
- [开发与测试](desktop/README.md#检查和真实试跑) · [Mac 分发](desktop/MAC_RELEASE.md)。

依赖、凭证、本机数据、安装包及整个 `qa/` 临时目录不进入版本库。
