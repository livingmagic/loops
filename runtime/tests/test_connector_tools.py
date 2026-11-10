import asyncio
import copy
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from loops_runtime.connector_tools import ManagedTools, TOOLS, ToolInstallError
from loops_runtime.connector_presets import presets
from loops_runtime.connectors import Connectors
from test_connector_onboarding import Vault as MemoryVault


def archive(content=b'#!/bin/sh\necho "lark-cli 1.0.96"\n', kind=tarfile.REGTYPE):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as package:
        item = tarfile.TarInfo('lark-cli')
        item.type = kind
        item.size = len(content) if kind == tarfile.REGTYPE else 0
        item.linkname = '/etc/passwd' if kind != tarfile.REGTYPE else ''
        package.addfile(item, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    return raw.getvalue()


class InstallerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tools = ManagedTools(self.root)
        self.tools.target = 'darwin-arm64'
        self.payload = archive()
        self.downloads = 0
        async def download(url, destination, progress=None):
            self.downloads += 1
            destination.write_bytes(self.payload)
        self.tools.download = download
        target = {**TOOLS['feishu']['targets']['darwin-arm64'], 'sha256': hashlib.sha256(self.payload).hexdigest()}
        self.patch = patch.dict(TOOLS['feishu']['targets'], {'darwin-arm64': target})
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    async def test_no_node_install_verified_cache_and_corruption_repair(self):
        with patch.dict(os.environ, {'PATH': '/no-node-or-npm'}):
            binary = await self.tools.ensure('feishu')
            self.assertTrue(self.tools.ready('feishu'))
            self.assertEqual(await self.tools.ensure('feishu'), binary)
            self.assertEqual(self.downloads, 1)
            binary.write_text('corrupt partial executable')
            await self.tools.ensure('feishu')
            self.assertTrue(self.tools.ready('feishu'))
            self.assertEqual(self.downloads, 2)
        self.assertFalse(list(binary.parent.glob('.prepare-*')))

    async def test_checksum_failure_never_installs_and_retry_recovers(self):
        good = self.payload
        self.payload = archive(b'not the reviewed archive')
        with self.assertRaisesRegex(ToolInstallError, '校验'):
            await self.tools.ensure('feishu')
        self.assertFalse(self.tools.path('feishu').exists())
        self.payload = good
        await self.tools.ensure('feishu')
        self.assertTrue(self.tools.ready('feishu'))

    async def test_zip_and_native_binary_installation_without_runtime_dependencies(self):
        for key, kind in [('github', 'zip'), ('tapd', 'binary')]:
            content = ('#!/bin/sh\necho ' + TOOLS[key]['version'] + '\n').encode()
            target = {**TOOLS[key]['targets']['darwin-arm64']}
            if kind == 'zip':
                raw = io.BytesIO()
                with zipfile.ZipFile(raw, 'w') as package: package.writestr(target['member'], content)
                self.payload = raw.getvalue()
            else:
                self.payload = content
            target['sha256'] = hashlib.sha256(self.payload).hexdigest()
            with patch.dict(TOOLS[key]['targets'], {'darwin-arm64': target}), patch.dict(os.environ, {'PATH': '/no-node-or-npm'}):
                await self.tools.ensure(key)
                self.assertTrue(self.tools.ready(key))

    async def test_zip_link_is_rejected(self):
        raw = io.BytesIO()
        target = {**TOOLS['github']['targets']['darwin-arm64']}
        with zipfile.ZipFile(raw, 'w') as package:
            item = zipfile.ZipInfo(target['member']); item.create_system = 3
            item.external_attr = 0o120777 << 16
            package.writestr(item, '/etc/passwd')
        self.payload = raw.getvalue(); target['sha256'] = hashlib.sha256(self.payload).hexdigest()
        with patch.dict(TOOLS['github']['targets'], {'darwin-arm64': target}):
            with self.assertRaisesRegex(ToolInstallError, '安装包'): await self.tools.ensure('github')
        self.assertFalse(self.tools.path('github').exists())

    async def test_archive_link_is_not_extracted(self):
        self.payload = archive(kind=tarfile.SYMTYPE)
        TOOLS['feishu']['targets']['darwin-arm64']['sha256'] = hashlib.sha256(self.payload).hexdigest()
        with self.assertRaisesRegex(ToolInstallError, '安装包'):
            await self.tools.ensure('feishu')
        self.assertFalse(self.tools.path('feishu').exists())

    async def test_cancel_during_download_leaves_no_partial_install(self):
        started = asyncio.Event()
        async def download(url, destination, progress=None):
            destination.write_bytes(b'incomplete')
            started.set()
            await asyncio.Event().wait()
        self.tools.download = download
        task = asyncio.create_task(self.tools.ensure('feishu'))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.tools.path('feishu').exists())
        self.assertFalse(list(self.tools.path('feishu').parent.glob('.prepare-*')))


class BuiltinConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_existing_config_is_not_overwritten_by_onboarding(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = Connectors(Path(directory), lambda event: None, vault=MemoryVault())
            broker.save(next(m for m in presets() if m['id'] == 'feishu'))
            broker.cli_login = AsyncMock()
            try:
                for error in [{'type': 'config', 'subtype': 'invalid_config'},
                              {'type': 'config', 'subtype': 'not_configured', 'field': 'currentApp'}]:
                    broker.process = AsyncMock(return_value=(1, '', json.dumps({'ok': False, 'error': error})))
                    with self.assertRaisesRegex(RuntimeError, '无法检查'):
                        await broker.prepare_feishu('feishu')
                broker.cli_login.assert_not_called()
            finally:
                await broker.close()

    async def test_one_connect_prepares_before_first_app_setup_and_auth_without_node(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            broker = Connectors(root, lambda event: events.append(copy.deepcopy(broker.list())), vault=MemoryVault())
            try:
                broker.save(next(m for m in presets() if m['id'] == 'feishu'), {'env': {'CONNECTOR_FIXTURE_ROOT': str(root)}})
                environment = broker.environment
                broker.environment = lambda key: {**environment(key), 'PATH': '/no-node-or-npm'}
                calls = 0
                async def ensure(key, progress=None):
                    nonlocal calls
                    calls += 1
                    await asyncio.sleep(.02)
                    target = broker.tools.path(key)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fixture = (Path(__file__).parent / 'fixtures/builtin_connector.py').read_text()
                    target.write_text('#!' + sys.executable + '\n' + fixture)
                    target.chmod(0o700)
                    return target
                broker.tools.ensure = ensure
                await broker.connect('feishu')
                first = broker.jobs['feishu']
                await broker.connect('feishu')
                self.assertIs(first, broker.jobs['feishu'])
                await first
                self.assertEqual(calls, 1)
                self.assertTrue((root / 'app').exists())
                self.assertTrue((root / 'login').exists())
                self.assertEqual(broker.entry('feishu')['status'], 'connected', broker.public('feishu'))
                self.assertTrue(broker.public('feishu')['builtin'])
                self.assertTrue(any('正在准备' in item[0]['message'] for item in events))
                self.assertFalse(broker.auth_views.get('feishu'))
            finally:
                await broker.close()

    async def test_prepare_error_is_retryable_and_never_starts_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = Connectors(Path(directory), lambda event: None, vault=MemoryVault())
            try:
                broker.save(next(m for m in presets() if m['id'] == 'feishu'))
                async def fail(key, progress=None):
                    raise ToolInstallError('飞书连接准备未完成，请检查网络后重新连接。')
                broker.tools.ensure = fail
                await broker.connect('feishu')
                await broker.jobs['feishu']
                self.assertEqual(broker.entry('feishu')['status'], 'error')
                self.assertIn('重新连接', broker.entry('feishu')['message'])
                self.assertIsNone(broker.auth_views.get('feishu'))
                self.assertFalse(broker.entry('feishu')['enabled'])
            finally:
                await broker.close()
