import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from loops_runtime.agent_clients import AgentClients, locate, step_agent
from loops_runtime.bridge import Service
from loops_runtime.agent_plugins.mimo.adapter import MimoSession
from loops_runtime.definition import validate


def definition():
    return {'schemaVersion': 2, 'title': '混合任务', 'goal': '分析和复核', 'completion': '报告存在',
            'inputs': [], 'scripts': {}, 'finalOutputs': ['${steps.review.outputs.report}'],
            'steps': [{'id': key, 'label': key, 'instructions': '给出报告正文', 'inputs': {},
                       'execution': {'tool': 'agent', 'mode': 'read-only', 'session': 'shared', 'responseOutput': 'report', **extra},
                       'outputs': [{'id': 'report', 'label': '报告', 'path': key + '.txt'}], 'checks': []}
                      for key, extra in [('analyze', {}), ('review', {'agentId': 'codex'}), ('followup', {})]]}


class FakeClients(AgentClients):
    """Explicit execution double; the workflow, snapshots and output validation remain real."""
    def __init__(self):
        super().__init__()
        self._models = {"codex": None, "mimo": None}
        self.created, self.calls, self.directories = [], [], []

    async def probe(self, client_id):
        return {'id': client_id, 'name': client_id, 'ready': True, 'installed': True, 'version': '1.0.0',
                'modes': ['read-only', 'write'], 'binary': '/test-only/' + client_id, 'detail': 'test double'}

    async def configured_probe(self, key):
        return await self.model_state(await self.probe(key))

    def session(self, binding, cwd, emit, *, read_paths=(), write_paths=()):
        owner = self
        key = binding['id']
        owner.created.append(key)
        owner.directories.append((str(cwd), list(write_paths)))
        class Session:
            async def __aenter__(self):
                emit('session', 'test session', threadId=key + '-session')
                return self
            async def __aexit__(self, *args):
                pass
            async def ask(self, prompt, *, readonly=False):
                owner.calls.append((key, readonly))
                return key + ' report'
        return Session()


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(self.temp.name, lambda event: None, workspace_root=Path(self.temp.name)/'workspaces')
        self.clients = FakeClients()
        self.service.agent_clients = self.service.runner.agent_clients = self.clients
        self.loop = await self.service.handle('new', {'agentId': 'mimo'})

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def build(self):
        return await self.service.handle('definition', {'loopId': self.loop['id'], 'definition': definition()})

    async def test_default_override_session_isolation_and_frozen_run(self):
        await self.build()
        trial = self.service.runner.start(self.loop['id'])
        await self.service.runner.tasks[trial['id']]
        await self.service.handle('save', {'loopId': self.loop['id']})
        self.clients.calls.clear()
        self.clients.created.clear()
        run = await self.service.handle('start', {'loopId': self.loop['id']})
        await self.service.handle('agent_selection', {'loopId': self.loop['id'], 'agentId': 'codex'})
        await self.service.runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'success', result.get('reason'))
        self.assertEqual(self.clients.calls, [('mimo', True), ('codex', True), ('mimo', True)])
        self.assertEqual(self.clients.created, ['mimo', 'codex'])
        self.assertEqual(len(result['agentSessions']), 2)
        self.assertEqual(set(result['agentBindings']), {'mimo', 'codex'})
        self.assertEqual(result['config']['defaultAgentId'], 'mimo')
        self.assertEqual(Path(result['stepResults'][0]['outputs']['report']).read_text(), 'mimo report')
        current = self.service.store.get(self.loop['id'])
        self.assertNotEqual(current['revision'], current['savedRevision'])
        self.assertEqual(json.loads((Path(current['versions'][0]['path'])/'loop.json').read_text())['config']['defaultAgentId'], 'mimo')

    async def test_custom_directories_are_persisted_frozen_and_isolate_sessions(self):
        await self.build()
        first = Path(self.temp.name) / '项目 A'; first.mkdir()
        second = Path(self.temp.name) / '项目 B'; second.mkdir()
        for step_id, directory in [('analyze', first), ('followup', second)]:
            await self.service.handle('agent_directory', {'loopId': self.loop['id'], 'stepId': step_id, 'cwd': str(directory)})
        trial = self.service.runner.start(self.loop['id'])
        await self.service.runner.tasks[trial['id']]
        await self.service.handle('save', {'loopId': self.loop['id']})
        self.clients.calls.clear()
        self.clients.created.clear()
        self.clients.directories.clear()
        run = await self.service.handle('start', {'loopId': self.loop['id']})
        await self.service.handle('agent_directory', {'loopId': self.loop['id'], 'stepId': 'analyze', 'cwd': None})
        await self.service.runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'success', result.get('reason'))
        self.assertEqual(self.clients.directories[0], (str(first.resolve()), []))
        self.assertEqual(self.clients.directories[2], (str(second.resolve()), []))
        self.assertEqual(self.clients.created, ['mimo', 'codex', 'mimo'])
        self.assertEqual(result['definition']['steps'][0]['execution']['cwd'], str(first.resolve()))
        current = self.service.store.get(self.loop['id'])
        self.assertNotIn('cwd', current['definition']['steps'][0]['execution'])
        self.assertNotEqual(current['revision'], current['savedRevision'])
        self.assertEqual(Path(result['stepResults'][0]['outputs']['report']).read_text(), 'mimo report')

    async def test_directory_errors_do_not_change_definition_and_missing_runtime_path_fails(self):
        await self.build()
        before = self.service.store.get(self.loop['id'])['revision']
        file = Path(self.temp.name) / 'file'; file.write_text('x')
        for path in ['relative', str(file), str(file / 'missing'), 3]:
            with self.assertRaises(ValueError):
                await self.service.handle('agent_directory', {'loopId': self.loop['id'], 'stepId': 'analyze', 'cwd': path})
        self.assertEqual(self.service.store.get(self.loop['id'])['revision'], before)
        folder = Path(self.temp.name) / 'removed'; folder.mkdir()
        await self.service.handle('agent_directory', {'loopId': self.loop['id'], 'stepId': 'analyze', 'cwd': str(folder)})
        folder.rmdir()
        run = await self.service.handle('start', {'loopId': self.loop['id']})
        await self.service.runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('工作目录不存在', result['reason'])
        self.assertEqual(self.clients.calls, [])

    async def test_step_selection_can_return_to_inheritance_and_legacy_stays_codex(self):
        await self.build()
        changed = await self.service.handle('agent_selection', {'loopId': self.loop['id'], 'stepId': 'review', 'agentId': None})
        self.assertNotIn('agentId', changed['definition']['steps'][1]['execution'])
        self.assertEqual(step_agent({'tool': 'codex'}, {'defaultAgentId': 'mimo'}), 'codex')
        await self.service.handle('configure', {'loopId': self.loop['id'], 'config': {'resources': [], 'inputDefaults': {}}})
        self.assertEqual(self.service.store.get(self.loop['id'])['config']['defaultAgentId'], 'mimo')
        with self.assertRaises(ValueError):
            await self.service.handle('agent_selection', {'loopId': self.loop['id'], 'agentId': '../unknown'})
        invalid = definition()
        invalid['steps'][0]['execution']['agentId'] = '${inputs.client}'
        with self.assertRaises(ValueError):
            validate(invalid, self.loop['config'])

    async def test_unavailable_client_fails_before_any_step_without_fallback(self):
        await self.build()
        original = self.clients.probe
        async def unavailable(key):
            result = await original(key)
            if key == 'mimo':
                result.update(ready=False, detail='登录失效')
            return result
        self.clients.probe = unavailable
        run = await self.service.handle('start', {'loopId': self.loop['id']})
        await self.service.runner.tasks[run['id']]
        result = self.service.store.run(self.loop['id'], run['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('登录失效', result['reason'])
        self.assertFalse(self.clients.calls)
        self.assertFalse((Path(run['directory'])/'workspace').exists())

    async def test_automation_snapshot_uses_saved_agent_selection(self):
        await self.build()
        trial = self.service.runner.start(self.loop['id'])
        await self.service.runner.tasks[trial['id']]
        self.clients.calls.clear()
        saved = await self.service.handle('save', {'loopId': self.loop['id']})
        snapshot = json.loads((Path(saved['versions'][0]['path'])/'loop.json').read_text())
        await self.service.handle('agent_selection', {'loopId': self.loop['id'], 'agentId': 'codex'})
        run = self.service.runner.start(self.loop['id'], snapshot=snapshot)
        await self.service.runner.tasks[run['id']]
        self.assertEqual(self.clients.calls[0][0], 'mimo')


class StepSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_agent_model_reasoning_change_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            service = Service(root, lambda e: None, workspace_root=Path(root)/'workspaces')
            loop = service.store.new()
            initial = definition()
            initial['steps'][0]['execution'].update(agentId='codex', model='old-model', reasoning='high')
            service.store.update_definition(loop['id'], initial)
            state = {'id': 'mimo', 'name': 'MiMo', 'ready': True, 'modes': ['read-only', 'write'],
                     'models': [{'id': 'new-model', 'reasoningLevels': ['low', 'high']}]}
            with patch.object(service.agent_clients, 'configured_probe', AsyncMock(return_value=state)):
                changed = await service.handle('agent_selection', {'loopId': loop['id'], 'stepId': 'analyze',
                    'agentId': 'mimo', 'model': 'new-model', 'reasoning': 'low'})
                execution = changed['definition']['steps'][0]['execution']
                self.assertEqual((execution['agentId'], execution['model'], execution['reasoning']), ('mimo', 'new-model', 'low'))
                self.assertEqual(changed['definition']['steps'][1:], initial['steps'][1:])
                revision = changed['revision']
                with self.assertRaises(ValueError):
                    await service.handle('agent_selection', {'loopId': loop['id'], 'stepId': 'analyze',
                        'agentId': 'mimo', 'model': 'old-model', 'reasoning': 'high'})
                self.assertEqual(service.store.get(loop['id'])['revision'], revision)
            cleared = await service.handle('agent_selection', {'loopId': loop['id'], 'stepId': 'analyze', 'agentId': 'codex'})
            self.assertNotIn('model', cleared['definition']['steps'][0]['execution'])
            self.assertNotIn('reasoning', cleared['definition']['steps'][0]['execution'])
            await service.handle('shutdown', {})


class ClientProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def executable(self, source):
        path = self.root/'mimo'
        path.write_text('#!' + sys.executable + '\n' + source)
        path.chmod(0o755)
        return str(path)

    async def test_mimo_json_stdin_resume_and_no_host_credentials(self):
        binary = self.executable('''import os,sys,json
assert 'PRIVATE_HOST_KEY' not in os.environ
assert 'dangerously-skip-permissions' not in ' '.join(sys.argv)
assert '--model' not in sys.argv and '-m' not in sys.argv
p=json.loads(os.environ['MIMOCODE_PERMISSION']);assert p['*']=='deny' and p['read']['*']=='allow'
assert sys.stdin.read()=='task; $(not a shell)'
if '--session' in sys.argv: assert sys.argv[sys.argv.index('--session')+1]=='session-one'
def emit(kind,part): print(json.dumps({'type':kind,'sessionID':'session-one','part':part}),flush=True)
emit('step_start',{})
emit('text',{'id':'first','text':'intermediate'})
emit('step_finish',{'reason':'tool-calls'})
emit('step_start',{})
emit('text',{'id':'final','text':'result'})
emit('step_finish',{'reason':'stop'})
''')
        events = []
        with patch.dict(os.environ, {'PRIVATE_HOST_KEY': 'test-secret'}):
            async with MimoSession(self.root, lambda *args, **kw: events.append((args, kw)), binary=binary) as session:
                self.assertEqual(await session.ask('task; $(not a shell)', readonly=True), 'result')
                self.assertEqual(await session.ask('task; $(not a shell)', readonly=True), 'result')
        self.assertEqual(sum(e[0][0]=='session' for e in events), 1)

    async def test_mimo_zero_exit_without_completion_or_with_error_is_failure(self):
        for events in [[], [{'type': 'text', 'part': {'text': 'success'}}],
                       [{'type': 'error', 'error': {'message': 'secret must not leak'}}, {'type': 'step_finish', 'part': {'reason': 'stop'}}]]:
            binary = self.executable('import sys,json\nsys.stdin.read()\nfor e in '+repr(events)+': print(json.dumps(e))\n')
            async with MimoSession(self.root, lambda *a, **kw: None, binary=binary) as session:
                with self.assertRaisesRegex(RuntimeError, '未完成') as error:
                    await session.ask('task')
                self.assertNotIn('secret', str(error.exception))

    async def test_mimo_cancel_reaps_process(self):
        binary = self.executable('import sys,time\nsys.stdin.read()\nprint("{}",flush=True)\ntime.sleep(60)\n')
        async with MimoSession(self.root, lambda *a, **kw: None, binary=binary) as session:
            task = asyncio.create_task(session.ask('task'))
            while session.process is None:
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNotNone(session.process.returncode)

    async def test_mimo_discovery_does_not_call_model_or_expose_listing(self):
        registry = AgentClients()
        with patch('loops_runtime.agent_clients.locate', return_value='/local/mimo'), patch('loops_runtime.agent_clients.capture', new=AsyncMock(side_effect=['0.1.1', '\x1b[0m1 credentials\n'])) as capture:
            result = await registry.probe('mimo')
            self.assertTrue(result['ready'])
            self.assertEqual(capture.call_args_list[1].args, ('/local/mimo', 'providers', 'list'))
            self.assertNotIn('credentials', json.dumps(result))
        with patch('loops_runtime.agent_clients.locate', return_value=None):
            self.assertFalse((await registry.probe('mimo'))['installed'])

    async def test_discovery_does_not_read_native_config_or_model_history(self):
        registry = AgentClients()
        with patch('loops_runtime.agent_clients.locate', return_value='/local/mimo'), patch('loops_runtime.agent_clients.capture', new=AsyncMock(side_effect=['0.1.1', '1 credentials'])) as capture, patch.object(Path, 'read_text', side_effect=AssertionError('must not read model history')):
            state = await registry.probe('mimo')
        self.assertTrue(state['ready'])
        self.assertNotIn('model', state)
        self.assertEqual([call.args for call in capture.call_args_list], [('/local/mimo', '--version'), ('/local/mimo', 'providers', 'list')])

    async def test_discovery_coalesces_simultaneous_ui_requests_and_refreshes(self):
        registry = AgentClients()
        registry.probe = AsyncMock(side_effect=lambda key: {'id': key})
        first, second = await asyncio.gather(registry.discover(), registry.discover())
        self.assertEqual(first, second)
        self.assertEqual(registry.probe.await_count, 2)
        await registry.discover()
        self.assertEqual(registry.probe.await_count, 2)
        await registry.discover(refresh=True)
        self.assertEqual(registry.probe.await_count, 4)
        await registry.close()

    async def test_selected_agent_does_not_wait_for_unrelated_discovery(self):
        registry = AgentClients()
        slow = asyncio.Event()
        async def probe(key):
            if key == 'mimo':
                await slow.wait()
            return {'id': key}
        registry.probe = probe
        full = asyncio.create_task(registry.discover())
        try:
            selected = await asyncio.wait_for(registry.discover(ids=['codex']), 1)
            self.assertEqual([item['id'] for item in selected], ['codex'])
            self.assertFalse(full.done())
        finally:
            slow.set()
            await full
            await registry.close()

class AgentModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_persists_once_across_client_changes_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            clients = AgentClients(root)
            state = {'id': 'mimo', 'nativeModel': 'xiaomi/configured', 'models': []}
            self.assertEqual((await clients.model_state(state))['defaultModel'], 'xiaomi/configured')
            state['nativeModel'] = 'xiaomi/changed'
            self.assertEqual((await AgentClients(root).model_state(state))['defaultModel'], 'xiaomi/configured')
            self.assertTrue(json.loads((Path(root)/'agent-models.json').read_text())['mimo']['initialized'])

    async def test_missing_defaults_pick_first_model_and_low_and_preserve_selection(self):
        clients = AgentClients()
        state = {'id': 'mimo', 'nativeModel': None, 'models': [
            {'id': 'first', 'reasoningLevels': ['high', 'low', 'medium']},
            {'id': 'second', 'reasoningLevels': ['low', 'high']}]}
        first = await clients.model_state(state)
        self.assertEqual((first['defaultModel'], first['defaultReasoning']), ('first', 'low'))
        clients.save_model('mimo', 'second', 'high')
        state['nativeModel'] = 'first'
        second = await clients.model_state(state)
        self.assertEqual((second['defaultModel'], second['defaultReasoning']), ('second', 'high'))

    async def test_legacy_empty_values_are_filled_when_catalog_becomes_available(self):
        clients = AgentClients()
        clients.save_model('mimo', None)
        await clients.model_state({'id': 'mimo', 'models': []})
        state = await clients.model_state({'id': 'mimo', 'models': [{'id': 'first', 'reasoningLevels': ['high', 'medium']}]})
        self.assertEqual((state['defaultModel'], state['defaultReasoning']), ('first', 'medium'))

    async def test_native_configuration_uses_codex_profile(self):
        from loops_runtime.agent_plugins.codex.catalog import codex_config
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'CODEX_HOME': root}):
            (Path(root)/'config.toml').write_text('model="base"\nprofile="work"\n[profiles.work]\nmodel="profile-model"\n')
            self.assertEqual(codex_config()['model'], 'profile-model')

    async def test_flow_override_and_step_override_isolate_sessions_and_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            service = Service(root, lambda e: None, workspace_root=Path(root)/'workspaces')
            clients = FakeClients()
            clients.save_model('mimo', 'xiaomi/default')
            service.agent_clients = service.runner.agent_clients = clients
            seen = []
            original = clients.session
            def session(binding, *args, **kwargs):
                seen.append((binding['id'], binding.get('model')))
                return original(binding, *args, **kwargs)
            clients.session = session
            try:
                loop = await service.handle('new', {'agentId': 'mimo'})
                await service.handle('definition', {'loopId': loop['id'], 'definition': definition()})
                await service.handle('agent_model_selection', {'loopId': loop['id'], 'agentId': 'mimo', 'model': 'xiaomi/flow'})
                await service.handle('agent_model_selection', {'loopId': loop['id'], 'stepId': 'followup', 'model': 'xiaomi/step'})
                run = await service.handle('start', {'loopId': loop['id']})
                await service.runner.tasks[run['id']]
                result = service.store.run(loop['id'], run['id'])
                self.assertEqual(result['status'], 'success', result.get('reason'))
                self.assertEqual(seen, [('mimo', 'xiaomi/flow'), ('codex', None), ('mimo', 'xiaomi/step')])
                self.assertEqual(result['agentBindings']['mimo']['model'], 'xiaomi/flow')
                self.assertEqual(len(result['agentSessions']), 3)
                changed = await service.handle('agent_model_selection', {'loopId': loop['id'], 'stepId': 'followup', 'model': None})
                self.assertNotIn('model', changed['definition']['steps'][2]['execution'])
                changed = await service.handle('agent_model_selection', {'loopId': loop['id'], 'agentId': 'mimo', 'model': None})
                self.assertNotIn('agentModels', changed['config'])
                with self.assertRaises(ValueError):
                    await service.handle('agent_model_default', {'agentId': 'mimo', 'model': '--bad model'})
            finally:
                await service.handle('shutdown', {})
                service.store.db.close()

    async def test_adapters_forward_explicit_model(self):
        from loops_runtime.agent_plugins.codex.adapter import CodexSession
        from types import SimpleNamespace
        with patch('loops_runtime.agent_plugins.codex.adapter.AsyncCodex') as sdk:
            client = sdk.return_value
            client.__aenter__ = AsyncMock()
            client.thread_start = AsyncMock(return_value=SimpleNamespace(id='thread'))
            client.close = AsyncMock()
            async with CodexSession('/tmp', lambda *a, **k: None, model='chosen-model', reasoning='high') as session:
                self.assertEqual(client.thread_start.call_args.kwargs['model'], 'chosen-model')
                session.thread.turn = AsyncMock(side_effect=RuntimeError('test stop'))
                with self.assertRaisesRegex(RuntimeError, 'test stop'):
                    await session.ask('hello')
                self.assertEqual(session.thread.turn.call_args.kwargs['effort'], 'high')
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root)/'mimo'
            binary.write_text('#!' + sys.executable + '''
import sys,json
assert sys.argv[sys.argv.index('--model')+1]=='xiaomi/chosen'
assert sys.argv[sys.argv.index('--variant')+1]=='high'
sys.stdin.read()
print(json.dumps({'type':'text','sessionID':'one','part':{'text':'OK'}}))
print(json.dumps({'type':'step_finish','sessionID':'one','part':{'reason':'stop'}}))
''')
            binary.chmod(0o755)
            async with MimoSession(root, lambda *a, **k: None, binary=str(binary), model='xiaomi/chosen', reasoning='high') as session:
                self.assertEqual(await session.ask('hello'), 'OK')

    async def test_mimo_delivery_access_is_only_added_in_write_mode(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            delivery = root / 'delivery'; delivery.mkdir()
            binary = root / 'mimo-permissions'
            binary.write_text('#!' + sys.executable + '\n' + """
import os,json,sys
permissions=json.loads(os.environ['MIMOCODE_PERMISSION'])
print(json.dumps({'type':'text','sessionID':'one','part':{'text':json.dumps(permissions)}}))
print(json.dumps({'type':'step_finish','sessionID':'one','part':{'reason':'stop'}}))
""")
            binary.chmod(0o755)
            for readonly in [False, True]:
                async with MimoSession(root, lambda *a, **k: None, binary=str(binary), write_paths=[delivery]) as session:
                    permissions = json.loads(await session.ask('hello', readonly=readonly))
                rule = permissions['external_directory'].get(str(delivery.resolve() / '*'))
                self.assertEqual(rule, None if readonly else 'allow')
                if readonly:
                    self.assertEqual(permissions['*'], 'deny')

    async def test_catalog_filters_private_metadata_and_keeps_native_levels(self):
        from loops_runtime.agent_plugins.mimo.catalog import parse_mimo_models
        model = {'id': 'test', 'providerID': 'xiaomi', 'name': 'Test Model', 'capabilities': {'toolcall': True},
                 'headers': {'Authorization': 'private'}, 'options': {'apiKey': 'private'},
                 'variants': {'low': {}, 'custom-depth': {}, 'hidden': {'disabled': True}}}
        catalog = parse_mimo_models('xiaomi/test\n' + json.dumps(model, indent=2))
        self.assertEqual(catalog[0]['reasoningLevels'], ['low', 'custom-depth'])
        self.assertNotIn('private', json.dumps(catalog))

    async def test_reasoning_validation_migration_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'agent-models.json').write_text('{"mimo":"xiaomi/old"}')
            clients = AgentClients(root)
            self.assertEqual(clients._models['mimo'], 'xiaomi/old')
            state = {'id': 'mimo', 'models': [{'id': 'xiaomi/new', 'reasoningLevels': ['low', 'high']}],
                     'nativeModel': 'xiaomi/new'}
            clients.discover = AsyncMock(return_value=[state])
            await clients.configure_model('mimo', 'xiaomi/new', 'high')
            restarted = AgentClients(root)
            self.assertEqual(restarted._models['mimo'], 'xiaomi/new')
            self.assertEqual(restarted._efforts['mimo'], 'high')
            with self.assertRaisesRegex(ValueError, '不支持'):
                await clients.configure_model('mimo', 'xiaomi/new', 'ultra')
            self.assertEqual(clients._efforts['mimo'], 'high')
            with self.assertRaisesRegex(ValueError, '模型列表'):
                await clients.configure_model('mimo', 'xiaomi/missing', None)
            with self.assertRaisesRegex(ValueError, '明确的模型'):
                await clients.configure_model('mimo', None, 'low')
            with self.assertRaisesRegex(ValueError, '明确的思考强度'):
                await clients.configure_model('mimo', 'xiaomi/new', None)

    async def test_native_reasoning_initializes_without_overwriting_user_choice(self):
        clients = AgentClients()
        state = {'id': 'codex', 'installed': True, 'binary': '/local/codex',
                 'nativeModel': 'native', 'nativeReasoning': 'high', 'models': [{'id': 'native', 'reasoningLevels': ['low', 'high']}]}
        first = await clients.model_state(state)
        self.assertEqual(first['defaultReasoning'], 'high')
        clients.save_model('codex', 'native', 'low')
        self.assertEqual((await clients.model_state(state))['defaultReasoning'], 'low')

    async def test_refresh_does_not_read_client_defaults_again(self):
        from loops_runtime.agent_clients import CLIENTS
        clients = AgentClients()
        clients.probe = AsyncMock(return_value={'id': 'mimo', 'ready': True, 'installed': True, 'binary': '/test'})
        catalog = AsyncMock(return_value={'models': [], 'nativeModel': 'xiaomi/first'})
        with patch.dict(CLIENTS['mimo'], {'catalog': catalog}):
            await clients.configured_probe('mimo')
            await clients.configured_probe('mimo')
        self.assertTrue(catalog.call_args_list[0].kwargs['include_defaults'])
        self.assertFalse(catalog.call_args_list[1].kwargs['include_defaults'])

    async def test_reasoning_overrides_persist_and_resolve_per_step(self):
        from loops_runtime.agent_clients import resolve_selection
        with tempfile.TemporaryDirectory() as root:
            service = Service(root, lambda e: None, workspace_root=Path(root)/'workspaces')
            try:
                loop = await service.handle('new', {'agentId': 'mimo'})
                await service.handle('definition', {'loopId': loop['id'], 'definition': definition()})
                changed = await service.handle('agent_model_selection', {'loopId': loop['id'], 'agentId': 'mimo', 'model': 'model-a', 'reasoning': 'high'})
                self.assertEqual(changed['config']['agentReasoning']['mimo'], 'high')
                changed = await service.handle('agent_model_selection', {'loopId': loop['id'], 'stepId': 'analyze', 'model': 'model-a', 'reasoning': 'low'})
                self.assertEqual(changed['definition']['steps'][0]['execution']['reasoning'], 'low')
                binding = {'name': 'MiMo', 'model': 'model-a', 'reasoning': 'high', 'models': [{'id': 'model-a', 'reasoningLevels': ['low','high']}]}
                selected = resolve_selection(binding, changed['definition']['steps'][0]['execution'])
                self.assertEqual(selected['reasoning'], 'low')
                self.assertEqual(binding['reasoning'], 'high')
                with self.assertRaisesRegex(ValueError, '不支持'):
                    resolve_selection(binding, {'reasoning': 'ultra'})
                changed = await service.handle('agent_model_selection', {'loopId': loop['id'], 'agentId': 'mimo', 'model': None, 'reasoning': None})
                self.assertNotIn('agentReasoning', changed['config'])
            finally:
                await service.handle('shutdown', {})
                service.store.db.close()
