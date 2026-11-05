"""Public, versioned model resources and node-scoped API calls."""
import copy
from .store import now


def model_parameters(execution):
    args = execution.get('args', [])
    if not isinstance(args, list) or any(not isinstance(arg, str) or not arg or '\0' in arg for arg in args):
        raise ValueError('Python args 必须是非空字符串组成的参数数组。')
    flags = ('--model', '--reasoning')
    if any(arg.startswith(flag + '=') for arg in args for flag in flags):
        raise ValueError('模型参数使用独立项，例如 ["--model", "模型资源ID", "--reasoning", "off"]。')
    if not any(flag in args for flag in flags):
        return None
    values = []
    for flag in flags:
        if args.count(flag) != 1:
            raise ValueError('Python 模型调用需要各一个 --model 和 --reasoning 参数。')
        index = args.index(flag)
        if index + 1 >= len(args) or args[index + 1].startswith('-') or '--' in args[:index] or '${' in args[index + 1]:
            raise ValueError('模型与思考强度需要明确的固定参数值。')
        values.append(args[index + 1])
    return dict(zip(('resourceId', 'reasoning'), values))


def selections(definition, config):
    resources = {item['id']: item for item in config['resources'] if item['kind'] == 'model'}
    result = {}
    for step in definition['steps']:
        if step['execution']['tool'] != 'python':
            continue
        selection = model_parameters(step['execution'])
        if selection:
            if selection['resourceId'] not in resources:
                raise ValueError('Python --model 参数引用了未登记的模型资源。')
            result[step['id']] = {**resources[selection['resourceId']], 'reasoning': selection['reasoning']}
    return result


async def call_model(context, step, message):
    if message.get('op') != 'complete':
        raise ValueError('Python 模型接口只支持 complete。')
    selected = context.run.get('modelSelections', {}).get(step['id'])
    if not selected or message.get('model') != selected['id'] or message.get('reasoning') != selected['reasoning']:
        raise ValueError('模型调用必须使用本节点 --model 和 --reasoning 传入的参数。')
    binding = copy.deepcopy(selected)
    record = {'stepId': step['id'], 'resourceId': binding['id'], 'modelId': binding['modelId'],
              'provider': binding['provider'], 'model': binding['model'], 'reasoning': binding['reasoning'],
              'startedAt': now(), 'status': 'running'}
    calls = context.run.setdefault('modelCalls', [])
    calls.append(record)
    context.store.update_run(context.loop_id, context.run_id, modelCalls=calls)
    context.emit('model', f"调用 {binding['model']} · {binding['reasoning']}")
    try:
        result = await context.model_host.call('complete', {'binding': binding,
            'prompt': message.get('prompt'), 'system': message.get('system', ''), 'maxTokens': message.get('maxTokens', 2048)})
        record.update(status='success', usage=result.get('usage', {}))
        return result
    except BaseException as error:
        record['status'] = 'failed' if isinstance(error, Exception) else 'stopped'
        raise
    finally:
        record['endedAt'] = now()
        context.store.update_run(context.loop_id, context.run_id, modelCalls=calls)
