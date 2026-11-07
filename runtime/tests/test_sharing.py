import asyncio
import base64
import json
import tempfile
import secrets
import time
import sys
import unittest
from pathlib import Path

from loops_runtime.bridge import Service
from loops_runtime.sharing_network import request


def definition(delay=0, kind='text'):
    code = f'''import os,json,time
from pathlib import Path
c=json.loads(os.environ['LOOPS_CONTEXT'])
time.sleep({delay})
value=c['inputs']['text']
if {kind == 'file'!r}: value=Path(value).read_text()
Path(c['outputs']['report']).write_text(json.dumps({{'text': value.upper(), 'length': len(value)}}))
'''
    return {'schemaVersion': 2, 'title': '文本整理', 'goal': '将输入转换为大写并统计长度', 'completion': '返回整理结果',
            'inputs': [{'id': 'text', 'label': '内容', 'kind': kind, 'required': True}],
            'scripts': {'scripts/process.py': code},
            'steps': [{'id': 'process', 'label': '整理', 'instructions': '整理输入', 'inputs': {'text': '${inputs.text}'},
                       'execution': {'tool': 'python', 'script': 'scripts/process.py'}, 'outputs': [{'id': 'report', 'label': '整理结果', 'path': 'report.json', 'kind': 'json'}],
                       'checks': [], 'onFailure': {'action': 'stop'}}], 'finalOutputs': ['${steps.process.outputs.report}']}


class SharingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.host = Service(self.root / 'host', self.events.append)
        self.client = Service(self.root / 'client', lambda _: None)
        await self.host.handle('sharing_settings', {'enabled': True, 'name': '发布端', 'concurrency': 1})
        self.address = f"https://127.0.0.1:{self.host.sharing.config['port']}"

    async def asyncTearDown(self):
        for service in (self.client, self.host):
            await service.handle('shutdown', {})
            service.store.db.close()
        self.temp.cleanup()

    async def build(self, delay=0, kind='text', spec=None):
        loop = self.host.store.new()
        await self.host.handle('definition', {'loopId': loop['id'], 'definition': spec or definition(delay, kind)})
        value = 'hello'
        if kind == 'file':
            file = self.root / 'source.txt'
            file.write_text(value)
            value = str(file)
        run = await self.host.handle('start', {'loopId': loop['id'], 'inputs': {'text': value}})
        await self.host.runner.tasks[run['id']]
        self.assertEqual(self.host.store.run(loop['id'], run['id'])['status'], 'success')
        saved = await self.host.handle('save', {'loopId': loop['id']})
        await self.host.handle('sharing_publish', {'loopId': loop['id']})
        return saved

    async def connect(self, approve=True):
        state = await self.client.handle('sharing_connect', {'address': self.address})
        peer = state['peers'][-1]
        client = self.host.sharing.state()['clients'][-1]
        self.assertEqual(peer['code'], client['code'])
        if approve:
            await self.host.handle('sharing_approve', {'clientId': client['id']})
            await self.client.handle('sharing_refresh_peer', {'peerId': peer['id']})
        return self.client.sharing.peer(peer['id']), client

    async def submit(self, peer, loop, key='once', value='hello'):
        return await self.client.sharing.remote(peer, 'submit', {'flowId': loop['id'], 'revision': loop['savedRevision'], 'inputs': {'text': value}, 'requestId': key})

    async def finish(self, peer, job):
        async with asyncio.timeout(8):
            while job['status'] in {'queued', 'running', 'correcting', 'stopping'}:
                await asyncio.sleep(.02)
                job = await self.client.sharing.remote(peer, 'task', {'taskId': job['id']})
        return job

    async def test_authorization_discovery_execution_and_download(self):
        loop = await self.build()
        with self.assertRaisesRegex(ValueError, '同意连接'):
            await asyncio.to_thread(request, self.address, 'flows')
        peer, client = await self.connect(False)
        with self.assertRaisesRegex(ValueError, '同意连接'):
            await self.client.sharing.remote(peer, 'flows')
        await self.host.handle('sharing_approve', {'clientId': client['id']})
        catalog = await self.client.sharing.remote(peer, 'flows')
        self.assertEqual(catalog['flows'][0]['name'], '文本整理')
        self.assertNotIn('scripts', json.dumps(catalog))
        job = await self.finish(peer, await self.submit(peer, loop, value='from another client'))
        self.assertEqual(job['status'], 'success')
        self.assertEqual(job['outputs']['process.report']['text'], 'FROM ANOTHER CLIENT')
        output = await self.client.handle('sharing_download', {'peerId': peer['id'], 'taskId': job['id'], 'index': 0})
        self.assertEqual(json.loads(Path(output['path']).read_text()), job['outputs']['process.report'])
        self.assertNotIn(peer['token'], json.dumps(self.host.sharing.state()))
        self.assertNotIn(peer['token'], json.dumps(self.client.sharing.state()))
        self.assertEqual((self.client.sharing.directory / 'peers' / f"{peer['id']}.json").stat().st_mode & 0o777, 0o600)

    async def test_local_history_detail_and_file_stay_in_sharing(self):
        loop = await self.build()
        peer, client = await self.connect()
        job = await self.finish(peer, await self.submit(peer, loop, value='history input'))
        history = await self.host.handle('sharing_history', {'flowId': loop['id']})
        self.assertEqual([item['id'] for item in history['items']], [job['id']])
        self.assertEqual(history['items'][0]['clientName'], client['name'])
        self.assertNotIn('outputs', history['items'][0])
        self.assertEqual((await self.host.handle('sharing_history', {'flowId': 'different-flow'}))['items'], [])
        detail = await self.host.handle('sharing_task_detail', {'taskId': job['id']})
        self.assertEqual(detail['inputs'][0]['value'], 'history input')
        self.assertEqual(detail['steps'][0]['label'], '整理')
        self.assertEqual(detail['stepResults'][-1]['status'], 'success')
        self.assertTrue(detail['events'])
        self.assertNotIn('config', detail)
        file = await self.host.handle('sharing_task_file', {'taskId': job['id'], 'index': 0})
        self.assertEqual(json.loads(file['content'])['text'], 'HISTORY INPUT')
        with self.assertRaisesRegex(ValueError, '产出文件'):
            await self.host.handle('sharing_task_file', {'taskId': job['id'], 'index': -1})
        with self.assertRaisesRegex(ValueError, '调用记录'):
            await self.host.handle('sharing_task_detail', {'taskId': 'missing'})
        # Local inspection routes must not be exposed to remote callers.
        for method in ('sharing_history', 'sharing_task_detail', 'sharing_task_file'):
            with self.assertRaises(ValueError):
                await self.client.sharing.remote(peer, method, {'taskId': job['id'], 'index': 0})
        await self.host.handle('sharing_unpublish', {'loopId': loop['id']})
        await self.host.handle('delete', {'loopId': loop['id']})
        history_flows = (await self.host.handle('sharing_state', {}))['pastFlows']
        self.assertEqual(history_flows, [{'id': loop['id'], 'name': job['name'], 'revision': job['revision']}])
        archived = await self.host.handle('sharing_task_detail', {'taskId': job['id']})
        self.assertEqual(archived['outputs'], detail['outputs'])
        # Seed additional historical rows to verify pagination independently of execution.
        stored = self.host.sharing.get('task', job['id'])
        for index in range(52):
            extra = {**stored, 'id': f'history-{index}'}
            self.host.sharing.put('task', extra['id'], extra)
        first = await self.host.handle('sharing_history', {'flowId': loop['id']})
        second = await self.host.handle('sharing_history', {'flowId': loop['id'], 'offset': 50})
        self.assertTrue(first['hasMore'])
        self.assertFalse(second['hasMore'])
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(len(second['items']), 3)
        self.assertEqual(second['items'][-1]['id'], job['id'])

    async def test_idempotency_concurrency_revoke_and_ownership(self):
        loop = await self.build(delay=.4)
        peer, client = await self.connect()
        jobs = await asyncio.gather(*(self.submit(peer, loop, key=f'job-{i}') for i in range(3)))
        duplicate = await self.submit(peer, loop, key='job-0')
        self.assertEqual(duplicate['id'], jobs[0]['id'])
        with self.assertRaisesRegex(ValueError, 'requestId'):
            await self.submit(peer, loop, key='job-0', value='changed')
        self.assertLessEqual(len(self.host.runner.tasks), 1)
        queued = next(job for job in self.host.sharing.rows('task') if job['status'] == 'queued')
        await self.host.handle('stop', {'loopId': loop['id'], 'runId': queued['runId']})
        self.assertEqual(self.host.sharing.get('task', queued['id'])['status'], 'stopped')
        other = {**peer, 'token': secrets.token_urlsafe(32)}
        await self.client.sharing.remote(other, 'pair', {'token': other['token'], 'name': '另一调用方'})
        await self.host.handle('sharing_approve', {'clientId': self.host.sharing.rows('client')[-1]['id']})
        with self.assertRaisesRegex(ValueError, '无法访问'):
            await self.client.sharing.remote(other, 'task', {'taskId': jobs[0]['id']})
        await self.host.handle('sharing_revoke', {'clientId': client['id']})
        with self.assertRaisesRegex(ValueError, '同意连接'):
            await self.client.sharing.remote(peer, 'flows')
        self.assertEqual(self.host.sharing.get('task', jobs[-1]['id'])['status'], 'stopped')
        await self.host.handle('sharing_cancel', {'taskId': jobs[0]['id']})

    async def test_published_version_and_unpublish_preserve_accepted_tasks(self):
        loop = await self.build(delay=.2)
        peer, _ = await self.connect()
        job = await self.submit(peer, loop)
        changed = definition()
        changed['scripts']['scripts/process.py'] = changed['scripts']['scripts/process.py'].replace('value.upper()', 'value.lower()')
        await self.host.handle('definition', {'loopId': loop['id'], 'definition': changed})
        await self.host.handle('sharing_unpublish', {'loopId': loop['id']})
        self.assertEqual((await self.client.sharing.remote(peer, 'flows'))['flows'], [])
        with self.assertRaisesRegex(ValueError, '未发布'):
            await self.submit(peer, loop, key='after-unpublish')
        result = await self.finish(peer, job)
        self.assertEqual(result['outputs']['process.report']['text'], 'HELLO')
        self.assertEqual(result['revision'], loop['savedRevision'])
        self.assertEqual((await self.submit(peer, loop))['id'], job['id'])

    async def test_stop_resume_keeps_uninvoked_flow_and_published_version(self):
        loop = await self.build()
        original_revision = loop['savedRevision']
        await self.host.handle('sharing_unpublish', {'loopId': loop['id']})
        managed = self.host.sharing.state()['managedFlows']
        self.assertEqual(len(managed), 1)
        self.assertTrue(managed[0]['stopped'])
        self.assertEqual(self.host.sharing.state()['tasks'], [])
        changed = definition()
        changed['scripts']['scripts/process.py'] = changed['scripts']['scripts/process.py'].replace('value.upper()', 'value.lower()')
        await self.host.handle('definition', {'loopId': loop['id'], 'definition': changed})
        trial = await self.host.handle('start', {'loopId': loop['id'], 'inputs': {'text': 'HELLO'}})
        await self.host.runner.tasks[trial['id']]
        saved = await self.host.handle('save', {'loopId': loop['id']})
        self.assertNotEqual(saved['savedRevision'], original_revision)
        resumed = await self.host.handle('sharing_resume', {'loopId': loop['id']})
        self.assertEqual(resumed['flows'][0]['revision'], original_revision)
        peer, _ = await self.connect()
        result = await self.finish(peer, await self.submit(peer, loop, value='Hello'))
        self.assertEqual(result['outputs']['process.report']['text'], 'HELLO')
        await self.host.handle('sharing_settings', {'enabled': False})
        await self.host.handle('sharing_unpublish', {'loopId': loop['id']})
        resumed = await self.host.handle('sharing_resume', {'loopId': loop['id']})
        self.assertFalse(resumed['config']['enabled'])
        self.assertFalse(resumed['online'])
        published = await self.host.handle('sharing_publish', {'loopId': loop['id'], 'revision': saved['savedRevision']})
        self.assertFalse(published['config']['enabled'])
        self.assertEqual(published['flows'][0]['revision'], saved['savedRevision'])
        published = await self.host.handle('sharing_publish', {'loopId': loop['id'], 'enableSharing': True})
        self.assertTrue(published['online'])
        await self.host.handle('delete', {'loopId': loop['id']})
        self.assertTrue(self.host.sharing.state()['managedFlows'][0]['unavailable'])
        with self.assertRaisesRegex(ValueError, '回收站'):
            await self.host.handle('sharing_resume', {'loopId': loop['id']})

    async def test_pair_retry_reuses_connection_and_requires_fresh_approval(self):
        loop = await self.build()
        peer, client = await self.connect(False)
        connections = await asyncio.gather(*(self.client.handle('sharing_connect', {'address': self.address}) for _ in range(3)))
        self.assertEqual({state['connectedPeerId'] for state in connections}, {peer['id']})
        self.assertEqual(len(self.client.sharing.peers()), 1)
        self.assertEqual(len(self.host.sharing.rows('client')), 1)
        await self.host.handle('sharing_reject', {'clientId': client['id']})
        for rejected_status in ('rejected', 'revoked', 'expired'):
            if rejected_status == 'revoked':
                await self.host.handle('sharing_revoke', {'clientId': client['id']})
            elif rejected_status == 'expired':
                await self.host.handle('sharing_revoke', {'clientId': client['id']})
                await self.client.handle('sharing_reapply', {'peerId': peer['id']})
                pending = self.host.sharing.get('client', client['id'])
                pending['requested'] = time.time() - 601
                self.host.sharing.put('client', client['id'], pending)
            state = await self.client.handle('sharing_refresh_peer', {'peerId': peer['id']})
            self.assertEqual(state['peers'][0]['status'], rejected_status)
            self.assertEqual(self.host.sharing.state()['clients'][0]['status'], rejected_status)
            with self.assertRaisesRegex(ValueError, '状态已变化'):
                await self.host.handle('sharing_approve', {'clientId': client['id']})
            state = await self.client.handle('sharing_reapply', {'peerId': peer['id']})
            self.assertEqual(state['peers'][0]['status'], 'pending')
            self.assertEqual(self.client.sharing.peer(peer['id'])['token'], peer['token'])
            with self.assertRaisesRegex(ValueError, '同意连接'):
                await self.client.sharing.remote(peer, 'flows')
            await self.host.handle('sharing_approve', {'clientId': client['id']})
            result = await self.finish(peer, await self.submit(peer, loop, key=rejected_status))
            self.assertEqual(result['status'], 'success')
        self.assertEqual(len(self.client.sharing.peers()), 1)
        self.assertEqual(len(self.host.sharing.rows('client')), 1)
        self.assertEqual(len(self.host.sharing.rows('task')), 3)

    async def test_local_notes_distinguish_same_names_persist_and_stay_private(self):
        loop = await self.build()
        peer, client = await self.connect(False)
        other = {**peer, 'token': secrets.token_urlsafe(32)}
        await self.client.sharing.remote(other, 'pair', {'token': other['token'], 'name': client['name'], 'note': 'remote-controlled'})
        other_client = self.host.sharing.rows('client')[-1]
        self.assertEqual(other_client['name'], client['name'])
        self.assertNotIn('note', other_client)
        await self.host.handle('sharing_approve', {'clientId': client['id'], 'note': '  办公室 Mac  '})
        await self.host.handle('sharing_client_note', {'clientId': other_client['id'], 'note': '家里的电脑'})
        self.assertEqual(self.host.sharing.get('client', client['id'])['note'], '办公室 Mac')
        self.assertEqual(self.host.sharing.get('client', other_client['id'])['status'], 'pending')
        await self.host.handle('sharing_approve', {'clientId': other_client['id']})
        job = await self.finish(peer, await self.submit(peer, loop))
        self.assertNotIn('note', json.dumps(job))
        self.assertEqual((await self.host.handle('sharing_history', {}))['items'][0]['clientNote'], '办公室 Mac')
        await self.host.handle('sharing_client_note', {'clientId': client['id'], 'note': '会议室 Mac'})
        self.assertEqual((await self.host.handle('sharing_task_detail', {'taskId': job['id']}))['clientNote'], '会议室 Mac')
        self.assertEqual(self.host.sharing.get('client', other_client['id'])['note'], '家里的电脑')
        for invalid in (None, 'x' * 121, 'line1\nline2'):
            with self.assertRaisesRegex(ValueError, '120'):
                await self.host.handle('sharing_client_note', {'clientId': client['id'], 'note': invalid})
        with self.assertRaises(ValueError):
            await self.client.sharing.remote(peer, 'sharing_client_note', {'clientId': client['id'], 'note': 'remote-controlled'})
        await self.host.handle('sharing_revoke', {'clientId': client['id']})
        await self.client.handle('sharing_reapply', {'peerId': peer['id']})
        self.assertEqual(self.host.sharing.get('client', client['id'])['note'], '会议室 Mac')
        status = await self.client.sharing.remote(peer, 'pair_status', {'token': peer['token']})
        self.assertNotIn('note', status)
        await self.host.handle('shutdown', {})
        self.host.store.db.close()
        self.host = Service(self.root / 'host', self.events.append)
        self.assertEqual(self.host.sharing.get('client', client['id'])['note'], '会议室 Mac')
        await self.host.handle('sharing_client_note', {'clientId': client['id'], 'note': '  '})
        self.assertEqual(self.host.sharing.get('client', client['id'])['note'], '')
        self.assertEqual(self.host.sharing.get('client', client['id'])['status'], 'pending')

    async def test_shell_reconnect_reuses_pending_and_revoked_connection(self):
        directory = self.root / 'shell-caller'
        script = Path(__file__).parents[1] / 'loops_runtime/sharing_client.py'
        async def connect():
            process = await asyncio.create_subprocess_exec(sys.executable, str(script), '--data-dir', str(directory), 'connect', self.address, '--name', 'shell', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            self.assertEqual(process.returncode, 0, stderr.decode())
            return json.loads(stdout)
        first, second = await asyncio.gather(connect(), connect())
        self.assertEqual(first['peer'], second['peer'])
        self.assertEqual(len(list((directory / 'sharing/peers').glob('*.json'))), 1)
        client = self.host.sharing.rows('client')[0]
        await self.host.handle('sharing_approve', {'clientId': client['id']})
        self.assertEqual((await connect())['status'], 'approved')
        await self.host.handle('sharing_revoke', {'clientId': client['id']})
        retry = await connect()
        self.assertEqual(retry['peer'], first['peer'])
        self.assertEqual(retry['status'], 'pending')
        self.assertEqual(len(self.host.sharing.rows('client')), 1)

    async def test_offline_refresh_keeps_authorization_and_recovers(self):
        peer, _ = await self.connect()
        await self.host.handle('sharing_settings', {'enabled': False})
        with self.assertRaises(Exception):
            await self.client.handle('sharing_refresh_peer', {'peerId': peer['id']})
        current = self.client.sharing.peer(peer['id'])
        self.assertFalse(current['online'])
        self.assertEqual(current['status'], 'approved')
        await self.host.handle('sharing_settings', {'enabled': True, 'name': '新名称'})
        state = await self.client.handle('sharing_refresh_peer', {'peerId': peer['id']})
        self.assertTrue(state['peers'][0]['online'])
        self.assertEqual(state['peers'][0]['name'], '新名称')
        self.assertEqual(state['peers'][0]['status'], 'approved')

    async def test_file_upload_validation_and_pinned_identity(self):
        loop = await self.build(kind='file')
        peer, _ = await self.connect()
        with self.assertRaisesRegex(ValueError, '文件输入'):
            await self.submit(peer, loop, value='/etc/passwd')
        with self.assertRaisesRegex(ValueError, '请提供'):
            await self.submit(peer, loop, value='')
        with self.assertRaisesRegex(ValueError, '身份已变化'):
            await self.client.sharing.remote({**peer, 'fingerprint': 'invalid'}, 'flows')
        job = await self.finish(peer, await self.submit(peer, loop, value={'name': '../../input.txt', 'base64': base64.b64encode(b'uploaded').decode()}))
        self.assertEqual(job['outputs']['process.report']['text'], 'UPLOADED')

    async def test_restart_preserves_access_and_results(self):
        loop = await self.build()
        peer, _ = await self.connect()
        job = await self.finish(peer, await self.submit(peer, loop))
        await self.host.handle('shutdown', {})
        self.host.store.db.close()
        self.host = Service(self.root / 'host', self.events.append)
        await self.host.handle('list', {})
        self.assertEqual((await self.client.sharing.remote(peer, 'task', {'taskId': job['id']}))['status'], 'success')
        self.assertEqual((await self.submit(peer, loop))['id'], job['id'])

    async def test_restart_resumes_queue_without_replaying_started_run(self):
        loop = await self.build(delay=.4)
        peer, _ = await self.connect()
        first = await self.submit(peer, loop, key='started')
        second = await self.submit(peer, loop, key='queued')
        await self.host.handle('shutdown', {})
        self.host.store.db.close()
        self.host = Service(self.root / 'host', self.events.append)
        await self.host.handle('list', {})
        result = await self.finish(peer, second)
        self.assertEqual(result['status'], 'success')
        previous = await self.client.sharing.remote(peer, 'task', {'taskId': first['id']})
        self.assertIn(previous['status'], {'stopped', 'interrupted', 'success'})
        self.assertEqual(len(self.host.store.get(loop['id'])['runs']), 3)

    async def test_deleted_workflow_keeps_results(self):
        loop = await self.build()
        peer, _ = await self.connect()
        job = await self.finish(peer, await self.submit(peer, loop))
        await self.host.handle('delete', {'loopId': loop['id']})
        self.assertEqual(self.host.sharing.state()['flows'], [])
        self.assertEqual((await self.client.sharing.remote(peer, 'task', {'taskId': job['id']}))['outputs']['process.report']['text'], 'HELLO')
        target = self.root / 'deleted-result.json'
        await self.client.sharing.remote(peer, 'file', {'taskId': job['id'], 'index': 0}, download=target)
        self.assertEqual(json.loads(target.read_text())['text'], 'HELLO')

    async def test_failed_execution_returns_terminal_status_and_no_automatic_replay(self):
        spec = definition()
        spec['scripts']['scripts/process.py'] += "\nif not value.isalpha(): raise ValueError('Only letters')\n"
        loop = await self.build(spec=spec)
        peer, _ = await self.connect()
        job = await self.finish(peer, await self.submit(peer, loop, value='123'))
        self.assertEqual(job['status'], 'failed')
        self.assertTrue(job['error'])
        self.assertEqual((await self.submit(peer, loop, value='123'))['id'], job['id'])
        self.assertEqual(len(self.host.store.get(loop['id'])['runs']), 2)

    async def test_automation_calls_remote_workflow_through_shell_and_waits_for_result(self):
        remote_loop = await self.build()
        peer, _ = await self.connect()
        inputs = self.root / 'request.json'
        inputs.write_text(json.dumps({'text': 'automation request'}))
        caller = self.client.store.new()
        script = str(Path(__file__).parents[1] / 'loops_runtime/sharing_client.py')
        command = [sys.executable, script, '--data-dir', str(self.client.store.root), '--peer', peer['id'],
                   'run', remote_loop['id'], '--revision', str(remote_loop['savedRevision']),
                   '--inputs', '${inputs.request}', '--request-id', '${inputs.requestId}', '--wait']
        spec = {'schemaVersion': 2, 'title': '调用共享流程', 'goal': '通过授权连接调用远程流程', 'completion': '取得成功结果',
                'inputs': [{'id': 'request', 'label': '输入 JSON', 'kind': 'file'}, {'id': 'requestId', 'label': '请求标识', 'kind': 'text'}],
                'scripts': {}, 'steps': [{'id': 'remote', 'label': '远程调用', 'instructions': '等待远程流程的真实结果', 'inputs': {},
                    'execution': {'tool': 'command', 'command': command},
                    'outputs': [{'id': 'result', 'label': '结果', 'kind': 'value', 'source': 'stdout', 'format': 'json'}], 'checks': []}],
                'finalOutputs': ['${steps.remote.outputs.result}']}
        await self.client.handle('definition', {'loopId': caller['id'], 'definition': spec})
        trial = await self.client.handle('start', {'loopId': caller['id'], 'inputs': {'request': str(inputs), 'requestId': 'trial'}})
        await self.client.runner.tasks[trial['id']]
        self.assertEqual(self.client.store.run(caller['id'], trial['id'])['status'], 'success')
        await self.client.handle('save', {'loopId': caller['id']})
        delivered = self.root / 'automation-result.json'
        automation = await self.client.handle('automation_save', {'spec': {
            'title': '跨客户端自动化', 'source': {'script': 'def run(ctx): pass\ndef sample(ctx): pass', 'lifecycle': 'once', 'config': {}},
            'nodes': [{'id': 'call', 'loopId': caller['id'], 'bindings': {'request': 'input.request', 'requestId': 'input.requestId'}}],
            'outputs': [{'id': 'delivery', 'title': '交付', 'bindings': {'result': 'call.remote.result'}, 'config': {'target': str(delivered)},
                         'script': "from pathlib import Path\ndef run(ctx):\n    target=Path(ctx.config['target'])\n    target.write_bytes(Path(ctx.inputs['result']).read_bytes())\n    ctx.receipt(target=str(target))"}],
            'policy': {'queueLimit': 10, 'scriptTimeout': 10, 'sourceRestarts': 0}}})
        accepted = await self.client.handle('automation_run', {'taskId': automation['id'], 'inputs': {
            'request': {'kind': 'file', 'path': str(inputs)}, 'requestId': {'kind': 'text', 'value': 'automatic'}}})
        async with asyncio.timeout(15):
            while not delivered.exists():
                result = self.client.automations.run(accepted['runId'])
                if result['status'] in {'failed', 'unknown', 'interrupted'}:
                    self.fail(str(result))
                await asyncio.sleep(.05)
        result = json.loads(delivered.read_text())
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['outputs']['process.report']['text'], 'AUTOMATION REQUEST')

    async def test_stdio_mcp_plugin_uses_real_authorized_connection(self):
        await self.build()
        peer, _ = await self.connect()
        manifest = await self.client.handle('sharing_peer_config', {'peerId': peer['id']})
        await self.client.handle('connector_save', {'manifest': manifest})
        await self.client.handle('connector_connect', {'connectorId': manifest['id']})
        await self.client.connectors.jobs[manifest['id']]
        self.assertEqual(self.client.connectors.entry(manifest['id'])['status'], 'connected', self.client.connectors.entry(manifest['id']))
        tools = await self.client.handle('connector_operations', {'connectorId': manifest['id']})
        self.assertIn('list_flows', json.dumps(tools))
        result = await self.client.connectors.call(manifest['id'], 'list_flows', {}, role='workflow')
        self.assertIn('文本整理', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
