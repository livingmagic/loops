"""Local acceptance CLI: real SQLite writes; deterministic orders, no remote service."""
import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('operation', choices=['list', 'create', 'dump'])
parser.add_argument('--database', required=True)
parser.add_argument('--count', type=int, default=2)
parser.add_argument('--order-id')
parser.add_argument('--behavior', default='normal', choices=['normal', 'fail', 'timeout', 'cancel', 'invalid-json'])
args = parser.parse_args()
db = sqlite3.connect(args.database)
db.execute('CREATE TABLE IF NOT EXISTS calls (order_id TEXT, pid INTEGER)')
db.execute('CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY, order_id TEXT)')
if args.operation == 'list':
    print('查询订单完成', file=sys.stderr)
    print(json.dumps([{'id': f'ORDER-{i+1}'} for i in range(args.count)]))
elif args.operation == 'dump':
    print(json.dumps({'calls': db.execute('SELECT order_id FROM calls').fetchall(),
                      'tickets': db.execute('SELECT id, order_id FROM tickets').fetchall()}))
else:
    # Deliberately no unique constraint or deduplication: replay would create duplicates.
    db.execute('INSERT INTO calls VALUES (?, ?)', (args.order_id, os.getpid()))
    ticket = db.execute('INSERT INTO tickets(order_id) VALUES (?)', (args.order_id,)).lastrowid
    db.commit()
    if args.order_id == 'ORDER-2' and args.behavior in {'timeout', 'cancel'}:
        # Descendant survives SIGTERM so cancellation must kill the whole group.
        child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
        Path(args.database + '.child').write_text(str(child.pid))
        print('写入已落库，等待后续响应', file=sys.stderr, flush=True)
        time.sleep(60)
    if args.order_id == 'ORDER-2' and args.behavior == 'fail':
        print('写入已落库，但返回失败', file=sys.stderr)
        sys.exit(7)
    if args.behavior == 'invalid-json':
        print('not JSON')
    else:
        print(json.dumps({'ticketId': ticket, 'orderId': args.order_id}))
db.close()
