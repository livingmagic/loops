import asyncio
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from loops_runtime.bridge import Service
from loops_runtime.definition import validate
from loops_runtime.fixture import create_fixture
from loops_runtime.runner import Runner
from loops_runtime.store import Store


REPRODUCTION = ('import unittest\nfrom checkout import total_price\n'
                'class Reproduction(unittest.TestCase):\n'
                '    def test_discount(self): self.assertEqual(total_price(2,100,10),180)\n')
TEST_COMMAND = ['python', '-m', 'unittest', 'discover', '-s', 'tests']


def protected_tests_check(source):
    protected = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (source / 'tests').rglob('*.py')}
    protected['tests/test_discount.py'] = hashlib.sha256(REPRODUCTION.encode()).hexdigest()
    guard = 'import hashlib; from pathlib import Path; expected=' + repr(protected) + '; assert all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in expected.items()), "测试被修改"'
    return {'kind': 'command', 'label': '保护已有与新增测试', 'command': ['python', '-c', guard]}


def definition():
    return {'schemaVersion': 2, 'title': '问题修复', 'goal': '根据输入复现并修复问题', 'completion': '测试通过，交付补丁',
            'inputs': [{'id': 'source', 'label': '错误日志', 'kind': 'file', 'required': True}],
            'finalOutputs': ['${steps.deliver.outputs.patch}', '${steps.deliver.outputs.report}'],
            'scripts': {'scripts/deliver.py': "import json,os,subprocess\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nsubprocess.run(['git','add','-N','tests/test_discount.py'],check=True)\nr=subprocess.run(['git','diff','--binary','HEAD'],capture_output=True,text=True,check=True)\nassert r.stdout.strip(), '没有代码改动'\nPath(c['outputs']['patch']).write_text(r.stdout)\nPath(c['outputs']['report']).write_text(json.dumps({'checks':c['checks']},ensure_ascii=False))\n"},
            'steps': [
                {'id': 'reproduce', 'label': '复现', 'instructions': 'test-stage:reproduce 新增独立复现测试，保持业务代码和已有测试。',
                 'inputs': {'source': '${inputs.source}'}, 'execution': {'tool': 'codex', 'mode': 'write', 'session': 'repair'},
                 'outputs': [{'id': 'test', 'label': '复现测试', 'path': 'tests/test_discount.py'}],
                 'checks': [{'kind': 'command', 'label': '修复前复现', 'command': TEST_COMMAND, 'expectedExitCode': 1}], 'onFailure': {'action': 'stop'}},
                {'id': 'repair', 'label': '修复与验证', 'instructions': 'test-stage:fix 保留全部测试；根据 ${failure} 修复。',
                 'inputs': {'test': '${steps.reproduce.outputs.test}'}, 'execution': {'tool': 'codex', 'mode': 'write', 'session': 'repair'}, 'outputs': [],
                 'checks': [{'kind': 'command', 'label': '定向验证', 'command': ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_discount.py'], 'expectedExitCode': 0},
                            {'kind': 'command', 'label': '回归测试', 'command': TEST_COMMAND, 'expectedExitCode': 0}],
                 'onFailure': {'action': 'retry', 'maxAttempts': 1}},
                {'id': 'deliver', 'label': '交付', 'instructions': '交付真实补丁和检查结果。', 'inputs': {},
                 'execution': {'tool': 'python', 'script': 'scripts/deliver.py'},
                 'outputs': [{'id': 'patch', 'label': '代码补丁', 'path': 'delivery/changes.patch'}, {'id': 'report', 'label': '验证记录', 'path': 'delivery/checks.json'}],
                 'checks': [], 'onFailure': {'action': 'stop'}}]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name, workspace_root=Path(self.temp.name) / 'workspaces')
        self.loop = self.store.new()
        self.fixture = create_fixture(self.temp.name)
        self.store.configure(self.loop['id'], self.fixture)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_saved_untried_definition_and_executable_survive_reload(self):
        self.store.update_definition(self.loop['id'], definition())
        saved = self.store.save(self.loop['id'])
        self.assertEqual(saved['savedRevision'], 1)
        self.assertEqual(saved['runs'], [])
        path = Path(saved['versions'][0]['path'])
        self.assertIn('context.run_workflow(DEFINITION)', (path / 'workflow.py').read_text())
        self.assertTrue((path / 'scripts/deliver.py').is_file())
        restored = Store(self.temp.name, workspace_root=Path(self.temp.name) / 'workspaces')
        try:
            self.assertEqual(restored.get(self.loop['id'])['definition'], definition())
        finally:
            restored.db.close()

    def test_feedback_cannot_change_a_running_snapshot(self):
        self.store.update_definition(self.loop['id'], definition())
        original = self.store.get(self.loop['id'])
        original['runs'] = [{'id': 'historical', 'status': 'success', 'definition': copy.deepcopy(original['definition']), 'revision': 1}]
        self.store.put(original)
        changed = definition()
        changed['steps'][1]['instructions'] = '依据实际触发条件修复。'
        self.store.update_definition(self.loop['id'], changed)
        current = self.store.get(self.loop['id'])
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['runs'][0]['definition'], definition())

    def test_restart_marks_unfinished_work_interrupted_without_replaying(self):
        loop = self.store.get(self.loop['id'])
        loop['runs'] = [{'id': 'pending', 'status': 'running'}]
        self.store.put(loop)
        restored = Store(self.temp.name, workspace_root=Path(self.temp.name) / 'workspaces')
        try:
            self.assertEqual(restored.get(self.loop['id'])['runs'][0]['status'], 'interrupted')
        finally:
            restored.db.close()

    def test_resource_and_input_configurations_are_versioned_without_rewriting_saved_config(self):
        self.store.update_definition(self.loop['id'], definition())
        saved = self.store.save(self.loop['id'])
        self.store.configure(self.loop['id'], self.fixture)
        self.assertEqual(self.store.get(self.loop['id'])['revision'], 1)
        configuration = {'resources': self.fixture['resources'], 'inputDefaults': {'source': self.fixture['secondInputPath']}}
        current = self.store.configure(self.loop['id'], configuration)
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['savedRevision'], 1)
        snapshot = json.loads((Path(saved['versions'][0]['path']) / 'loop.json').read_text())
        self.assertEqual(snapshot['config']['inputDefaults'], self.fixture['inputDefaults'])
        self.assertEqual(current['definition'], definition())

    def test_only_current_workflow_and_resource_contracts_are_accepted(self):
        original = self.store.get(self.loop['id'])['config']
        for value in (None, {}, {'schemaVersion': 1, 'title': '旧流程'}):
            with self.assertRaisesRegex(ValueError, '通用配置'):
                validate(value, self.fixture)
        with self.assertRaisesRegex(ValueError, 'resources'):
            self.store.configure(self.loop['id'], {'repoPath': self.fixture['resources'][0]['path']})
        value = definition()
        value['steps'][0]['inputs']['source'] = '${resources.missing}'
        with self.assertRaisesRegex(ValueError, '尚不可用'):
            validate(value, self.fixture)
        self.assertEqual(self.store.get(self.loop['id'])['config'], original)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(self.temp.name, lambda _: None, workspace_root=Path(self.temp.name) / 'workspaces')
        self.loop = self.service.store.new()
        self.fixture = await self.service.handle('fixture', {'loopId': self.loop['id']})
        self.service.store.configure(self.loop['id'], self.fixture)
        self.service.store.update_definition(self.loop['id'], definition())

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def test_source_reads_cannot_escape_selected_project(self):
        with self.assertRaises(ValueError):
            await self.service.handle('read_project', {'loopId': self.loop['id'], 'path': '../../loops.sqlite3'})
        source = await self.service.handle('read_project', {'loopId': self.loop['id'], 'path': 'checkout.py'})
        self.assertIn('total_price', source)

    async def test_registered_input_outside_repo_is_readable_without_opening_arbitrary_files(self):
        other = self.fixture['secondInputPath']
        result = await self.service.handle('read_input', {'loopId': self.loop['id'], 'path': other})
        self.assertEqual(result['name'], 'quantity-error.log')
        self.assertIn('sample-2', result['content'])
        default = await self.service.handle('read_input', {'loopId': self.loop['id']})
        self.assertEqual(default['name'], 'discount-error.log')
        with self.assertRaises(ValueError):
            await self.service.handle('read_project', {'loopId': self.loop['id'], 'path': other})
        with self.assertRaises(ValueError):
            await self.service.handle('read_input', {'loopId': self.loop['id'], 'path': str(Path(self.temp.name) / 'loops.sqlite3')})

    async def test_step_commands_use_full_journal_and_dynamic_step_ids(self):
        directory = Path(self.temp.name) / 'journal-run'
        directory.mkdir()
        events = [{'type': 'command', 'text': 'prepare workspace'}]
        events += [{'type': 'command', 'stage': 'reproduce', 'text': f'reproduction command {index}'} for index in range(250)]
        events += [{'type': 'command', 'stage': 'repair', 'text': 'first repair'},
                   {'type': 'command', 'stage': 'repair', 'text': 'second repair'},
                   {'type': 'command', 'stage': 'deliver', 'text': 'deliver patch'}]
        (directory / 'events.jsonl').write_text('\n'.join(json.dumps(event) for event in events) + '\n')
        loop = self.service.store.get(self.loop['id'])
        loop['runs'].append({'id': 'journal', 'definition': definition(), 'status': 'success', 'directory': str(directory), 'events': events[-200:]})
        self.service.store.put(loop)
        result = await self.service.handle('commands', {'loopId': loop['id'], 'runId': 'journal'})
        self.assertEqual(set(result['steps']), {'reproduce', 'repair', 'deliver'})
        self.assertEqual(len(result['steps']['reproduce']), 250)
        self.assertEqual(result['steps']['reproduce'][0]['text'], 'reproduction command 0')
        self.assertEqual([command['text'] for command in result['steps']['repair']], ['first repair', 'second repair'])
        self.assertEqual(result['preparation'][0]['text'], 'prepare workspace')
        self.assertEqual(self.service.store.get(loop['id']), loop)
        with self.assertRaises(ValueError):
            await self.service.handle('commands', {'loopId': loop['id'], 'runId': 'unrelated'})

    async def test_two_starts_do_not_overlap_and_stop_is_terminal(self):
        entered = asyncio.Event()
        class Waiting:
            def __init__(self, *args): pass
            async def __aenter__(self):
                entered.set()
                await asyncio.Event().wait()
            async def __aexit__(self, *args): pass
        runner = Runner(self.service.store, lambda: None, session_factory=Waiting)
        run = runner.start(self.loop['id'])
        with self.assertRaises(ValueError):
            runner.start(self.loop['id'])
        task = runner.tasks[run['id']]
        await asyncio.wait_for(entered.wait(), 5)
        runner.stop(self.loop['id'], run['id'])
        await task
        self.assertEqual(self.service.store.run(self.loop['id'], run['id'])['status'], 'stopped')

    async def test_false_success_cannot_bypass_required_outputs_and_reproduction_checks(self):
        class ClaimsOnly:
            def __init__(self, *args): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def ask(self, *args, **kwargs): return '已全部修复并通过测试。'
        runner = Runner(self.service.store, lambda: None, session_factory=ClaimsOnly)
        run = runner.start(self.loop['id'])
        await runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('修复前复现', result['reason'])
        self.assertEqual(result['checks'][0]['exitCode'], 0)
        self.assertFalse(result['checks'][0]['passed'])
        self.assertEqual(result['artifacts'], [])

    async def test_generic_engineering_repairs_after_real_failure_and_delivers_evidence(self):
        class RepairAfterEvidence:
            sessions = 0
            fixes = 0
            def __init__(self, cwd, emit): self.cwd = Path(cwd)
            async def __aenter__(self):
                type(self).sessions += 1
                return self
            async def __aexit__(self, *args): pass
            async def ask(self, prompt, readonly=False):
                if prompt.startswith('test-stage:reproduce'):
                    (self.cwd / 'tests/test_discount.py').write_text(REPRODUCTION)
                if prompt.startswith('test-stage:fix'):
                    type(self).fixes += 1
                    if type(self).fixes == 2:
                        (self.cwd / 'checkout.py').write_text('def total_price(quantity, unit_price, discount=0):\n    return round(quantity * unit_price * (1-discount/100),2)\n')
                return '已处理，等待外部验证。'
        source = Path(self.fixture['resources'][0]['path'])
        original = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*.py')}
        # Test preservation is an explicit workflow check, executed outside Codex.
        value = definition()
        value['steps'][1]['checks'].insert(0, protected_tests_check(source))
        self.service.store.update_definition(self.loop['id'], value)
        runner = Runner(self.service.store, lambda: None, session_factory=RepairAfterEvidence)
        run = runner.start(self.loop['id'])
        await runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'success', result.get('reason'))
        self.assertEqual(result['corrections'], 1)
        self.assertEqual([check['exitCode'] for check in result['checks'] if check['label'] in {'修复前复现', '定向验证', '回归测试'}], [1, 1, 0, 0])
        self.assertEqual(RepairAfterEvidence.sessions, 1)
        self.assertEqual(len(self.service.store.get(self.loop['id'])['runs']), 1)
        self.assertEqual([item['stepId'] for item in result['stepResults']], ['reproduce', 'repair', 'repair', 'deliver'])
        report = next(item for item in result['artifacts'] if item['title'] == '验证记录')
        evidence = json.loads(Path(report['path']).read_text())['checks']
        self.assertEqual(evidence, result['checks'][:-1])  # The delivery process records its own exit after writing the report.
        patch = next(item for item in result['artifacts'] if item['title'] == '代码补丁')
        self.assertIn('checkout.py', Path(patch['path']).read_text())
        self.assertIn('tests/test_discount.py', Path(patch['path']).read_text())
        subprocess.run(['git', 'apply', '--check', patch['path']], cwd=source, check=True, capture_output=True)
        self.assertEqual(original, {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*.py')})
        runner.stop(self.loop['id'], run['id'])
        self.assertEqual(self.service.store.run(self.loop['id'], run['id'])['status'], 'success')

    async def test_engineering_checks_reject_rewritten_tests_despite_success_claims(self):
        class WeakensTests:
            def __init__(self, cwd, emit): self.cwd = Path(cwd)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def ask(self, prompt, readonly=False):
                if prompt.startswith('test-stage:reproduce'):
                    (self.cwd / 'tests/test_discount.py').write_text(REPRODUCTION)
                else:
                    (self.cwd / 'tests/test_discount.py').write_text(REPRODUCTION.replace('180)', '190)'))
                return '所有检查均已通过。'
        value = definition()
        value['steps'][1]['checks'].insert(0, protected_tests_check(Path(self.fixture['resources'][0]['path'])))
        value['steps'][1]['onFailure'] = {'action': 'stop'}
        self.service.store.update_definition(self.loop['id'], value)
        runner = Runner(self.service.store, lambda: None, session_factory=WeakensTests)
        run = runner.start(self.loop['id'])
        await runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('保护已有与新增测试', result['reason'])
        self.assertEqual(result['checks'][-1]['exitCode'], 1)
        self.assertFalse(result['checks'][-1]['passed'])
        self.assertEqual([s['stepId'] for s in result['stepResults']], ['reproduce', 'repair'])
        self.assertEqual(result['artifacts'], [])

    async def test_invalid_stored_definitions_and_string_inputs_do_not_launch_processes(self):
        loop = self.service.store.get(self.loop['id'])
        current = copy.deepcopy(loop)
        loop['definition'].pop('schemaVersion')
        self.service.store.put(loop)
        with self.assertRaisesRegex(ValueError, '通用配置'):
            self.service.runner.start(loop['id'])
        self.assertEqual(self.service.runner.tasks, {})
        self.assertEqual(self.service.store.get(loop['id'])['runs'], [])
        self.service.store.put(current)
        with self.assertRaisesRegex(ValueError, '输入标识'):
            self.service.runner.start(loop['id'], self.fixture['inputDefaults']['source'])
        self.assertEqual(self.service.store.get(loop['id'])['runs'], [])


if __name__ == '__main__':
    unittest.main()
