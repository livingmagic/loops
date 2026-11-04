import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from loops_runtime.agent_plugin_loader import BUILTINS, load_plugins
from loops_runtime.agent_clients import CLIENTS
from loops_runtime.bridge import Service


class AgentPluginTests(unittest.IsolatedAsyncioTestCase):
    def install_fixture(self, directory):
        folder = Path(directory)/'fixture'
        shutil.copytree(Path(__file__).parent/'fixtures/agent_plugin', folder)
        client = folder/'client'
        client.write_text('#!' + sys.executable + '\n' + (folder/'client.py').read_text())
        client.chmod(0o755)
        return folder

    async def test_drop_in_plugin_discovers_initializes_executes_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            installed = Path(root)/'plugins'
            self.install_fixture(installed)
            plugins = load_plugins([installed])
            self.assertEqual(list(plugins), ['fixture-agent'])
            with patch.dict(CLIENTS, plugins, clear=True):
                service = Service(Path(root)/'data', lambda e: None, workspace_root=Path(root)/'workspaces')
                try:
                    environment = await service.handle('environment', {})
                    client = environment['agents'][0]
                    self.assertTrue(client['ready'])
                    self.assertEqual((client['defaultModel'], client['defaultReasoning']), ('fixture-model', 'low'))
                    self.assertIsNone(environment['codex'])
                    loop = await service.handle('new', {})
                    self.assertEqual(loop['config']['defaultAgentId'], 'fixture-agent')
                    definition = {'schemaVersion': 2, 'title': 'Plugin test', 'goal': 'test fixture', 'completion': 'report exists',
                        'inputs': [], 'scripts': {}, 'finalOutputs': ['${steps.report.outputs.result}'], 'steps': [{
                            'id': 'report', 'label': 'Report', 'instructions': 'fixture task', 'inputs': {},
                            'execution': {'tool': 'agent', 'mode': 'read-only', 'responseOutput': 'result'},
                            'outputs': [{'id': 'result', 'label': 'Report', 'path': 'report.txt'}], 'checks': []}]}
                    await service.handle('definition', {'loopId': loop['id'], 'definition': definition})
                    await service.handle('save', {'loopId': loop['id']})
                    run = await service.handle('start', {'loopId': loop['id']})
                    await service.runner.tasks[run['id']]
                    result = service.store.run(loop['id'], run['id'])
                    self.assertEqual(result['status'], 'success', result.get('reason'))
                    self.assertEqual(Path(result['artifacts'][0]['path']).read_text().strip(), 'test fixture output: fixture-model/low')
                    self.assertEqual(result['agentBindings']['fixture-agent']['pluginVersion'], '1.0.0')
                finally:
                    await service.handle('shutdown', {})
                    service.store.db.close()
                restarted = Service(Path(root)/'data', lambda e: None)
                try:
                    state = (await restarted.handle('agent_clients', {}))[0]
                    self.assertEqual(state['defaultModel'], 'fixture-model')
                    self.assertEqual(restarted.store.get(loop['id'])['runs'][0]['status'], 'success')
                finally:
                    await restarted.handle('shutdown', {})
                    restarted.store.db.close()

    async def test_invalid_duplicate_and_broken_plugins_do_not_disable_valid_plugins(self):
        with tempfile.TemporaryDirectory() as root:
            folder = self.install_fixture(root)
            duplicate = Path(root)/'duplicate'
            shutil.copytree(folder, duplicate)
            bad = Path(root)/'unsupported'; shutil.copytree(folder, bad)
            manifest = json.loads((bad/'manifest.json').read_text()); manifest['apiVersion'] = 9
            (bad/'manifest.json').write_text(json.dumps(manifest))
            broken = Path(root)/'broken'; shutil.copytree(folder, broken)
            manifest['apiVersion'] = 1; manifest['id'] = 'broken'
            (broken/'manifest.json').write_text(json.dumps(manifest))
            (broken/'plugin.py').write_text('raise RuntimeError("private credential must not leak")')
            errors = []
            registry = load_plugins([root], errors=errors)
            self.assertEqual(set(registry), {'fixture-agent'})
            self.assertEqual(len(errors), 3)
            self.assertNotIn('private credential', json.dumps(errors))

    async def test_bundled_clients_use_same_plugin_contract(self):
        errors = []
        plugins = load_plugins([BUILTINS], errors=errors)
        self.assertEqual(set(plugins), {'codex', 'mimo'})
        self.assertEqual(errors, [])
        self.assertTrue(all(p['apiVersion'] == 1 for p in plugins.values()))

    async def test_production_loader_finds_installed_plugin_without_registry_changes(self):
        import asyncio
        import os
        with tempfile.TemporaryDirectory() as home:
            self.install_fixture(Path(home)/'.config/loops/agent-plugins')
            env = {**os.environ, 'HOME': home, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            process = await asyncio.create_subprocess_exec(sys.executable, '-c',
                'import json; from loops_runtime.agent_clients import CLIENTS; print(json.dumps(sorted(CLIENTS)))',
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(json.loads(stdout), ['codex', 'fixture-agent', 'mimo'])
