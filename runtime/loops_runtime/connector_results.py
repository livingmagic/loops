"""Transport decoding shared by CLI and MCP, without service field mappings."""
import json
from urllib.parse import urlparse


RESPONSE_CONTRACT = {
    'result': '原始 CLI JSON 或 MCP CallToolResult，完整保留。',
    'payload': '业务返回：CLI 原始 JSON；MCP 优先 structuredContent，否则解码单个 JSON 文本块；多个内容块保留列表。不展开业务字段。服务 outputSchema 描述此层，但动态透传 Schema 不保证具体字段。',
    'links': '从实际 payload 中收集的完整 HTTP(S) 字符串，格式 [{path: JSON Pointer, url: 绝对链接}]；// 链接补 https:。只是候选链接，不代表业务成功或已送达。',
    'receipt': '先检查真实业务错误及目标，再确认回执。新建资源可从实际 links 选择对应资源链接，不假定服务动态 Schema 的字段。缺少确认依据时停止，不重复写入。',
}


def mcp_payload(result):
    if result.get('structuredContent') is not None:
        return result['structuredContent']
    decoded = []
    for part in result.get('content', []):
        if part.get('type') == 'text':
            try:
                decoded.append(json.loads(part['text']))
            except (ValueError, TypeError):
                decoded.append(part['text'])
        else:
            decoded.append(part)
    return decoded[0] if len(decoded) == 1 else decoded


def connector_response(key, operation, result, *, mcp=False):
    payload = mcp_payload(result) if mcp else result
    links = []
    def visit(value, path=''):
        if isinstance(value, dict):
            for name, item in value.items():
                visit(item, path + '/' + str(name).replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + '/' + str(index))
        elif isinstance(value, str):
            url = 'https:' + value if value.startswith('//') else value
            try:
                parsed = urlparse(url)
            except ValueError:
                return
            if (parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username
                    and not parsed.password and not any(char.isspace() for char in url)):
                links.append({'path': path, 'url': url})
    visit(payload)
    return {'connectorId': key, 'operation': operation, 'result': result, 'payload': payload, 'links': links}
