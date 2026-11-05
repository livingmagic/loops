import copy
import json
import re
from pathlib import PurePosixPath


from .agent_clients import validate_agent_id, validate_model


TOOLS = {"agent": "Agent", "codex": "Codex", "python": "Python", "command": "命令行", "mcp": "MCP"}
IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
REFERENCE = re.compile(r"\$\{([^{}]+)\}")


def validate_model_resource(resource):
    fields = {'id', 'kind', 'label', 'modelId', 'provider', 'model', 'baseUrl'}
    if set(resource) - fields or any(not isinstance(resource.get(key), str) or not resource[key].strip()
                                   or len(resource[key]) > 300 or '${' in resource[key]
                                   for key in ('id', 'label', 'modelId', 'provider', 'model')):
        raise ValueError('模型资源需要明确的模型配置标识、供应商和模型名称，不能包含凭证或其他字段。')
    if 'baseUrl' in resource:
        from urllib.parse import urlsplit
        if not isinstance(resource['baseUrl'], str):
            raise ValueError('模型 API 地址必须是文本。')
        url = urlsplit(resource['baseUrl'])
        if url.username or url.password or url.query or url.fragment or not url.hostname or not (url.scheme == 'https' or url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('模型 API 地址无效，不能包含凭证。')


def script_source(definition, config, name):
    return definition['scripts'][name]


def script_path(value):
    path = PurePosixPath(value)
    return str(path) == value and len(path.parts) >= 2 and path.parts[0] == "scripts" and ".." not in path.parts and path.suffix == ".py"


def declared_outputs(value):
    return {f"${{steps.{step['id']}.outputs.{output['id']}}}": output
            for step in value["steps"] for output in step.get("outputs", [])}


def validate(value, config):
    if not isinstance(value, dict) or value.get("schemaVersion") != 2:
        raise ValueError("流程需要 schemaVersion=2 的通用配置，请重新搭建流程。")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in ("title", "goal", "completion")):
        raise ValueError("流程需要名称、目标和交付标准。")
    inputs, steps, scripts = value.get("inputs", []), value.get("steps", []), value.get("scripts", {})
    model_resources = {item['id']: item for item in config.get('resources', []) if item.get('kind') == 'model'}
    for resource in model_resources.values():
        validate_model_resource(resource)
    if not isinstance(inputs, list) or not isinstance(steps, list) or not steps or not isinstance(scripts, dict):
        raise ValueError("流程至少需要一个步骤。")
    for name, source in scripts.items():
        if not script_path(name) or not isinstance(source, str) or len(source) > 100000:
            raise ValueError("脚本必须是 scripts/ 中的 Python 文件。")
        try:
            compile(source, name, "exec")
        except SyntaxError as error:
            raise ValueError(f"脚本 {name} 第 {error.lineno} 行有语法错误。") from error
    for items in (inputs, steps):
        ids = [item.get("id", "") for item in items if isinstance(item, dict)]
        if len(ids) != len(items) or len(ids) != len(set(ids)) or any(not IDENTIFIER.fullmatch(key) for key in ids):
            raise ValueError("输入和步骤需要各自唯一的有效标识。")
    for item in inputs:
        if not item.get("label") or item.get("kind") not in {"file", "text"}:
            raise ValueError("输入需要名称和文件或文本类型。")
    step_ids = {step["id"] for step in steps}
    available = {"workspace", "bundle", "failure", "checks"}
    available.update(f"inputs.{item['id']}" for item in inputs)
    available.update(f"resources.{item['id']}" for item in config.get("resources", []))
    for step in steps:
        if not step.get("label") or not isinstance(step.get("instructions"), str) or not isinstance(step.get("inputs", {}), dict):
            raise ValueError("每个步骤需要名称、任务说明和输入配置。")
        execution = step.get("execution", {})
        tool = execution.get("tool")
        if tool not in TOOLS:
            raise ValueError("当前可使用 Agent、Python、命令行或 MCP 执行步骤。")
        if tool == 'mcp':
            if (not isinstance(execution.get('connectorId'), str) or not IDENTIFIER.fullmatch(execution['connectorId'])
                    or not isinstance(execution.get('operation'), str) or not execution['operation']
                    or '${' in execution['operation'] or not isinstance(execution.get('arguments'), dict)):
                raise ValueError('MCP 步骤需要固定的连接器、操作名称和参数对象。')
            if 'forEach' in execution and (not isinstance(execution['forEach'], str) or not REFERENCE.fullmatch(execution['forEach'])):
                raise ValueError('forEach 必须完整引用前序步骤的列表数据。')
        if 'forEach' in execution:
            if tool not in {'mcp', 'command'} or not isinstance(execution['forEach'], str) or not REFERENCE.fullmatch(execution['forEach']):
                raise ValueError('forEach 仅用于 MCP 或命令行，必须完整引用列表数据。')
        if tool in {'agent', 'codex'} and 'reasoning' in execution:
            if not execution['reasoning']:
                raise ValueError('思考强度不能为空；继承时请省略 reasoning。')
            validate_model(execution['reasoning'])
        if tool in {'agent', 'codex'} and 'model' in execution:
            if execution['model'] is None:
                raise ValueError('继承默认模型时请省略 model。')
            validate_model(execution['model'])
        if tool == "agent" and "agentId" in execution:
            validate_agent_id(execution["agentId"])
        if tool in {"agent", "codex"} and (not isinstance(execution.get("session", "default"), str) or not execution.get("session", "default").strip()):
            raise ValueError("Agent 会话名称不能为空。")
        if tool == "python" and execution.get("script") not in scripts:
            raise ValueError("Python 步骤需要引用本流程保存的脚本。")
        if 'models' in execution:
            raise ValueError('模型调用名绑定已移除，请改用 Python args 中的 --model 和 --reasoning 参数并更新脚本。')
        if tool == 'python':
            from .workflow_models import model_parameters
            selection = model_parameters(execution)
            if selection and selection['resourceId'] not in model_resources:
                raise ValueError('Python --model 参数引用了未登记的模型资源。')
        if tool == "command":
            validate_command(execution.get("command"))
            if execution.get('effect', 'write') not in {'read', 'write'}:
                raise ValueError('命令 effect 必须为 read 或 write；未知影响按 write 处理。')
            timeout = execution.get('timeoutSeconds', 180)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 1200:
                raise ValueError('命令超时必须大于 0 且不超过 1200 秒。')
            if 'forEach' in execution and '${item' in execution['command'][0]:
                raise ValueError('逐项数据只能用于命令参数，不能选择可执行程序。')
        if tool in {"agent", "codex"} and execution.get("mode", "write") not in {"read-only", "write"}:
            raise ValueError("Agent 步骤需要有效的执行权限。")
        if tool in {'agent', 'codex'} and execution.get('responseOutput') and execution.get('mode', 'write') != 'read-only':
            raise ValueError('写入模式由 Agent 生成产出文件，不能同时把结束回复保存为产出；请移除 responseOutput，或使用只读模式直接返回正文。')
        failure = step.get("onFailure", {"action": "stop"})
        if failure.get("action") not in {"stop", "retry", "goto"}:
            raise ValueError("失败处理需要选择结束、重试或返回步骤。")
        attempts = failure.get("maxAttempts", 0)
        if not isinstance(attempts, int) or not 0 <= attempts <= 5:
            raise ValueError("自动纠正次数需要在 0 到 5 之间。")
        if failure.get("action") == "goto" and failure.get("target") not in step_ids:
            raise ValueError("失败后返回的步骤不存在。")
        output_ids = set()
        for output in step.get("outputs", []):
            if not IDENTIFIER.fullmatch(output.get("id", "")) or output["id"] in output_ids or not output.get("label"):
                raise ValueError("步骤产出需要唯一标识和名称。")
            if output.get('kind', 'file') == 'value':
                pointer = output.get('pointer', '') if tool == 'command' else output.get('pointer')
                if tool not in {'mcp', 'command'} or not isinstance(pointer, str) or (pointer and not pointer.startswith('/')) or re.search(r'~(?![01])', pointer):
                    raise ValueError('数据产出只用于 MCP 或命令行；pointer 使用 JSON Pointer。')
                if tool == 'command' and (output.get('source') != 'stdout' or output.get('format') != 'json'):
                    raise ValueError('命令数据产出需要 source=stdout、format=json。')
            elif output.get('kind', 'file') not in {'file', 'json'} or not isinstance(output.get('path'), str) or not output['path']:
                raise ValueError('文件产出需要有效路径。')
            elif tool == 'command' and 'forEach' in execution:
                raise ValueError('逐项命令使用 stdout JSON 数据产出；文件汇总放在后续步骤。')
            elif tool == 'mcp':
                raise ValueError('MCP 返回请声明为数据产出；需要生成本机文件时使用后续步骤。')
            output_ids.add(output["id"])
        if tool in {'agent', 'codex'} and execution.get('responseOutput') and execution['responseOutput'] not in output_ids:
            raise ValueError('回复保存位置需要引用本步骤产出标识。')
        for check in step.get("checks", []):
            if check.get("kind") == "command":
                validate_command(check.get("command"))
                if not isinstance(check.get("expectedExitCode", 0), int):
                    raise ValueError("检查需要整数退出码。")
            elif check.get("kind") != "file" or not check.get("path"):
                raise ValueError("完成条件需要命令检查或文件检查。")
        if not step.get("outputs") and not step.get("checks"):
            raise ValueError("每一步至少需要一个可检查的产出或完成条件。")
        def refs(item):
            if isinstance(item, str):
                yield from REFERENCE.findall(item)
            elif isinstance(item, dict):
                for entry in item.values():
                    yield from refs(entry)
            elif isinstance(item, list):
                for entry in item:
                    yield from refs(entry)
        execution_refs = dict(execution)
        if tool in {'mcp', 'command'} and 'forEach' in execution:
            field = 'arguments' if tool == 'mcp' else 'command'
            for ref in refs(execution[field]):
                if ref not in available and ref != 'item' and not ref.startswith('item.'):
                    raise ValueError(f"逐项参数引用了不可用的数据：{ref}")
            execution_refs.pop(field)
        referenced = {key: step[key] for key in ('instructions', 'inputs', 'outputs', 'checks') if key in step}
        referenced['execution'] = execution_refs
        for ref in refs(referenced):
            if ref not in available:
                raise ValueError(f"步骤 {step['label']} 引用了尚不可用的数据：{ref}")
        available.update(f"steps.{step['id']}.outputs.{key}" for key in output_ids)
    selected = value.get("finalOutputs")
    if not isinstance(selected, list):
        raise ValueError("请明确指定 finalOutputs 最终输出清单；无文件交付时使用空数组。")
    declared = declared_outputs(value)
    if any(not isinstance(ref, str) or ref not in declared for ref in selected):
        raise ValueError("最终输出必须引用已声明的节点产出，例如 ${steps.report.outputs.file}。")
    if len(selected) != len(set(selected)):
        raise ValueError("最终输出不能重复引用同一节点产出。")
    return copy.deepcopy(value)


def validate_command(command):
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command):
        raise ValueError("命令需要有效的程序和参数数组。")


def write_bundle(directory, definition, config=None):
    for name, source in definition.get("scripts", {}).items():
        if not script_path(name):
            raise ValueError("无效的脚本路径。")
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script_source(definition, config, name) if config is not None else source)
