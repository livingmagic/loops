import copy
import re
from pathlib import PurePosixPath


TOOLS = {"codex": "Codex", "python": "Python", "command": "命令行"}
IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
REFERENCE = re.compile(r"\$\{([^{}]+)\}")


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
    available = {"workspace", "failure", "checks"}
    available.update(f"inputs.{item['id']}" for item in inputs)
    available.update(f"resources.{item['id']}" for item in config.get("resources", []))
    for step in steps:
        if not step.get("label") or not isinstance(step.get("instructions"), str) or not isinstance(step.get("inputs", {}), dict):
            raise ValueError("每个步骤需要名称、任务说明和输入配置。")
        execution = step.get("execution", {})
        tool = execution.get("tool")
        if tool not in TOOLS:
            raise ValueError("当前可使用 Codex、Python 或命令行执行步骤。")
        if tool == "python" and execution.get("script") not in scripts:
            raise ValueError("Python 步骤需要引用本流程保存的脚本。")
        if tool == "command":
            validate_command(execution.get("command"))
        if tool == "codex" and execution.get("mode", "write") not in {"read-only", "write"}:
            raise ValueError("Codex 步骤需要有效的执行权限。")
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
            if not IDENTIFIER.fullmatch(output.get("id", "")) or output["id"] in output_ids or not output.get("path") or not output.get("label"):
                raise ValueError("步骤产出需要唯一标识、名称和路径。")
            output_ids.add(output["id"])
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
        for ref in refs({key: step[key] for key in ("instructions", "inputs", "execution", "outputs", "checks") if key in step}):
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


def write_bundle(directory, definition):
    for name, source in definition.get("scripts", {}).items():
        if not script_path(name):
            raise ValueError("无效的脚本路径。")
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
