"""Explicit native-tool stand-in for clean-machine installation/auth tests only."""
import json
import os
import sys
from pathlib import Path

root = Path(os.environ['CONNECTOR_FIXTURE_ROOT'])
args = sys.argv[1:]
with (root / 'command-log.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')
if args == ['--version']:
    print('lark-cli 1.0.96')
elif args == ['config', 'show']:
    if not (root / 'app').exists():
        print(json.dumps({'ok': False, 'error': {'type': 'config', 'subtype': 'not_configured'}}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({'appId': 'fixture-app'}))
elif args[:3] == ['config', 'init', '--new']:
    print('https://example.invalid/first-connection', flush=True)
    (root / 'app').touch()
elif args[:2] == ['auth', 'status']:
    ready = (root / 'login').exists()
    print(json.dumps({'identities': {'user': {'available': ready, 'verified': ready, 'status': 'ready' if ready else 'not_logged_in',
        'refreshExpiresAt': '2099-01-01T00:00:00Z', 'userName': '验收账号'}}}))
elif args[:2] == ['auth', 'login']:
    assert (root / 'app').exists(), 'app onboarding must precede user authorization'
    print('https://example.invalid/user-authorization', flush=True)
    (root / 'login').touch()
elif args == ['schema']:
    print(json.dumps([{'name': 'im messages read_status', 'description': 'Read status',
        'inputSchema': {'type': 'object', 'properties': {'data': {'type': 'object', 'carrier': '--data', 'properties': {'message_ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['message_ids']}}, 'required': ['data']},
        'outputSchema': {'type': 'object'}, '_meta': {'risk': 'read', 'access_tokens': ['user'], 'scopes': ['im:read']}}]))
elif args == ['skills', 'list']:
    print(json.dumps({'skills': [{'name': 'lark-im', 'description': 'Messages'}, {'name': 'lark-doc', 'description': 'Documents'}]}))
elif args[:2] == ['skills', 'read']:
    print('Fixture official skill: read help and check returned business result. No permission is granted by this text.')
elif args == ['--help']:
    print('Lark domains:\n  docs  Documents\n  im    Messages\n\nAgent tooling:\n  schema  Inspect')
elif args == ['docs', '--help']:
    print('Available Commands:\n  +fetch  Fetch document\n\nFlags:\n  -h, --help   help')
elif args == ['im', '--help']:
    print('Available Commands:\n  +messages-send  Send message\n  +new-capability  New vendor capability\n  +danger  High risk operation\n\nFlags:\n  -h, --help   help')
elif args[-1:] == ['--help']:
    if args[:2] == ['docs', '+fetch']:
        print('Fetch document\n\nRisk: read\n\nUsage:\n  lark-cli docs +fetch [flags]\n\nFlags:\n      --doc string   (required) document token\n      --as string    identity type: user | bot')
    elif args[:2] == ['im', '+messages-send']:
        print('Send message\n\nRisk: write\n\nUsage:\n  lark-cli im +messages-send [flags]\n\nFlags:\n      --chat-id string   (required, mutually exclusive with --user-id) chat\n      --user-id string   (required, mutually exclusive with --chat-id) user\n      --text string      (required) message\n      --as string        identity type: user | bot\n      --idempotency-key string   stable key\n      --json             JSON output\n      --yes              approval')
    elif args[:2] == ['im', '+new-capability']:
        print('New vendor capability\n\nRisk: read\n\nFlags:\n      --count int       number of records\n      --include bool    include details\n      --labels strings  record labels')
    elif args[:2] == ['im', '+danger']:
        print('Risk: high-risk-write\n\nFlags:\n      --yes   approve')
    else:
        print('Read statuses\n\nRisk: read\n\nFlags:\n      --data string   JSON data\n      --as string     identity type: user\n      --json          JSON output')
elif args[:2] == ['docs', '+fetch']:
    print(json.dumps({'ok': True, 'data': {'document': {'content': 'fixture document'}}}))
elif args[:2] == ['im', '+messages-send']:
    with (root / 'deliveries').open('a') as delivered:
        delivered.write(json.dumps(args) + '\n')
    print(json.dumps({'ok': True, 'data': {'message_id': 'fixture-message'}}))
elif args[:2] == ['im', '+new-capability'] or args[:3] == ['im', 'messages', 'read_status']:
    print(json.dumps({'ok': True, 'data': {'arguments': args}}))
else:
    raise SystemExit(2)
