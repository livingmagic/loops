"""Shell and stdio MCP entry point. Credentials stay in the local peer profile."""
import argparse
import asyncio
import base64
import json
import fcntl
import os
from pathlib import Path
import secrets
import sys
import time

# The absolute script entry point also works with connectors' isolated cwd/env.
if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loops_runtime.sharing_network import discover, request
from loops_runtime.store import now


def read_peer(directory, peer_id):
    if not peer_id or not peer_id.startswith('peer-') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in peer_id):
        raise ValueError('请提供有效的 --peer 连接标识。')
    return json.loads((directory / 'sharing' / 'peers' / f'{peer_id}.json').read_text())


def call(peer, method, params=None, **kwargs):
    return request(peer['address'], method, params, token=peer['token'], fingerprint=peer['fingerprint'], **kwargs)


def mcp_server(peer, directory):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    server = FastMCP('Loops shared workflows')
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)

    @server.tool(annotations=read)
    async def list_flows() -> dict:
        """Discover approved shared workflows, descriptions, input contracts and published revisions."""
        return await asyncio.to_thread(call, peer, 'flows')

    @server.tool(annotations=write)
    async def submit_flow(flow_id: str, revision: int, inputs: dict, request_id: str) -> dict:
        """Start a shared workflow asynchronously. Reuse request_id only for identical retries. File inputs use {name,base64}. Poll get_task until success/failed/stopped/interrupted, then download outputs. Queued/running is not completion."""
        return await asyncio.to_thread(call, peer, 'submit', {'flowId': flow_id, 'revision': revision, 'inputs': inputs, 'requestId': request_id})

    @server.tool(annotations=read)
    async def get_task(task_id: str) -> dict:
        """Get real task status, returned data and output file references. Only success confirms completion."""
        return await asyncio.to_thread(call, peer, 'task', {'taskId': task_id})

    @server.tool(annotations=write)
    async def cancel_task(task_id: str) -> dict:
        """Cancel a queued task or stop an executing task owned by this client."""
        return await asyncio.to_thread(call, peer, 'cancel', {'taskId': task_id})

    @server.tool(annotations=write)
    async def download_file(task_id: str, index: int) -> dict:
        """Download a task output to this client's local downloads directory. Returns the local file path."""
        folder = directory / 'sharing' / 'downloads' / peer['id']
        folder.mkdir(parents=True, exist_ok=True)
        import hashlib
        target = folder / (hashlib.sha256(task_id.encode()).hexdigest()[:20] + f'-{index}')
        return await asyncio.to_thread(call, peer, 'file', {'taskId': task_id, 'index': index}, download=target)

    server.run(transport='stdio')


def main():
    parser = argparse.ArgumentParser(description='连接已授权的 Loops 共享流程')
    parser.add_argument('--data-dir', required=True, type=Path, help='独立的本机客户端配置目录')
    parser.add_argument('--peer')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('discover')
    connect = commands.add_parser('connect')
    connect.add_argument('address')
    connect.add_argument('--name', required=True)
    commands.add_parser('status')
    commands.add_parser('flows')
    run = commands.add_parser('run')
    run.add_argument('flow_id')
    run.add_argument('--revision', required=True, type=int)
    run.add_argument('--inputs', type=Path, help='输入 JSON 文件')
    run.add_argument('--request-id', required=True)
    run.add_argument('--wait', action='store_true')
    for name in ('task', 'cancel'):
        commands.add_parser(name).add_argument('task_id')
    download = commands.add_parser('download')
    download.add_argument('task_id')
    download.add_argument('index', type=int)
    download.add_argument('destination', type=Path)
    commands.add_parser('mcp')
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    if args.command == 'discover':
        result = asyncio.run(discover())
    elif args.command == 'connect':
        folder = directory / 'sharing' / 'peers'
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        with os.fdopen(os.open(folder / '.connect.lock', os.O_WRONLY | os.O_CREAT, 0o600), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            identity = request(args.address, 'identity')
            known = [json.loads(path.read_text()) for path in sorted(folder.glob('peer-*.json'))]
            peer = next((item for item in known if item['hostId'] == identity['id']), None)
            if peer and peer['fingerprint'] != identity['fingerprint']:
                raise ValueError('对方身份已变化，无法复用原连接。')
            peer = peer or {'id': 'peer-' + secrets.token_hex(6), 'hostId': identity['id'], 'fingerprint': identity['fingerprint'], 'token': secrets.token_urlsafe(32), 'createdAt': now()}
            peer.update(name=identity['name'], address=args.address)
            peer.update(call(peer, 'pair', {'name': args.name, 'token': peer['token'], 'retry': True}), online=True)
            target = folder / f'{peer["id"]}.json'
            temporary = target.with_suffix('.connect.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(peer, output)
            os.replace(temporary, target)
        result = {'peer': peer['id'], 'status': peer['status'], 'code': peer['code'], 'message': '连接已授权。' if peer['status'] == 'approved' else '请在对方 Loops 核对配对码并同意连接。'}

    else:
        peer = read_peer(directory, args.peer)
        if args.command == 'mcp':
            mcp_server(peer, directory)
            return
        if args.command == 'status':
            result = call(peer, 'pair_status', {'token': peer['token']})
        elif args.command == 'flows':
            result = call(peer, 'flows')
        elif args.command == 'run':
            inputs = json.loads(args.inputs.read_text()) if args.inputs else {}
            # Local file references are converted here; they are never interpreted on the server.
            for key, value in inputs.items():
                if isinstance(value, dict) and set(value) == {'file'}:
                    file = Path(value['file']).expanduser()
                    if file.stat().st_size > 10_000_000:
                        raise ValueError('输入文件不能超过 10MB。')
                    inputs[key] = {'name': file.name, 'base64': base64.b64encode(file.read_bytes()).decode()}
            result = call(peer, 'submit', {'flowId': args.flow_id, 'revision': args.revision, 'inputs': inputs, 'requestId': args.request_id})
            while args.wait and result['status'] in {'queued', 'running', 'correcting', 'stopping'}:
                time.sleep(1)
                result = call(peer, 'task', {'taskId': result['id']})
        elif args.command == 'download':
            result = call(peer, 'file', {'taskId': args.task_id, 'index': args.index}, download=args.destination)
        else:
            result = call(peer, args.command, {'taskId': args.task_id})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get('status') in {'failed', 'interrupted', 'stopped'}:
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
