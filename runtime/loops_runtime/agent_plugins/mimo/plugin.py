import re
from loops_runtime.agent_plugin_api import cli_version
from .adapter import MimoSession as Session
from .catalog import mimo_catalog as catalog


async def probe(binary, capture):
    state = {'version': await cli_version(binary, capture)}
    # This command lists credential metadata only, never credential values.
    listing = await capture(binary, 'providers', 'list')
    clean = re.sub(r'\x1b\[[0-9;]*m', '', listing)
    state['ready'] = bool(re.search(r'[1-9]\d* credentials?\b', clean))
    state['authenticated'] = state['ready']
    state['detail'] = '已配置本地凭证' if state['ready'] else '请先打开 MiMo，运行 /connect 登录。'
    return state
