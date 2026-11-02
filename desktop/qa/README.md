# 验收与发布检查

本目录公开保留可复用入口和简短结论。应用运行不依赖此目录，常规自动测试放在 `../tests/` 和 `../../runtime/tests/`。

- [历史验收结论](acceptance.md)：真实模型、手工流程与界面验证的范围。
- [开源发布检查](open-source-release.md)：已验证事项和剩余发布条件。
- [安装包检查脚本](check-release-package.mjs)：验证包内依赖、实际 Python 执行、保存与重启恢复；不调用模型。
- [公开源码导出脚本](export-public-source.mjs)：生成不含本机资料、原型或 Git 历史的源码快照。

## 可复用真实服务验收

以下命令从 `desktop/` 执行，需要本机模型配置并消耗额度。脚本自行准备样例与运行记录，不依赖归档中的旧运行 ID。

| 命令 | 范围 |
| --- | --- |
| `node scripts/live-generic-journey.mjs sales` | Pi + DeepSeek 生成 Python 流程，试跑、反馈修改与保存 |
| `node scripts/live-generic-journey.mjs engineering` | Pi + DeepSeek 与真实 Codex 的本地研发流程；需 Codex 登录 |
| `node scripts/live-automation-journey.mjs` | 手工 Python Loop、真实自动化采集、串联、投递及重启 |
| `node scripts/live-automation-chat.mjs` | 真实模型多轮创建、修改、保存、试跑和重启续聊 |
| `node scripts/check-chat-trigger-contract.mjs` | 模型生成的触发契约检查，不代表已等待完整定时周期 |

自动化验收新证据分别写到 `qa/artifacts/live-automation/` 和 `qa/artifacts/live-automation-chat/`。通用流程运行快照保存在脚本自身的 `.local/` 数据目录。需要全新验收时使用隔离源码目录，不复用旧运行数据。

## 本机归档

详细人工记录、供应商请求 ID、截图、日志、JSON 快照及一次性修复脚本放在被忽略的 `artifacts/`。整理前的报告和一次性脚本原文保存在 `artifacts/internal-history/`，其 manifest.json 记录原路径及文件哈希。旧证据目录和工作区不迁移或删除；归档脚本不是当前受维护的执行入口。

公开文档链接到本目录中的结论，不链接仅本机存在的证据。新增记录遵循目录约定，无需按文件名或日期追加忽略规则。
