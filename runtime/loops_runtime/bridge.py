import asyncio
import json
import os
import sys
from pathlib import Path
from .store import Store
from .runner import Runner
from .fixture import create_fixture
from .codex_adapter import probe_codex
from .commands import read_commands
from .workflow import resources
from .automation import Automations


def output(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


class Service:
    def __init__(self, root, notify=output, workspace_root=None):
        self.store = Store(root, workspace_root=workspace_root)
        self.notify = notify
        self.runner = Runner(self.store, self.changed)
        self.automations = Automations(self.store, self.runner, notify)

    def changed(self):
        self.notify({"event": "state", "loops": self.store.list()})

    async def handle(self, method, params):
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
            value = self.store.new(params.get("workspace"))
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
                     "capabilities": {"tools": ["codex", "python", "command"], "pythonContext": "Python 脚本从 os.environ['LOOPS_CONTEXT'] 读取 JSON，字段 inputs、outputs、workspace、resources、failure、checks；不要写死本次路径。inputs 只包含本步骤 inputs 映射后的值；outputs 只包含本步骤声明的文件路径，不支持目录产出。checks 是执行器已经运行的真实命令记录列表，每项包含 stage、command、exitCode、output、expectedExitCode、passed；passed 表示退出码符合预期，因此验证预期失败（expectedExitCode=1）时 exitCode=1 且 passed=true。报告直接读取这些记录，不把预期退出码当作运行结果。可修改仓库是 workspace，resources 是原始只读资料。脚本路径位于 scripts/。", "references": "${inputs.ID}、${resources.ID}、${steps.ID.outputs.ID}、${workspace}、${failure}、${checks}；任务指令、执行配置与 checks 支持引用。resources 为已选原始资料路径；选择 Git 资料时自动建立独立 workspace，代码修改在 workspace 中执行。", "codex": "instructions 为任务指令；execution.session 相同且工作目录相同则共享会话；mode 可为 write 或 read-only；只读时可用 responseOutput 将最终回复保存到指定产出 ID。", "commands": "命令使用程序和参数数组；cwd 默认本次 workspace；Python 使用 python；checks 的 expectedExitCode 默认 0，可以用 1 验证修复前失败。"}}
        elif method == "read_project":
            loop = self.store.get(loop_id)
            selected = resources(loop["config"])
            resource = next((item for item in selected if item["id"] == params.get("resourceId")), None) if params.get("resourceId") else next((item for item in selected if item["kind"] in {"directory", "git"}), None)
            if not resource:
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
            value = self.store.update_definition(loop_id, params["definition"])
        elif method == "save":
            value = self.store.save(loop_id)
        elif method == "start":
            value = self.runner.start(loop_id, params.get("inputs"))
        elif method == "script":
            loop = self.store.get(loop_id)
            source = (loop.get("definition") or {}).get("scripts", {}).get(params["path"])
            if source is None:
                raise ValueError("流程中未保存该脚本。")
            return {"title": params["path"], "content": source}
        elif method == "stop":
            value = self.runner.stop(loop_id, params["runId"])
        elif method == "message":
            value = self.store.message(loop_id, params["role"], params["text"], **params.get("extra", {}))
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
            try:
                codex = await probe_codex()
            except Exception as error:
                permission = "Operation not permitted" in str(error) or "Permission denied" in str(error)
                codex = {"ready": False, "detail": "系统权限阻止了 Codex 启动，请从 Mac 客户端检查本机权限。" if permission else "无法启动本机 Codex，请确认本地执行环境与登录状态。"}
            return {"python": sys.version.split()[0], "codex": codex, "dataDirectory": str(self.store.root)}
        elif method == "shutdown":
            await self.automations.close()
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
    service = Service(root, workspace_root=os.environ.get("LOOPS_WORKSPACE_ROOT"))
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
            task = asyncio.create_task(respond(request))
            pending.add(task)
            task.add_done_callback(pending.discard)
        except ValueError:
            output({"error": "无效的消息格式。"})
    await asyncio.gather(*pending, return_exceptions=True)
    await service.handle("shutdown", {})


if __name__ == "__main__":
    asyncio.run(main())
