"""Official connector endpoints and authentication, without business operation maps."""


def presets():
    return [
        {"id": "feishu", "name": "飞书", "type": "cli", "version": "1", "provider": "feishu", "identity": "user",
         "command": ["lark-cli"], "package": "@larksuite/cli", "auth": {
             "mode": "cli", "status": ["auth", "status", "--json", "--verify"],
             "login": ["auth", "login", "--domain", "docs,im,drive,wiki", "--scope", "offline_access"],
             "logout": ["auth", "logout"]},
         },
        {"id": "feishu-project", "name": "飞书项目", "description": "查询、创建和更新飞书项目中的数据", "type": "mcp", "version": "1", "provider": "feishu-project",
         "transport": "http", "url": "https://project.feishu.cn/mcp_server/v1", "auth": {"mode": "oauth"}},
        {"id": "wecom", "name": "企业微信", "type": "cli", "version": "1", "provider": "wecom",
         "command": ["wecom-cli"], "package": "@wecom/cli", "auth": {
             "mode": "cli", "status": ["auth", "show", "--status"],
             "login": ["auth", "init", "--noninteractive", "--no-browser", "--output-qrcode", "${qrcode}"]},
        },
        {"id": "tapd", "name": "TAPD", "description": "读取项目需求与缺陷，更新任务和评论", "type": "mcp", "version": "2", "provider": "tapd",
         "transport": "http", "url": "https://websocket.tapd.cn/mcp/mcp", "auth": {"mode": "oauth", "scope": "user"}},
        {"id": "tencent-docs", "name": "腾讯文档", "description": "查找和读取在线文档，创建与更新文档和表格", "type": "mcp", "version": "1", "provider": "tencent-docs",
         "transport": "http", "url": "https://docs.qq.com/openapi/mcp", "auth": {"mode": "oauth", "scope": "docs:read docs:write"}},
        {"id": "github", "name": "GitHub", "description": "读取代码仓库和 Issue，创建及更新 Issue 与 PR", "type": "mcp", "version": "1", "provider": "github",
         "transport": "http", "url": "https://api.githubcopilot.com/mcp/", "command": ["gh"], "package": "gh", "auth": {
             "mode": "cli", "status": ["auth", "status", "--active", "--hostname", "github.com", "--json", "hosts"],
             "login": ["auth", "login", "--web", "--hostname", "github.com", "--git-protocol", "https", "--skip-ssh-key", "--expiring"]}},
    ]


def preset(manifest):
    """Only canonical connection/authentication fields can use built-in privileges."""
    for candidate in presets():
        if manifest.get('id') == candidate['id'] and manifest.get('provider') == candidate['provider']:
            legacy = candidate['id'] in {'feishu', 'wecom'}
            fields = ('type', 'package', 'url', 'transport', 'auth', *(() if legacy else ('command',)))
            if all(manifest.get(field) == candidate.get(field) for field in fields) and (legacy or 'operations' not in manifest):
                return candidate
    return None


TOKEN_PAGES = {
    'tapd': 'https://www.tapd.cn/personal_settings/index?tab=personal_token',
}
