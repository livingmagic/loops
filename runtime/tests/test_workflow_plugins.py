"""Real local CLI process and workflow persistence, with a hand-written fixture."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from loops_runtime.bridge import Service


class PluginWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_plugin_contract_and_typed_data_survive_save_and_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = Service(root / 'state', lambda event: None, workspace_root=root / 'workspaces')
            try:
                manifest = {'id': 'echo', 'name': 'Echo CLI', 'type': 'cli',
                    'command': [sys.executable, '-c', 'import sys; print(sys.argv[1])'], 'auth': {'mode': 'none'},
                    'operations': [{'name': 'read', 'description': 'Read JSON', 'effect': 'read',
                        'inputSchema': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']},
                        'args': ['${arguments}'], 'format': 'json'}]}
                await service.handle('connector_save', {'manifest': manifest})
                await service.connectors.check('echo')
                loop = service.store.new()
                definition = {'schemaVersion': 2, 'title': 'CLI plugin', 'goal': 'Read JSON', 'completion': 'Data exists',
                    'inputs': [], 'scripts': {}, 'finalOutputs': ['${steps.read.outputs.data}'], 'steps': [
                        {'id': 'read', 'label': 'Read', 'instructions': 'Read JSON', 'inputs': {},
                         'execution': {'tool': 'mcp', 'connectorId': 'echo', 'operation': 'read', 'arguments': {'value': '真实 CLI 输出'}},
                         'outputs': [{'id': 'data', 'label': 'Data', 'kind': 'value', 'pointer': '/payload'}], 'checks': []}]}
                updated = await service.handle('definition', {'loopId': loop['id'], 'definition': definition})
                self.assertEqual(updated['definition']['connectorBindings']['echo']['operations']['read']['metadataKind'], 'cli')
                trial = service.runner.start(loop['id'])
                await service.runner.tasks[trial['id']]
                await service.handle('save', {'loopId': loop['id']})
                run = await service.handle('start', {'loopId': loop['id'], 'inputs': {}})
                await service.runner.tasks[run['id']]
                result = service.store.run(loop['id'], run['id'])
                self.assertEqual(result['status'], 'success', result.get('reason'))
                self.assertEqual(result['stepResults'][0]['outputs']['data'], {'value': '真实 CLI 输出'})
                contract = updated['definition']['connectorBindings']['echo']['operations']['read']
                tampered = copy.deepcopy(contract)
                tampered['inputSchema'] = {}
                with self.assertRaisesRegex(Exception, '契约|说明'):
                    await service.connectors.call('echo', 'read', {'value': 'x'}, role='workflow', expected_contract=tampered)
                service.connectors.save({**manifest, 'name': 'Changed'})
                with self.assertRaisesRegex(Exception, '定义已修改'):
                    await service.connectors.call('echo', 'read', {'value': 'x'}, role='workflow', expected_revision=updated['definition']['connectorBindings']['echo']['revision'], expected_contract=contract)
            finally:
                await service.handle('shutdown', {})
                service.store.db.close()
