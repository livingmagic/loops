"""Synchronous SDK injected into Python workflow nodes by model_worker.py."""
import json
import threading

_channel = _replies = None
_lock = threading.Lock()


def complete(prompt, *, model, reasoning, system='', max_tokens=2048):
    """同步返回正文；model 和 reasoning 使用脚本的 --model、--reasoning 参数。"""
    if not isinstance(model, str) or not model or not isinstance(reasoning, str) or not reasoning:
        raise ValueError('请提供模型资源 ID 和思考强度。')
    if _channel is None:
        raise RuntimeError('请通过 Loops Python 节点调用模型。')
    with _lock:
        _channel.write(json.dumps({'op': 'complete', 'model': model, 'reasoning': reasoning, 'prompt': prompt,
                                   'system': system, 'maxTokens': max_tokens}, ensure_ascii=False) + '\n')
        _channel.flush()
        line = _replies.readline()
    if not line:
        raise RuntimeError('Loops 模型服务已断开。')
    response = json.loads(line)
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response['result']['text']
