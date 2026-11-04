import json
import re

def parse_mimo_models(text):
    # The CLI prints an ID line followed by a formatted JSON object for each model.
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    decoder, models, position = json.JSONDecoder(), [], 0
    while position < len(text):
        start = text.find('{', position)
        if start == -1:
            break
        item, length = decoder.raw_decode(text[start:])
        position = start + length
        if item.get('status') == 'deprecated' or not item.get('capabilities', {}).get('toolcall', False):
            continue
        models.append({'id': item['providerID'] + '/' + item['id'], 'name': item.get('name', item['id']), 'provider': item['providerID'],
                       'reasoningLevels': [key for key, value in item.get('variants', {}).items() if not value.get('disabled')],
                       'defaultReasoning': None})
    return models


async def mimo_catalog(binary, capture, *, include_defaults=True):
    listing = await capture(binary, 'models', '--verbose', '--pure')
    # Native config may contain secrets; only extract model selection metadata.
    config = json.loads(await capture(binary, 'debug', 'config')) if include_defaults else {}
    return {'models': parse_mimo_models(listing), 'nativeModel': config.get('model'), 'nativeReasoning': None}
