import copy
import json
import tempfile
import unittest
from pathlib import Path
from loops_runtime.bridge import Service
from loops_runtime.definition import validate


def data_flow():
    return {
        'schemaVersion': 2, 'title': '数据产出测试', 'goal': '直接返回数据', 'completion': '保留原始类型',
        'inputs': [{'id': 'text', 'label': '输入', 'kind': 'text', 'required': False}],
        'scripts': {'scripts/greeting.py': 'result = None'},
        'steps': [{'id': 'greet', 'label': '返回数据', 'instructions': '返回结果', 'inputs': {},
                   'execution': {'tool': 'python', 'script': 'scripts/greeting.py'},
                   'outputs': [{'id': 'reply', 'label': '结果', 'kind': 'value', 'source': 'result'}],
                   'checks': [], 'onFailure': {'action': 'stop'}}],
        'finalOutputs': ['${steps.greet.outputs.reply}'],
    }


class PythonOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(self.temp.name, lambda _: None)
        self.loop = self.service.store.new()

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def execute(self, source, spec=None):
        spec = spec or data_flow()
        spec['scripts']['scripts/greeting.py'] = source
        await self.service.handle('definition', {'loopId': self.loop['id'], 'definition': spec})
        run = await self.service.handle('start', {'loopId': self.loop['id'], 'inputs': {'text': ''}})
        await self.service.runner.tasks[run['id']]
        return self.service.store.run(self.loop['id'], run['id'])

    async def test_native_types_logs_and_persisted_preview_without_files(self):
        for value in [None, False, 42, '你好\nworld', [], {'items': [1, True, None, {'x': '中文'}]}]:
            with self.subTest(value=value):
                run = await self.execute(f"print('ordinary log')\nresult = {value!r}")
                self.assertEqual(run['status'], 'success', run.get('reason'))
                self.assertEqual(run['stepResults'][0]['outputs']['reply'], value)
                self.assertEqual(run['stepResults'][0]['outputFiles'], {})
                self.assertFalse((Path(run['directory']) / 'results').exists())
                self.assertEqual(list((Path(run['directory']) / 'workspace').iterdir()), [])
                self.assertIn('ordinary log', run['checks'][0]['output'])
                artifact = run['artifacts'][0]
                self.assertEqual(artifact['value'], value)
                self.assertNotIn('path', artifact)
                preview = await self.service.handle('artifact', {'loopId': self.loop['id'], 'runId': run['id'], 'ref': artifact['ref']})
                self.assertEqual(preview['content'], value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.service = Service(self.temp.name, lambda _: None)
        saved = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(saved['artifacts'], run['artifacts'])

    async def test_pointer_and_downstream_input_keep_types(self):
        spec = data_flow()
        spec['steps'][0]['outputs'][0]['pointer'] = '/items'
        second = copy.deepcopy(spec['steps'][0])
        second.update(id='next', inputs={'data': '${steps.greet.outputs.reply}'})
        second['execution']['script'] = 'scripts/next.py'
        second['outputs'][0].pop('pointer')
        spec['scripts']['scripts/next.py'] = "import os,json\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nassert c['inputs']['data'] == [1, False, None]\nresult={'received':c['inputs']['data']}"
        spec['steps'].append(second)
        spec['finalOutputs'] = ['${steps.next.outputs.reply}']
        run = await self.execute("result={'items':[1,False,None]}", spec)
        self.assertEqual(run['status'], 'success', run.get('reason'))
        self.assertEqual(run['artifacts'][0]['value'], {'received': [1, False, None]})

    async def test_missing_invalid_oversized_or_failed_results_are_not_success(self):
        for source in ["print('no result')", "result=float('nan')", 'result={1,2}', "result='x'*900001", "result='not delivered'\nraise RuntimeError('failed')"]:
            with self.subTest(source=source):
                run = await self.execute(source)
                self.assertEqual(run['status'], 'failed')
                self.assertEqual(run['artifacts'], [])

    def test_value_contract_rejects_missing_source_and_unsupported_foreach(self):
        spec = data_flow()
        spec['steps'][0]['outputs'][0].pop('source')
        with self.assertRaisesRegex(ValueError, 'source=result'):
            validate(spec, {'resources': []})
        spec = data_flow()
        spec['steps'][0]['execution']['forEach'] = '${inputs.text}'
        with self.assertRaisesRegex(ValueError, 'forEach'):
            validate(spec, {'resources': []})

    async def test_large_values_fall_back_to_file(self):
        run = await self.execute("result='x'*100001")
        self.assertEqual(run['status'], 'success', run.get('reason'))
        self.assertEqual(json.loads(Path(run['artifacts'][0]['path']).read_text()), 'x'*100001)
        self.assertIn('reply', run['stepResults'][0]['outputFiles'])
