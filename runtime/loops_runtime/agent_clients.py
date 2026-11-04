"""Agent plugin orchestration, persisted defaults and execution bindings."""
import asyncio
import json
import re
import time
from pathlib import Path
from .agent_plugin_api import capture, find_executable
from .agent_plugin_loader import load_plugins

PLUGIN_ERRORS = []
CLIENTS = load_plugins(errors=PLUGIN_ERRORS)


def validate_agent_id(value):
    if not isinstance(value, str) or value not in CLIENTS:
        raise ValueError('请选择支持的执行 Agent。')
    return value


def validate_model(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/+-]{0,199}', value)):
        raise ValueError('请输入有效的模型 ID。')
    return value


def step_agent(execution, config):
    # Legacy definitions always retain their original Codex binding.
    return 'codex' if execution['tool'] == 'codex' else execution.get('agentId', config.get('defaultAgentId', 'codex'))


def resolve_selection(binding, execution):
    selected = dict(binding)
    if 'model' in execution:
        if execution['model'] != selected.get('model'):
            selected['reasoning'] = None
        selected['model'] = execution['model']
    if 'reasoning' in execution:
        selected['reasoning'] = execution['reasoning']
    if 'models' in selected:
        entry = next((m for m in selected['models'] if m['id'] == selected.get('model')), None)
        effort = selected.get('reasoning')
        if not entry:
            raise ValueError(f"{selected['name']} 的所选模型当前不可用，请重新选择。")
        if not effort and entry['reasoningLevels']:
            levels = entry['reasoningLevels']
            selected['reasoning'] = entry.get('defaultReasoning') or ('low' if 'low' in levels else levels[0])
        if selected.get('reasoning') and selected['reasoning'] not in entry['reasoningLevels']:
            raise ValueError(f"{selected['name']} 当前模型不支持所选思考强度，请重新选择。")
    return selected


def locate(client_id):
    spec = CLIENTS[client_id]
    return spec.get('locate', find_executable)(spec)


def initial_agent_id():
    for key in CLIENTS:
        try:
            if locate(key):
                return key
        except Exception:
            continue
    if CLIENTS:
        return next(iter(CLIENTS))
    raise ValueError('尚未安装可用的 Agent 插件。')


class AgentClients:
    def __init__(self, root=None):
        self.settings_path = Path(root) / 'agent-models.json' if root else None
        self._models = json.loads(self.settings_path.read_text()) if self.settings_path and self.settings_path.exists() else {}
        self._initialized = {key for key, value in self._models.items() if isinstance(value, dict) and value.get('initialized')}
        self._efforts = {key: value.get('reasoning') for key, value in self._models.items() if isinstance(value, dict)}
        self._models = {key: value.get('model') if isinstance(value, dict) else value for key, value in self._models.items()}
        self._discovery = None
        self._checked_at = 0

    def save_model(self, client_id, model, reasoning=None):
        validate_agent_id(client_id)
        model = validate_model(model)
        reasoning = validate_model(reasoning)
        self._initialized.add(client_id)
        self._models[client_id] = model
        self._efforts[client_id] = reasoning
        if self.settings_path:
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps({key: {'model': value, 'reasoning': self._efforts.get(key), 'initialized': key in self._initialized} for key, value in self._models.items()}, ensure_ascii=False, indent=2))
            temporary.replace(self.settings_path)
        self._checked_at = 0

    async def configure_model(self, client_id, model, reasoning=None):
        validate_agent_id(client_id)
        validate_model(model)
        validate_model(reasoning)
        states = await self.discover()
        state = next(s for s in states if s['id'] == client_id)
        if not model:
            raise ValueError('请选择明确的模型。')
        effective = model
        entry = next((m for m in state.get('models', []) if m['id'] == effective), None)
        if model and not entry:
            raise ValueError('该模型不在客户端当前模型列表中，请重新检查后选择。')
        if entry and entry['reasoningLevels'] and not reasoning:
            raise ValueError('请选择明确的思考强度。')
        if reasoning and (not entry or reasoning not in entry['reasoningLevels']):
            raise ValueError('当前模型不支持此思考强度，请重新选择。')
        self.save_model(client_id, model, reasoning)
        updated = {**state, 'defaultModel': model, 'defaultReasoning': reasoning}
        states[states.index(state)] = updated
        self._checked_at = time.monotonic()
        return updated

    async def model_state(self, state):
        key = state['id']
        models = state.get('models', [])
        saved_model = self._models.get(key)
        entry = next((m for m in models if m['id'] == saved_model), None)
        incomplete = not saved_model or (entry and entry['reasoningLevels'] and not self._efforts.get(key))
        if 'models' in state and (key not in self._initialized or incomplete):
            model = saved_model
            if not model:
                native = state.get('nativeModel') if key not in self._initialized else None
                model = native if any(m['id'] == native for m in models) else (models[0]['id'] if models else native)
            entry = next((m for m in models if m['id'] == model), None)
            effort = self._efforts.get(key)
            if not effort and entry:
                levels = entry['reasoningLevels']
                native = state.get('nativeReasoning') if key not in self._initialized and model == state.get('nativeModel') else None
                candidate = native or entry.get('defaultReasoning')
                effort = candidate if candidate in levels else ('low' if 'low' in levels else next((level for level in ['minimal', 'medium', 'high', 'xhigh', 'max', 'ultra'] if level in levels), levels[0] if levels else None))
            self.save_model(key, model, effort)
        return {**state, 'defaultModel': self._models.get(key), 'defaultReasoning': self._efforts.get(key)}

    async def configured_probe(self, key):
        state = await self.probe(key)
        if state.get('ready'):
            try:
                state.update(await CLIENTS[key]['catalog'](state['binary'], capture, include_defaults=key not in self._initialized))
            except Exception:
                state['catalogError'] = '暂时无法读取模型列表，请重新检查。'
        return await self.model_state(state)

    async def probe(self, client_id):
        spec = CLIENTS[client_id]
        state = {'id': client_id, 'name': spec['name'], 'installed': False, 'ready': False,
                 'modes': spec['modes'], 'pluginVersion': spec['version'], 'version': None, 'detail': '未安装，请先安装并登录客户端。'}
        try:
            binary = locate(client_id)
            state['installed'] = bool(binary)
            if not binary:
                return state
            state.update(await spec['probe'](binary, capture))
            state['binary'] = binary
        except Exception:
            state['ready'] = False
            state['detail'] = '无法检查客户端，请打开客户端确认安装、登录和本机访问权限。'
        return state

    async def discover(self, refresh=False):
        if self._discovery is None or self._discovery.done() and (refresh or time.monotonic() - self._checked_at > 30):
            self._discovery = asyncio.ensure_future(asyncio.gather(*(self.configured_probe(key) for key in CLIENTS)))
            self._checked_at = time.monotonic()
        return await asyncio.shield(self._discovery)

    async def close(self):
        if self._discovery is not None:
            if not self._discovery.done():
                self._discovery.cancel()
            await asyncio.gather(self._discovery, return_exceptions=True)

    async def bind(self, definition, config):
        needed = {}
        for step in definition['steps']:
            execution = step['execution']
            if execution['tool'] in {'agent', 'codex'}:
                key = validate_agent_id(step_agent(execution, config))
                needed.setdefault(key, set()).add(execution.get('mode', 'write'))
        states = await asyncio.gather(*(self.configured_probe(key) for key in needed))
        bindings = {}
        for state in states:
            if not state['ready']:
                raise ValueError(f"{state['name']} 不可用：{state['detail']}")
            if not needed[state['id']].issubset(state['modes']):
                raise ValueError(f"{state['name']} 不支持此步骤的执行权限。请选择其他 Agent。")
            model = config.get('agentModels', {}).get(state['id'], state.get('defaultModel'))
            if not model and 'models' in state and any(step_agent(s['execution'], config) == state['id'] and not s['execution'].get('model') for s in definition['steps'] if s['execution']['tool'] in {'agent', 'codex'}):
                raise ValueError(f"请先在设置中选择 {state['name']} 的默认模型。")
            effort = config.get('agentReasoning', {}).get(state['id'], state.get('defaultReasoning') if model == state.get('defaultModel') else None)
            bindings[state['id']] = {**state, 'model': model, 'reasoning': effort}
            for step in definition['steps']:
                execution = step['execution']
                if execution['tool'] in {'agent', 'codex'} and step_agent(execution, config) == state['id']:
                    resolve_selection(bindings[state['id']], execution)
        return bindings

    def session(self, binding, cwd, emit, *, read_paths=()):
        options = {'binary': binding['binary'], 'read_paths': read_paths, 'model': binding.get('model'), 'reasoning': binding.get('reasoning')}
        return CLIENTS[binding['id']]['factory'](cwd, emit, **options)
