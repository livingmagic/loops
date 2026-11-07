# Plugin brand assets

These assets identify third-party products in the plugin page, slash picker and message chips. They are copied unchanged from first-party sources, without recoloring or redrawing. Product names and marks belong to their respective owners; inclusion does not imply endorsement. The application source license does not relicense these brand assets.

| File | First-party source |
| --- | --- |
| `codex.png` | `icon-codex-light.png` from the installed OpenAI desktop app, `/Applications/ChatGPT.app/Contents/Resources/`; verified macOS signer `OpenAI OpCo, LLC (2DC432GLL2)`. This is the Codex-specific asset, not the ChatGPT icon. |
| `mimo.ico` | Favicon referenced by https://mimo.xiaomi.com/ |
| `feishu-official.png` | Product image referenced by the official https://www.feishu.cn/ homepage (`og:image`). |
| `feishu-project.png` | Favicon referenced by the official https://project.feishu.cn/ homepage. |
| `wecom.png` | 48px favicon referenced by https://work.weixin.qq.com/ |
| `tapd.png` | Favicon referenced by https://www.tapd.cn/ |
| `tencent-docs.ico` | Favicon referenced by https://docs.qq.com/ |
| `github.svg` | `GitHub_Invertocat_Black.svg` from https://brand.github.com/GitHub_Logos.zip, linked by https://brand.github.com/foundations/logo |

Exact download URLs, retrieval date, original file SHA256, and the OpenAI app version are recorded in `sources.json`. No runtime requests to these websites are needed.

Custom local test plugins have no official brand asset and use a generated initial mark. Do not substitute generated lettering or generic library pictograms for known product logos.

Legacy `feishu.svg` (IconPark, Apache-2.0) and `wecom.svg` (Ant Design Icons, MIT) and their original licenses are retained for attribution of prior revisions; the current plugin component uses the official assets listed above.
