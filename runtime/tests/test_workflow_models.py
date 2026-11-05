import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loops_runtime.bridge import Service
from loops_runtime.definition import validate
from loops_runtime.model_host import ModelHost


class ModelHostDouble:
    """Explicit host double; never loads credentials or calls a provider."""
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.wait = False
        self.failure = None
        self.changed = False

    async def call(self, method, params=None):
        if method == 'describe':
            return {'modelId': params['modelId'], 'provider': 'deepseek', 'model': params['modelId'],
                    'name': '测试模型', 'thinkingLevels': ['off', 'high'], 'thinkingLevel': 'off'}
        if method == 'validate':
            if self.changed:
                raise ValueError('模型资源对应的 API 配置已改变')
            for binding in params['bindings']:
                if binding['reasoning'] not in {'off', 'high'}:
                    raise ValueError('不支持思考强度')
            return True
        if method == 'complete':
            self.calls.append(copy.deepcopy(params))
            self.started.set()
            if self.wait:
                await asyncio.Future()
            if self.failure:
                raise ValueError(self.failure)
            return {'text': params['prompt'].upper(), 'usage': {'input': 2, 'output': 3, 'totalTokens': 5}}
        raise AssertionError(method)


def definition(resource, source=None):
    return {'schemaVersion': 2, 'title': '模型处理', 'goal': '提取文本', 'completion': '文件存在',
            'inputs': [{'id': 'text', 'kind': 'text', 'label': '文本'}],
            'scripts': {'scripts/main.py': source or """import os,json
from pathlib import Path
from loops_ai import complete
import argparse
p=argparse.ArgumentParser()
p.add_argument("--model", required=True)
p.add_argument("--reasoning", required=True)
args=p.parse_args()
c=json.loads(os.environ['LOOPS_CONTEXT'])
assert 'DEEPSEEK_API_KEY' not in os.environ
assert 'OPENAI_API_KEY' not in os.environ
Path(c['outputs']['text']).write_text(complete(c['inputs']['text'], model=args.model, reasoning=args.reasoning, max_tokens=128))
print('脚本执行完成')
"""}, 'steps': [{'id': 'extract', 'label': '提取', 'instructions': '提取输入文本',
            'inputs': {'text': '${inputs.text}'}, 'execution': {'tool': 'python', 'script': 'scripts/main.py',
            'args': ['--model', resource['id'], '--reasoning', 'off']},
            'outputs': [{'id': 'text', 'label': '结果', 'path': 'result.txt'}], 'checks': [], 'onFailure': {'action': 'stop'}}],
            'finalOutputs': ['${steps.extract.outputs.text}']}


class WorkflowModelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = ModelHostDouble()
        self.service = Service(self.temp.name, lambda _: None, workspace_root=Path(self.temp.name) / 'workspaces', model_host=self.host)
        self.loop = self.service.store.new()
        self.resource = await self.service.handle('add_model_resource', {'loopId': self.loop['id'], 'modelId': 'test-model'})

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def build(self, value=None):
        return await self.service.handle('definition', {'loopId': self.loop['id'], 'definition': value or definition(self.resource)})

    async def execute(self, text='hello', snapshot=None):
        run = self.service.runner.start(self.loop['id'], {'text': text}, snapshot=snapshot)
        await self.service.runner.tasks[run['id']]
        return self.service.store.run(self.loop['id'], run['id'])

    async def test_script_calls_declared_model_and_preserves_real_result(self):
        await self.build()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'must-not-inherit', 'OPENAI_API_KEY': 'must-not-inherit'}):
            run = await self.execute()
        self.assertEqual(run['status'], 'success', run.get('reason'))
        self.assertEqual(Path(run['artifacts'][0]['path']).read_text(), 'HELLO')
        self.assertEqual(self.host.calls[0]['binding']['reasoning'], 'off')
        self.assertEqual(self.host.calls[0]['maxTokens'], 128)
        self.assertEqual(run['modelCalls'][0]['usage']['totalTokens'], 5)
        self.assertEqual(run['modelCalls'][0]['status'], 'success')
        self.assertIn('脚本执行完成', run['checks'][0]['output'])
        self.assertNotIn('must-not-inherit', json.dumps(run))
        script = (Path(run['directory']) / 'scripts/main.py').read_text()
        self.assertEqual(script, definition(self.resource)['scripts']['scripts/main.py'])
        self.assertEqual(run['modelSelections']['extract']['id'], self.resource['id'])

    async def test_adjustment_is_versioned_and_old_saved_snapshot_keeps_model_and_effort(self):
        await self.build()
        saved = await self.service.handle('save', {'loopId': self.loop['id']})
        snapshot = json.loads((Path(saved['versions'][0]['path']) / 'loop.json').read_text())
        old = await self.execute('first')
        changed = await self.service.handle('step_model_selection', {'loopId': self.loop['id'], 'stepId': 'extract', 'modelId': 'other-model', 'reasoning': 'high'})
        self.assertEqual(changed['revision'], saved['revision'] + 1)
        self.assertEqual(changed['savedRevision'], saved['revision'])
        new = await self.execute('second')
        self.assertEqual(new['status'], 'success', new.get('reason'))
        self.assertEqual(new['modelCalls'][0]['model'], 'other-model')
        self.assertEqual(new['modelCalls'][0]['reasoning'], 'high')
        repeated = await self.execute('third', snapshot=snapshot)
        self.assertEqual(repeated['modelCalls'][0]['model'], 'test-model')
        self.assertEqual(repeated['modelCalls'][0]['reasoning'], 'off')
        self.assertEqual(self.service.store.run(self.loop['id'], old['id']), old)
        preview = await self.service.handle('script', {'loopId': self.loop['id'], 'path': 'scripts/main.py'})
        self.assertEqual(preview['content'], definition(self.resource)['scripts']['scripts/main.py'])
        self.assertEqual(changed['definition']['steps'][0]['execution']['args'][-1], 'high')

    async def test_missing_resource_effort_and_non_python_declarations_are_rejected(self):
        config = self.service.store.get(self.loop['id'])['config']
        for args in [['--model', self.resource['id']], ['--model', 'unknown', '--reasoning', 'off'],
                     ['--model', self.resource['id'], '--reasoning'], ['--model', self.resource['id'], '--reasoning', 'off', '--reasoning', 'high'],
                     ['--model=x', '--reasoning', 'off'], ['--', '--model', self.resource['id'], '--reasoning', 'off'],
                     ['--model', self.resource['id'], '--reasoning', '${inputs.text}']]:
            flow = definition(self.resource)
            flow['steps'][0]['execution']['args'] = args
            with self.assertRaises(ValueError):
                validate(flow, config)
        flow = definition(self.resource)
        flow['steps'][0]['execution']['models'] = {}
        with self.assertRaisesRegex(ValueError, '调用名绑定已移除'):
            validate(flow, config)
        flow = definition(self.resource)
        flow['steps'][0]['execution']['args'][-1] = 'unsupported'
        with self.assertRaisesRegex(ValueError, '思考强度'):
            await self.build(flow)

    async def test_wrong_model_parameter_cannot_reach_host(self):
        await self.build(definition(self.resource, "from loops_ai import complete\ncomplete('hello', model='undeclared', reasoning='off')"))
        run = await self.execute()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('传入的参数', run['reason'])
        self.assertEqual(self.host.calls, [])

    async def test_subprocess_stdout_is_a_log(self):
        source = definition(self.resource)['scripts']['scripts/main.py']
        source = source.replace("Path(c['outputs']['text'])", "import subprocess, sys\nsubprocess.run([sys.executable, '-c', \"print('child output')\"], check=True)\nPath(c['outputs']['text'])")
        await self.build(definition(self.resource, source))
        run = await self.execute()
        self.assertEqual(run['status'], 'success', run.get('reason'))
        self.assertEqual(self.host.calls[0]['binding']['reasoning'], 'off')
        self.assertIn('child output', run['checks'][0]['output'])

    async def test_script_cannot_override_reasoning_parameter(self):
        source = definition(self.resource)['scripts']['scripts/main.py'].replace('reasoning=args.reasoning', "reasoning='high'")
        await self.build(definition(self.resource, source))
        run = await self.execute()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.host.calls, [])

    async def test_api_failure_and_model_drift_fail_without_fabricated_output(self):
        await self.build()
        self.host.failure = '模型账户额度不足'
        run = await self.execute()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['modelCalls'][0]['status'], 'failed')
        self.assertEqual(run['artifacts'], [])
        self.host.changed = True
        run = await self.execute()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('配置已改变', run['reason'])
        self.assertEqual(len(self.host.calls), 1)

    async def test_stop_cancels_pending_request_and_keeps_evidence(self):
        await self.build()
        self.host.wait = True
        run = self.service.runner.start(self.loop['id'], {'text': 'cancel'})
        task = self.service.runner.tasks[run['id']]
        await asyncio.wait_for(self.host.started.wait(), 5)
        self.service.runner.stop(self.loop['id'], run['id'])
        await asyncio.wait_for(task, 5)
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['modelCalls'][0]['status'], 'stopped')

    async def test_host_rpc_cancellation_and_private_reply(self):
        messages = []
        rpc = ModelHost(messages.append)
        task = asyncio.create_task(rpc.call('catalog'))
        await asyncio.sleep(0)
        rpc.receive({'hostResponse': messages[0]['hostRequest'], 'result': {'models': []}})
        self.assertEqual(await task, {'models': []})
        task = asyncio.create_task(rpc.call('complete'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn('hostCancel', messages[-1])
        self.assertEqual(rpc.pending, {})
