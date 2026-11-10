# 参与 Loops

先阅读 [快速开始](README.md#快速开始) 和所在目录的 `AGENTS.md`。项目采用 [Apache-2.0](LICENSE) 许可证。提交贡献前，请确认你有权按该许可证提供相关代码或资料。

## 本地检查

Node.js 22.19+、Python 3.13；先执行 `runtime/setup.sh` 和 `desktop/` 下的 `npm ci`。客户端部分测试会启动 Python，不能省略执行层安装。

常规检查：

```sh
(cd runtime && .venv/bin/python -m unittest discover -s tests -v)
(cd desktop && npm test && npm run build)
```

与 PR 门禁一致的检查，从仓库根目录执行：

```sh
runtime/.venv/bin/python -m pip install -r runtime/requirements-dev.txt
runtime/.venv/bin/python -m unittest discover -s scripts/tests -v
runtime/.venv/bin/python scripts/run-coverage.py
runtime/.venv/bin/python scripts/check-coverage.py --base origin/main
(cd desktop && npm run build && npm run test:ui)
# Apple Silicon 上验证同一次构建的正式安装包：
sh runtime/setup-bundled-python.sh
(cd desktop && npm run package:mac && npm run test:package)
```

覆盖率采集会运行两套现有测试，临时安装启动钩子采集 Python 子进程，并在退出时移除钩子；不要在同一虚拟环境中并行执行两次采集。若进程被强制终止，下次执行会提示如何处理残留钩子。

## PR 质量门禁

PR 必须通过 `coverage`、`desktop-ui`、`secrets`，且分支保持与最新 main 同步。

- `coverage`：现有 Python、Node 行为和跨进程集成测试、依赖审计，以及总体行/分支覆盖率和 PR 改动行检查。
- `desktop-ui`：在 Apple Silicon runner 上只构建一次。先执行加载恢复、异步加载、模型连接与设置关闭的界面测试（明确的后端替身），再通过正式 preload/IPC、真实 Host 和 Python 验证手写草稿的试跑、保存、结果查看、历史版本脚本、删除恢复和页面重载。原生 Agent 发现被禁用，测试不会读取本机账号。随后打包同一构建，验证锁定依赖、内置流程、真实执行和重启后的保存恢复。所有验收数据及工作目录隔离。
- `secrets`：检查历史中是否泄露密钥。

覆盖率唯一数值来源是 [coverage-policy.json](.github/coverage-policy.json)。Python 与 JavaScript 分别计算总体行和分支覆盖率，门槛基于首次完整采集结果固定；各语言本次新增/修改的可执行行还需达到 `changedLines`。没有可执行行变更时，该项不适用。首次安装门禁的 PR 建立基线；之后 PR 不能降低已存在的门槛或变更采集范围来绕过检查。

统计范围：Python 为 `runtime/loops_runtime/**/*.py`；JavaScript 为 `desktop/host/**/*.mjs` 和 `desktop/src/**/*.js`、`*.mjs`。完全未加载的范围内文件仍计入分母；测试、依赖、产物和原型不在范围内。React JSX 和 Electron 入口目前由构建及界面回归保障，不计入上述百分比。采集缺失或源文件记录缺失会直接失败。

核心执行、版本保存恢复、密钥和共享授权的行为断言仍需维护，覆盖率通过不替代这些检查。失败时先看 CI 的文件/行号提示，按需下载保留 7 天的覆盖率诊断附件；补有效测试或修复行为，不为凑数编写无效断言。阈值或统计范围的调整需要单独解释原因和风险，不能与业务改动混在一起悄悄放宽。

普通自动测试不需要真实模型账号。所有测试隔离数据库、配置与工作目录，不复用已安装客户端数据。真实模型、外部投递、正式安装升级与操作系统权限验收由维护者在受控环境中单独执行；不要在来自外部的 PR 中运行带凭证的任务。

## 提交变更

一个 PR 聚焦一个问题，说明原行为、修改后的行为、验证方式和未验证部分。修复执行或安全问题时补充能复现问题的测试。不要把模型替身、模拟页面或本地补丁称为真实模型、远程 PR 验收。

正式客户端代码在 `desktop/`，执行层在 `runtime/`；历史原型仅在本机归档，不进入公开仓库。长期自动测试放在 `tests/`，开发、构建和发布工具放在 `scripts/`。`qa/` 整体忽略，仅用于本机临时检查，无需整理或提交报告。保留他人正在进行的改动，仅提交本次相关文件。

报告普通问题时提供系统架构、运行方式、复现步骤、预期与实际结果及脱敏错误信息。不要上传 `.env.local`、数据库、完整对话、账号信息或未经检查的运行目录。安全问题遵循 [安全说明](SECURITY.md)。
