import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from loops_runtime.bridge import Service

CLI = Path(__file__).parent / 'fixtures/order_cli.py'

def output(key='data', **extra):
    return {'id': key, 'label': key, 'kind': 'value', 'source': 'stdout', 'format': 'json', **extra}

def command_step(key, argv, outputs=None, **execution):
    return {'id': key, 'label': key, 'instructions': 'Execute the local CLI', 'inputs': {},
            'execution': {'tool': 'command', 'command': argv, **execution},
            'outputs': outputs if outputs is not None else [output()], 'checks': [],
            'onFailure': {'action': 'retry', 'maxAttempts': 2}}

def order_flow(database, count=2, behavior='normal'):
    base = [sys.executable, str(CLI), '--database', str(database)]
    return {'schemaVersion': 2, 'title': '订单 CLI', 'goal': '逐项建单', 'completion': '真实工单进入报告',
            'inputs': [], 'scripts': {'scripts/report.py': "import json,os\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text(json.dumps(c['inputs']['tickets']))"},
            'steps': [command_step('orders', [*base, 'list', '--count', str(count)], effect='read'),
                command_step('tickets', [*base, 'create', '--order-id', '${item.id}', '--behavior', behavior],
                    forEach='${steps.orders.outputs.data}', timeoutSeconds=1 if behavior == 'timeout' else 30),
                {'id': 'report', 'label': '报告', 'instructions': '报告', 'inputs': {'tickets': '${steps.tickets.outputs.data}'},
                 'execution': {'tool': 'python', 'script': 'scripts/report.py'},
                 'outputs': [{'id': 'report', 'label': '报告', 'path': 'report.json', 'kind': 'json'}],
                 'checks': [], 'onFailure': {'action': 'stop'}}],
            'finalOutputs': ['${steps.report.outputs.report}']}

class CommandWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root/'state', lambda _: None, workspace_root=self.root/'workspaces')
        self.loop = self.service.store.new()
        self.database = self.root/'orders.sqlite3'

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def start(self, definition):
        await self.service.handle('definition', {'loopId': self.loop['id'], 'definition': definition})
        return await self.service.handle('start', {'loopId': self.loop['id']})

    async def run_flow(self, definition):
        run = await self.start(definition)
        await self.service.runner.tasks[run['id']]
        return self.service.store.run(self.loop['id'], run['id'])

    def tickets(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute('SELECT order_id FROM tickets').fetchall()

    async def test_two_one_zero_and_json_handoff(self):
        for count in [2, 1, 0]:
            with self.subTest(count=count):
                self.database = self.root/f'orders-{count}.sqlite3'
                result = await self.run_flow(order_flow(self.database, count))
                self.assertEqual(result['status'], 'success', result.get('reason'))
                self.assertEqual(len(self.tickets()), count)
                report = json.loads(Path(result['artifacts'][0]['path']).read_text())
                self.assertEqual(len(report), count)
                self.assertEqual([r['ticketId'] for r in report], list(range(1, count+1)))
                self.assertEqual(len(result['commandCalls']), count+1)
                self.assertIn('查询订单', result['commandCalls'][0]['stderr'])

    async def test_failure_after_write_never_retries_completed_items(self):
        result = await self.run_flow(order_flow(self.database, behavior='fail'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.tickets(), [('ORDER-1',), ('ORDER-2',)])
        self.assertEqual(result['commandCalls'][-1]['exitCode'], 7)
        self.assertEqual(result['commandCalls'][-1]['outcome'], 'unknown')
        self.assertIn('不会自动重复', result['reason'])

    async def test_timeout_kills_process_group_and_does_not_retry(self):
        result = await self.run_flow(order_flow(self.database, behavior='timeout'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['commandCalls'][-1]['status'], 'timeout')
        self.assertIn('超过 1 秒', result['reason'])
        self.assertEqual(len(self.tickets()), 2)
        await self.assert_child_dead()

    async def assert_child_dead(self):
        pid = int(Path(str(self.database)+'.child').read_text())
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(.01)
        self.fail(f'child process {pid} survived')

    async def test_cancel_kills_process_group_and_preserves_completed_items(self):
        run = await self.start(order_flow(self.database, behavior='cancel'))
        task = self.service.runner.tasks[run['id']]
        async with asyncio.timeout(10):
            while not Path(str(self.database)+'.child').exists():
                await asyncio.sleep(.01)
        self.service.runner.stop(self.loop['id'], run['id'])
        await task
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['commandCalls'][-1]['status'], 'cancelled')
        self.assertEqual(result['commandCalls'][1]['status'], 'success')
        self.assertEqual(len(self.tickets()), 2)
        await self.assert_child_dead()

    async def test_downstream_goto_cannot_replay_write(self):
        definition = order_flow(self.database)
        definition['steps'][-1] = command_step('report', [sys.executable, '-c', 'raise SystemExit(1)'], effect='read')
        definition['steps'][-1]['onFailure'] = {'action': 'goto', 'target': 'orders', 'maxAttempts': 1}
        definition['finalOutputs'] = []
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.tickets()), 2)
        self.assertIn('不会自动重复', result['reason'])

    async def test_invalid_json_after_write_is_not_retried(self):
        result = await self.run_flow(order_flow(self.database, behavior='invalid-json'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.tickets()), 1)
        self.assertIn('不是有效 JSON', result['reason'])

    async def test_all_arguments_validated_before_first_write(self):
        definition = order_flow(self.database)
        definition['steps'][0]['execution']['command'] = [sys.executable, '-c', 'print(\'[ {"id":"ok"}, {"wrong":1} ]\')']
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.database.exists())
        self.assertEqual(len(result['commandCalls']), 1)

    async def test_large_json_stdout_is_not_truncated_and_stderr_is_separate(self):
        definition = order_flow(self.database)
        definition['steps'] = [command_step('large', [sys.executable, '-c', 'import json,sys; print("warning",file=sys.stderr);print(json.dumps({"data":"x"*20000}))'], [output(pointer='/data')], effect='read')]
        definition['finalOutputs'] = ['${steps.large.outputs.data}']
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'success', result.get('reason'))
        self.assertEqual(len(result['stepResults'][0]['outputs']['data']), 20000)

    async def test_legacy_file_output_command(self):
        definition = order_flow(self.database)
        definition['steps'] = [command_step('legacy', [sys.executable, '-c', 'from pathlib import Path;Path("result.txt").write_text("legacy")'], [{'id':'file','label':'file','path':'result.txt'}])]
        definition['finalOutputs'] = ['${steps.legacy.outputs.file}']
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(Path(result['artifacts'][0]['path']).read_text(), 'legacy')

    async def test_invalid_contracts_rejected(self):
        for field, value in [('timeoutSeconds', 0), ('timeoutSeconds', True), ('effect', 'guess'), ('forEach', 'literal')]:
            definition = order_flow(self.database)
            definition['steps'][1]['execution'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                await self.start(definition)

    async def test_read_only_failure_can_retry(self):
        definition = order_flow(self.database)
        definition['steps'] = [command_step('read', [sys.executable, '-c', 'raise SystemExit(2)'], effect='read')]
        definition['finalOutputs'] = []
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['commandCalls']), 3)
        self.assertEqual(result['corrections'], 2)

    async def test_numeric_items_and_json_types_survive(self):
        definition = order_flow(self.database)
        definition['steps'] = [command_step('items', [sys.executable, '-c', 'print("[1,2]")'], effect='read'),
            command_step('echo', [sys.executable, '-c', 'import json,sys; print(json.dumps({"n":int(sys.argv[1]),"ok":True,"empty":None}))', '${item}'], forEach='${steps.items.outputs.data}', effect='read')]
        definition['finalOutputs'] = ['${steps.echo.outputs.data}']
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'success', result.get('reason'))
        self.assertEqual(result['stepResults'][-1]['outputs']['data'], [{'n':1,'ok':True,'empty':None}, {'n':2,'ok':True,'empty':None}])

    async def test_oversize_stdout_fails_without_replaying_write(self):
        definition = order_flow(self.database)
        definition['steps'] = [command_step('large', [sys.executable, '-c', 'print("x"*9000000)'])]
        definition['finalOutputs'] = []
        result = await self.run_flow(definition)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('8 MiB', result['reason'])
        self.assertEqual(len(result['commandCalls']), 1)
        self.assertLessEqual(len(result['commandCalls'][0]['stdout']), 8*1024*1024)
