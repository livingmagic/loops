# Loops 项目约定

用通俗易懂的中文输出最终结果，出现问题时要解释清楚。

Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining.

本文件所在目录是项目根目录，用于整个 Loops 项目。

- 公开源码包含 `desktop/` 正式客户端与 `runtime/` 本地执行层。
- `prototype/` 是本机保留的历史原型，不跟踪、不打包、不上传；不把模拟演示当成真实执行能力。
- 首次公开发布使用 `desktop/qa/export-public-source.mjs` 导出的源码快照建立新仓库，不能直接推送含历史原型的本机 Git 历史。

## 文件与版本管理

- `qa/` 放可复用验收脚本和简短结论；截图、日志、运行快照、样例工作区及一次性排查材料统一放在其下的 `artifacts/`，仅保存在本机。常规自动测试放在 `tests/`。
- `design/` 默认保留采用的设计源稿与参考图；未采用方案放入 `drafts/`，临时导出放入 `exports/`。应用运行和构建所需的正式图片、图标继续保存在 `public/`、`assets/` 并提交。
- 新增测试或设计沿用以上目录约定，不在 `.gitignore` 中按日期、版本或文件名维护白名单。本机凭证、Loop 数据和构建产物继续忽略，不因整理验收材料而迁移或删除。

## 正式客户端

- 正式 Mac 客户端在 `desktop/`，本地执行器在 `runtime/`，不要混入原型目录。
- Loops AI 基于 Pi Agent，模型使用 DeepSeek；Codex 任务使用官方 Python SDK。模型凭证仅从本机配置读取，不能出现在版本库、页面状态、日志或模型工具结果中。
- 本地样例可以验证真实 Codex 修复与测试，但手工测试定义、模型替身和无远程仓库的补丁交付都必须如实标注，不能当成 DeepSeek 完整对话或真实 PR 已通过验收。
