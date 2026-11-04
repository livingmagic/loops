import asyncio
import json
import os
import re
import tomllib
from pathlib import Path
from openai_codex import AsyncCodex, CodexConfig
from ...environment import command_environment

def codex_config():
    path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return {**data, **data.get('profiles', {}).get(data.get('profile'), {})}


async def codex_catalog(binary, capture, *, include_defaults=True):
    async with AsyncCodex(CodexConfig(codex_bin=binary, env=command_environment(), client_name='loops')) as client:
        response = await asyncio.wait_for(client.models(), 12)
    data = response.model_dump(mode='json', by_alias=True)
    config = codex_config() if include_defaults else {}
    return {'models': [{'id': m['model'], 'name': m['displayName'],
                        'reasoningLevels': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                        'defaultReasoning': m.get('defaultReasoningEffort')}
                       for m in data['data'] if not m.get('hidden')],
            'nativeModel': (config.get('model') or next((m['model'] for m in data['data'] if m.get('isDefault')), None)) if include_defaults else None, 'nativeReasoning': config.get('model_reasoning_effort')}
