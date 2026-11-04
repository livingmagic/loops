"""Codex: SDK-matched runtime, native login, model catalog and sessions."""
import os
from pathlib import Path
from loops_runtime.agent_plugin_api import find_executable, cli_version
from .adapter import CodexSession as Session, probe_codex
from .catalog import codex_catalog as catalog


def locate(manifest):
    override = os.environ.get('LOOPS_CODEX_BIN')
    if override:
        return override if Path(override).is_file() and os.access(override, os.X_OK) else None
    try:
        from codex_cli_bin import bundled_codex_path
        bundled = bundled_codex_path()
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
    except (ImportError, OSError):
        pass
    return find_executable(manifest, extra_paths=[
        '/Applications/Codex.app/Contents/Resources/codex',
        str(Path.home() / 'Applications/Codex.app/Contents/Resources/codex')])


async def probe(binary, capture):
    version = await cli_version(binary, capture)
    return {**await probe_codex(binary), 'version': version}
