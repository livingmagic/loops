"""Shipped workflows use the same versioned scripts and executor as user workflows."""


def greeting():
    return {
        'schemaVersion': 2,
        'title': '打个招呼',
        'goal': '拆分一句或多句招呼，逐段回复，体验多步骤流程和共享调用。',
        'completion': '按句末标点分段，逐段回复后用句号连接：“天王盖地虎”回复“小鸡炖蘑菇”；“你好，我是{名字}”回复本机名称；其他内容返回随机乱码。',
        'inputs': [{'id': 'text', 'label': '招呼', 'kind': 'text', 'required': False}],
        'scripts': {'scripts/recognize.py': '''import json
import os
import re

context = json.loads(os.environ['LOOPS_CONTEXT'])
text = context['inputs']['text']
# Commas belong to a greeting, such as “你好，我是小王”.
result = [part.strip() for part in re.split(r'[。！？!?；;.\\r\\n…]+', text) if part.strip()] or ['']
''', 'scripts/greeting.py': '''import json
import os
import re
import secrets
import string

context = json.loads(os.environ['LOOPS_CONTEXT'])
replies = []
for segment in context['inputs']['greeting']:
    introduction = re.fullmatch(r'你好[，,]\\s*我是(.+)', segment)
    if segment == '天王盖地虎':
        reply = '小鸡炖蘑菇'
    elif introduction and introduction.group(1).strip():
        reply = '你好，我是' + context['host']['name']
    else:
        alphabet = string.ascii_letters + string.digits + '!@#$%^&*'
        reply = ''.join(secrets.choice(alphabet) for _ in range(24))
    replies.append(reply)
result = '。'.join(replies)
'''},
        'steps': [{
            'id': 'recognize', 'label': '拆分招呼',
            'instructions': '按句号、问号、叹号、分号、省略号或换行分段，保留句内逗号，忽略空段。',
            'inputs': {'text': '${inputs.text}'},
            'execution': {'tool': 'python', 'script': 'scripts/recognize.py'},
            'outputs': [{'id': 'greeting', 'label': '招呼片段', 'kind': 'value', 'source': 'result', 'dataType': 'array'}],
            'checks': [], 'onFailure': {'action': 'stop'},
        }, {
            'id': 'greet', 'label': '生成回复',
            'instructions': '按顺序逐段回复暗号、介绍本机或生成随机字符，再用句号连接回复。',
            'inputs': {'greeting': '${steps.recognize.outputs.greeting}'},
            'execution': {'tool': 'python', 'script': 'scripts/greeting.py'},
            'outputs': [{'id': 'reply', 'label': '回复', 'kind': 'value', 'source': 'result', 'dataType': 'string'}],
            'checks': [], 'onFailure': {'action': 'stop'},
        }],
        'finalOutputs': ['${steps.greet.outputs.reply}'],
    }
