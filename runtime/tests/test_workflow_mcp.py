import json
import sys
import tempfile
import unittest
from pathlib import Path
from loops_runtime.bridge import Service


def value(key, pointer):
    return {'id': key, 'label': key, 'kind': 'value', 'pointer': pointer}


def step(key, operation, arguments, outputs):
    return {'id': key, 'label': key, 'instructions': 'Use the registered operation', 'inputs': {},
            'execution': {'tool': 'mcp', 'connectorId': 'custom', 'operation': operation, 'arguments': arguments},
            'outputs': outputs, 'checks': [], 'onFailure': {'action': 'stop', 'maxAttempts': 0}}


def flow():
    return {'schemaVersion': 2, 'title': 'Custom tools', 'goal': 'Pass typed data', 'completion': 'Receipt exists',
            'inputs': [{'id': 'text', 'label': 'Text', 'kind': 'text', 'required': True}], 'scripts': {},
            'steps': [step('measure', 'measure', {'text': '${inputs.text}'},
                           [value('data', '/payload'), value('count', '/payload/length'), value('items', '/payload/items'),
                            value('enabled', '/payload/enabled'), value('optional', '/payload/optional')]),
                      step('combine', 'combine', {k: '${steps.measure.outputs.' + k + '}' for k in ['data', 'count', 'items', 'enabled', 'optional']},
                           [value('data', '/payload')])], 'finalOutputs': ['${steps.combine.outputs.data}']}


class MCPWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.external = self.root / 'external'
        self.service = Service(self.root / 'state', lambda event: None, workspace_root=self.root / 'workspaces')
        self.loop = self.service.store.new()
        self.manifest = {'id': 'custom', 'name': 'Custom', 'type': 'mcp', 'transport': 'stdio',
                         'command': [sys.executable, str(Path(__file__).parent / 'fixtures/workflow_mcp.py'), str(self.external)],
                         'auth': {'mode': 'none'}}
        await self.service.handle('connector_save', {'manifest': self.manifest})
        await self.service.handle('connector_connect', {'connectorId': 'custom'})
        await self.service.connectors.jobs['custom']
        self.assertEqual(self.service.connectors.entry('custom')['status'], 'connected')

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def build(self, definition):
        return await self.service.handle('definition', {'loopId': self.loop['id'], 'definition': definition})

    async def run_flow(self, text='中文 typed data'):
        run = await self.service.handle('start', {'loopId': self.loop['id'], 'inputs': {'text': text}})
        await self.service.runner.tasks[run['id']]
        return self.service.store.run(self.loop['id'], run['id'])

    def calls(self):
        path = self.external / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def test_discovery_typed_results_file_handoff_and_saved_version(self):
        info = await self.service.handle('connector_describe', {'connectorId': 'custom', 'operation': 'measure'})
        self.assertIn('inputSchema', info)
        definition = flow()
        definition['scripts']['scripts/report.py'] = "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['file']).write_text(json.dumps(c['inputs']['data'],ensure_ascii=False))"
        definition['steps'].append({'id': 'report', 'label': '报告', 'instructions': 'Persist actual data',
                                   'inputs': {'data': '${steps.combine.outputs.data}'}, 'execution': {'tool': 'python', 'script': 'scripts/report.py'},
                                   'outputs': [{'id': 'file', 'label': '报告', 'path': 'report.json'}], 'checks': []})
        definition['finalOutputs'].append('${steps.report.outputs.file}')
        await self.build(definition)
        saved = await self.service.handle('save', {'loopId': self.loop['id']})
        first = await self.run_flow()
        self.assertEqual(first['status'], 'success', first.get('reason'))
        result = first['stepResults'][1]['outputs']['data']
        self.assertEqual(result['count'], len('中文 typed data'))
        self.assertIs(result['enabled'], True)
        self.assertIsNone(result['optional'])
        self.assertEqual(result['items'], ['中文 typed data', len('中文 typed data')])
        for artifact in first['artifacts']:
            self.assertEqual(json.loads(Path(artifact['path']).read_text()), result)
        self.assertEqual(len(first['connectorCalls']), 2)
        self.assertEqual([call['operation'] for call in self.calls()], ['measure', 'combine'])
        second = await self.run_flow('different')
        self.assertEqual(second['stepResults'][1]['outputs']['data']['count'], 9)
        self.assertEqual(self.service.store.run(self.loop['id'], first['id']), first)
        snapshot = json.loads((Path(saved['versions'][0]['path']) / 'loop.json').read_text())
        self.assertIn('connectorBindings', snapshot['definition'])
        catalog = await self.service.handle('automation_catalog', {})
        self.assertEqual(len(catalog[0]['versions'][0]['outputs']), 2)

    async def test_stale_schema_and_revoked_connection_do_not_call_tools(self):
        await self.build(flow())
        (self.external / 'changed').touch()
        run = await self.run_flow()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('已改变', run['reason'])
        self.assertEqual(self.calls(), [])
        await self.service.handle('connector_disconnect', {'connectorId': 'custom'})
        run = await self.run_flow()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.calls(), [])

    async def test_missing_pointer_and_invalid_arguments_fail(self):
        definition = flow()
        definition['steps'] = definition['steps'][:1]
        definition['steps'][0]['outputs'] = [value('data', '/payload/missing')]
        definition['finalOutputs'] = ['${steps.measure.outputs.data}']
        await self.build(definition)
        run = await self.run_flow()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('缺少声明的数据', run['reason'])
        definition['steps'][0]['execution']['arguments'] = {'unexpected': 1}
        await self.build(definition)
        run = await self.run_flow()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('参数无效', run['reason'])
        self.assertEqual(len(self.calls()), 1)

    async def test_write_failure_is_not_retried(self):
        definition = flow()
        definition['steps'] = [step('send', 'deliver', {'data': {'value': '${inputs.text}'}, 'fail': True}, [value('receipt', '/payload')])]
        definition['steps'][0]['onFailure'] = {'action': 'retry', 'maxAttempts': 3}
        definition['finalOutputs'] = ['${steps.send.outputs.receipt}']
        await self.build(definition)
        run = await self.run_flow()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue((self.external / 'receipt.json').exists())
        self.assertEqual(run['connectorCalls'][0]['status'], 'unknown')
        self.assertIn('先核对外部系统', run['reason'])

    async def test_goto_cannot_replay_successful_write(self):
        definition = flow()
        definition['steps'] = [step('send', 'deliver', {'data': {}}, [value('receipt', '/payload')]),
                               step('bad', 'measure', {'text': 'bad'}, [value('data', '/payload/missing')])]
        definition['steps'][1]['onFailure'] = {'action': 'goto', 'target': 'send', 'maxAttempts': 2}
        definition['finalOutputs'] = ['${steps.send.outputs.receipt}']
        await self.build(definition)
        run = await self.run_flow()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('不会自动重复写入', run['reason'])
        self.assertEqual([c['operation'] for c in self.calls()], ['deliver', 'measure'])

    async def test_data_output_remains_a_file_when_used_by_automation(self):
        import asyncio
        await self.build(flow())
        await self.service.handle('save', {'loopId': self.loop['id']})
        target = self.root / 'automation-receipt.json'
        spec = {'title': 'MCP automation',
                'source': {'script': "def sample(ctx):\n    ctx.emit('one', {'text':'automation'})\ndef run(ctx):\n    sample(ctx)\n", 'config': {}, 'lifecycle': 'once'},
                'nodes': [{'id': 'measure', 'loopId': self.loop['id'], 'bindings': {'text': 'input.text'}}],
                'outputs': [{'id': 'save', 'title': 'Receipt',
                             'script': "from pathlib import Path\ndef run(ctx):\n    target=Path(ctx.config['target'])\n    target.write_bytes(Path(ctx.inputs['data']).read_bytes())\n    ctx.receipt(target=str(target))\n",
                             'config': {'target': str(target)}, 'bindings': {'data': 'measure.combine.data'}}],
                'policy': {'queueLimit': 10, 'scriptTimeout': 5, 'sourceRestarts': 0}}
        task = await self.service.handle('automation_save', {'spec': spec})
        accepted = await self.service.handle('automation_run', {'taskId': task['id'], 'inputs': {'text': 'automation'}})
        async def settle():
            while True:
                run = self.service.automations.run(accepted['runId'])
                if run['status'] in {'success', 'failed', 'unknown', 'authorization_required'}:
                    return run
                await asyncio.sleep(.01)
        result = await asyncio.wait_for(settle(), 15)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(json.loads(target.read_text())['data']['text'], 'automation')

    def list_flow(self):
        definition = flow()
        definition['scripts'] = {'scripts/items.py': "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['items']).write_text(c['inputs']['text'])\n"}
        definition['steps'] = [
            {'id': 'prepare', 'label': '准备列表', 'instructions': 'Read JSON input', 'inputs': {'text': '${inputs.text}'},
             'execution': {'tool': 'python', 'script': 'scripts/items.py'}, 'outputs': [{'id': 'items', 'label': '列表', 'kind': 'json', 'path': 'items.json'}], 'checks': []},
            step('send', 'deliver', {'data': '${item.data}', 'fail': '${item.fail}'}, [value('receipts', '/payload')])]
        definition['steps'][1]['execution']['forEach'] = '${steps.prepare.outputs.items}'
        definition['finalOutputs'] = ['${steps.prepare.outputs.items}', '${steps.send.outputs.receipts}']
        return definition

    async def test_foreach_two_one_zero_and_native_json_output(self):
        definition = self.list_flow()
        await self.build(definition)
        for count in (2, 1, 0):
            before = len(self.calls())
            values = [{'data': {'n': n, 'enabled': True, 'nullable': None}, 'fail': False} for n in range(count)]
            run = await self.run_flow(json.dumps(values))
            self.assertEqual(run['status'], 'success', run.get('reason'))
            self.assertEqual(run['stepResults'][0]['outputs']['items'], values)
            receipts = run['stepResults'][1]['outputs']['receipts']
            self.assertEqual([r['data'] for r in receipts], [v['data'] for v in values])
            self.assertEqual(len(self.calls()) - before, count)
            self.assertEqual([c['itemIndex'] for c in run.get('connectorCalls', [])], list(range(count)))
            self.assertEqual(json.loads(Path(run['artifacts'][1]['path']).read_text()), receipts)

    async def test_foreach_stops_after_partial_write_without_replay(self):
        definition = self.list_flow()
        definition['steps'][1]['onFailure'] = {'action': 'retry', 'maxAttempts': 3}
        await self.build(definition)
        run = await self.run_flow(json.dumps([{'data': {'id': i}, 'fail': i == 1} for i in range(3)]))
        self.assertEqual(run['status'], 'failed')
        self.assertIn('第 2/3 项', run['reason'])
        self.assertEqual([c['value']['id'] for c in self.calls()], [0, 1])
        self.assertEqual([c['status'] for c in run['connectorCalls']], ['success', 'unknown'])
        self.assertEqual(run['connectorCalls'][0]['response']['payload']['data']['id'], 0)

    async def test_foreach_rejects_nonlist_and_bad_later_item_before_any_write(self):
        await self.build(self.list_flow())
        for bad in ({'data': 1}, [{'data': {}, 'fail': False}, {'data': {}, 'fail': 'wrong type'}], [{'data': {}, 'fail': False}, {}]):
            run = await self.run_flow(json.dumps(bad))
            self.assertEqual(run['status'], 'failed')
            self.assertEqual(self.calls(), [])
        run = await self.run_flow('not json')
        self.assertEqual(run['status'], 'failed')
        self.assertIn('不是有效 JSON', run['reason'])

    async def test_item_reference_is_scoped_to_foreach_arguments(self):
        definition = self.list_flow()
        definition['steps'][1]['execution'].pop('forEach')
        with self.assertRaisesRegex(ValueError, '尚不可用'):
            await self.build(definition)
        definition = self.list_flow()
        definition['steps'][1]['instructions'] = '${item.data}'
        with self.assertRaisesRegex(ValueError, '尚不可用'):
            await self.build(definition)
