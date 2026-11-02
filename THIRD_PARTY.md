# 第三方组件与素材

Loops 自有代码采用 Apache-2.0，Copyright 2026 livingmagic。下列组件保留各自许可证，不因项目许可证而重新授权。

| 组件 | 版本来源 | 许可证及随包位置 |
| --- | --- | --- |
| Pi Agent、Pi AI、Pi Telemetry | desktop/package-lock.json，1.4.0 | MIT；desktop/licenses/pi-LICENSE.txt |
| React、Phosphor、Markdown 渲染及其他 npm 组件 | desktop/package-lock.json | 按每个包的元数据及许可文本生成 npm-notices.txt |
| Electron / Chromium | desktop/package-lock.json，45.0.0 | Electron LICENSE 与 LICENSES.chromium.html |
| Codex Python SDK / CLI | runtime/requirements.lock，0.163.0 | Apache-2.0；保留上游 LICENSE 和 NOTICE |
| Python | 固定 Python 3.13.12 / 20260303，arm64 | PSF 及原生依赖随附许可；desktop/licenses/python-notices.txt |
| Pydantic 等 Python 依赖 | runtime/requirements.lock | 保留各发行包 dist-info/licenses；包含 MIT、Apache/BSD、PSF 等许可 |

`desktop/licenses/sources.json` 记录补充许可文本的上游地址。npm 清单生成器读取锁文件及实际安装包，不把开发依赖列为应用运行依赖。Python 打包只安装锁文件中的发行包，保留其许可文件。应用内许可材料位于 `Contents/Resources/legal/`，Python 发行包许可位于 `Contents/Resources/runtime/python/lib/python3.13/site-packages/`。

## 仍需完成的二进制分发核对

- Python 上游随附的许可文本合并保存在 desktop/licenses/python-notices.txt，随安装包携带。原始分文件、组件映射和逐文件指纹只作本机核对资料，不进入源码。打包仍使用校验下载哈希的固定发行版。
- `proxy-agent-negotiate@1.1.0` 的 npm 元数据声明 MIT，但包内缺少许可文本，公开仓库暂未找到对应包的许可证。生成器保留其声明并列入未完成清单，不将其视为已完成署名核对。
- Codex 和 code-mode-host 已与官方发行包核对，ripgrep 许可已补齐。Codex 静态依赖的完整声明仍需向上游核实；详细核对过程仅在本机保留。

## 图像资源

- 正式蓝色 Loops 图标有 image_gen 生成记录，见 `desktop/design/app-icon/README.md`。正式 PNG 和 ICNS 从该源稿导出。
- 历史原型和参考图不进入公开源码；原型人物画面不再属于发布素材。
- 旧紫色 `loops-ai.png` 已从客户端和本机原型源文件中删除。公开客户端使用正式蓝色图标。
