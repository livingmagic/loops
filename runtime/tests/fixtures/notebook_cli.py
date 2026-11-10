#!/usr/bin/env python3
"""Independent local CLI for acceptance: real files, durable auth and deliveries.

No Loops imports, configuration or special-case protocol. The local credentials
exercise lifecycle boundaries; they are not an external OAuth implementation.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def emit(value, code=0):
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(code)


args = sys.argv[1:]
home = Path(os.environ.get('NOTEBOOK_CLI_HOME', str(Path(__file__).resolve().parent / '.notebook-data')))
home.mkdir(parents=True, exist_ok=True)
with (home / 'commands.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')
schema = [
    {'name': 'records read', 'description': 'Read a named record from the local notebook.',
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]+$'}}, 'required': ['name']},
     'outputSchema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}, 'content': {'type': 'string'}}, 'required': ['ok', 'content']}, 'annotations': {'readOnlyHint': True}},
    {'name': 'notifications append', 'description': 'Append a notification to the local outbox and return its persistent ID.',
     'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1}}, 'required': ['text']},
     'outputSchema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}, 'messageId': {'type': 'string'}, 'text': {'type': 'string'}}, 'required': ['ok', 'messageId', 'text']}, 'annotations': {'readOnlyHint': False, 'destructiveHint': False}},
]
if args == ['--version']:
    print('notebook-cli 1.0.0'); raise SystemExit()
if args == ['--help']:
    print('Usage: notebook-cli [COMMAND]\n\nCommands:\n  auth           Manage durable authorization\n  records        Notebook records\n  notifications  Local notification outbox\n  schema         JSON Schema for commands')
    raise SystemExit()
if args == ['schema']:
    emit(schema)
if args == ['auth', '--help']:
    print('Commands:\n  status   Show authorization as JSON\n  login    Start a local authorization\n  refresh  Renew the short-lived access token'); raise SystemExit()
if args[-1:] == ['--help']:
    if args[:2] == ['auth', 'status']:
        print('Options:\n  --json  JSON output')
    elif args[0] == 'records':
        print('Read a notebook record. No changes are made.\n\nOptions:\n  --json <JSON>  Request body')
    elif args[0] == 'notifications':
        print('Append one local notification.\n\nOptions:\n  --json <JSON>             Request body\n  --idempotency-key string  Stable delivery key')
    else:
        print('Local authorization command')
    raise SystemExit()
credentials = home / 'credentials.json'
status = json.loads(credentials.read_text()) if credentials.exists() else {}
now = time.time()
if args[:2] in (['auth', 'login'], ['auth', 'refresh']):
    if args[1] == 'refresh' and (home / 'refresh-unavailable').exists():
        emit({'error': {'type': 'TemporaryError', 'message': 'Local renewal temporarily unavailable'}}, 1)
    if args[1] == 'refresh' and status.get('refreshUntil', 0) <= now:
        emit({'authorized': False, 'renewable': False}, 1)
    status = {'accessUntil': now + 60, 'refreshUntil': now + 86400 * 30,
              'rotation': status.get('rotation', 0) + 1, 'nonce': uuid.uuid4().hex}
    credentials.write_text(json.dumps(status)); credentials.chmod(0o600)
    emit({'authorized': True, 'renewable': True})
authorized = status.get('accessUntil', 0) > now
if args[:2] == ['auth', 'status']:
    emit({'authorized': authorized, 'renewable': status.get('refreshUntil', 0) > now,
          'account': 'Local notebook owner',
          'expiresAt': datetime.fromtimestamp(status.get('accessUntil', 0), timezone.utc).isoformat(),
          'refreshExpiresAt': datetime.fromtimestamp(status.get('refreshUntil', 0), timezone.utc).isoformat()}, 0 if authorized else 1)
if not authorized:
    emit({'error': {'type': 'AuthError', 'message': 'Login or refresh required'}}, 1)
parser = argparse.ArgumentParser()
parser.add_argument('domain'); parser.add_argument('method')
parser.add_argument('--json', required=True)
parser.add_argument('--idempotency-key', default='')
options = parser.parse_args(args)
body = json.loads(options.json)
if (options.domain, options.method) == ('records', 'read'):
    path = home / 'records' / (body['name'] + '.txt')
    emit({'ok': True, 'content': path.read_text()})
if (options.domain, options.method) == ('notifications', 'append'):
    outbox = home / 'outbox'; outbox.mkdir(exist_ok=True)
    key = options.idempotency_key or uuid.uuid4().hex
    destination = outbox / (hashlib.sha256(key.encode()).hexdigest() + '.json')
    if destination.exists():
        emit(json.loads(destination.read_text()))
    value = {'ok': True, 'messageId': uuid.uuid4().hex, 'text': body['text']}
    destination.write_text(json.dumps(value)); emit(value)
emit({'error': {'type': 'UsageError'}}, 2)
