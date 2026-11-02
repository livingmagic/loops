# Loops 图标

白色连续带状图形形成双回环，表达工作流程的衔接、反复运行与复用。蓝色沿用正式客户端的主色，轻微折叠阴影突出连接关系。

- `loops-app-icon.png`：白底图标设计稿。
- `drafts/loops-app-icon-transparent-draft.png`：透明背景初稿，边缘仍有杂点，暂不用于正式打包。
- `prompt.txt`：初次生成使用的完整提示词。
- `cleanup-prompt.txt`：透明边缘清理使用的完整提示词。
- `presentation-prompt.txt`：白底设计稿使用的完整提示词。

使用内置 image_gen 工具生成。客户端选用白底稿导出生产资源：导出时仅移除与画布边界相连的白色背景，保留蓝底内部的白色回环。

生产 PNG 位于 `../../public/assets/loops-icon.png`，Mac 图标位于 `../../assets/loops.icns`。Dock、AI 头像、网页标签与 Mac 打包均已引用。

客户端采用本目录的立体带状设计。`../drafts/icon-v2/` 的扁平几何方案未采用。执行 `swift scripts/export-icon.swift` 可重新导出本版生产资源。
