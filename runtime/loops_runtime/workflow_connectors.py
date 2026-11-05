"""Freeze connector contracts alongside a Loop definition, never model credentials."""
import copy


async def bind_connectors(definition, connectors):
    value = copy.deepcopy(definition)
    value.pop('connectorBindings', None)
    grants = {}
    for step in value['steps']:
        execution = step['execution']
        if execution['tool'] != 'mcp':
            continue
        key = execution['connectorId']
        grants.setdefault(key, set()).add(execution['operation'])
    if not grants:
        return value
    grants = [{'id': key, 'operations': sorted(names)} for key, names in grants.items()]
    await connectors.prepare_contracts(grants)
    contracts = connectors.snapshot_contracts(grants)
    value['connectorBindings'] = {
        key: {'revision': connectors.entry(key)['revision'], 'operations': operations}
        for key, operations in contracts.items()
    }
    return value


def pointer_value(value, pointer):
    for token in pointer.split('/')[1:] if pointer else []:
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and str(int(token)) == token and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ValueError('MCP 返回中缺少声明的数据：' + pointer)
    return value
