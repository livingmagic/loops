"""Test-only plugin using a deterministic CLI subprocess; no model capability claim."""
from pathlib import Path
from loops_runtime.agent_plugin_api import capture, cli_version
from .helpers import MODELS


def locate(manifest):
    return str(Path(__file__).with_name('client'))


async def probe(binary, capture):
    return {'version': await cli_version(binary, capture), 'ready': (await capture(binary, 'status')).strip() == 'ready', 'detail': 'test fixture'}


async def catalog(binary, capture, *, include_defaults=True):
    return {'models': MODELS, 'nativeModel': None, 'nativeReasoning': None}


class Session:
    def __init__(self, cwd, emit, *, binary, read_paths=(), model=None, reasoning=None):
        self.binary, self.emit, self.model, self.reasoning = binary, emit, model, reasoning

    async def __aenter__(self):
        self.emit('session', 'test fixture session', threadId='fixture-session')
        return self

    async def __aexit__(self, *args):
        pass

    async def ask(self, prompt, *, readonly=False):
        assert readonly
        return await capture(self.binary, 'ask', self.model, self.reasoning)
