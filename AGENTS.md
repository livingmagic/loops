# Loops 项目约定

用通俗易懂的中文输出最终结果，出现问题时要解释清楚。

Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining.

本文件所在目录是项目根目录，用于整个 Loops 项目。

- 公开源码包含 `desktop/` 正式客户端与 `runtime/` 本地执行层。
- `prototype/` 是本机保留的历史原型，不跟踪、不打包、不上传；不把模拟演示当成真实执行能力。
- 公开仓库为 `livingmagic/loops`；改动通过 PR 和 CI 合并，不推送本机历史中的原型与内部资料。

## 文件与版本管理

- `qa/` 是本机临时目录，整体忽略，不提交；可直接存放探索脚本、人工验收、截图和日志，无需定期整理、归档或维护报告。
- 长期维护的自动测试放在 `tests/`，开发、构建和发布工具放在 `scripts/`。临时检查只有需要持续防止问题复发时，才整理为正式测试。
- `design/` 默认保留采用的设计源稿与参考图；未采用方案放入 `drafts/`，临时导出放入 `exports/`。应用运行和构建所需的正式图片、图标继续保存在 `public/`、`assets/` 并提交。
- 新增测试或设计沿用以上目录约定，不在 `.gitignore` 中按日期、版本或文件名维护白名单。本机凭证、Loop 数据和构建产物继续忽略，不因临时验收而迁移或删除。

## 正式客户端

- 正式 Mac 客户端在 `desktop/`，本地执行器在 `runtime/`，不要混入原型目录。
- Loops AI 基于 Pi Agent，模型使用 DeepSeek；Codex 任务使用官方 Python SDK。模型凭证仅从本机配置读取，不能出现在版本库、页面状态、日志或模型工具结果中。
- 本地样例可以验证真实 Codex 修复与测试，但手工测试定义、模型替身和无远程仓库的补丁交付都必须如实标注，不能当成 DeepSeek 完整对话或真实 PR 已通过验收。
