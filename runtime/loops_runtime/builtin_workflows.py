"""Shipped workflows use the same versioned scripts and executor as user workflows."""


def greeting():
    return {
        'schemaVersion': 2,
        'title': '打个招呼',
        'goal': '发送一句招呼，验证共享连接和流程调用。',
        'completion': '“天王盖地虎”回复“小鸡炖蘑菇”；“你好，我是{名字}”回复本机名称；其他输入返回随机乱码。',
        'inputs': [{'id': 'text', 'label': '招呼', 'kind': 'text', 'required': False}],
        'scripts': {'scripts/greeting.py': '''import json
import os
import re
import secrets
import string
from pathlib import Path

context = json.loads(os.environ['LOOPS_CONTEXT'])
text = context['inputs']['text']
introduction = re.fullmatch(r'你好，我是(.+)', text)
if text == '天王盖地虎':
    reply = '小鸡炖蘑菇'
elif introduction and introduction.group(1).strip():
    reply = '你好，我是' + context['host']['name']
else:
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
    reply = ''.join(secrets.choice(alphabet) for _ in range(24))
Path(context['outputs']['reply']).write_text(json.dumps(reply, ensure_ascii=False), encoding='utf-8')
'''},
        'steps': [{
            'id': 'greet', 'label': '回复招呼',
            'instructions': '按招呼内容回复；自我介绍使用运行时的本机名称。',
            'inputs': {'text': '${inputs.text}'},
            'execution': {'tool': 'python', 'script': 'scripts/greeting.py'},
            'outputs': [{'id': 'reply', 'label': '回复', 'kind': 'json', 'path': 'reply.json'}],
            'checks': [], 'onFailure': {'action': 'stop'},
        }],
        'finalOutputs': ['${steps.greet.outputs.reply}'],
    }
