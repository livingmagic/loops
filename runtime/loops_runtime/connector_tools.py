"""Pinned vendor executables; no Node/npm, shell, or machine-wide installation."""
import asyncio
import base64
import hashlib
import json
import os
import platform
import re
import signal
import tarfile
import tempfile
import zipfile
from pathlib import Path

import httpx
from .connector_presets import preset


class ToolInstallError(RuntimeError):
    pass


# Pin vendor release digests / npm integrity, or hashes of reviewed official assets.
# Updating a tool is a reviewed code change, never an unpinned 'latest' install.
TOOLS = {
    'feishu': {'name': '飞书', 'binary': 'lark-cli', 'package': '@larksuite/cli', 'version': '1.0.96', 'targets': {
        'darwin-arm64': {'url': 'https://github.com/larksuite/cli/releases/download/v1.0.96/lark-cli-1.0.96-darwin-arm64.tar.gz',
            'sha256': '124acdf380f72fa4b1b1e8307a0b00ee93217cf9558baca5bc40091304f2adcd', 'member': 'lark-cli'},
        'darwin-x64': {'url': 'https://github.com/larksuite/cli/releases/download/v1.0.96/lark-cli-1.0.96-darwin-amd64.tar.gz',
            'sha256': 'f6bd28263dfc4a4d6c2581657efa6af8e8f92eba22394c9c4d840d4ad1fae65e', 'member': 'lark-cli'},
    }},
    'wecom': {'name': '企业微信', 'binary': 'wecom-cli', 'package': '@wecom/cli', 'version': '1.3.4', 'targets': {
        'darwin-arm64': {'url': 'https://registry.npmjs.org/@wecom/cli-darwin-arm64/-/cli-darwin-arm64-1.3.4.tgz',
            'sha512': 'mktTGWYWqwE5exPs/vdiYcwas77G7DPEhAmj1Hl0LqQHUp6tfwplSfJQ/TaESZvmKtoqDglrywtIQeJ0ksFsWA==', 'member': 'package/bin/wecom-cli'},
        'darwin-x64': {'url': 'https://registry.npmjs.org/@wecom/cli-darwin-x64/-/cli-darwin-x64-1.3.4.tgz',
            'sha512': 'w9TuS95c06uyqbylxNjBCtC3GvR/6Vf6WK1zvsIcif7ivUq6Gu5scssagbD6rBWwhQUJ6o2MQKufpa439mL8bg==', 'member': 'package/bin/wecom-cli'},
    }},
    'github': {'name': 'GitHub', 'binary': 'gh', 'package': 'gh', 'version': '2.103.3', 'targets': {
        'darwin-arm64': {'url': 'https://github.com/cli/cli/releases/download/v2.103.3/gh_2.103.3_macOS_arm64.zip',
            'sha256': 'd700207eae9ae98fe2b89535c2c087c07823d4628c56364695a5a07ef311c062', 'format': 'zip', 'member': 'gh_2.103.3_macOS_arm64/bin/gh'},
        'darwin-x64': {'url': 'https://github.com/cli/cli/releases/download/v2.103.3/gh_2.103.3_macOS_amd64.zip',
            'sha256': 'a2f86418f4350ae6e9ec60d5000bdf7665728436946a97fc27fe50e6ae752682', 'format': 'zip', 'member': 'gh_2.103.3_macOS_amd64/bin/gh'},
    }},
    'tapd': {'name': 'TAPD', 'binary': 'tapd-cli', 'package': 'tapd-cli', 'version': '1.16.1', 'targets': {
        'darwin-arm64': {'url': 'https://cnb.cool/tapd.cn/skills/tapd-cli/-/releases/download/v1.16.1/tapd-cli-darwin-arm64',
            'sha256': 'b0e41c22327eaed65f1e8f29674a57295e1550f5529818346cad85c1856cf689', 'format': 'binary'},
        'darwin-x64': {'url': 'https://cnb.cool/tapd.cn/skills/tapd-cli/-/releases/download/v1.16.1/tapd-cli-darwin-x64',
            'sha256': '965a231291c448242534dca279e8fe9dc18d1480c061027bc033504c453cecb2', 'format': 'binary'},
    }},
}


def builtin(manifest):
    key = manifest.get('provider')
    return key if key in TOOLS and preset(manifest) else None


class ManagedTools:
    def __init__(self, root):
        self.root = Path(root) / 'connector-tools' / 'native'
        machine = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}.get(platform.machine(), platform.machine())
        self.target = platform.system().lower() + '-' + machine

    def path(self, key):
        tool = TOOLS[key]
        return self.root / key / tool['version'] / self.target / tool['binary']

    def ready(self, key):
        binary = self.path(key)
        try:
            receipt = json.loads(binary.with_suffix('.json').read_text())
            return (binary.is_file() and not binary.is_symlink() and os.access(binary, os.X_OK)
                    and receipt['version'] == TOOLS[key]['version']
                    and hashlib.sha256(binary.read_bytes()).hexdigest() == receipt['sha256'])
        except (OSError, ValueError, KeyError):
            return False

    async def download(self, url, destination):
        total = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=True, trust_env=False) as client:
            async with client.stream('GET', url) as response:
                response.raise_for_status()
                with destination.open('wb') as output:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > 100_000_000:
                            raise ToolInstallError('连接工具下载内容过大，请稍后重新连接。')
                        output.write(chunk)

    async def ensure(self, key):
        if self.ready(key):
            return self.path(key)
        tool = TOOLS[key]
        target = tool['targets'].get(self.target)
        if not target:
            raise ToolInstallError('当前系统暂不支持此连接器。')
        binary = self.path(key)
        binary.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with asyncio.timeout(180):
                with tempfile.TemporaryDirectory(prefix='.prepare-', dir=binary.parent) as temporary:
                    work = Path(temporary)
                    archive = work / 'download.tar.gz'
                    await self.download(target['url'], archive)
                    raw = archive.read_bytes()
                    valid = (hashlib.sha256(raw).hexdigest() == target['sha256']) if 'sha256' in target else (base64.b64encode(hashlib.sha512(raw).digest()).decode() == target['sha512'])
                    if not valid:
                        raise ToolInstallError('连接工具校验未通过，请重新连接以重新下载。')
                    candidate = work / tool['binary']
                    self.extract(archive, candidate, target)
                    candidate.chmod(0o700)
                    await self.verify(candidate, tool['version'])
                    receipt = {'version': tool['version'], 'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()}
                    metadata = work / 'receipt.json'
                    metadata.write_text(json.dumps(receipt))
                    metadata.chmod(0o600)
                    candidate.replace(binary)
                    metadata.replace(binary.with_suffix('.json'))
            return binary
        except (asyncio.CancelledError, ToolInstallError):
            raise
        except Exception as error:
            raise ToolInstallError(f"{tool['name']}连接准备未完成，请检查网络后重新连接。") from error

    @staticmethod
    def extract(archive, candidate, target):
        # Only write the selected regular file; archive paths are never extracted.
        if target.get('format') == 'binary':
            candidate.write_bytes(archive.read_bytes())
            return
        if target.get('format') == 'zip':
            with zipfile.ZipFile(archive) as contents:
                matches = [m for m in contents.infolist() if m.filename.removeprefix('./') == target['member']]
                if (len(matches) != 1 or matches[0].is_dir() or matches[0].file_size > 100_000_000
                    or (matches[0].external_attr >> 16) & 0o170000 not in {0, 0o100000}):
                    raise ToolInstallError('连接工具安装包无效，请重新连接。')
                with contents.open(matches[0]) as source, candidate.open('wb') as output:
                    while chunk := source.read(65536):
                        output.write(chunk)
            return
        with tarfile.open(archive, 'r:gz') as contents:
            matches = [m for m in contents.getmembers() if m.name.removeprefix('./') == target['member']]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 100_000_000:
                raise ToolInstallError('连接工具安装包无效，请重新连接。')
            with contents.extractfile(matches[0]) as source, candidate.open('wb') as output:
                while chunk := source.read(65536):
                    output.write(chunk)

    async def verify(self, binary, version):
        process = await asyncio.create_subprocess_exec(str(binary), '--version',
            env={'PATH': '/usr/bin:/bin'}, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 10)
            if process.returncode or not re.search(r'(?<![\d.])' + re.escape(version) + r'(?![\d.])', stdout.decode(errors='replace')):
                raise ToolInstallError('连接工具无法运行，请重新连接。')
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
