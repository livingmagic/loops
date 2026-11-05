import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from .store import Store, uid
from .runner import Runner
from .fixture import create_fixture
from .agent_clients import AgentClients, validate_agent_id, validate_model, step_agent, resolve_selection, PLUGIN_ERRORS
from .commands import read_commands
from .workflow import resources
from .automation import Automations
from .connectors import Connectors


def output(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


class Service:
    def __init__(self, root, notify=output, workspace_root=None, model_host=None):
        self.store = Store(root, workspace_root=workspace_root)
        self.notify = notify
        self.connectors = Connectors(self.store.root, notify)
        self.agent_clients = AgentClients(self.store.root)
        self.model_host = model_host
        self.runner = Runner(self.store, self.changed, connectors=self.connectors, agent_clients=self.agent_clients, model_host=model_host)
        self.automations = Automations(self.store, self.runner, notify, connectors=self.connectors)

    def changed(self):
        self.notify({"event": "state", "loops": self.store.list()})

    async def validate_models(self, definition, config):
        from .workflow_models import selections
        selected = selections(definition, config)
        if selected:
            if not self.model_host:
                raise ValueError('模型 API 服务未连接。')
            await self.model_host.call('validate', {'bindings': list(selected.values())})

    async def handle(self, method, params):
        if method != "shutdown":
            self.connectors.startup()
        if method.startswith("connector_"):
            return await self.connectors.handle(method, params)
        if method.startswith("automation_"):
            return await self.automations.handle(method, params)
        if method != "shutdown":
            await self.automations.startup()
        loop_id = params.get("loopId")
        if method == "list":
            return self.store.list()
        if method == "trash":
            return [{key: loop[key] for key in ("id", "title", "deletedAt")} for loop in self.store.list(deleted=True)]
        if method == "new":
            value = self.store.new(params.get("workspace"), params.get("agentId"))
        elif method == "delete":
            if any(n["loopId"] == loop_id for t in self.automations.list() for spec in (t.get("draftSpec"), t.get("spec")) if spec for n in spec.get("nodes", [])):
                raise ValueError("这个 Loop 正被自动化任务使用，请先移除引用或删除对应任务。")
            value = self.store.delete(loop_id)
        elif method == "restore":
            value = self.store.restore(loop_id)
        elif method == "rename":
            value = self.store.rename(loop_id, params.get("title"))
        elif method == "configure":
            value = self.store.configure(loop_id, params["config"])
        elif method in {'model_catalog', 'add_model_resource', 'step_model_selection'}:
            if not self.model_host:
                raise ValueError('模型 API 服务未连接，请通过 Loops 客户端配置。')
            if method == 'model_catalog':
                return await self.model_host.call('catalog')
            description = await self.model_host.call('describe', {'modelId': params.get('modelId')})
            loop = self.store.get(loop_id)
            metadata = {key: description[key] for key in ('modelId', 'provider', 'model', 'baseUrl') if key in description}
            resource = next((item for item in loop['config']['resources'] if item['kind'] == 'model'
                             and all(item.get(key) == metadata.get(key) for key in ('modelId', 'provider', 'model', 'baseUrl'))), None)
            config = copy.deepcopy(loop['config'])
            if resource is None:
                resource = {'id': uid('model'), 'kind': 'model', 'label': description['name'], **metadata}
                config['resources'].append(resource)
            if method == 'add_model_resource':
                self.store.configure(loop_id, config)
                self.changed()
                return {**resource, 'thinkingLevels': description['thinkingLevels'], 'thinkingLevel': description['thinkingLevel']}
            definition = copy.deepcopy(loop['definition'])
            step = next((item for item in (definition or {}).get('steps', []) if item['id'] == params.get('stepId')), None)
            from .workflow_models import model_parameters
            reasoning = params.get('reasoning')
            if not step or step['execution']['tool'] != 'python' or not model_parameters(step['execution']):
                raise ValueError('请选择使用 --model 和 --reasoning 参数的 Python 节点。')
            if reasoning not in description['thinkingLevels']:
                raise ValueError('此模型不支持所选思考强度。')
            args = step['execution']['args']
            args[args.index('--model') + 1] = resource['id']
            args[args.index('--reasoning') + 1] = reasoning
            value = self.store.update_definition(loop_id, definition, config=config)
        elif method == "agent_selection":
            loop = self.store.get(loop_id)
            agent_id = params.get('agentId')
            if agent_id is not None:
                validate_agent_id(agent_id)
            if params.get('stepId'):
                definition = copy.deepcopy(loop['definition'])
                step = next((s for s in (definition or {}).get('steps', []) if s['id'] == params['stepId']), None)
                if not step or step['execution']['tool'] not in {'agent', 'codex'}:
                    raise ValueError('只能为智能步骤选择执行 Agent。')
                if agent_id and agent_id != step_agent(step['execution'], loop['config']):
                    step['execution'].pop('model', None)
                    step['execution'].pop('reasoning', None)
                if 'model' in params or 'reasoning' in params:
                    state = await self.agent_clients.configured_probe(agent_id)
                    if not state.get('ready') or step['execution'].get('mode', 'write') not in state.get('modes', []):
                        raise ValueError('所选 Agent 当前不可用或不支持此节点的执行权限。')
                    selection = {'model': validate_model(params.get('model')), 'reasoning': validate_model(params.get('reasoning'))}
                    resolve_selection(state, selection)
                    for field, selected in selection.items():
                        step['execution'].pop(field, None)
                        if selected is not None:
                            step['execution'][field] = selected
                step['execution']['tool'] = 'agent'
                step['execution'].pop('agentId', None)
                if agent_id is not None:
                    step['execution']['agentId'] = agent_id
                value = self.store.update_definition(loop_id, definition)
            else:
                validate_agent_id(agent_id)
                value = self.store.configure(loop_id, {**loop['config'], 'defaultAgentId': agent_id})
        elif method == "agent_model_default":
            return await self.agent_clients.configure_model(params['agentId'], params.get('model') or None, params.get('reasoning') or None)
        elif method == "agent_model_selection":
            loop = self.store.get(loop_id)
            model = validate_model(params.get('model'))
            reasoning = validate_model(params.get('reasoning'))
            if params.get('stepId'):
                definition = copy.deepcopy(loop['definition'])
                step = next((s for s in (definition or {}).get('steps', []) if s['id'] == params['stepId']), None)
                if not step or step['execution']['tool'] not in {'agent', 'codex'}:
                    raise ValueError('只能为智能步骤选择模型。')
                if 'reasoning' in params:
                    step['execution'].pop('reasoning', None)
                    if reasoning is not None:
                        step['execution']['reasoning'] = reasoning
                elif step['execution'].get('model') != model:
                    step['execution'].pop('reasoning', None)
                step['execution'].pop('model', None)
                if model is not None:
                    step['execution']['model'] = model
                value = self.store.update_definition(loop_id, definition)
            else:
                key = validate_agent_id(params.get('agentId'))
                models = {**loop['config'].get('agentModels', {})}
                efforts = {**loop['config'].get('agentReasoning', {})}
                if 'reasoning' in params:
                    efforts.pop(key, None)
                    if reasoning is not None:
                        efforts[key] = reasoning
                elif models.get(key) != model:
                    efforts.pop(key, None)
                if model is None:
                    models.pop(key, None)
                else:
                    models[key] = model
                value = self.store.configure(loop_id, {**loop['config'], 'agentModels': models, 'agentReasoning': efforts})
        elif method == "agent_clients":
            return await self.agent_clients.discover()
        elif method == "fixture":
            if params.get("kind") == "sales":
                from .fixture import create_sales_fixture
                fixture = create_sales_fixture(self.store.root)
            else:
                fixture = create_fixture(self.store.root)
            value = fixture
        elif method == "inspect":
            loop = self.store.get(loop_id)
            selected = resources(loop["config"])
            root = next((Path(item["path"]) for item in selected if item["kind"] in {"directory", "git"}), Path(loop["workdir"]))
            files = []
            for directory, dirs, names in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in {".git", ".ssh", ".codex", ".aws", "node_modules", ".venv", "__pycache__", "dist"} and not Path(directory, d).is_symlink())
                for name in sorted(names):
                    if name.startswith(".env"):
                        continue
                    files.append(str(Path(directory, name).relative_to(root)))
                    if len(files) >= 150:
                        break
                if len(files) >= 150:
                    break
            value = {"config": loop["config"], "files": files, "readme": (root / "README.md").read_text(errors="replace")[:12000] if (root / "README.md").is_file() else "", "definition": loop["definition"], "python": sys.executable,
                     "capabilities": {"agents": await self.agent_clients.discover(), "defaultAgentId": loop["config"].get("defaultAgentId", "codex"), "tools": ["agent", "python", "command", "mcp"], "mcp": "先 list_connectors、list_connector_operations 和 inspect_connector_operation 发现真实能力。execution={tool:mcp,connectorId,operation,arguments}；outputs 的 kind=value、pointer 为指向响应的 JSON Pointer（/payload 为业务数据，/links 为链接列表，/result 为原始结果）。完整步骤输出引用保留原始类型。MCP execution.forEach 可完整引用 JSON 数组，arguments 用 ${item} 或 ${item.字段} 逐项绑定；value 输出按项汇总数组，空输入产生空数组且不调用。Python/命令步骤的 outputs 可用 kind=json，仍写 path 文件，平台解析 JSON 后传递原始数据。数据产出自动保存 JSON 供查看，不是本机文件路径；旧文件产出保持不变。", "pythonContext": "Python 脚本从 os.environ['LOOPS_CONTEXT'] 读取 JSON，字段 inputs、outputs、workspace、bundle、scripts、resources、failure、checks；不要写死本次路径。inputs 只包含本步骤 inputs 映射后的值；outputs 只包含本步骤声明的文件路径，不支持目录产出。checks 是执行器已经运行的真实命令记录列表，每项包含 stage、command、exitCode、output、expectedExitCode、passed；passed 表示退出码符合预期，因此验证预期失败（expectedExitCode=1）时 exitCode=1 且 passed=true。报告直接读取这些记录，不把预期退出码当作运行结果。可修改仓库是 workspace；resources 中的文件资源是原始只读资料，model 资源是无凭证的模型元数据。bundle 是本次运行的固定版本包；scripts 是声明脚本名到固定绝对路径的映射，校验代码直接使用这些路径，不在 workspace 重建。", "references": "${inputs.ID}、${resources.ID}、${steps.ID.outputs.ID}、${workspace}、${bundle}、${failure}、${checks}；任务指令、执行配置与 checks 支持引用。resources 的文件资源为原始资料路径，model 资源为模型元数据；选择 Git 资料时自动建立独立 workspace，代码修改在 workspace 中执行。", "agent": "需要自主使用工具的步骤使用 tool=agent，省略 agentId 使用 config.defaultAgentId；员工指定某步骤使用其他客户端时填写 agentId。客户端不可用时告知用户，不自动替换。模型优先级为 execution.model、config.agentModels[agentId]、客户端条目的 defaultModel。execution.reasoning 或 config.agentReasoning[agentId] 可指定思考强度，取 models[].reasoningLevels 中的值。用户未要求时不填写模型或强度覆盖。instructions 为任务指令；execution.session 相同且工作目录相同则共享会话；mode 可为 write 或 read-only；只读时可用 responseOutput 将最终回复保存到指定产出 ID。", "commands": "command 支持 forEach 完整引用 JSON 数组，参数用 ${item} 或 ${item.字段}，空数组不执行。value 产出声明 source=stdout、format=json，pointer 可选；单次输出原始 JSON，逐项输出数组。timeoutSeconds 默认 180 秒。effect=read 仅用于确认只读的命令；省略按 write，发起后不自动重放。命令使用程序和参数数组；cwd 默认本次 workspace；Python 使用 python；声明脚本使用 ${bundle}/scripts/name.py；旧的 scripts/name.py 与 ${workspace}/scripts/name.py 参数也会解析到同一固定版本脚本。checks 的 expectedExitCode 默认 0，可以用 1 验证修复前失败。"}}
        elif method == "read_project":
            loop = self.store.get(loop_id)
            selected = resources(loop["config"])
            resource = next((item for item in selected if item["id"] == params.get("resourceId")), None) if params.get("resourceId") else next((item for item in selected if item["kind"] in {"directory", "git"}), None)
            if not resource or resource['kind'] == 'model':
                raise ValueError("请先选择项目资料。")
            root = Path(resource["path"]).resolve()
            path = (root / params["path"]).resolve()
            if not path.is_relative_to(root) or any(p.startswith(".env") or p in {".git", ".ssh", ".codex", ".aws"} for p in path.relative_to(root).parts) or not path.is_file():
                raise ValueError("只能读取当前项目范围内的普通文件。")
            value = path.read_text(errors="replace")[:20000]
        elif method == "read_input":
            loop = self.store.get(loop_id)
            config = loop["config"]
            registered = list(config["inputDefaults"].values()) + [item["path"] for item in resources(config) if item["kind"] == "file"]
            allowed = {Path(value).expanduser().resolve() for value in registered if isinstance(value, str) and value}
            path = Path(params.get("path") or next(iter(config["inputDefaults"].values()), ".")).expanduser().resolve()
            if path not in allowed or not path.is_file() or path.stat().st_size > 2_000_000:
                raise ValueError("只能读取项目资料中已登记且不超过 2MB 的文件输入。")
            if any(part.startswith(".env") or part in {".git", ".ssh"} for part in path.parts):
                raise ValueError("不能将本机凭证或配置作为日志读取。")
            value = {"path": str(path), "name": path.name, "content": path.read_text(errors="replace")[:20000]}
        elif method == "definition":
            from .definition import validate
            from .workflow_connectors import bind_connectors
            definition = validate(params['definition'], self.store.get(loop_id)['config'])
            await self.validate_models(definition, self.store.get(loop_id)['config'])
            value = self.store.update_definition(loop_id, await bind_connectors(definition, self.connectors))
        elif method == "version":
            return self.store.version(loop_id, params.get("revision"))
        elif method == "restore_version":
            value = self.store.restore_version(loop_id, params.get("revision"),
                expected_revision=params.get("expectedRevision"), discard_draft=params.get("discardDraft") is True)
        elif method == "save":
            loop = self.store.get(loop_id)
            expected_revision = params.get("expectedRevision", loop["revision"])
            if loop["definition"]:
                await self.validate_models(loop["definition"], loop["config"])
            value = self.store.save(loop_id, expected_revision=expected_revision)
        elif method == "start":
            revision = params.get("revision")
            snapshot = self.store.version(loop_id, revision) if revision is not None else None
            expected = params.get("expectedRevision")
            if snapshot is None and expected is not None and self.store.get(loop_id)["revision"] != expected:
                raise ValueError("流程已更新，请重新打开试跑。")
            value = self.runner.start(loop_id, params.get("inputs"), snapshot=snapshot)
        elif method == "script":
            from .definition import script_source
            loop = self.store.get(loop_id)
            source = (loop.get("definition") or {}).get("scripts", {}).get(params["path"])
            if source is None:
                raise ValueError("流程中未保存该脚本。")
            return {"title": params["path"], "content": script_source(loop['definition'], loop['config'], params['path'])}
        elif method == "stop":
            value = self.runner.stop(loop_id, params["runId"])
        elif method == "message":
            value = self.store.message(loop_id, params["role"], params["text"], **params.get("extra", {}))
        elif method == "message_update":
            value = self.store.update_message(loop_id, params["messageId"], params["process"])
        elif method == "claim_failure_review":
            loop = self.store.get(loop_id)
            run = loop["runs"][-1] if loop["runs"] else None
            if not run or run["id"] != params["runId"] or run["revision"] != loop["revision"] or run["status"] != "failed" or run.get("failureReview"):
                return False
            self.store.update_run(loop_id, run["id"], failureReview={"status": "claimed"})
            return True
        elif method == "session_get":
            return self.store.session(loop_id)
        elif method == "session_set":
            self.store.session(loop_id, params["messages"])
            return True
        elif method == "commands":
            loop = self.store.get(loop_id)
            run = next((run for run in loop["runs"] if run["id"] == params["runId"]), None)
            if not run:
                raise ValueError("找不到运行记录。")
            return read_commands(run)
        elif method == "artifact":
            loop = self.store.get(loop_id)
            run = next((run for run in loop["runs"] if run["id"] == params["runId"]), None)
            if not run:
                raise ValueError("找不到运行记录。")
            artifact = next((a for a in run["artifacts"] + run.get("intermediateArtifacts", []) if a.get("path") == params["path"]), None)
            if not artifact or not Path(artifact["path"]).is_file():
                raise ValueError("交付文件不存在。")
            return {"title": artifact["title"], "content": Path(artifact["path"]).read_text(errors="replace")[:150000]}
        elif method == "environment":
            clients = await self.agent_clients.discover(refresh=True)
            return {"python": sys.version.split()[0], "agents": clients, "agentPluginErrors": PLUGIN_ERRORS, "codex": next((c for c in clients if c['id'] == 'codex'), None), "dataDirectory": str(self.store.root)}
        elif method == "shutdown":
            await self.agent_clients.close()
            await self.automations.close()
            await self.connectors.close()
            for task in list(self.runner.tasks.values()):
                task.cancel()
            await asyncio.gather(*list(self.runner.tasks.values()), return_exceptions=True)
            return True
        else:
            raise ValueError("不支持的操作。")
        self.changed()
        return value


async def main():
    root = os.environ.get("LOOPS_DATA_DIR")
    if not root:
        raise RuntimeError("LOOPS_DATA_DIR is required")
    from .model_host import ModelHost
    model_host = ModelHost(output)
    service = Service(root, workspace_root=os.environ.get("LOOPS_WORKSPACE_ROOT"), model_host=model_host)
    async def respond(request):
        try:
            result = await service.handle(request["method"], request.get("params", {}))
            output({"id": request["id"], "result": result})
        except Exception as error:
            output({"id": request.get("id"), "error": str(error)})
    pending = set()
    while line := await asyncio.to_thread(sys.stdin.readline):
        try:
            request = json.loads(line)
            if 'hostResponse' in request:
                model_host.receive(request)
                continue
            task = asyncio.create_task(respond(request))
            pending.add(task)
            task.add_done_callback(pending.discard)
        except ValueError:
            output({"error": "无效的消息格式。"})
    await asyncio.gather(*pending, return_exceptions=True)
    await service.handle("shutdown", {})


if __name__ == "__main__":
    asyncio.run(main())
