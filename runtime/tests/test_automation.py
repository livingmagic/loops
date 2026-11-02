import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from loops_runtime.bridge import Service
from test_workflow import flow

SOURCE = '''from pathlib import Path
import hashlib

def sample(ctx):
    path = Path(ctx.config['path'])
    ctx.emit(hashlib.sha256(path.read_bytes()).hexdigest(), {'source': ctx.file(path)}, checkpoint={'offset': 1})

def run(ctx):
    sample(ctx)
'''
SINK = '''from pathlib import Path
import hashlib

def run(ctx):
    path = Path(ctx.config['directory']) / (hashlib.sha256(ctx.delivery_key.encode()).hexdigest()[:12] + '.txt')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(Path(ctx.inputs['report']).read_bytes())
    ctx.receipt(target=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
'''

class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root / 'db', lambda e: None, workspace_root=self.root / 'workspaces')
        self.a = self.service.automations
        self.source = self.root / 'source.txt'; self.source.write_text('Real pipeline input')
        self.loops = []
        for _ in range(2):
            loop = self.service.store.new()
            await self.service.handle('definition', {'loopId': loop['id'], 'definition': flow()})
            self.loops.append(await self.service.handle('save', {'loopId': loop['id']}))
        self.spec = {'title': '自动文本处理', 'source': {'script': SOURCE, 'config': {'path': str(self.source)}, 'lifecycle': 'once'},
            'nodes': [{'id': 'first', 'loopId': self.loops[0]['id'], 'bindings': {'source': 'input.source'}}, {'id': 'second', 'loopId': self.loops[1]['id'], 'bindings': {'source': 'first.convert.report'}}],
            'outputs': [{'id': 'delivery', 'title': '交付', 'script': SINK, 'config': {'directory': str(self.root / 'delivered')}, 'bindings': {'report': 'second.convert.report'}}],
            'policy': {'queueLimit': 10, 'scriptTimeout': 5, 'sourceRestarts': 0}}

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close(); self.temp.cleanup()

    async def save(self, spec=None):
        return await self.service.handle('automation_save', {'spec': spec or self.spec})

    async def settle(self, task_id, listener=False):
        async def wait():
            while True:
                await asyncio.sleep(.01)
                runs = self.a.runs(task_id)
                if runs and all(r['status'] in {'success','failed','unknown','sample','stopped','interrupted'} for r in runs) and task_id not in self.a.jobs and (listener or task_id not in self.a.sources): return runs
        return await asyncio.wait_for(wait(), 12)

    async def manual(self, task):
        accepted = await self.service.handle('automation_run', {'taskId': task['id'], 'inputs': {'source': {'kind':'file','path':str(self.source)}}})
        await self.settle(task['id'])
        return self.a.run(accepted['runId'])

    async def test_real_source_two_loops_receipt_and_sample_is_not_execution(self):
        task = await self.save()
        sample = await self.service.handle('automation_sample', {'taskId':task['id']})
        self.assertEqual(sample['runs'][0]['status'],'sample')
        self.assertFalse((self.root/'delivered').exists())
        await self.a.enable(task['id'],True)
        result = (await self.settle(task['id']))[-1]
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(Path(result['nodes']['delivery']['receipt']['target']).read_text(),'REAL PIPELINE INPUT')
        self.assertEqual(self.a.task(task['id'])['checkpoint'],{'offset':1})
        self.assertEqual([len(self.service.store.get(l['id'])['runs']) for l in self.loops],[1,1])

    async def test_input_provenance_survives_replay_and_legacy_times_are_not_invented(self):
        task = await self.save()
        accepted = self.a.accept(task['id'], 'source-event', {'source': {'kind': 'file', 'path': str(self.source)}}, sample=True)
        sample = self.a.run(accepted['runId'])
        replay = self.a.run(self.a.replay(task['id'], sample['id'])['runId'])
        self.assertEqual(sample['inputOrigin']['kind'], 'collected')
        self.assertEqual(replay['inputOrigin']['kind'], 'reused')
        self.assertEqual(replay['inputOrigin']['collectedAt'], sample['createdAt'])
        self.assertEqual(replay['inputOrigin']['runId'], sample['id'])
        self.assertEqual(replay['inputs']['source']['name'], 'source.txt')
        again = self.a.run(self.a.replay(task['id'], replay['id'])['runId'])
        self.assertEqual(again['inputOrigin']['collectedAt'], sample['createdAt'])
        replay.pop('inputOrigin')
        self.a.put('automation_runs', replay)
        view = next(r for r in self.a.detail(task['id'])['runs'] if r['id'] == replay['id'])
        self.assertEqual(view['inputNode']['kind'], 'snapshot')
        self.assertIsNone(view['inputNode']['collectedAt'])
        self.assertTrue(view['inputLabels']['source'])

    async def test_dedupe_conflict_queue_capacity_checkpoint_and_snapshot(self):
        spec=copy.deepcopy(self.spec); spec['policy']['queueLimit']=1; task=await self.save(spec)
        inputs={'source':{'kind':'file','path':str(self.source)}}
        one=self.a.accept(task['id'],'one',inputs,{'offset':1})
        two=self.a.accept(task['id'],'one',inputs,{'offset':99})
        self.assertEqual(one['runId'],two['runId']); self.assertTrue(two['duplicate'])
        self.source.write_text('changed')
        with self.assertRaisesRegex(ValueError,'内容发生变化'): self.a.accept(task['id'],'one',inputs)
        with self.assertRaisesRegex(ValueError,'队列已满'): self.a.accept(task['id'],'two',inputs,{'offset':2})
        self.assertEqual(self.a.task(task['id'])['checkpoint'],{'offset':1})
        self.assertEqual(len(self.a.runs(task['id'])),1)
        self.assertEqual(Path(self.a.run(one['runId'])['inputs']['source']['path']).read_text(),'Real pipeline input')

    async def test_pinned_version_does_not_overwrite_edited_loop(self):
        task=await self.save(); changed=flow(); changed['scripts']['scripts/process.py']=changed['scripts']['scripts/process.py'].replace('.upper()', '.lower()')
        await self.service.handle('definition', {'loopId':self.loops[0]['id'],'definition':changed})
        result=await self.manual(task)
        self.assertEqual(result['status'],'success',result)
        current=self.service.store.get(self.loops[0]['id'])
        self.assertEqual(current['revision'],2); self.assertEqual(current['runs'][0]['revision'],1)
        self.assertIn('.lower()',current['definition']['scripts']['scripts/process.py'])
        self.assertEqual(Path(result['nodes']['first']['outputs']['convert.report']['path']).read_text(),'REAL PIPELINE INPUT')

    async def test_failed_delivery_only_retries_output_with_stable_delivery_key(self):
        spec=copy.deepcopy(self.spec); marker=self.root/'allow'
        spec['outputs'][0]['script']=f"def run(ctx):\n    from pathlib import Path\n    assert Path({str(marker)!r}).exists(), 'unavailable'\n    ctx.receipt(target='local-confirmed', key=ctx.delivery_key)\n"
        task=await self.save(spec); run=await self.manual(task)
        self.assertEqual(run['status'],'unknown',run)
        before=run['nodes']['first']['runId']; key=run['nodes']['delivery']['deliveryKey']
        with self.assertRaisesRegex(ValueError,'核对'): self.a.retry(task['id'],run['id'])
        marker.touch(); self.a.retry(task['id'],run['id'],True); await self.settle(task['id']); result=self.a.run(run['id'])
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(result['nodes']['first']['runId'],before)
        self.assertEqual(result['nodes']['delivery']['receipt']['key'],key)
        self.assertEqual(result['nodes']['delivery']['attempts'],2)
        self.assertEqual(result['nodes']['delivery']['history'][0]['status'],'unknown')
        self.assertIn('unavailable',result['nodes']['delivery']['history'][0]['error'])

    async def test_multiple_targets_missing_receipt_does_not_claim_success(self):
        spec=copy.deepcopy(self.spec); sink=copy.deepcopy(spec['outputs'][0]); sink.update(id='other',script='def run(ctx):\n    print("success")\n'); spec['outputs'].append(sink)
        task=await self.save(spec); run=await self.manual(task)
        self.assertEqual(run['status'],'unknown',run)
        self.assertEqual(run['nodes']['delivery']['status'],'success')
        self.assertIn('未提供投递回执',run['error'])

    async def test_persisted_receipt_survives_subsequent_script_error(self):
        spec=copy.deepcopy(self.spec); spec['outputs'][0]['script']="def run(ctx):\n    ctx.receipt(target='remote', status='accepted')\n    raise RuntimeError('after receipt')\n"
        task=await self.save(spec); run=await self.manual(task)
        self.assertEqual(run['status'],'success',run)
        self.assertEqual(run['nodes']['delivery']['receipt']['status'],'accepted')

    async def test_pause_queue_restore_and_referenced_loop_deletion(self):
        task=await self.save(); accepted=self.a.accept(task['id'],'queued',{'source':{'kind':'file','path':str(self.source)}})
        await asyncio.sleep(.03); self.assertEqual(self.a.run(accepted['runId'])['status'],'queued')
        with self.assertRaisesRegex(ValueError,'自动化任务'): await self.service.handle('delete',{'loopId':self.loops[0]['id']})
        await self.a.handle('automation_delete',{'taskId':task['id']}); self.assertEqual(self.a.list(),[])
        await self.a.handle('automation_restore',{'taskId':task['id']}); self.assertFalse(self.a.task(task['id'])['enabled'])
        await self.a.enable(task['id'],True); await self.settle(task['id']); self.assertEqual(self.a.run(accepted['runId'])['status'],'success')

    async def test_invalid_reference_syntax_missing_input_and_type_rejected(self):
        for ref in ['second.convert.report','first.missing.file','unknown']:
            spec=copy.deepcopy(self.spec); spec['nodes'][0]['bindings']['source']=ref
            with self.assertRaises(ValueError): await self.save(spec)
        spec=copy.deepcopy(self.spec); spec['source']['script']='def bad('
        with self.assertRaisesRegex(ValueError,'语法错误'): await self.save(spec)
        spec=copy.deepcopy(self.spec); spec['source']['script']='def run(ctx):\n    pass\n'
        with self.assertRaisesRegex(ValueError,'sample'): await self.save(spec)
        task=await self.save()
        with self.assertRaisesRegex(ValueError,'缺少输入'): self.a.accept(task['id'],'missing',{})
        with self.assertRaisesRegex(ValueError,'需要文件'): self.a.accept(task['id'],'wrong',{'source':'text'})
        self.assertEqual(self.a.runs(task['id']),[])

    async def test_busy_loop_wait_and_cancel_does_not_stop_unrelated_run(self):
        slow=flow("import time,json,os\nfrom pathlib import Path\ntime.sleep(.5)\nc=json.loads(os.environ['LOOPS_CONTEXT']); Path(c['outputs']['report']).write_text('manual')")
        await self.service.handle('definition',{'loopId':self.loops[0]['id'],'definition':slow})
        manual=await self.service.handle('start',{'loopId':self.loops[0]['id'],'inputs':{'source':str(self.source)}})
        task=await self.save(); accepted=await self.a.handle('automation_run',{'taskId':task['id'],'inputs':{'source':{'kind':'file','path':str(self.source)}}})
        async def waiting():
            while self.a.run(accepted['runId'])['status']!='waiting': await asyncio.sleep(.01)
        await asyncio.wait_for(waiting(),3); await self.a.stop(task['id'],accepted['runId'])
        self.assertEqual(self.a.run(accepted['runId'])['status'],'stopped')
        await self.service.runner.tasks[manual['id']]
        self.assertEqual(self.service.store.run(self.loops[0]['id'],manual['id'])['status'],'success')
        self.assertEqual((await self.manual(task))['status'],'success')

    async def test_listener_pause_resume_checkpoint_and_environment_isolation(self):
        spec=copy.deepcopy(self.spec); spec['source']['lifecycle']='service'
        spec['source']['script']=SOURCE+"\ndef run(ctx):\n    import os,time\n    assert 'DEEPSEEK_API_KEY' not in os.environ\n    if not ctx.checkpoint: sample(ctx)\n    while True: time.sleep(.1)\n"
        task=await self.save(spec); previous=os.environ.get('DEEPSEEK_API_KEY'); os.environ['DEEPSEEK_API_KEY']='must-not-reach-child'
        try:
            await self.a.enable(task['id'],True)
            async def receive():
                while not self.a.task(task['id'])['checkpoint']: await asyncio.sleep(.01)
            await asyncio.wait_for(receive(),3); await self.settle(task['id'],listener=True); await self.a.enable(task['id'],False)
            self.assertNotIn(task['id'],self.a.sources)
            await self.a.enable(task['id'],True); await asyncio.sleep(.1)
            self.assertEqual(len(self.a.runs(task['id'])),1)
        finally:
            if previous is None: os.environ.pop('DEEPSEEK_API_KEY',None)
            else: os.environ['DEEPSEEK_API_KEY']=previous

    async def test_restart_preserves_queue_marks_inflight_interrupted_and_never_replays(self):
        task=await self.save(); inputs={'source':{'kind':'file','path':str(self.source)}}
        queued=self.a.accept(task['id'],'queued',inputs); active=self.a.accept(task['id'],'active',inputs)
        run=self.a.run(active['runId']); run.update(status='running',nodes={'delivery':{'status':'running'}}); self.a.put('automation_runs',run)
        await self.service.handle('shutdown',{}); self.service.store.db.close()
        self.service=Service(self.root/'db',lambda e:None,workspace_root=self.root/'workspaces'); self.a=self.service.automations
        await self.service.handle('automation_list',{})
        self.assertEqual(self.a.run(queued['runId'])['status'],'queued')
        self.assertEqual(self.a.run(active['runId'])['status'],'interrupted')
        self.assertEqual(self.a.run(active['runId'])['nodes']['delivery']['status'],'unknown')
        self.assertFalse((self.root/'delivered').exists())

    async def test_output_test_uses_cached_results_and_does_not_repeat_other_targets(self):
        task=await self.save(); run=await self.manual(task)
        before=[len(self.service.store.get(l['id'])['runs']) for l in self.loops]
        accepted=await self.a.handle('automation_test_output',{'taskId':task['id'],'runId':run['id'],'nodeId':'delivery'})
        await self.settle(task['id']); result=self.a.run(accepted['runId'])
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(result['nodes']['first']['runId'],run['nodes']['first']['runId'])
        self.assertEqual([len(self.service.store.get(l['id'])['runs']) for l in self.loops],before)
        self.assertNotEqual(result['nodes']['delivery']['deliveryKey'],run['nodes']['delivery']['deliveryKey'])

    async def test_tampered_cached_artifact_blocks_retry_before_external_delivery(self):
        spec=copy.deepcopy(self.spec); spec['outputs'][0]['script']='def run(ctx):\n    raise RuntimeError("target unavailable")\n'
        task=await self.save(spec); run=await self.manual(task)
        Path(run['nodes']['first']['outputs']['convert.report']['path']).write_text('tampered')
        self.a.retry(task['id'],run['id'],True); await self.settle(task['id']); result=self.a.run(run['id'])
        self.assertEqual(result['status'],'failed')
        self.assertIn('已被修改',result['error'])
        self.assertEqual(result['nodes']['delivery']['attempts'],1)

    async def test_default_inputs_are_frozen_at_acceptance(self):
        current=self.service.store.get(self.loops[0]['id']); current['config']['inputDefaults']={'source':str(self.source)}
        self.service.store.put(current)
        await self.service.handle('definition',{'loopId':current['id'],'definition':flow()})
        await self.service.handle('save',{'loopId':current['id']})
        spec=copy.deepcopy(self.spec); spec['nodes'][0]['bindings']={}; task=await self.save(spec)
        accepted=self.a.accept(task['id'],'frozen-default',{},manual=True)
        self.source.write_text('changed after event')
        await self.settle(task['id']); run=self.a.run(accepted['runId'])
        self.assertEqual(run['status'],'success',run)
        self.assertEqual(Path(run['nodes']['delivery']['receipt']['target']).read_text(),'REAL PIPELINE INPUT')

    async def test_read_helpers_obey_input_kind_and_script_environment_is_isolated(self):
        spec=copy.deepcopy(self.spec)
        spec['outputs'][0]['script']=SINK.replace("path.write_bytes(Path(ctx.inputs['report']).read_bytes())", "assert ctx.inputEvidence['report']['kind'] == 'file'\n    assert ctx.read_text('report') == 'REAL PIPELINE INPUT'\n    import site,sys\n    assert not site.ENABLE_USER_SITE and sys.prefix != sys.base_prefix\n    path.write_bytes(ctx.read_bytes('report'))")
        task=await self.save(spec); run=await self.manual(task)
        self.assertEqual(run['status'],'success',run)

    async def test_conversation_draft_and_messages_survive_restart_without_saving_or_running(self):
        task=await self.a.handle('automation_new_conversation',{'seedLoopId':self.loops[0]['id']})
        await self.a.handle('automation_message',{'taskId':task['id'],'role':'user','text':'帮我自动处理文件'})
        await self.a.handle('automation_message',{'taskId':task['id'],'role':'assistant','text':'从哪里读取？'})
        updated=await self.a.handle('automation_update_draft',{'taskId':task['id'],'spec':self.spec})
        self.assertEqual(updated['revision'],0); self.assertEqual(updated['draftSpec']['title'],self.spec['title'])
        self.assertEqual(updated['runs'],[]); self.assertIsNone(updated['snapshot'])
        with self.assertRaisesRegex(ValueError,'先保存'): await self.a.sample(task['id'])
        await self.service.handle('shutdown',{}); self.service.store.db.close()
        self.service=Service(self.root/'db',lambda e:None,workspace_root=self.root/'workspaces'); self.a=self.service.automations
        current=await self.a.handle('automation_get',{'taskId':task['id']})
        self.assertEqual(len(current['messages']),2); self.assertIsNotNone(current['draftSpec']); self.assertFalse(current['enabled'])
        saved=await self.a.handle('automation_save',{'taskId':task['id']})
        self.assertEqual(saved['revision'],1); self.assertIsNone(saved['draftSpec']); self.assertEqual(len(saved['messages']),2)

    async def test_unsaved_conversation_changes_cannot_run_old_saved_version(self):
        task=await self.save()
        edited=copy.deepcopy(self.spec); edited['title']='反馈后的版本'
        await self.a.handle('automation_update_draft',{'taskId':task['id'],'spec':edited})
        for method,params in [('automation_sample',{}),('automation_enable',{'enabled':True}),('automation_run',{'inputs':{}})]:
            with self.assertRaisesRegex(ValueError,'未保存'):
                await self.a.handle(method,{'taskId':task['id'],**params})
        self.assertEqual(self.a.version(task['id'],1)['spec']['title'],self.spec['title'])
        saved=await self.a.handle('automation_save',{'taskId':task['id']})
        self.assertEqual(saved['revision'],2); self.assertEqual(saved['spec']['title'],'反馈后的版本')

    async def test_connection_broker_keeps_auth_in_host_and_redacts_echo(self):
        from http.server import BaseHTTPRequestHandler,HTTPServer
        from threading import Thread
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get('Authorization')); self.send_response(400 if self.path=='/bad' else 200); self.end_headers(); self.wfile.write(self.headers.get('Authorization','').encode())
            def log_message(self,*args): pass
        from socketserver import TCPServer
        class LocalServer(HTTPServer):
            def server_bind(self):
                TCPServer.server_bind(self)
                self.server_name='localhost'; self.server_port=self.server_address[1]
        server=LocalServer(('127.0.0.1',0),Handler); Thread(target=server.serve_forever,daemon=True).start()
        try:
            host=f'127.0.0.1:{server.server_port}'
            (self.a.store.root/'connections.local.json').write_text(json.dumps({'local':{'hosts':[host],'headers':{'Authorization':'Bearer private-test-token'}}}))
            for path in ['', '/bad']:
                result=await asyncio.to_thread(self.a.http_request,{'url':f'http://{host}{path}','connection':'local'})
                self.assertNotIn('private-test-token',result['body'])
            self.assertEqual(seen,['Bearer private-test-token']*2)
            with self.assertRaisesRegex(ValueError,'未授权'): self.a.http_request({'url':'http://example.invalid','connection':'local'})
        finally: server.shutdown(); server.server_close()
