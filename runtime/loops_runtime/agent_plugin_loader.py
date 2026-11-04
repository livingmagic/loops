"""Load trusted installed plugins, never code from a workflow's workspace or inputs."""
import hashlib
import importlib
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path

BUILTINS = Path(__file__).parent / 'agent_plugins'
USER_PLUGINS = Path.home() / '.config/loops/agent-plugins'


def validate_manifest(value):
    if not isinstance(value, dict) or value.get('apiVersion') != 1:
        raise ValueError('插件接口版本不兼容，需要 apiVersion=1。')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', str(value.get('id', ''))):
        raise ValueError('插件 ID 无效。')
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in ['name', 'version', 'command']):
        raise ValueError('插件需要名称、版本和客户端命令。')
    if not isinstance(value.get('modes'), list) or not value['modes'] or not set(value['modes']).issubset({'read-only', 'write'}):
        raise ValueError('插件需要声明有效的执行权限。')
    if not isinstance(value.get('paths', []), list) or any(not isinstance(p, str) for p in value.get('paths', [])):
        raise ValueError('插件的客户端路径格式无效。')
    return value


def load_plugins(directories=None, *, errors=None):
    registry = {}
    errors = errors if errors is not None else []
    for directory in directories if directories is not None else [BUILTINS, USER_PLUGINS]:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for folder in sorted(directory.iterdir()):
            if not folder.is_dir() or not (folder / 'manifest.json').is_file():
                continue
            try:
                manifest = validate_manifest(json.loads((folder / 'manifest.json').read_text()))
                if manifest['id'] in registry:
                    raise ValueError('插件 ID 重复，不能覆盖已加载的插件。')
                if directory.resolve() == BUILTINS.resolve():
                    module = importlib.import_module(f'loops_runtime.agent_plugins.{folder.name}.plugin')
                else:
                    # A package namespace lets plugins ship private helper modules beside plugin.py.
                    name = '_loops_agent_' + hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:20]
                    spec = importlib.util.spec_from_file_location(name, folder / 'plugin.py', submodule_search_locations=[str(folder)])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    try:
                        spec.loader.exec_module(module)
                    except Exception:
                        sys.modules.pop(name, None)
                        raise
                if not inspect.iscoroutinefunction(getattr(module, 'probe', None)) or not inspect.iscoroutinefunction(getattr(module, 'catalog', None)) or not callable(getattr(module, 'Session', None)):
                    raise ValueError('插件必须实现异步 probe、catalog 和 Session 工厂。')
                if hasattr(module, 'locate') and not callable(module.locate):
                    raise ValueError('插件 locate 必须是可调用函数。')
                registry[manifest['id']] = {**manifest, 'probe': module.probe, 'catalog': module.catalog, 'factory': module.Session}
                if hasattr(module, 'locate'):
                    registry[manifest['id']]['locate'] = module.locate
            except Exception as error:
                # Do not expose exception bodies from imported plugin code (may contain credentials).
                errors.append({'plugin': folder.name, 'message': f'插件加载失败（{type(error).__name__}），请检查声明、接口版本和安装文件。'})
    return registry
