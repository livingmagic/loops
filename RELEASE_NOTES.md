# v0.1.0-preview.1 · 源码预览版

Loops 提供通过自然语言搭建和复用本机工作流的 Mac 客户端。支持 Python、直接命令和 Codex 步骤，以及定时或监听输入的自动化任务。

本次仅提供源码，未提供可下载安装包。使用方式见 README，需要自己的 DeepSeek 配置；Codex 步骤另需本机登录及相应授权。项目采用 Apache-2.0，Copyright 2026 livingmagic。

## 当前边界

- 主要验证平台为 macOS Apple Silicon，其他平台尚未验收。
- 历史原型及设计参考不包含在公开源码中。
- Python 和命令以当前用户权限运行，仅适用于可信流程；没有完整系统隔离。
- 彻底退出应用或电脑休眠期间不执行自动化任务。
- 尚未验收真实远程 PR 交付，不包含云端常驻调度。
- 本机开发安装包没有签名、公证和自动更新；第三方二进制声明尚待核对，暂不作为发行附件。

## 本地验证

本地 20 项客户端测试、49 项 Python 执行层测试及客户端构建通过；真实模型与手工流程的验证边界见 [验收结论](https://github.com/livingmagic/loops/blob/v0.1.0-preview.1/desktop/qa/acceptance.md)。发布提交的自动检查见 [GitHub Actions](https://github.com/livingmagic/loops/actions)。公开源码不包含历史原型、内部验收材料和旧图标。
