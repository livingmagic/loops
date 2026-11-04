"""Discover CLI metadata by format; compile, review and freeze selected calls.

Profiles describe metadata dialects, never business-operation mappings.
"""
import copy
import csv
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

ADAPTER_VERSION = 2
DISCOVERY_VERSION = 2
RESERVED = {'help', 'as', 'profile', 'format', 'json', 'jq', 'yes', 'idempotency-key', 'dry-run'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def effect(risk):
    return 'read' if risk == 'read' else 'write' if risk in {'write', 'high-risk-write'} else 'unknown'


def local_refs(value):
    if isinstance(value, list):
        for item in value:
            local_refs(item)
    elif isinstance(value, dict):
        if '$ref' in value and (not isinstance(value['$ref'], str) or not value['$ref'].startswith('#')):
            raise ValueError('CLI Schema 只能使用内部引用，不能在校验参数时访问外部文件或网络。')
        for item in value.values():
            local_refs(item)


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.catalog-', delete=False) as temporary:
        staged = Path(temporary.name)
        try:
            temporary.write(json.dumps(value, ensure_ascii=False))
            temporary.flush()
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def rows(help_text, heading):
    active = False
    result = []
    for line in help_text.splitlines():
        if line.strip() == heading:
            active = True
        elif active and line and not line[0].isspace():
            break
        elif active:
            match = re.fullmatch(r'  (\S+)(?: (?:<[^>]+>|\[[^\]]+\]))*\s{2,}(.+)', line)
            if match:
                result.append(match.groups())
    return result


def flags(help_text):
    result = {}
    for line in help_text.splitlines():
        match = re.fullmatch(r'\s+(?:-\w,\s+)?--([\w-]+)(?:\s+([A-Za-z][A-Za-z0-9]*))?\s{2,}(.+)', line)
        if match:
            name, kind, description = match.groups()
            result[name] = {'type': kind or 'bool', 'description': description}
        else:
            match = re.fullmatch(r'\s+(?:-\w,\s+)?--([\w-]+)\s+<([^>]+)>\s{2,}(.+)', line)
            if match:
                name, kind, description = match.groups()
                kind = {'str': 'string', 'JSON': 'string', 'STRING': 'string', 'INTEGER': 'int'}.get(kind, kind)
                result[name] = {'type': kind, 'description': description}
    return result


def shortcut_contract(record, help_text):
    definitions = flags(help_text)
    match = re.search(r'^Risk:\s*(read|write|high-risk-write)\s*$', help_text, re.M)
    risk = match[1] if match else 'unknown'
    usage = re.search(r'Usage:\s*([^\n]+)', help_text)
    if usage and re.search(r'\[(\w+)\.\.\.\]', usage[1]):
        if record.get('parameterStyle') != 'key-value':
            raise ValueError('可变位置参数需要声明 key=value 格式或结构化 Schema。')
        return {'name': record['name'], 'description': record['description'], 'command': record['command'],
                'inputSchema': {'type': 'object', 'properties': {'params': {
                    'type': 'object', 'description': '按官方帮助填写 key=value 参数；值使用字符串，由 CLI 校验业务约束。',
                    'propertyNames': {'pattern': r'^[a-zA-Z][a-zA-Z0-9_-]*(?:\[[a-zA-Z0-9_-]+\])*$'},
                    'additionalProperties': {'type': 'string'}}}, 'additionalProperties': False},
                'risk': risk, 'effect': effect(risk), 'bindings': [{'key': 'params', 'flag': '', 'kind': 'key-value'}],
                'identity': [], 'identityFlag': False, 'jsonFlag': None, 'idempotencyFlag': False, 'help': help_text}
    if usage and re.search(r'<[^>]+>', usage[1]):
        raise ValueError('这个 CLI 操作含位置参数，需要结构化 Schema 才能可靠编译。')
    properties, required, alternatives, exclusive, bindings = {}, [], [], [], []
    types = {'bool': 'boolean', 'string': 'string', 'duration': 'string', 'int': 'integer', 'int32': 'integer', 'int64': 'integer',
             'uint': 'integer', 'uint32': 'integer', 'uint64': 'integer', 'float64': 'number', 'float32': 'number'}
    for name, item in definitions.items():
        if name in RESERVED:
            continue
        kind, description = item['type'], item['description']
        if kind in {'strings', 'stringArray'}:
            schema = {'type': 'array', 'items': {'type': 'string'}}
        elif kind in types:
            schema = {'type': types[kind]}
        else:
            raise ValueError('CLI 参数类型暂不支持自动编译：' + kind)
        properties[name] = {**schema, 'description': description}
        bindings.append({'key': name, 'flag': '--' + name, 'kind': kind})
        constraint = re.match(r'^\(([^)]+)\)', description)
        if constraint:
            constraint = constraint[1]
            if 'mutually exclusive' in constraint:
                group = sorted({name, *re.findall(r'--([\w-]+)', constraint)})
                exclusive.append(group)
                if re.search(r'\brequired\b', constraint):
                    alternatives.append(group)
            elif constraint.startswith('one of ') and constraint.endswith('required'):
                alternatives.append(sorted(set(re.findall(r'--([\w-]+)', constraint))))
            elif constraint == 'required':
                required.append(name)
    schema = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        schema['required'] = required
    groups = {tuple(group) for group in alternatives if group and all(k in properties for k in group)}
    constraints = [{'anyOf': [{'required': [key]} for key in group]} for group in sorted(groups)]
    exclusive_groups = {tuple(group) for group in exclusive if group and all(k in properties for k in group)}
    constraints.extend({'not': {'required': [left, right]}} for group in sorted(exclusive_groups)
                       for i, left in enumerate(group) for right in group[i + 1:])
    if constraints:
        schema['allOf'] = constraints
    identity = re.findall(r'\b(user|bot)\b', definitions.get('as', {}).get('description', ''))
    return {'name': record['name'], 'description': record['description'], 'command': record['command'],
            'inputSchema': schema, 'effect': effect(risk), 'risk': risk,
            'bindings': bindings, 'identity': sorted(set(identity)), 'identityFlag': 'as' in definitions,
            'jsonFlag': '--json' if 'json' in definitions else '--format' if 'format' in definitions else None,
            'idempotencyFlag': 'idempotency-key' in definitions, 'help': help_text}


def typed_contract(record, help_text):
    source = record['schema']
    bindings = []
    definitions = flags(help_text)
    schema = copy.deepcopy(source['inputSchema'])
    for group, item in schema.get('properties', {}).items():
        if group in RESERVED:
            continue
        if item.get('carrier'):
            if not re.fullmatch(r'--[\w-]+', item['carrier']):
                raise ValueError('CLI Schema 的参数位置无效。')
            binary_fields = [field.get('format') == 'binary' for field in item.get('properties', {}).values()]
            bindings.append({'key': group, 'flag': item['carrier'], 'kind': 'files' if binary_fields and all(binary_fields) else 'json'})
        elif group == 'params':
            for name, field in item.get('properties', {}).items():
                flag = field.get('flag')
                if not isinstance(flag, str) or not re.fullmatch(r'--[\w-]+', flag):
                    raise ValueError('CLI Schema 缺少参数对应的 flag。')
                if flag[2:] in RESERVED:
                    raise ValueError('CLI Schema 使用了平台管理的参数。')
                bindings.append({'key': 'params.' + name, 'flag': flag, 'kind': definitions.get(flag[2:], {}).get('type', field.get('type', 'string'))})
        elif item.get('flag'):
            bindings.append({'key': group, 'flag': item['flag'], 'kind': definitions.get(item['flag'][2:], {}).get('type', item.get('type', 'string'))})
        else:
            raise ValueError('CLI Schema 参数暂不支持自动编译。')
    for name in list(schema.get('properties', {})):
        if name in RESERVED:
            schema['properties'].pop(name)
            if name in schema.get('required', []):
                schema['required'].remove(name)
    schema['additionalProperties'] = False
    if 'params' in schema.get('properties', {}):
        schema['properties']['params']['additionalProperties'] = False
    metadata = source.get('_meta', {})
    risk = metadata.get('risk', 'unknown')
    local_refs(schema)
    return {'name': record['name'], 'description': record['description'], 'command': record['command'],
            'inputSchema': schema, 'outputSchema': source.get('outputSchema'), 'effect': effect(risk),
            'risk': risk, 'bindings': bindings, 'identity': metadata.get('access_tokens', []),
            'identityFlag': 'as' in definitions, 'jsonFlag': '--json' if 'json' in definitions else '--format' if 'format' in definitions else None,
            'idempotencyFlag': 'idempotency-key' in definitions, 'scopes': metadata.get('scopes', []),
            'requiredScopes': metadata.get('required_scopes', []), 'help': help_text}


def command_rows(help_text):
    return [item for heading in ('Available Commands:', 'Commands:', '子命令:') for item in rows(help_text, heading)]


def check_command(command):
    if not isinstance(command, list) or not command or any(not isinstance(part, str) or not re.fullmatch(r'[\w.+-]+', part) for part in command):
        raise ValueError('CLI Schema 命令名称无效。')


def declared_risk(source):
    risk = source.get('_meta', {}).get('risk', source.get('risk'))
    if risk in {'read', 'write', 'high-risk-write'}:
        return risk
    hints = source.get('annotations', {})
    if hints.get('readOnlyHint') is True:
        return 'read'
    if hints.get('destructiveHint') is True:
        return 'high-risk-write'
    if hints.get('readOnlyHint') is False:
        return 'write'
    return 'unknown'


def json_contract(record, source, help_text):
    # --json means an input body here; it is an output switch in other dialects.
    if not re.search(r'--json\s+(?:<[^>]+>|(?:string|str)\b)', help_text):
        raise ValueError('CLI 需要声明 --json <JSON> 请求体，或在 Schema 中提供参数 flag/carrier。')
    from jsonschema import Draft202012Validator
    schema = copy.deepcopy(source['inputSchema'])
    if schema.get('type') != 'object':
        raise ValueError('CLI 请求 Schema 需要为 object。')
    schema.setdefault('additionalProperties', False)
    local_refs(schema)
    Draft202012Validator.check_schema(schema)
    risk = declared_risk(source)
    return {'name': record['name'], 'description': record['description'], 'command': record['command'],
            'inputSchema': schema, 'outputSchema': source.get('outputSchema'), 'effect': effect(risk),
            'risk': risk, 'bindings': [{'key': '', 'flag': '--json', 'kind': 'json'}],
            'idempotencyFlag': 'idempotency-key' in flags(help_text), 'help': help_text}


def service_contract(record, source, help_text):
    definitions = source['schemas']
    def refs(value):
        if isinstance(value, list):
            return [refs(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: refs(item) for key, item in value.items()}
        if '$ref' in value:
            name = value['$ref']
            if name not in definitions:
                raise ValueError('CLI Schema 引用了未知类型，不能可靠编译。')
            value['$ref'] = '#/$defs/' + name.replace('~', '~0').replace('/', '~1')
        return value
    def root(item):
        name = item.get('$ref')
        value = copy.deepcopy(definitions[name]) if name in definitions else copy.deepcopy(item)
        value = refs(value)
        value['$defs'] = refs(definitions)
        return value
    contract = json_contract(record, {'inputSchema': root(source['request']), 'outputSchema': root(source['response']),
                                    'annotations': source.get('annotations', {}), '_meta': source.get('_meta', {})}, help_text)
    contract.update(metadataKind='service-schema', sourceHash=digest(source))
    return contract


class CliDiscovery:
    def __init__(self, broker, key):
        self.broker, self.key = broker, key
        self.index = None
        self.binary_stamp = None
        self.directory = None
        self.described = {}

    def fingerprint(self):
        command = self.broker.executable(self.broker.entry(self.key)['manifest']['command'], self.key)
        binary = Path(command[0])
        files = [binary]
        for argument in command[1:]:
            candidate = Path(argument)
            if candidate.is_absolute() and candidate.is_file():
                files.append(candidate)
        stamp = (tuple(command), tuple((str(path), path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_size) for path in files))
        if stamp != self.binary_stamp:
            fingerprint = hashlib.sha256(binary.read_bytes()).hexdigest()
            self.command_hash = digest({'command': command, 'files': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}})
            self.binary_stamp = stamp
            self.index, self.described = None, {}
            self.directory = self.broker.root / 'connector-catalogs' / self.key / fingerprint
        return self.directory.name

    async def process(self, args):
        code, output, diagnostic = await self.broker.process(self.key, args)
        if code:
            try:
                error = json.loads(output if output.strip() else diagnostic).get('error', {})
                if isinstance(error, dict) and error.get('type') in {'AuthError', 'authentication'}:
                    from .connectors import AuthorizationRequired
                    raise AuthorizationRequired('CLI 元数据访问需要重新授权。')
            except (ValueError, AttributeError):
                pass
            raise ValueError('无法读取 CLI 元数据：' + ' '.join(args))
        return output

    async def optional(self, args):
        code, output, _ = await self.broker.process(self.key, args)
        return output if not code else None

    async def auth(self):
        index = await self.load()
        profile = index.get('profile', 'lark')
        if profile == 'lark':
            return {'kind': 'lark', 'mode': 'cli', 'status': ['auth', 'status', '--json', '--verify'],
                    'login': ['auth', 'login', '--domain', 'docs,im,drive,wiki', '--scope', 'offline_access']}
        return copy.deepcopy(index.get('auth', {'mode': 'none'}))

    async def generic_auth(self, help_text):
        if 'auth' not in {name for name, _ in command_rows(help_text)}:
            return {'mode': 'none'}
        auth_help = await self.process(['auth', '--help'])
        commands = {name for name, _ in command_rows(auth_help)}
        if {'show', 'init'} <= commands:
            show_help = await self.process(['auth', 'show', '--help'])
            init_help = await self.process(['auth', 'init', '--help'])
            if '--status' not in show_help or not all(flag in init_help for flag in ('--noninteractive', '--no-browser', '--output-qrcode')):
                raise ValueError('CLI 的 auth show/init 接口不符合可发现的非交互授权约定。')
            return {'kind': 'wecom', 'mode': 'cli', 'status': ['auth', 'show', '--status'],
                    'login': ['auth', 'init', '--noninteractive', '--no-browser', '--output-qrcode', '${qrcode}']}
        if not {'status', 'login'} <= commands:
            raise ValueError('CLI 的 auth 接口需要提供 status 和 login；也可导入已有授权命令配置。')
        status_help = await self.process(['auth', 'status', '--help'])
        return {'mode': 'cli', 'kind': 'generic', 'status': ['auth', 'status', *(['--json'] if '--json' in status_help else [])],
                'login': ['auth', 'login'], **({'refresh': ['auth', 'refresh']} if 'refresh' in commands else {})}

    async def load(self, refresh=False):
        fingerprint = self.fingerprint()
        if self.index and not (refresh and self.index.get('profile') == 'service-schema'):
            return self.index
        cache = self.directory / 'catalog.json'
        if cache.is_file():
            try:
                value = json.loads(cache.read_text())
                if (value['adapterVersion'] == ADAPTER_VERSION and value['binaryHash'] == fingerprint
                    and value.get('commandHash', self.command_hash) == self.command_hash
                    and (value.get('profile', 'lark') == 'lark' or value.get('discoveryVersion') == DISCOVERY_VERSION)
                    and not (refresh and value.get('profile') == 'service-schema')
                    and value['catalogHash'] == digest({k: v for k, v in value.items() if k != 'catalogHash'})):
                    self.index = value
                    return value
            except (KeyError, ValueError):
                pass
        version = (await self.process(['--version'])).strip()
        help_text = await self.process(['--help'])
        if 'Lark domains:' not in help_text:
            return await self.load_other(fingerprint, version, help_text, cache)
        methods = json.loads(await self.process(['schema']))
        skill_result = json.loads(await self.process(['skills', 'list']))
        if not isinstance(methods, list) or not isinstance(skill_result.get('skills'), list):
            raise ValueError('CLI 能力索引格式无效。')
        operations = {}
        for source in methods:
            command = source['name'].split()
            name = '.'.join(command)
            if not command or any(not re.fullmatch(r'[\w.-]+', part) for part in command):
                raise ValueError('CLI Schema 命令无效。')
            operations[name] = {'name': name, 'command': command, 'kind': 'schema', 'description': source.get('description', ''),
                                'risk': source.get('_meta', {}).get('risk', 'unknown'), 'schema': source}
        domains = {command for command, _ in rows(help_text, 'Lark domains:')}
        domains.update(item['command'][0] for item in operations.values())
        skills = [{k: item[k] for k in ('name', 'description', 'version') if k in item} for item in skill_result['skills']]
        for domain in sorted(domains):
            if not re.fullmatch(r'[\w-]+', domain):
                raise ValueError('CLI 领域名称无效。')
            domain_help = await self.process([domain, '--help'])
            for command, description in rows(domain_help, 'Available Commands:'):
                if not command.startswith('+'):
                    continue
                if not re.fullmatch(r'\+[\w-]+', command):
                    raise ValueError('CLI 快捷命令名称无效。')
                name = domain + '.' + command
                operations[name] = {'name': name, 'command': [domain, command], 'kind': 'help', 'description': description, 'risk': 'unknown'}
        value = {'adapterVersion': ADAPTER_VERSION, 'binaryHash': fingerprint, 'commandHash': self.command_hash,
                 'toolVersion': version, 'operations': operations, 'skills': skills}
        value['catalogHash'] = digest(value)
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json(cache, value)
        self.index = value
        return value

    async def load_other(self, fingerprint, version, help_text, cache):
        schema_help = await self.optional(['schema', '--help'])
        schema_commands = {name for name, _ in command_rows(schema_help or '')}
        operations, skills = {}, []
        if {'list', 'get'} <= schema_commands:
            profile = 'service-schema'
            services = json.loads(await self.process(['schema', 'list']))
            for service in services:
                for method in service['methods']:
                    name = method['name']
                    command = name.split('.')
                    check_command(command)
                    operations[name] = {'name': name, 'command': command, 'description': method.get('description') or '',
                                        'kind': 'service-schema', 'risk': 'unknown'}
                skills.extend({'name': name, 'description': 'CLI 服务声明的外部 Skill（本机未内置）', 'external': True}
                              for name in service.get('skills', []))
            auth = await self.generic_auth(help_text)
        else:
            raw = await self.optional(['schema'])
            try:
                methods = json.loads(raw or 'null')
            except ValueError:
                methods = None
            if isinstance(methods, dict):
                methods = methods.get('tools', methods.get('operations'))
            if isinstance(methods, list) and methods:
                profile = 'json-schema'
                for source in methods:
                    command = source.get('command') or source['name'].split()
                    check_command(command)
                    name = '.'.join(command)
                    operations[name] = {'name': name, 'command': command, 'description': source.get('description') or '',
                                        'kind': 'json-schema', 'schema': source, 'risk': declared_risk(source)}
            else:
                profile = 'help'
                for name, description in command_rows(help_text):
                    if name not in {'help', 'auth', 'schema', 'skills', 'completion'}:
                        check_command([name])
                        operations[name] = {'name': name, 'command': [name], 'description': description, 'kind': 'help', 'risk': 'unknown',
                                            **({'parameterStyle': 'key-value'} if re.search(r'\bkey=value\b', help_text) else {})}
            auth = await self.generic_auth(help_text)
        if not operations:
            raise ValueError('CLI 未提供可发现的 Schema 或命令帮助。')
        value = {'adapterVersion': ADAPTER_VERSION, 'binaryHash': fingerprint, 'commandHash': self.command_hash, 'toolVersion': version,
                 'discoveryVersion': DISCOVERY_VERSION, 'profile': profile, 'operations': operations,
                 'skills': list({item['name']: item for item in skills}.values()), 'auth': auth}
        value['catalogHash'] = digest(value)
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json(cache, value)
        self.index = value
        return value

    async def operation(self, name):
        index = await self.load()
        if index.get('profile') == 'service-schema' and name not in index['operations']:
            raise ValueError('CLI 的当前目录中没有这项操作。')
        previous = None
        if name in self.described:
            previous = self.described[name]
            try:
                await self.verify_frozen(previous)
                if previous['catalogHash'] == index['catalogHash']:
                    return copy.deepcopy(previous)
                self.described.pop(name)
            except ValueError:
                self.described.pop(name)
        cache = self.directory / ('operation-' + hashlib.sha256(name.encode()).hexdigest() + '.json')
        if cache.is_file():
            try:
                value = json.loads(cache.read_text())
                if value.get('name') == name and value.get('contractHash') == digest({k: v for k, v in value.items() if k != 'contractHash'}):
                    await self.verify_frozen(value)
                    if value.get('catalogHash') == index['catalogHash']:
                        self.described[name] = value
                        return copy.deepcopy(value)
                    previous = value
            except (ValueError, OSError):
                pass
        record = index['operations'].get(name)
        if not record:
            parent, _, child = name.rpartition('.')
            if not parent or not re.fullmatch(r'[\w-]+', child) or name.count('.') > 8:
                raise ValueError('CLI 中没有这项操作。')
            group = await self.operation(parent)
            record = next((item for item in group.get('children', []) if item['name'] == name), None)
            if not record:
                raise ValueError('CLI 中没有这项操作。')
        help_text = await self.process([*record['command'], '--help'])
        children = command_rows(help_text)
        if children:
            contract = {**record, 'risk': 'unknown', 'children': [
                {'name': name + '.' + child, 'description': description, 'command': [*record['command'], child], 'kind': 'help', 'risk': 'unknown',
                 **({'parameterStyle': record['parameterStyle']} if record.get('parameterStyle') else {})}
                for child, description in children if re.fullmatch(r'[\w+-]+', child)]}
        else:
            if record['kind'] == 'schema':
                contract = typed_contract(record, help_text)
            elif record['kind'] == 'service-schema':
                source = json.loads(await self.process(['schema', 'get', name]))
                contract = service_contract(record, source, help_text)
                contract['documentation'] = await self.process([*record['command'], '--doc'])
            elif record['kind'] == 'json-schema':
                source = record['schema']
                if any(item.get('carrier') or item.get('flag') or key == 'params' for key, item in source['inputSchema'].get('properties', {}).items()):
                    contract = typed_contract(record, help_text)
                else:
                    contract = json_contract(record, source, help_text)
            else:
                contract = shortcut_contract(record, help_text)
        contract.update(adapterVersion=ADAPTER_VERSION, binaryHash=index['binaryHash'], commandHash=self.command_hash,
                        catalogHash=index['catalogHash'], toolVersion=index['toolVersion'])
        if (previous and previous.get('riskAssessment') and contract.get('sourceHash')
            and previous.get('sourceHash') == contract['sourceHash'] and previous['binaryHash'] == contract['binaryHash']
            and previous.get('commandHash') == contract['commandHash']):
            contract.update(risk=previous['risk'], effect=previous['effect'], riskAssessment=previous['riskAssessment'])
        contract['contractHash'] = digest(contract)
        atomic_json(cache, contract)
        self.described[name] = contract
        return copy.deepcopy(contract)

    async def review(self, name, risk, evidence):
        contract = await self.operation(name)
        if contract.get('risk') != 'unknown' or contract.get('children'):
            raise ValueError('只能评估未声明风险的具体操作，不能覆盖 CLI 已声明的风险。')
        if risk not in {'read', 'write', 'high-risk-write'} or not isinstance(evidence, str) or not 10 <= len(evidence) <= 2000:
            raise ValueError('请根据完整 Schema 和帮助说明读写影响及判断依据。')
        contract.update(risk=risk, effect='read' if risk == 'read' else 'write',
                        riskAssessment={'source': 'task-author', 'evidence': evidence})
        contract.pop('contractHash', None)
        contract['contractHash'] = digest(contract)
        cache = self.directory / ('operation-' + hashlib.sha256(name.encode()).hexdigest() + '.json')
        atomic_json(cache, contract)
        self.described[name] = contract
        return copy.deepcopy(contract)

    async def verify_frozen(self, contract):
        self.fingerprint()
        if contract.get('commandHash', self.command_hash) != self.command_hash:
            raise ValueError('CLI 程序或入口脚本版本已改变，请重新检查并保存任务。')
        # Service-driven CLIs may change a method without changing the executable.
        if contract.get('metadataKind') == 'service-schema':
            current = json.loads(await self.process(['schema', 'get', contract['name']]))
            if digest(current) != contract['sourceHash']:
                raise ValueError('CLI 服务端操作 Schema 已改变，请重新检查并保存任务。')

    async def catalog(self, domain=None):
        index = await self.load()
        if domain is None:
            domains = sorted({item['command'][0] for item in index['operations'].values()})
            return {'toolVersion': index['toolVersion'], 'catalogHash': index['catalogHash'],
                    'domains': [{'name': name, 'count': sum(item['command'][0] == name for item in index['operations'].values())} for name in domains],
                    'skills': index['skills']}
        return [{'name': item['name'], 'description': item['description'], 'risk': item['risk']}
                for item in index['operations'].values() if item['command'][0] == domain]

    async def skill(self, path):
        index = await self.load()
        if not isinstance(path, str) or not re.fullmatch(r'[\w-]+(?:/[\w.-]+)*', path) or any(p in {'.', '..'} for p in path.split('/')):
            raise ValueError('Skill 路径无效。')
        if path.split('/')[0] not in {skill['name'] for skill in index['skills']}:
            raise ValueError('CLI 中没有这个 Skill。')
        if index.get('profile', 'lark') != 'lark':
            raise ValueError('这个 CLI 未内置可读取的 Skill；请查看操作契约中的官方帮助与文档。')
        text = await self.process(['skills', 'read', path])
        return {'path': path, 'toolVersion': index['toolVersion'], 'content': text[:80000], 'truncated': len(text) > 80000}

    def frozen(self, name):
        contract = self.described.get(name)
        if not contract or contract.get('risk') not in {'read', 'write'} or contract.get('children'):
            raise ValueError('请先检查这项 CLI 操作的契约；未知或高风险操作不能直接用于无人值守任务：' + name)
        index = self.index
        if (self.fingerprint() != contract['binaryHash'] or contract.get('commandHash', self.command_hash) != self.command_hash
            or not index or contract['catalogHash'] != index['catalogHash']):
            raise ValueError('CLI 已更新，请重新检查操作后保存任务。')
        return copy.deepcopy(contract)


def argv(contract, arguments, identity, delivery_key):
    if contract.get('risk') not in {'read', 'write'} or contract.get('children'):
        raise ValueError('这项 CLI 操作尚不能用于自动化。')
    if contract.get('identity') and identity not in contract['identity']:
        raise ValueError('这项 CLI 操作不支持当前连接身份。')
    result = list(contract['command'])
    for binding in contract['bindings']:
        value = arguments
        for part in binding['key'].split('.') if binding['key'] else []:
            if not isinstance(value, dict) or part not in value:
                break
            value = value[part]
        else:
            flag, kind = binding['flag'], binding['kind']
            if kind == 'key-value':
                if (not isinstance(value, dict) or any(not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*(?:\[[a-zA-Z0-9_-]+\])*', k)
                    or not isinstance(v, str) or '\x00' in v for k, v in value.items())):
                    raise ValueError('key=value 参数需要合法名称与字符串值。')
                result.extend(name + '=' + text for name, text in sorted(value.items()))
            elif kind == 'files':
                for name, path in value.items():
                    result.extend([flag, name + '=' + path])
            elif kind in {'json', 'object', 'array'}:
                result.extend([flag, json.dumps(value, ensure_ascii=False)])
            elif kind in {'bool', 'boolean'}:
                result.append(flag + '=' + str(value).lower())
            elif kind == 'strings':
                stream = io.StringIO(); csv.writer(stream, lineterminator='').writerow(value)
                result.extend([flag, stream.getvalue()])
            elif kind == 'stringArray':
                for item in value:
                    result.extend([flag, item])
            else:
                result.extend([flag, str(value)])
    if contract.get('identityFlag'):
        result.extend(['--as', identity])
    if contract.get('jsonFlag') == '--json':
        result.append('--json')
    elif contract.get('jsonFlag') == '--format':
        result.extend(['--format', 'json'])
    if contract.get('idempotencyFlag') and delivery_key:
        result.extend(['--idempotency-key', hashlib.sha256(delivery_key.encode()).hexdigest()[:48]])
    return result
