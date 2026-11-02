# Mac 安装包签名与公证

当前源码预览版不含安装包。本机已安装 Xcode、codesign、notarytool 和 stapler，但钥匙串中没有有效的代码签名身份；现有 Loops.app 的主程序是 ad-hoc 签名，尚未使用 Developer ID，也不代表 Hardened Runtime 或公证通过。

## 维护者需要准备

1. 可用于 Developer ID 分发的 Apple Developer Program 团队，并确认 Team ID。
2. 本机钥匙串中的 **Developer ID Application** 证书及对应私钥。仅有 .cer 公钥不够；可在本机生成 CSR 后由团队签发，或从已有开发机器安全导入带私钥的 .p12。不要把私钥、.p12 密码或账号密码发送到聊天或提交到 Git。
3. 公证凭证，以下选一种并存入本机钥匙串：Apple ID + Team ID + App 专用密码；或有公证权限的 App Store Connect API key。配置完成后，只需提供钥匙串 profile 名称。

现有 bundle ID 是 com.loops.desktop。签名前确认发布团队和此标识；对站外分发 .app / .dmg 使用 Developer ID Application。只有另外发布 .pkg 安装器时才需要 Developer ID Installer。本轮不需要 Mac App Store 发布配置。

## 在本机配置公证

使用 Apple ID 时，交互输入密码，避免放进命令历史：

```sh
xcrun notarytool store-credentials loops-notary \
  --apple-id YOUR_APPLE_ID --team-id YOUR_TEAM_ID
```

API key 的 .p8 文件仅在本机安全保存，按 notarytool store-credentials --help 配置；团队 key 需要 Key ID 和 Issuer ID，个人 key 的参数要求可能不同。不要把文件内容粘贴到对话。可以只告知 profile 名称，不必交出凭证值。

## 实际发布顺序

- 先完成 THIRD_PARTY.md 中的许可阻塞，构建全新包并验证未签名的功能；本机归档的旧包不直接上传。
- 完成 Python 路径重定位、资源复制和所有生成文件后再签名。当前打包脚本会在 packager 返回后调整 Python 符号链接，不能在该操作前签完名。
- 按从内到外顺序签署 Electron Helper、Python、扩展模块、动态库、Codex、code-mode-host、ripgrep 和主应用。签名需要安全时间戳及 Hardened Runtime；不能只签最外层应用。
- Electron / V8 可能需要 allow-jit；使用当前实现所需的最小 entitlements，不直接采用包含相机、麦克风、蓝牙等权限的通用默认模板。具体 Python 扩展加载和 Codex 子进程需在签名后实测。
- codesign --verify --deep --strict --verbose=2 检查完整包，再压缩并通过 notarytool submit --keychain-profile loops-notary --wait 提交。
- 只有返回 Accepted 才进行 stapler staple、stapler validate 和 spctl --assess --type execute，最后在另一台干净 Mac 验证下载后的首次启动、Python/Codex 执行及重启。
- 签名后不要再更改包内文件。公证会把待发布二进制上传至 Apple；当前没有上传或申请签发任何证书。

官方参考：[Developer ID](https://developer.apple.com/developer-id/)、[Electron 公证工具](https://github.com/electron/notarize)。
