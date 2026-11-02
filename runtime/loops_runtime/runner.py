import asyncio
import importlib.util
import os
import signal
import shlex
import sys
from pathlib import Path

from .codex_adapter import CodexSession
from .environment import command_environment
from .store import ACTIVE, now
from .workflow import create_run


class ExecutionError(RuntimeError):
    pass


class Runner:
    def __init__(self, store, notify, session_factory=CodexSession):
        self.store, self.notify, self.session_factory = store, notify, session_factory
        self.tasks = {}

    def start(self, loop_id, inputs=None, snapshot=None):
        loop = self.store.get(loop_id)
        if not loop["definition"]:
            raise ValueError("请先搭建流程。")
        if any(run["status"] in ACTIVE for run in loop["runs"]):
            raise ValueError("这个 Loop 已有运行中的任务。")
        run = create_run(self.store, loop, inputs, snapshot=snapshot)
        return self.launch(loop_id, run)

    def launch(self, loop_id, run):
        run_id = run["id"]
        task = asyncio.create_task(self.execute(loop_id, run_id))
        self.tasks[run_id] = task
        def settled(completed):
            self.tasks.pop(run_id, None)
            if completed.cancelled() and self.store.run(loop_id, run_id)["status"] in ACTIVE:
                self.store.update_run(loop_id, run_id, status="stopped", endedAt=now(), reason="本次执行已停止，记录已保留。")
                self.notify()
        task.add_done_callback(settled)
        self.notify()
        return run

    def stop(self, loop_id, run_id):
        task = self.tasks.get(run_id)
        if task and self.store.run(loop_id, run_id)["status"] in ACTIVE:
            self.store.update_run(loop_id, run_id, status="stopping")
            task.cancel()
            self.notify()
        return self.store.run(loop_id, run_id)

    async def execute(self, loop_id, run_id):
        context = RunContext(self.store, loop_id, run_id, self.notify, self.session_factory)
        try:
            self.store.update_run(loop_id, run_id, status="running")
            self.notify()
            source = Path(context.run["directory"]) / "workflow.py"
            spec = importlib.util.spec_from_file_location(f"workflow_{run_id.replace('-', '_')}", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            await asyncio.wait_for(module.run(context), timeout=1200)
        except asyncio.CancelledError:
            if self.store.run(loop_id, run_id)["status"] in ACTIVE:
                context.finish("stopped", "本次执行已停止，工作区和记录已保留。")
        except asyncio.TimeoutError:
            if self.store.run(loop_id, run_id)["status"] in ACTIVE:
                context.finish("failed", "达到本次运行的 20 分钟上限，工作区和证据已保留。")
        except Exception as error:
            if self.store.run(loop_id, run_id)["status"] in ACTIVE:
                context.finish("failed", str(error))


class RunContext:
    def __init__(self, store, loop_id, run_id, notify, session_factory):
        self.store, self.loop_id, self.run_id, self.notify = store, loop_id, run_id, notify
        self.session_factory = session_factory
        self.run = store.run(loop_id, run_id)
        self.directory = Path(self.run["directory"])
        self.repo = self.directory / "workspace"
        self.stage_id = None

    def emit(self, kind, text, **extra):
        if self.stage_id is not None:
            extra.setdefault("stage", self.stage_id)
        self.store.event(self.loop_id, self.run_id, kind, text, **extra)
        if kind == "session":
            self.store.update_run(self.loop_id, self.run_id, threadId=extra["threadId"])
        self.notify()

    def stage(self, stage_id, correcting=False):
        self.stage_id = stage_id
        self.store.update_run(self.loop_id, self.run_id, stage=stage_id, status="correcting" if correcting else "running")
        label = next((step["label"] for step in self.run["definition"]["steps"] if step["id"] == stage_id), stage_id)
        self.emit("stage", label, stage=stage_id)

    async def run_workflow(self, definition):
        from .workflow import Workflow
        return await Workflow(self).run(definition)

    def finish(self, status, reason="", **extra):
        if status in {"failed", "stopped", "interrupted"} and "artifacts" not in extra:
            extra["artifacts"] = self.store.run(self.loop_id, self.run_id).get("artifacts", [])
        self.store.update_run(self.loop_id, self.run_id, status=status, reason=reason, endedAt=now(), **extra)
        self.emit("finished", reason or "已完成交付。")

    async def command(self, argv, *, cwd=None, check=True, timeout=180, capture_all=False, env=None):
        argv = [sys.executable if value in {"python", "python3"} and index == 0 else value for index, value in enumerate(argv)]
        self.emit("command", shlex.join(argv), stage=self.stage_id)
        process = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd or self.repo), stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.STDOUT, start_new_session=True,
                                                       env=command_environment() if env is None else env)
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        text = raw.decode("utf-8", "replace")
        if check and process.returncode:
            raise ExecutionError(f"命令失败：{' '.join(argv)}\n{text[-4000:]}")
        return {"command": argv, "exitCode": process.returncode, "output": text if capture_all else text[-12000:], "at": now()}

    async def git(self, *argv, cwd=None):
        return (await self.command(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=cwd, capture_all=True))["output"].strip()
