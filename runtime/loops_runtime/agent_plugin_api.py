"""Shared utilities for Agent plugin API v1; no client-specific behavior."""
import asyncio
import os
import re
import shutil
from pathlib import Path
from .environment import command_environment


def find_executable(manifest, *, extra_paths=()):
    command = manifest['command']
    candidates = [shutil.which(command), *(str(Path.home() / p) for p in manifest.get('paths', [])),
                  f'/opt/homebrew/bin/{command}', f'/usr/local/bin/{command}', *extra_paths]
    return next((str(p) for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)


async def capture(binary, *args):
    process = await asyncio.create_subprocess_exec(binary, *args, cwd=str(Path.home()), env=command_environment(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 5)
        if process.returncode:
            raise ValueError('客户端检查失败，请打开客户端检查登录状态。')
        return stdout.decode('utf-8', 'replace')
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def cli_version(binary, capture):
    value = await capture(binary, '--version')
    match = re.search(r'\d+\.\d+\.\d+(?:[-+][\w.-]+)?', value)
    if not match:
        raise ValueError('无法识别客户端版本，请更新客户端。')
    return match[0]
