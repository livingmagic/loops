"""Locally approved workflow sharing. Public requests never reach Service.handle."""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import sys
import time
import tempfile
from pathlib import Path

from .definition import declared_outputs
from .sharing_network import Discovery, MAX_BODY, certificate, discover, local_addresses, pairing_code, request
from .store import ACTIVE, now, uid
from .workflow import create_run


class Sharing:
    def __init__(self, store, runner, notify):
        self.store, self.runner, self.notify = store, runner, notify
        self.directory = store.root / 'sharing'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        store.db.execute('CREATE TABLE IF NOT EXISTS sharing (kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id))')
        store.db.commit()
        self.config = self.get('config', 'local') or {'id': uid('host'), 'name': socket.gethostname().split('.')[0], 'enabled': False, 'concurrency': 2, 'port': 0}
        self.put('config', 'local', self.config)
        self.server = self.server_task = self.discovery = self.worker = None
        self.start_lock = asyncio.Lock()
        self.connect_lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.closing = False
        self.error = ''
        # Runs were recovered by Store. Queued jobs can resume, started jobs never replay.
        for job in self.rows('task'):
            run = self.run(job)
            if job['status'] == 'queued':
                self.store.update_run(job['loopId'], job['runId'], status='queued', reason='')
            elif job['status'] in ACTIVE:
                job['status'] = run['status']
                self.put('task', job['id'], job)

    def rows(self, kind):
        return [json.loads(row[0]) for row in self.store.db.execute('SELECT data FROM sharing WHERE kind=? ORDER BY rowid', (kind,))]

    def get(self, kind, key):
        row = self.store.db.execute('SELECT data FROM sharing WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, key, value):
        self.store.db.execute('INSERT INTO sharing VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data', (kind, key, json.dumps(value)))
        self.store.db.commit()
        return value

    def changed(self):
        self.wakeup.set()
        self.notify({'event': 'sharing_state'})

    def identity(self):
        return {'id': self.config['id'], 'name': self.config['name'], 'port': self.config['port'], 'protocol': 'loops-sharing-v1'}

    async def startup(self):
        if self.closing:
            return
        if not self.worker:
            self.worker = asyncio.create_task(self.dispatch())
        if not self.config['enabled'] or self.server_task:
            return
        async with self.start_lock:
            if self.server_task or self.closing:
                return
            try:
                await self.listen()
                self.error = ''
            except Exception:
                self.error = '共享服务启动失败，请检查端口占用后重新开启共享。'
                raise ValueError(self.error) from None

    async def listen(self):
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Route
        cert, key, self.fingerprint = certificate(self.directory, self.config['id'])
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.config['port']))
            sock.listen(128)
            self.config['port'] = sock.getsockname()[1]
            self.put('config', 'local', self.config)
            app = Starlette(routes=[Route('/v1/action', self.http_action, methods=['POST']), Route('/v1/files/{task_id}/{index:int}', self.http_file)])
            self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level='critical', access_log=False, lifespan='off', ssl_certfile=str(cert), ssl_keyfile=str(key), timeout_graceful_shutdown=2))
            self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
            while not self.server.started:
                if self.server_task.done():
                    await self.server_task
                    raise ValueError('共享监听未启动。')
                await asyncio.sleep(0.01)
        except BaseException:
            sock.close()
            self.server_task = None
            raise
        self.discovery = Discovery(self.identity)
        try:
            await self.discovery.start()
        except OSError:
            self.error = '局域网自动发现不可用，可通过连接地址接入。'

    async def stop_server(self):
        if self.discovery:
            self.discovery.close()
            self.discovery = None
        if self.server_task:
            self.server.should_exit = True
            await self.server_task
            self.server_task = None

    def authenticate(self, authorization):
        if not self.config['enabled']:
            raise PermissionError('共享已关闭。')
        token = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
        digest = hashlib.sha256(token.encode()).hexdigest()
        client = self.get('client', digest)
        if not client or client['status'] != 'approved':
            raise PermissionError('请先在对方 Loops 中同意连接。')
        return client

    async def http_action(self, http):
        from starlette.responses import JSONResponse
        try:
            if http.headers.get('origin'):
                raise PermissionError('请使用 Loops 客户端或已授权的命令行连接。')
            body = bytearray()
            async with asyncio.timeout(30):
                async for chunk in http.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise ValueError('请求不能超过 15 MiB。')
            payload = json.loads(body)
            method, params = payload.get('method'), payload.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('参数需要 JSON 对象。')
            if method == 'identity':
                result = self.identity()
            elif method == 'pair':
                result = self.pair(params, http.client.host)
            elif method == 'pair_status':
                digest = hashlib.sha256(str(params.get('token', '')).encode()).hexdigest()
                client = self.get('client', digest)
                self.expire_client(client)
                result = {'status': client['status'] if client else 'unknown', 'name': self.config['name']}
            else:
                client = self.authenticate(http.headers.get('authorization', ''))
                result = self.external(method, params, client)
            return JSONResponse(result)
        except PermissionError as error:
            return JSONResponse({'error': str(error)}, status_code=403)
        except ValueError as error:
            message = str(error)
            if '/' in message or '\\' in message or len(message) > 200:
                message = '请求无效，请检查流程、输入、任务标识及调用参数。'
            return JSONResponse({'error': message}, status_code=400)
        except (KeyError, TypeError):
            return JSONResponse({'error': '请求无效，请检查流程、输入、任务标识及调用参数。'}, status_code=400)
        except Exception:
            return JSONResponse({'error': '共享请求未完成，请在本机 Loops 查看记录。'}, status_code=500)

    async def http_file(self, http):
        from starlette.responses import FileResponse, JSONResponse
        try:
            client = self.authenticate(http.headers.get('authorization', ''))
            job = self.owned_task(http.path_params['task_id'], client)
            files = self.result(job)['files']
            item = next((item for item in files if item['index'] == http.path_params['index']), None)
            if item is None:
                raise ValueError('找不到产出文件。')
            run = self.run(job)
            artifact = run['artifacts'][item['index']]
            file = Path(artifact['path']).resolve()
            if not self.output_path(run, file) or not file.is_file():
                raise ValueError('产出文件不可用。')
            return FileResponse(file, filename=file.name, media_type='application/octet-stream')
        except PermissionError as error:
            return JSONResponse({'error': str(error)}, status_code=403)
        except (ValueError, KeyError, IndexError):
            return JSONResponse({'error': '产出文件不可用。'}, status_code=404)

    def expire_client(self, client):
        if client and client['status'] == 'pending' and time.time() - client['requested'] > 600:
            client['status'] = 'expired'
            self.put('client', client['id'], client)
            self.changed()

    def pair(self, params, source):
        token, name = params.get('token'), params.get('name')
        if not isinstance(token, str) or not 40 <= len(token) <= 128 or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('连接申请需要客户端名称和随机令牌。')
        digest = hashlib.sha256(token.encode()).hexdigest()
        existing = self.get('client', digest)
        self.expire_client(existing)
        if existing and (existing['status'] in {'pending', 'approved'} or params.get('retry') is not True):
            return {'status': existing['status'], 'code': existing['code']}
        pending = [c for c in self.rows('client') if c['status'] == 'pending' and time.time() - c['requested'] < 600]
        if len(pending) >= 20:
            raise ValueError('待处理申请过多，请稍后重试。')
        client = {'id': digest, 'name': name.strip(), 'source': source, 'status': 'pending', 'requested': time.time(), 'createdAt': existing['createdAt'] if existing else now(), 'code': pairing_code(self.fingerprint, token)}
        if existing and existing.get('note'):
            client['note'] = existing['note']
        self.put('client', digest, client)
        self.changed()
        return {'status': 'pending', 'code': client['code']}

    def catalog(self, include_stopped=False):
        result = []
        for published in self.rows('publication'):
            if not published['enabled'] and not include_stopped:
                continue
            try:
                snapshot = self.store.version(published['id'], published['revision'])
            except ValueError as error:
                if include_stopped:
                    loop = self.store.get(published['id'], include_deleted=True)
                    result.append({'id': published['id'], 'name': published.get('name', loop['title']), 'revision': published['revision'], 'stopped': True, 'unavailable': str(error)})
                continue
            definition = snapshot['definition']
            outputs = declared_outputs(definition)
            result.append({'id': published['id'], 'name': snapshot['title'], 'description': definition['goal'], 'revision': published['revision'],
                           'inputs': [{k: v for k, v in item.items() if k in {'id', 'label', 'kind', 'required'}} for item in definition['inputs']],
                           'outputs': [{'label': outputs[ref]['label'], 'kind': outputs[ref].get('kind', 'file')} for ref in definition['finalOutputs']], 'completion': definition['completion'], **({'stopped': not published['enabled']} if include_stopped else {})})
        return result

    @staticmethod
    def validate_client_note(note):
        if not isinstance(note, str) or len(note) > 120 or '\n' in note or '\r' in note:
            raise ValueError('备注请填写不超过 120 字的单行文本。')
        return note.strip()

    def task_client_note(self, job):
        return (self.get('client', job['clientId']) or {}).get('note', '')

    def owned_task(self, task_id, client):
        job = self.get('task', task_id)
        if not job or job['clientId'] != client['id']:
            raise PermissionError('无法访问这个任务。')
        return job

    def external(self, method, params, client):
        if method == 'flows':
            return {'flows': self.catalog()}
        if method == 'submit':
            return self.submit(params, client)
        if method in {'task', 'cancel'}:
            job = self.owned_task(params['taskId'], client)
            if method == 'cancel':
                self.cancel(job)
            return self.result(self.get('task', job['id']))
        raise ValueError('不支持的共享操作。')

    def submit(self, params, client):
        key = params.get('requestId')
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError('请提供唯一 requestId，以便安全重试。')
        digest = hashlib.sha256(json.dumps({k: params.get(k) for k in ('flowId', 'revision', 'inputs')}, sort_keys=True).encode()).hexdigest()
        for previous in self.rows('task'):
            if previous['clientId'] == client['id'] and previous['requestId'] == key:
                if previous['digest'] != digest:
                    raise ValueError('同一 requestId 的请求内容不能改变。')
                return self.result(previous)
        published = next((p for p in self.catalog() if p['id'] == params.get('flowId')), None)
        if not published or published['revision'] != params.get('revision'):
            raise ValueError('流程未发布或版本已变化，请重新获取流程列表。')
        if len([j for j in self.rows('task') if j['status'] == 'queued']) >= 100:
            raise ValueError('等待队列已满，请稍后使用同一 requestId 重试。')
        snapshot = self.store.version(published['id'], published['revision'])
        values = params.get('inputs', {})
        if not isinstance(values, dict) or set(values) - {item['id'] for item in published['inputs']}:
            raise ValueError('请按流程声明提供输入。')
        task_id = uid('shared')
        inputs = {}
        with tempfile.TemporaryDirectory(prefix='upload-', dir=self.directory) as temporary:
            uploads = Path(temporary)
            for item in published['inputs']:
                value = values.get(item['id'], '')
                if not value and item.get('required', True):
                    raise ValueError(f"请提供{item['label']}。")
                if item['kind'] == 'file' and value:
                    if not isinstance(value, dict) or set(value) != {'name', 'base64'}:
                        raise ValueError('文件输入需要 name 和 base64 内容。')
                    content = base64.b64decode(value['base64'], validate=True)
                    if len(content) > 10_000_000:
                        raise ValueError('文件不能超过 10MB。')
                    suffix = Path(str(value['name'])).suffix
                    if len(suffix) > 16 or any(c not in '.abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' for c in suffix):
                        suffix = ''
                    uploads.mkdir(parents=True, exist_ok=True)
                    file = uploads / (item['id'] + suffix)
                    file.write_bytes(content)
                    inputs[item['id']] = str(file)
                else:
                    if not isinstance(value, str) or len(value) > 100000:
                        raise ValueError('文本输入不能超过 100000 字符。')
                    inputs[item['id']] = value
            loop = self.store.get(published['id'])
            # Creating the run now freezes its version, scripts and uploaded inputs before queueing.
            run = create_run(self.store, loop, inputs, snapshot=snapshot)
        self.store.update_run(loop['id'], run['id'], sharedTask=task_id, sourceClient=client['name'])
        job = {'id': task_id, 'loopId': loop['id'], 'runId': run['id'], 'name': published['name'], 'revision': published['revision'], 'clientId': client['id'], 'clientName': client['name'], 'requestId': key, 'digest': digest, 'status': 'queued', 'createdAt': now()}
        self.put('task', task_id, job)
        self.changed()
        self.runner.notify()
        return self.result(job)

    @staticmethod
    def output_path(run, file):
        root = Path(run['directory']).resolve()
        return any(file.is_relative_to(root / folder) for folder in ('workspace', 'results'))

    def run(self, job):
        loop = self.store.get(job['loopId'], include_deleted=True)
        return next(run for run in loop['runs'] if run['id'] == job['runId'])

    def result(self, job):
        run = self.run(job)
        files = []
        for index, artifact in enumerate(run.get('artifacts', [])):
            if artifact.get('path'):
                file = Path(artifact['path']).resolve()
                if file.is_file() and self.output_path(run, file):
                    files.append({'index': index, 'name': file.name, 'label': artifact['title'], 'size': file.stat().st_size})
        outputs = {}
        declarations = declared_outputs(run['definition'])
        for ref in run['definition']['finalOutputs']:
            declaration = declarations[ref]
            if declaration.get('kind') not in {'value', 'json'}:
                continue
            _, step_id, _, output_id = ref[2:-1].split('.')
            step = next((step for step in reversed(run['stepResults']) if step['stepId'] == step_id and step['status'] == 'success'), None)
            if step and output_id in step.get('outputs', {}):
                value = step['outputs'][output_id]
                if len(json.dumps(value)) <= 100000:
                    outputs[step_id + '.' + output_id] = value
        status = job['status'] if job['status'] == 'queued' else run['status']
        return {'id': job['id'], 'flowId': job['loopId'], 'name': job['name'], 'revision': job['revision'], 'status': status,
                'createdAt': job['createdAt'], 'endedAt': run.get('endedAt'), 'files': files, 'outputs': outputs,
                'error': '流程执行失败，请联系本机用户查看运行详情。' if status in {'failed', 'interrupted'} else None}

    def cancel(self, job):
        if job['status'] == 'queued':
            job['status'] = 'stopped'
            self.put('task', job['id'], job)
            self.store.update_run(job['loopId'], job['runId'], status='stopped', endedAt=now(), reason='共享任务已取消。')
        else:
            self.runner.stop(job['loopId'], job['runId'])
        self.changed()
        self.runner.notify()

    async def dispatch(self):
        while not self.closing:
            self.wakeup.clear()
            jobs = self.rows('task')
            running = 0
            for job in jobs:
                if job['status'] in ACTIVE and job['status'] != 'queued':
                    run = self.run(job)
                    if run['status'] in ACTIVE:
                        running += 1
                    else:
                        job['status'] = run['status']
                        self.put('task', job['id'], job)
                        self.notify({'event': 'sharing_state'})
            for job in jobs:
                if job['status'] != 'queued' or running >= self.config['concurrency']:
                    continue
                client = self.get('client', job['clientId'])
                if not client or client['status'] != 'approved':
                    self.cancel(job)
                    continue
                job['status'] = 'running'
                self.put('task', job['id'], job)
                run = self.run(job)
                self.runner.launch(job['loopId'], run)
                self.runner.tasks[run['id']].add_done_callback(lambda _: self.wakeup.set())
                running += 1
                self.notify({'event': 'sharing_state'})
            await self.wakeup.wait()

    def state(self):
        clients = self.rows('client')
        for client in clients:
            self.expire_client(client)
        addresses = local_addresses()
        return {'config': self.config, 'online': bool(self.server and self.server.started and self.server_task and not self.server_task.done()), 'error': self.error,
                'addresses': [f'https://{host}:{self.config["port"]}' for host in sorted(addresses)] if self.config['port'] else [],
                'pastFlows': list({job['loopId']: {'id': job['loopId'], 'name': job['name'], 'revision': job['revision']} for job in self.rows('task')}.values()),
                'managedFlows': self.catalog(include_stopped=True), 'flows': self.catalog(), 'clients': [{k: v for k, v in c.items() if k != 'requested'} for c in clients],
                'peers': self.peers(), 'tasks': [{**self.result(j), 'clientName': j['clientName'], 'clientNote': self.task_client_note(j), 'runId': j['runId']} for j in self.rows('task')[-100:]][::-1]}

    def peers(self):
        return sorted([{k: v for k, v in json.loads(file.read_text()).items() if k != 'token'} for file in (self.directory / 'peers').glob('*.json')], key=lambda peer: peer.get('createdAt', ''))

    def peer(self, peer_id):
        if not isinstance(peer_id, str) or not peer_id.startswith('peer-') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in peer_id):
            raise ValueError('连接标识无效。')
        return json.loads((self.directory / 'peers' / f'{peer_id}.json').read_text())

    def save_peer(self, peer):
        directory = self.directory / 'peers'
        directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / f'{peer["id"]}.json'
        temporary = target.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(peer, output)
        os.replace(temporary, target)

    async def remote(self, peer, method, params=None, **kwargs):
        return await asyncio.to_thread(request, peer['address'], method, params, token=peer['token'], fingerprint=peer['fingerprint'], **kwargs)

    async def handle(self, method, params):
        if method == 'sharing_state':
            return self.state()
        if method == 'sharing_history':
            offset = params.get('offset', 0)
            if type(offset) is not int or offset < 0:
                raise ValueError('无效的记录位置。')
            jobs = [job for job in reversed(self.rows('task')) if not params.get('flowId') or job['loopId'] == params['flowId']]
            return {'items': [{key: value for key, value in {**self.result(job), 'clientName': job['clientName'], 'clientNote': self.task_client_note(job)}.items()
                               if key not in {'files', 'outputs'}} for job in jobs[offset:offset + 50]], 'hasMore': len(jobs) > offset + 50}
        if method in {'sharing_task_detail', 'sharing_task_file'}:
            job = self.get('task', params['taskId'])
            if not job:
                raise ValueError('找不到调用记录。')
            run = self.run(job)
            result = self.result(job)
            if method == 'sharing_task_file':
                item = next((item for item in result['files'] if item['index'] == params.get('index')), None)
                if not item:
                    raise ValueError('找不到产出文件。')
                path = Path(run['artifacts'][item['index']]['path'])
                with path.open('r', errors='replace') as source:
                    return {'title': item['label'], 'content': source.read(150000)}
            return {**result, 'clientName': job['clientName'], 'clientNote': self.task_client_note(job), 'startedAt': run.get('startedAt') if job['status'] != 'queued' else None,
                    'reason': run.get('reason'), 'inputs': [{'label': item['label'], **run.get('inputEvidence', {}).get(item['id'], {})} for item in run['definition']['inputs']],
                    'steps': [{'id': step['id'], 'label': step['label']} for step in run['definition']['steps']],
                    'stage': run.get('stage'), 'stepResults': run['stepResults'], 'checks': run['checks'], 'events': run['events']}
        if method == 'sharing_settings':
            concurrency = params.get('concurrency', self.config['concurrency'])
            name = params.get('name', self.config['name'])
            if type(concurrency) is not int or not 1 <= concurrency <= 32 or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ValueError('请填写名称，并设置 1 到 32 的并发数。')
            self.config.update(name=name.strip(), concurrency=concurrency, enabled=params.get('enabled', self.config['enabled']) is True)
            self.put('config', 'local', self.config)
            if self.config['enabled']:
                await self.startup()
            else:
                await self.stop_server()
        elif method == 'sharing_publish':
            loop = self.store.get(params['loopId'])
            snapshot = self.store.version(loop['id'], params.get('revision', loop['savedRevision']))
            self.put('publication', loop['id'], {'id': loop['id'], 'name': snapshot['title'], 'revision': snapshot['revision'], 'enabled': True})
            if params.get('enableSharing') is True:
                self.config['enabled'] = True
                self.put('config', 'local', self.config)
            await self.startup()
        elif method == 'sharing_resume':
            publication = self.get('publication', params['loopId'])
            if not publication:
                raise ValueError('请先发布流程。')
            self.store.version(publication['id'], publication['revision'])
            publication['enabled'] = True
            self.put('publication', publication['id'], publication)
        elif method == 'sharing_unpublish':
            publication = self.get('publication', params['loopId'])
            if publication:
                publication['enabled'] = False
                self.put('publication', publication['id'], publication)
        elif method == 'sharing_client_note':
            client = self.get('client', params['clientId'])
            if not client:
                raise ValueError('找不到该客户端。')
            client['note'] = self.validate_client_note(params.get('note'))
            self.put('client', client['id'], client)
        elif method in {'sharing_approve', 'sharing_reject', 'sharing_revoke'}:
            client = self.get('client', params['clientId'])
            if not client:
                raise ValueError('连接申请已不存在。')
            self.expire_client(client)
            expected = 'approved' if method == 'sharing_revoke' else 'pending'
            if client['status'] != expected:
                raise ValueError('授权状态已变化，请刷新后重试；失效申请需由对方重新申请。')
            if method == 'sharing_approve' and 'note' in params:
                client['note'] = self.validate_client_note(params['note'])
            client['status'] = {'sharing_approve': 'approved', 'sharing_reject': 'rejected', 'sharing_revoke': 'revoked'}[method]
            self.put('client', client['id'], client)
            if method == 'sharing_revoke':
                for job in self.rows('task'):
                    if job['clientId'] == client['id'] and job['status'] == 'queued':
                        self.cancel(job)
        elif method == 'sharing_cancel':
            job = self.get('task', params['taskId'])
            if not job:
                raise ValueError('找不到任务。')
            self.cancel(job)
        elif method == 'sharing_discover':
            return [item for item in await discover() if item['id'] != self.config['id']]
        elif method == 'sharing_connect':
            async with self.connect_lock:
                identity = await asyncio.to_thread(request, params['address'], 'identity')
                if identity['id'] == self.config['id']:
                    raise ValueError('请选择另一台 Loops 客户端。')
                known = next((peer for peer in self.peers() if peer['hostId'] == identity['id']), None)
                peer = self.peer(known['id']) if known else {'id': uid('peer'), 'hostId': identity['id'], 'fingerprint': identity['fingerprint'], 'token': secrets.token_urlsafe(32), 'createdAt': now()}
                if peer['fingerprint'] != identity['fingerprint']:
                    raise ValueError('对方身份已变化，无法复用原连接。')
                peer.update(name=identity['name'], address=params['address'])
                response = await self.remote(peer, 'pair', {'token': peer['token'], 'name': self.config['name'], 'retry': True})
                peer.update(response, online=True)
                self.save_peer(peer)
                self.changed()
                return {**self.state(), 'connectedPeerId': peer['id']}
        elif method == 'sharing_reapply':
            peer = self.peer(params['peerId'])
            response = await self.remote(peer, 'pair', {'token': peer['token'], 'name': self.config['name'], 'retry': True})
            peer.update(response, online=True)
            self.save_peer(peer)
        elif method == 'sharing_refresh_peer':
            peer = self.peer(params['peerId'])
            try:
                response = await self.remote(peer, 'pair_status', {'token': peer['token']})
            except Exception:
                peer['online'] = False
                self.save_peer(peer)
                self.changed()
                raise
            peer.update(status=response['status'], name=response.get('name', peer['name']), online=True)
            self.save_peer(peer)
        elif method == 'sharing_peer_flows':
            return await self.remote(self.peer(params['peerId']), 'flows')
        elif method == 'sharing_peer_submit':
            return await self.remote(self.peer(params['peerId']), 'submit', {key: params[key] for key in ('flowId', 'revision', 'inputs', 'requestId')})
        elif method in {'sharing_peer_task', 'sharing_peer_cancel'}:
            return await self.remote(self.peer(params['peerId']), 'task' if method.endswith('task') else 'cancel', {'taskId': params['taskId']})
        elif method == 'sharing_download':
            peer = self.peer(params['peerId'])
            job = await self.remote(peer, 'task', {'taskId': params['taskId']})
            item = next(item for item in job['files'] if item['index'] == params['index'])
            directory = self.directory / 'downloads' / peer['id'] / hashlib.sha256(job['id'].encode()).hexdigest()[:20] / str(item['index'])
            directory.mkdir(parents=True, exist_ok=True)
            filename = Path(item['name']).name
            if filename in {'', '.', '..'}:
                raise ValueError('产出文件名称无效。')
            target = directory / filename
            return await self.remote(peer, 'file', {'taskId': job['id'], 'index': item['index']}, download=target)
        elif method == 'sharing_peer_config':
            peer = self.peer(params['peerId'])
            script = str(Path(__file__).with_name('sharing_client.py'))
            return {'id': 'shared-' + peer['id'], 'name': peer['name'] + ' · 共享流程', 'type': 'mcp', 'version': '1', 'transport': 'stdio', 'command': [sys.executable, script, '--data-dir', str(self.store.root), '--peer', peer['id'], 'mcp'], 'auth': {'mode': 'none'}}
        else:
            raise ValueError('不支持的共享操作。')
        self.changed()
        return self.state()

    async def close(self):
        self.closing = True
        self.wakeup.set()
        if self.worker:
            await self.worker
        await self.stop_server()
