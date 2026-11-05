import asyncio
import importlib.util
import json
import os
import signal
import shlex
import sys
from pathlib import Path

from .agent_clients import AgentClients
from .environment import command_environment
from .store import ACTIVE, now
from .workflow import create_run


class ExecutionError(RuntimeError):
    pass


class Runner:
    def __init__(self, store, notify, session_factory=None, connectors=None, agent_clients=None, model_host=None):
        self.store, self.notify, self.session_factory = store, notify, session_factory
        self.tasks = {}
        self.connectors = connectors
        self.agent_clients = agent_clients or AgentClients(store.root)
        self.model_host = model_host

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
        context.connectors = self.connectors
        context.agent_clients = self.agent_clients
        context.model_host = self.model_host
        try:
            from .workflow_models import selections as model_selections
            selected = model_selections(context.run['definition'], context.run['config'])
            if selected:
                if not self.model_host:
                    raise ValueError('模型 API 服务未连接，请通过 Loops 客户端运行。')
                await self.model_host.call('validate', {'bindings': list(selected.values())})
                context.run['modelSelections'] = selected
                self.store.update_run(loop_id, run_id, modelSelections=selected)
            if not self.session_factory:
                bindings = await self.agent_clients.bind(context.run["definition"], context.run["config"])
                context.run["agentBindings"] = bindings
                self.store.update_run(loop_id, run_id, agentBindings=bindings)
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
            sessions = dict(self.store.run(self.loop_id, self.run_id).get("agentSessions", {}))
            key = extra.get("sessionKey", "legacy")
            sessions[key] = {"agentId": extra.get("agentId", "codex"), "threadId": extra["threadId"]}
            self.store.update_run(self.loop_id, self.run_id, threadId=extra["threadId"], agentSessions=sessions)
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

    async def command(self, argv, *, cwd=None, check=True, timeout=180, capture_all=False, env=None, separate_output=False, rpc=None):
        separate_output = separate_output or rpc is not None
        argv = [sys.executable if value in {"python", "python3"} and index == 0 else value for index, value in enumerate(argv)]
        self.emit("command", shlex.join(argv), stage=self.stage_id)
        process = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd or self.repo), stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE if separate_output else asyncio.subprocess.STDOUT, start_new_session=True,
                                                       stdin=asyncio.subprocess.PIPE if rpc else None, limit=2_000_000,
                                                       env=command_environment() if env is None else env)
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        overflow = asyncio.get_running_loop().create_future()
        async def read_stream(reader, name):
            while chunk := await reader.read(65536):
                buffers[name].extend(chunk)
                if name == 'stdout' and len(buffers[name]) > 8 * 1024 * 1024:
                    del buffers[name][8 * 1024 * 1024:]
                    if not overflow.done():
                        overflow.set_result(ExecutionError('命令标准输出超过 8 MiB，请改用文件产出。'))
                if name == 'stderr' and len(buffers[name]) > 12000:
                    del buffers[name][:-12000]
        async def exchange():
            while line := await process.stdout.readline():
                if len(line) > 1_000_000:
                    raise ExecutionError('模型请求过大。')
                message = json.loads(line)
                try:
                    response = {'result': await rpc(message)}
                except Exception as error:
                    response = {'error': str(error)}
                process.stdin.write((json.dumps(response, ensure_ascii=False) + '\n').encode())
                await process.stdin.drain()
        async def collect():
            await asyncio.gather(exchange() if rpc else read_stream(process.stdout, 'stdout'), read_stream(process.stderr, 'stderr'))
            await process.wait()
        collector = asyncio.create_task(collect()) if separate_output else asyncio.create_task(process.communicate())
        def evidence():
            return {'command': argv, 'exitCode': process.returncode,
                    'stdout': buffers['stdout'].decode('utf-8', 'replace'),
                    'stderr': buffers['stderr'].decode('utf-8', 'replace'), 'at': now()}
        try:
            done, _ = await asyncio.wait([collector, overflow], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if overflow in done:
                raise overflow.result()
            if collector not in done:
                raise asyncio.TimeoutError()
            captured = collector.result()
        except BaseException as error:
            # Always kill the group, including descendants that outlive their parent.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(asyncio.shield(collector), 0.5)
            except BaseException:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # macOS can deny a signal after the group has already exited.
                    # Suppress only when process exit and both pipe closures are known.
                    if process.returncode is None or not collector.done() or collector.cancelled() or collector.exception():
                        raise
                await process.wait()
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
            if isinstance(error, asyncio.TimeoutError):
                error = ExecutionError(f'命令超过 {timeout:g} 秒，已终止进程。')
                error.timed_out = True
            error.command_result = evidence()
            raise error
        if separate_output:
            result = evidence()
            result['output'] = (result['stdout'] + result['stderr'])[-12000:]
            if check and process.returncode:
                error = ExecutionError(f"命令失败，退出码 {process.returncode}：{result['output'][-4000:]}")
                error.command_result = result
                raise error
            return result
        raw, _ = captured
        text = raw.decode("utf-8", "replace")
        if check and process.returncode:
            raise ExecutionError(f"命令失败：{' '.join(argv)}\n{text[-4000:]}")
        return {"command": argv, "exitCode": process.returncode, "output": text if capture_all else text[-12000:], "at": now()}

    async def git(self, *argv, cwd=None):
        return (await self.command(["git", "-c", "core.hooksPath=/dev/null", *argv], cwd=cwd, capture_all=True))["output"].strip()
