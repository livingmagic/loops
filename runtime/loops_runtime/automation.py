"""Durable automation orchestration. Schedules/listeners belong to user Python scripts."""
import asyncio
import copy
import hashlib
import json
import os
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .definition import IDENTIFIER, declared_outputs, validate
from .store import ACTIVE, now, uid

TERMINAL = {"success", "failed", "unknown", "stopped", "interrupted"}


def output_keys(definition):
    return {ref: ref.removeprefix("${steps.").removesuffix("}").replace(".outputs.", ".")
            for ref in definition.get("finalOutputs", [])}


class Automations:
    def __init__(self, store, runner, notify):
        self.store, self.runner, self.notify = store, runner, notify
        self.db = store.db
        for name in ("automation_tasks", "automation_runs"):
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS automation_versions (task TEXT, revision INTEGER, data TEXT NOT NULL, PRIMARY KEY(task,revision))")
        self.db.execute("CREATE TABLE IF NOT EXISTS automation_events (task TEXT, event_key TEXT, fingerprint TEXT, run TEXT, PRIMARY KEY(task,event_key))")
        self.db.commit()
        self.sources, self.jobs, self.sampling = {}, {}, {}
        self.environment_locks = {}
        self.wake = asyncio.Event()
        self.started, self.closing, self.pump_task = False, False, None
        for run in self.runs():
            if run["status"] in {"running", "waiting", "stopping"}:
                run.update(status="interrupted", error="执行服务已重启，本次运行中断；请核对外部操作后再继续。", endedAt=now())
                for node in run["nodes"].values():
                    if node["status"] in {"running", "waiting"}:
                        node["status"] = "unknown"
                self.put("automation_runs", run)
        for task in self.list(include_deleted=True):
            task["sourceStatus"] = "stopped"
            self.put("automation_tasks", task)

    def put(self, table, value):
        self.db.execute(f"INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], json.dumps(value, ensure_ascii=False)))
        self.db.commit()
        return value

    def rows(self, table):
        return [json.loads(row[0]) for row in self.db.execute(f"SELECT data FROM {table} ORDER BY rowid")]

    def list(self, include_deleted=False):
        return [t for t in self.rows("automation_tasks") if include_deleted or not t.get("deletedAt")]

    def runs(self, task_id=None):
        return [r for r in self.rows("automation_runs") if not task_id or r["taskId"] == task_id]

    def task(self, task_id, deleted=False):
        row = self.db.execute("SELECT data FROM automation_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("找不到自动化任务。")
        task = json.loads(row[0])
        if task.get("deletedAt") and not deleted:
            raise ValueError("自动化任务已在回收站。")
        return task

    def run(self, run_id):
        row = self.db.execute("SELECT data FROM automation_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("找不到自动化运行。")
        return json.loads(row[0])

    def version(self, task_id, revision):
        return json.loads(self.db.execute("SELECT data FROM automation_versions WHERE task=? AND revision=?", (task_id, revision)).fetchone()[0])

    def detail(self, task_id):
        task = self.task(task_id)
        runs = []
        for run in self.runs(task_id):
            version = self.version(task_id, run["revision"])
            labels = {n["id"]: version["loops"][n["id"]]["title"] for n in version["spec"]["nodes"]}
            labels.update({n["id"]: n.get("title") or n["id"] for n in version["spec"]["outputs"]})
            output_labels = {}
            for node in version["spec"]["nodes"]:
                definition = version["loops"][node["id"]]["definition"]
                declared = declared_outputs(definition)
                output_labels.update({f"{node['id']}.{key}": declared[ref]["label"] for ref, key in output_keys(definition).items()})
            input_labels = {}
            for node in version["spec"]["nodes"]:
                for field in version["loops"][node["id"]]["definition"]["inputs"]:
                    ref = node.get("bindings", {}).get(field["id"], "")
                    if ref.startswith("input."):
                        input_labels[ref[6:]] = field["label"]
            # Legacy manual runs have no collection provenance; do not infer it from equal content.
            origin = run.get("inputOrigin") or {"kind": "snapshot" if run.get("manual") else "collected", "collectedAt": None if run.get("manual") else run["createdAt"]}
            runs.append({**run, "nodeLabels": labels, "outputLabels": output_labels, "inputLabels": input_labels,
                         "inputNode": {"label": version["spec"]["source"].get("label") or "获取输入", **origin}})
        return {**task, "runs": runs, "snapshot": self.version(task_id, task["revision"]) if task["revision"] else None}

    def new_conversation(self, seed_loop_id=None):
        if seed_loop_id:
            self.store.get(seed_loop_id)
        task = {"id": uid("automation"), "title": "新建自动化任务", "revision": 0, "enabled": False,
                "sourceStatus": "stopped", "logs": [], "checkpoint": None, "createdAt": now(),
                "spec": None, "draftSpec": None, "draftRevision": 0, "messages": [], "seedLoopId": seed_loop_id}
        self.put("automation_tasks", task)
        self.changed(task["id"])
        return self.detail(task["id"])

    def message(self, task_id, role, text):
        task = self.task(task_id)
        if role not in {"user", "assistant", "error"} or not isinstance(text, str):
            raise ValueError("对话消息无效。")
        task.setdefault("messages", []).append({"id": uid("message"), "role": role, "text": self.redact(text), "createdAt": now()})
        self.put("automation_tasks", task)
        self.changed(task_id)
        return True

    def update_draft(self, task_id, spec):
        task = self.task(task_id)
        if task["enabled"]:
            raise ValueError("请先暂停自动化任务，再修改流程。")
        normalized, _ = self.check_spec(spec)
        task.update(draftSpec=normalized, draftRevision=task.get("draftRevision", 0) + 1, title=normalized["title"], updatedAt=now())
        self.put("automation_tasks", task)
        self.changed(task_id)
        return self.detail(task_id)

    def require_saved(self, task_id):
        task = self.task(task_id)
        if not task["revision"] or not task.get("spec"):
            raise ValueError("请先保存自动化流程，再测试或启用。")
        if task.get("draftSpec"):
            raise ValueError("当前有未保存的修改，请先保存，避免执行旧版本。")
        return task

    def changed(self, task_id=None):
        self.wake.set()
        self.notify({"event": "automation_state", "taskId": task_id})

    def log(self, task_id, text):
        task = self.task(task_id, deleted=True)
        task["logs"] = (task.get("logs", []) + [{"at": now(), "text": self.redact(str(text))[:3000]}])[-100:]
        self.put("automation_tasks", task)
        self.changed(task_id)

    def secrets(self):
        path = self.store.root / "connections.local.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return {}

    def redact(self, value):
        text = str(value)
        for connection in self.secrets().values():
            for secret in connection.get("headers", {}).values():
                if secret:
                    text = text.replace(str(secret), "[已隐藏]")
                    if str(secret).startswith("Bearer "):
                        text = text.replace(str(secret)[7:], "[已隐藏]")
        return text

    def check_spec(self, value):
        if not isinstance(value, dict):
            raise ValueError("任务配置需要 JSON 对象。")
        value = copy.deepcopy(value)
        title = value.get("title", "").strip()
        if not title or len(title) > 120:
            raise ValueError("请输入不超过 120 字的任务名称。")
        value["title"] = title
        source, nodes, outputs = value.get("source", {}), value.get("nodes", []), value.get("outputs", [])
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 30 or not isinstance(outputs, list) or len(outputs) > 20:
            raise ValueError("请连接 1 到 30 个 Loop，输出目标最多 20 个。")
        snapshots, available, ids = {}, {}, set()
        self.check_script(source, "输入", requires_sample=True)
        if source.get("lifecycle", "once") not in {"once", "service"}:
            raise ValueError("请选择单次采集或持续监听。")
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError("Loop 节点需要有效配置。")
            key = node.get("id", "")
            if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or key in ids or key == "input":
                raise ValueError("节点标识必须唯一且有效。")
            ids.add(key)
            loop = self.store.get(node.get("loopId"))
            revision = node.get("revision") or loop["savedRevision"]
            version = next((v for v in loop["versions"] if v["revision"] == revision), None)
            if not version:
                raise ValueError(f"请先保存 Loop「{loop['title']}」。")
            snapshot = json.loads((Path(version["path"]) / "loop.json").read_text())
            definition = validate(snapshot["definition"], snapshot["config"])
            snapshot.update(title=loop["title"], definition=definition)
            snapshots[key] = snapshot
            node["revision"] = revision
            bindings = node.get("bindings", {})
            if not isinstance(bindings, dict):
                raise ValueError("输入映射需要键值配置。")
            fields = {i["id"]: i for i in definition["inputs"]}
            if any(k not in fields for k in bindings):
                raise ValueError(f"Loop「{loop['title']}」包含不存在的输入映射。")
            for field in fields.values():
                ref = bindings.get(field["id"])
                if ref is None:
                    if field.get("required", True) and field["id"] not in snapshot["config"].get("inputDefaults", {}):
                        raise ValueError(f"请为「{loop['title']} / {field['label']}」配置输入。")
                    continue
                self.check_ref(ref, available)
                if not ref.startswith("input.") and field["kind"] != "file":
                    raise ValueError("Loop 文件产出只能映射到文件输入；请在 Loop 内通过 Python 转换。")
            available.update({f"{key}.{k}": "file" for k in output_keys(definition).values()})
        for sink in outputs:
            if not isinstance(sink, dict) or not isinstance(sink.get("bindings", {}), dict):
                raise ValueError("输出节点需要有效配置与输入映射。")
            key = sink.get("id", "")
            if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or key in ids or key == "input":
                raise ValueError("输出节点标识必须唯一且有效。")
            ids.add(key)
            self.check_script(sink, "输出")
            for ref in sink.get("bindings", {}).values():
                self.check_ref(ref, available)
        policy = value.setdefault("policy", {})
        if not isinstance(policy, dict):
            raise ValueError("运行规则需要 JSON 对象。")
        for key, default, maximum in (("queueLimit", 100, 1000), ("scriptTimeout", 180, 1200), ("sourceRestarts", 3, 5)):
            number = policy.get(key, default)
            if not isinstance(number, int) or not (0 if key == "sourceRestarts" else 1) <= number <= maximum:
                raise ValueError(f"{key} 配置超出范围。")
            policy[key] = number
        return value, snapshots

    @staticmethod
    def check_ref(ref, available):
        if not isinstance(ref, str) or not (ref.startswith("input.") and IDENTIFIER.fullmatch(ref[6:]) or ref in available):
            raise ValueError(f"输入映射引用不存在或顺序不正确：{ref}")

    @staticmethod
    def check_script(spec, label, *, requires_sample=False):
        if not isinstance(spec, dict):
            raise ValueError(f"{label}脚本需要有效配置。")
        source = spec.get("script")
        if not isinstance(source, str) or not source.strip() or len(source) > 100000:
            raise ValueError(f"请提供{label} Python 脚本。")
        try:
            compile(source, f"{label}.py", "exec")
        except SyntaxError as error:
            raise ValueError(f"{label}脚本第 {error.lineno} 行有语法错误。") from error
        import ast
        tree = ast.parse(source)
        functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "run" not in functions:
            raise ValueError(f"{label}脚本需要定义 run(ctx)。")
        if requires_sample and "sample" not in functions:
            raise ValueError("输入脚本需要定义 sample(ctx)，用于立即采集一份真实样本。")
        if not isinstance(spec.get("config", {}), dict):
            raise ValueError("脚本配置需要 JSON 对象。")

    def save(self, spec, task_id=None):
        if task_id:
            task = self.task(task_id)
            if task["enabled"]:
                raise ValueError("请先暂停任务，再更新配置。")
            if task_id in self.sampling:
                raise ValueError("请等待输入测试结束。")
        else:
            task = {"id": uid("automation"), "revision": 0, "enabled": False, "sourceStatus": "stopped", "logs": [], "checkpoint": None, "createdAt": now()}
        spec, loops = self.check_spec(spec)
        task.update(title=spec["title"], revision=task["revision"] + 1, spec=spec, draftSpec=None, updatedAt=now(), sourceStatus="stopped", sourceError="")
        # Version definitions and scripts are immutable, including their saved Loop snapshots.
        self.db.execute("INSERT INTO automation_versions VALUES (?,?,?)", (task["id"], task["revision"], json.dumps({"spec": spec, "loops": loops}, ensure_ascii=False)))
        self.put("automation_tasks", task)
        self.changed(task["id"])
        return self.detail(task["id"])

    async def startup(self):
        if self.started:
            return
        self.started = True
        self.pump_task = asyncio.create_task(self.pump())
        for task in self.list():
            if task["enabled"]:
                self.start_source(task["id"])
        self.wake.set()

    async def pump(self):
        while not self.closing:
            await self.wake.wait()
            self.wake.clear()
            for task in self.list():
                if task["id"] in self.jobs:
                    continue
                queued = next((r for r in self.runs(task["id"]) if r["status"] == "queued" and (task["enabled"] or r.get("manual"))), None)
                if queued:
                    job = asyncio.create_task(self.execute(queued["id"]))
                    self.jobs[task["id"]] = job
                    job.add_done_callback(lambda future, key=task["id"]: self.job_done(key, future))

    def job_done(self, key, future):
        self.jobs.pop(key, None)
        if not future.cancelled() and future.exception():
            self.log(key, "运行异常：" + str(future.exception()))
        self.wake.set()

    def start_source(self, task_id):
        if task_id not in self.sources:
            self.sources[task_id] = asyncio.create_task(self.listen(task_id))

    async def enable(self, task_id, enabled):
        task = self.task(task_id)
        if enabled:
            task = self.require_saved(task_id)
            for node in task["spec"]["nodes"]:
                self.store.get(node["loopId"])
        task.update(enabled=bool(enabled), sourceError="")
        self.put("automation_tasks", task)
        if enabled:
            self.start_source(task_id)
        else:
            source = self.sources.get(task_id)
            if source:
                source.cancel()
                await asyncio.gather(source, return_exceptions=True)
            task = self.task(task_id)
            task["sourceStatus"] = "stopped"
            self.put("automation_tasks", task)
        self.changed(task_id)
        return self.detail(task_id)

    async def listen(self, task_id):
        try:
            task = self.task(task_id)
            version = self.version(task_id, task["revision"])
            spec = version["spec"]
            for attempt in range(spec["policy"]["sourceRestarts"] + 1):
                task = self.task(task_id)
                task.update(sourceStatus="listening", sourceError="")
                self.put("automation_tasks", task)
                self.changed(task_id)
                try:
                    await self.script(task_id, spec["source"], "input", version=version)
                    if spec["source"].get("lifecycle", "once") == "service":
                        raise RuntimeError("持续监听脚本已退出。")
                    task = self.task(task_id)
                    task["sourceStatus"] = "completed"
                    self.put("automation_tasks", task)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    task = self.task(task_id)
                    task.update(sourceStatus="error", sourceError=self.redact(error)[:3000])
                    self.put("automation_tasks", task)
                    self.changed(task_id)
                    if attempt < spec["policy"]["sourceRestarts"]:
                        await asyncio.sleep(min(2 ** attempt, 30))
        finally:
            self.sources.pop(task_id, None)
            self.changed(task_id)

    def capture_inputs(self, inputs, directory):
        if not isinstance(inputs, dict) or len(inputs) > 100:
            raise ValueError("输入必须是按名称组织的对象。")
        captured = {}
        for key, item in inputs.items():
            if not IDENTIFIER.fullmatch(key):
                raise ValueError("输入标识无效。")
            if isinstance(item, str):
                item = {"kind": "text", "value": item}
            if not isinstance(item, dict):
                raise ValueError(f"输入 {key} 需要文本或文件。")
            if item.get("kind") == "file":
                path = Path(item["path"]).expanduser().resolve()
                if not path.is_file() or path.stat().st_size > 10_000_000 or any(p.startswith(".env") or p in {".ssh", ".aws", ".codex", "connections.local.json"} for p in path.parts):
                    raise ValueError("输入需要不超过 10MB 的普通文件，不能使用凭证文件。")
                raw = path.read_bytes()
                if len(raw) > 10_000_000:
                    raise ValueError("输入文件超过 10MB。")
                target = directory / f"{key}{path.suffix}"
                target.write_bytes(raw)
                captured[key] = {"kind": "file", "path": str(target), "name": item.get("name") if isinstance(item.get("name"), str) else path.name, "sha256": hashlib.sha256(raw).hexdigest()}
            elif item.get("kind") == "text" and isinstance(item.get("value"), str) and len(item["value"]) <= 100000:
                captured[key] = {"kind": "text", "value": item["value"]}
            else:
                raise ValueError(f"输入 {key} 需要有效文本或文件。")
        return captured

    @staticmethod
    def fingerprint(inputs):
        canonical = {k: {p: v[p] for p in ("kind", "sha256", "value") if p in v} for k, v in inputs.items()}
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def resolve(self, bindings, values):
        result = {}
        for key, ref in bindings.items():
            if ref not in values:
                raise ValueError(f"缺少输入：{ref}")
            result[key] = values[ref]
        return result

    def check_inputs(self, version, inputs):
        values = {f"input.{k}": v for k, v in inputs.items()}
        for node in version["spec"]["nodes"]:
            definition = version["loops"][node["id"]]["definition"]
            for field in definition["inputs"]:
                ref = node.get("bindings", {}).get(field["id"])
                if ref and ref.startswith("input."):
                    if ref not in values:
                        raise ValueError(f"缺少输入：{ref}")
                    if values[ref]["kind"] != field["kind"]:
                        raise ValueError(f"输入 {ref} 需要{ '文件' if field['kind'] == 'file' else '文本'}。")
        for sink in version["spec"]["outputs"]:
            for ref in sink.get("bindings", {}).values():
                if ref.startswith("input.") and ref not in values:
                    raise ValueError(f"缺少输入：{ref}")

    def accept(self, task_id, event_key, inputs, checkpoint=None, *, sample=False, manual=False, version=None, input_origin=None):
        task = self.task(task_id)
        version = version or self.version(task_id, task["revision"])
        if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 500:
            raise ValueError("每份输入需要不超过 500 字的稳定事件标识。")
        run_id = uid("auto_run")
        directory = self.store.root / "automations" / task_id / "runs" / run_id
        directory.mkdir(parents=True)
        import shutil
        try:
            captured = self.capture_inputs(inputs, directory)
            self.check_inputs(version, captured)
            defaults = {}
            for node in version["spec"]["nodes"]:
                snapshot = version["loops"][node["id"]]
                missing = {field["id"]: {"kind": field["kind"], "path" if field["kind"] == "file" else "value": snapshot["config"]["inputDefaults"][field["id"]]}
                           for field in snapshot["definition"]["inputs"] if field["id"] not in node.get("bindings", {}) and field["id"] in snapshot["config"].get("inputDefaults", {})}
                destination = directory / node["id"]
                destination.mkdir()
                defaults[node["id"]] = self.capture_inputs(missing, destination)
        except Exception:
            shutil.rmtree(directory)
            raise
        fingerprint = self.fingerprint(captured)
        duplicate = self.db.execute("SELECT fingerprint,run FROM automation_events WHERE task=? AND event_key=?", (task_id, event_key)).fetchone() if not sample and not manual else None
        if duplicate:
            shutil.rmtree(directory)
            if duplicate[0] != fingerprint:
                raise ValueError("同一事件标识的输入内容发生变化，请使用新的事件标识。")
            return {"runId": duplicate[1], "duplicate": True}
        if not sample and sum(r["status"] in {"queued", "running", "waiting"} for r in self.runs(task_id)) >= version["spec"]["policy"]["queueLimit"]:
            shutil.rmtree(directory)
            raise ValueError("任务队列已满；输入未确认接收，请稍后重试。")
        revision = next((rev for rev in range(task["revision"], 0, -1) if self.version(task_id, rev) == version), task["revision"])
        run = {"id": run_id, "taskId": task_id, "revision": revision, "eventKey": event_key, "directory": str(directory), "inputs": captured, "defaults": defaults, "status": "sample" if sample else "queued", "nodes": {}, "createdAt": now(), "manual": manual, "error": ""}
        run["inputOrigin"] = copy.deepcopy(input_origin) if input_origin else {"kind": "collected", "collectedAt": run["createdAt"]}
        (directory / "snapshot.json").write_text(json.dumps({"version": version, "inputs": captured, "defaults": defaults}, ensure_ascii=False, indent=2))
        # Input files exist before this transaction. Event, queue and checkpoint commit together.
        with self.db:
            self.db.execute("INSERT INTO automation_runs VALUES (?,?)", (run_id, json.dumps(run, ensure_ascii=False)))
            if not sample and not manual:
                self.db.execute("INSERT INTO automation_events VALUES (?,?,?,?)", (task_id, event_key, fingerprint, run_id))
                task["checkpoint"] = checkpoint
                self.db.execute("UPDATE automation_tasks SET data=? WHERE id=?", (json.dumps(task, ensure_ascii=False), task_id))
        self.changed(task_id)
        return {"runId": run_id, "duplicate": False}

    async def sample(self, task_id):
        task = self.require_saved(task_id)
        if task_id in self.sampling:
            raise ValueError("正在测试输入。")
        version = self.version(task_id, task["revision"])
        job = asyncio.create_task(self.script(task_id, version["spec"]["source"], "sample", version=version))
        self.sampling[task_id] = job
        try:
            await job
        finally:
            self.sampling.pop(task_id, None)
        return self.detail(task_id)

    def replay(self, task_id, run_id):
        self.require_saved(task_id)
        old = self.run(run_id)
        if old["taskId"] != task_id:
            raise ValueError("运行不属于这个任务。")
        return self.accept(task_id, uid("manual"), old["inputs"], manual=True, input_origin=self.reused_input(old))

    @staticmethod
    def reused_input(run):
        origin = copy.deepcopy(run.get("inputOrigin") or {"collectedAt": None if run.get("manual") else run["createdAt"]})
        return {**origin, "kind": "reused", "runId": run["id"]}

    def retry(self, task_id, run_id, confirm=False):
        run = self.run(run_id)
        if run["taskId"] != task_id or run["status"] not in {"failed", "unknown", "interrupted", "stopped"}:
            raise ValueError("只能继续本任务失败或中断的运行。")
        if not confirm:
            raise ValueError("请先核对已发生的外部操作，再确认从失败处继续。")
        if task_id in self.jobs:
            raise ValueError("任务仍在结束中，请稍后再试。")
        run.update(status="queued", error="", manual=True)
        self.put("automation_runs", run)
        self.changed(task_id)
        return run

    def test_output(self, task_id, run_id, node_id):
        old = self.run(run_id)
        task = self.task(task_id)
        if old["taskId"] != task_id:
            raise ValueError("运行不属于这个任务。")
        version = self.version(task_id, task["revision"])
        previous = self.version(task_id, old["revision"])
        if version["spec"]["nodes"] != previous["spec"]["nodes"] or not any(s["id"] == node_id for s in version["spec"]["outputs"]):
            raise ValueError("Loop 链路已变化，请先重新试跑获取对应产出。")
        for node in version["spec"]["nodes"]:
            state = old["nodes"].get(node["id"], {})
            if state.get("status") != "success":
                raise ValueError("请先完成所有 Loop，才能单独试投递。")
            self.verify_values(state["outputs"])
        accepted = self.accept(task_id, uid("delivery_test"), old["inputs"], manual=True, input_origin=self.reused_input(old))
        run = self.run(accepted["runId"])
        run.update(testOutputId=node_id, derivedFrom=run_id, nodes={n["id"]: copy.deepcopy(old["nodes"][n["id"]]) for n in version["spec"]["nodes"]})
        self.put("automation_runs", run)
        self.changed(task_id)
        return accepted

    async def stop(self, task_id, run_id):
        run = self.run(run_id)
        if run["taskId"] != task_id:
            raise ValueError("运行不属于这个任务。")
        if run["status"] in {"running", "waiting"} and task_id in self.jobs:
            self.jobs[task_id].cancel()
            await asyncio.gather(self.jobs[task_id], return_exceptions=True)
        elif run["status"] == "queued":
            run.update(status="stopped", endedAt=now())
            self.put("automation_runs", run)
        self.changed(task_id)
        return self.run(run_id)

    async def execute(self, run_id):
        run = self.run(run_id)
        if run["status"] != "queued" or not (self.task(run["taskId"])["enabled"] or run.get("manual")):
            return
        version = self.version(run["taskId"], run["revision"])
        spec = version["spec"]
        child = None
        current_key = None
        try:
            run.update(status="running", error="")
            self.put("automation_runs", run)
            values = {f"input.{k}": v for k, v in run["inputs"].items()}
            self.verify_values(values)
            for node in spec["nodes"]:
                current_key = node["id"]
                previous = run["nodes"].get(current_key, {})
                if previous.get("status") == "success":
                    self.verify_values(previous["outputs"])
                    values.update({f"{current_key}.{k}": v for k, v in previous["outputs"].items()})
                    continue
                state = {**self.next_attempt(previous), "status": "waiting", "startedAt": now(), "loopId": node["loopId"]}
                run["nodes"][current_key] = state
                run["status"] = "waiting"
                self.put("automation_runs", run)
                self.changed(run["taskId"])
                while True:
                    loop = self.store.get(node["loopId"])
                    running = [self.runner.tasks[r["id"]] for r in loop["runs"] if r["status"] in ACTIVE and r["id"] in self.runner.tasks]
                    if not running:
                        break
                    await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                resolved = {**run.get("defaults", {}).get(current_key, {}), **self.resolve(node.get("bindings", {}), values)}
                self.verify_values(resolved)
                inputs = {k: v["path"] if v["kind"] == "file" else v["value"] for k, v in resolved.items()}
                started = self.runner.start(node["loopId"], inputs, snapshot=version["loops"][current_key])
                child = (node["loopId"], started["id"])
                state.update(status="running", runId=started["id"])
                run["status"] = "running"
                self.put("automation_runs", run)
                self.changed(run["taskId"])
                await asyncio.shield(self.runner.tasks[started["id"]])
                result = self.store.run(*child)
                child = None
                if result["status"] != "success":
                    raise RuntimeError(result.get("reason") or "Loop 执行失败。")
                produced = {}
                for ref, key in output_keys(result["definition"]).items():
                    step_id, output_id = key.split(".")
                    step = next(s for s in reversed(result["stepResults"]) if s["stepId"] == step_id and s["status"] == "success")
                    path = Path(step["outputs"][output_id])
                    produced[key] = {"kind": "file", "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                state.update(status="success", outputs=produced, endedAt=now())
                values.update({f"{current_key}.{k}": v for k, v in produced.items()})
                self.put("automation_runs", run)
                self.changed(run["taskId"])
            for sink in spec["outputs"]:
                if run.get("testOutputId") and sink["id"] != run["testOutputId"]:
                    continue
                current_key = sink["id"]
                previous = run["nodes"].get(current_key, {})
                if previous.get("status") == "success" or previous.get("receipt"):
                    if previous.get("receipt"):
                        previous["status"] = "success"
                        self.put("automation_runs", run)
                    continue
                state = {**self.next_attempt(previous), "status": "running", "deliveryKey": f"{run_id}:{current_key}", "startedAt": now()}
                run["nodes"][current_key] = state
                self.put("automation_runs", run)
                self.changed(run["taskId"])
                try:
                    await self.script(run["taskId"], sink, "output", version=version, run_id=run_id, inputs=self.resolve(sink.get("bindings", {}), values), node_id=current_key)
                except Exception:
                    # A persisted remote receipt remains valid even if the script fails afterwards.
                    if not self.run(run_id)["nodes"][current_key].get("receipt"):
                        raise
                run = self.run(run_id)
                state = run["nodes"][current_key]
                if not state.get("receipt"):
                    raise RuntimeError("输出脚本未提供投递回执，结果待核对。")
                state.update(status="success", endedAt=now())
                self.put("automation_runs", run)
                self.changed(run["taskId"])
            run.update(status="success", endedAt=now())
        except asyncio.CancelledError:
            if child:
                self.runner.stop(*child)
                pending = self.runner.tasks.get(child[1])
                if pending:
                    await asyncio.gather(pending, return_exceptions=True)
            run = self.run(run_id)
            if current_key in run["nodes"]:
                run["nodes"][current_key]["status"] = "unknown" if current_key in {s["id"] for s in spec["outputs"]} else "stopped"
                run["nodes"][current_key].update(error="进程已停止，请核对外部操作。", endedAt=now())
            run.update(status="interrupted" if self.closing else "stopped", error="运行已停止，请核对已发生的外部操作。", endedAt=now())
        except Exception as error:
            run = self.run(run_id)
            uncertain = current_key in {s["id"] for s in spec["outputs"]}
            if current_key in run["nodes"]:
                run["nodes"][current_key]["status"] = "unknown" if uncertain else "failed"
                run["nodes"][current_key].update(error=self.redact(error)[:4000], endedAt=now())
            run.update(status="unknown" if uncertain else "failed", error=self.redact(error)[:4000], endedAt=now())
        finally:
            self.put("automation_runs", run)
            self.changed(run["taskId"])

    @staticmethod
    def next_attempt(previous):
        history = list(previous.get("history", []))
        if previous.get("attempts"):
            history.append({k: v for k, v in previous.items() if k not in {"history", "outputs"}})
        return {"attempts": previous.get("attempts", 0) + 1, "history": history}

    @staticmethod
    def verify_values(values):
        for key, value in values.items():
            if value.get("kind") == "file":
                path = Path(value["path"])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != value.get("sha256"):
                    raise ValueError(f"输入或已完成产出已被修改或删除：{key}。请重新采集输入创建新运行。")

    def http_request(self, message):
        url = message["url"]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("只支持不含账号密码的 HTTP/HTTPS 地址。")
        headers = dict(message.get("headers", {}))
        connection_id = message.get("connection")
        if connection_id:
            connection = self.secrets().get(connection_id)
            if not connection or parsed.netloc not in connection.get("hosts", []):
                raise ValueError("本机连接不存在或目标地址未授权。")
            headers.update(connection.get("headers", {}))
        body = message.get("body")
        if body is not None and not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=body.encode() if body is not None else None, headers=headers, method=message.get("method", "GET"))
        # Never forward authentication across redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            response = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        except Exception as error:
            raise RuntimeError("网络请求未确认完成，请核对目标系统。") from error
        with response:
            raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise ValueError("接口响应超过 10MB。")
            return {"status": response.status, "body": self.redact(raw.decode("utf-8", "replace"))}

    async def interpreter(self, task_id, version):
        identity = hashlib.sha256(json.dumps(version, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
        directory = self.store.root / "automations" / task_id / "environments" / identity
        python = directory / "bin" / "python"
        manifest = directory / "loops-environment.json"
        expected = {"baseInterpreter": sys.executable, "pythonVersion": sys.version, "systemSitePackages": False, "packages": []}
        async with self.environment_locks.setdefault(str(directory), asyncio.Lock()):
            if not python.is_file() or not manifest.is_file() or json.loads(manifest.read_text()) != expected:
                import venv
                preparing = asyncio.create_task(asyncio.to_thread(venv.EnvBuilder(with_pip=False, clear=True, symlinks=True).create, directory))
                try:
                    await asyncio.shield(preparing)
                except asyncio.CancelledError:
                    await preparing
                    raise
                manifest.write_text(json.dumps(expected, ensure_ascii=False, indent=2))
        return str(python)

    async def script(self, task_id, script_spec, role, *, version, run_id=None, inputs=None, node_id=None):
        task = self.task(task_id)
        directory = self.store.root / "automations" / task_id / "processes" / uid("process")
        directory.mkdir(parents=True)
        script = directory / "script.py"
        script.write_text(script_spec["script"])
        normalized = {k: v["path"] if v["kind"] == "file" else v["value"] for k, v in (inputs or {}).items()}
        data = {"config": script_spec.get("config", {}), "inputs": normalized, "inputEvidence": inputs or {}, "checkpoint": task.get("checkpoint"), "workspace": str(directory), "delivery_key": f"{run_id}:{node_id}" if run_id else None}
        context_file = directory / "context.json"
        context_file.write_text(json.dumps(data, ensure_ascii=False))
        if run_id:
            run = self.run(run_id)
            run["nodes"][node_id]["processDirectory"] = str(directory)
            self.put("automation_runs", run)
        # Minimal environment: no inherited provider keys or other application secrets.
        env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT") if k in os.environ}
        env.update(PYTHONPATH=str(Path(__file__).resolve().parent.parent), PYTHONUNBUFFERED="1")
        interpreter = await self.interpreter(task_id, version)
        process = await asyncio.create_subprocess_exec(interpreter, "-m", "loops_runtime.script_worker", str(context_file), str(script), "sample" if role == "sample" else "run", cwd=directory, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=2_000_000)
        sample_count, received = 0, None
        errors = []
        async def stderr():
            while line := await process.stderr.read(4096):
                text = line.decode(errors="replace")[-3000:]
                errors.append(text)
                del errors[:-15]
                self.log(task_id, text)
                logfile = directory / "stderr.log"
                if not logfile.exists() or logfile.stat().st_size < 2_000_000:
                    with logfile.open("a") as handle:
                        handle.write(self.redact(text))
        error_task = asyncio.create_task(stderr())
        async def exchange():
            nonlocal sample_count, received
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                    op = message.get("op")
                    if op == "emit" and role in {"input", "sample"}:
                        received = self.accept(task_id, message.get("eventKey"), message.get("inputs"), message.get("checkpoint"), sample=role == "sample", version=version)
                        sample_count += 1
                        result = received
                    elif op == "log":
                        self.log(task_id, message.get("message", ""))
                        result = True
                    elif op == "receipt" and role == "output":
                        receipt = message.get("receipt", {})
                        if not isinstance(receipt.get("target"), str) or not receipt["target"] or receipt.get("status") not in {"accepted", "delivered"}:
                            raise ValueError("投递回执需要目标与 accepted/delivered 状态。")
                        run = self.run(run_id)
                        if run["nodes"][node_id].get("receipt"):
                            raise ValueError("每个输出节点只能确认一次投递。")
                        run["nodes"][node_id]["receipt"] = json.loads(self.redact(json.dumps(receipt, ensure_ascii=False)))
                        self.put("automation_runs", run)
                        self.changed(task_id)
                        result = True
                    elif op == "request":
                        result = await asyncio.to_thread(self.http_request, message)
                    else:
                        raise ValueError("当前脚本不能执行这个操作。")
                    reply = {"result": result}
                except Exception as error:
                    reply = {"error": self.redact(error)}
                process.stdin.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())
                await process.stdin.drain()
            await process.wait()
            await error_task
            if process.returncode:
                raise RuntimeError(self.redact("".join(errors)[-3000:]) or f"Python 脚本退出码 {process.returncode}")
            if role == "sample" and not sample_count:
                raise ValueError("输入测试未产生样本，请在 sample(ctx) 中调用 ctx.emit。")
        try:
            timeout = None if role == "input" and script_spec.get("lifecycle") == "service" else version["spec"]["policy"]["scriptTimeout"]
            if role == "sample":
                timeout = min(timeout, 20)  # Keep the interactive test within the host RPC deadline.
            await asyncio.wait_for(exchange(), timeout)
        except asyncio.TimeoutError as error:
            raise RuntimeError("脚本执行超时；监听脚本请选择持续监听。") from error
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), 2)
                except (ProcessLookupError, asyncio.TimeoutError):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            if not error_task.done():
                error_task.cancel()
            await asyncio.gather(error_task, return_exceptions=True)
        return received

    async def close(self):
        self.closing = True
        tasks = list(self.sources.values()) + list(self.jobs.values()) + list(self.sampling.values())
        if self.pump_task:
            tasks.append(self.pump_task)
        for job in tasks:
            job.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def handle(self, method, params):
        await self.startup()
        task_id = params.get("taskId")
        if method == "automation_new_conversation":
            return self.new_conversation(params.get("seedLoopId"))
        if method == "automation_message":
            return self.message(task_id, params["role"], params["text"])
        if method == "automation_update_draft":
            return self.update_draft(task_id, params["spec"])
        if method == "automation_session_get":
            self.task(task_id)
            return self.store.session(f"automation:{task_id}")
        if method == "automation_session_set":
            self.task(task_id)
            self.store.session(f"automation:{task_id}", params["messages"])
            return True
        if method == "automation_catalog":
            catalog = []
            for loop in self.store.list():
                versions = []
                for item in loop["versions"]:
                    snapshot = json.loads((Path(item["path"]) / "loop.json").read_text())
                    definition = snapshot["definition"]
                    if definition.get("schemaVersion") != 2 or "finalOutputs" not in definition:
                        continue
                    declared = declared_outputs(definition)
                    versions.append({"revision": item["revision"], "inputs": definition["inputs"], "outputs": [{"key": key, "label": declared[ref]["label"]} for ref, key in output_keys(definition).items()]})
                if versions:
                    catalog.append({"id": loop["id"], "title": loop["title"], "versions": versions})
            return catalog
        if method == "automation_list":
            runs = self.runs()
            return [{**task, "latestRun": next(({"status": r["status"], "createdAt": r["createdAt"]} for r in reversed(runs) if r["taskId"] == task["id"] and r["status"] != "sample"), None)} for task in self.list()]
        if method == "automation_get":
            return self.detail(task_id)
        if method == "automation_save":
            spec = params.get("spec") or (self.task(task_id).get("draftSpec") if task_id else None)
            if not spec:
                raise ValueError("请先通过对话搭建自动化流程。")
            return self.save(spec, task_id)
        if method == "automation_enable":
            return await self.enable(task_id, params["enabled"])
        if method == "automation_sample":
            return await self.sample(task_id)
        if method == "automation_run":
            self.require_saved(task_id)
            if params.get("runId"):
                return self.replay(task_id, params["runId"])
            return self.accept(task_id, uid("manual"), params.get("inputs", {}), manual=True)
        if method == "automation_retry":
            return self.retry(task_id, params["runId"], params.get("confirm", False))
        if method == "automation_test_output":
            return self.test_output(task_id, params["runId"], params["nodeId"])
        if method == "automation_stop":
            return await self.stop(task_id, params["runId"])
        if method == "automation_trash":
            return [t for t in self.list(include_deleted=True) if t.get("deletedAt")]
        if method == "automation_delete":
            if task_id in self.jobs or task_id in self.sampling:
                raise ValueError("请先停止运行和输入测试。")
            await self.enable(task_id, False)
            task = self.task(task_id)
            task["deletedAt"] = now()
            self.put("automation_tasks", task)
            self.changed(task_id)
            return True
        if method == "automation_restore":
            task = self.task(task_id, deleted=True)
            task.pop("deletedAt", None)
            task["enabled"] = False
            self.put("automation_tasks", task)
            self.changed(task_id)
            return task
        if method == "automation_file":
            run = self.run(params["runId"])
            if run["taskId"] != task_id:
                raise ValueError("运行不属于这个任务。")
            paths = [v["path"] for v in run["inputs"].values() if v["kind"] == "file"]
            paths += [v["path"] for n in run["nodes"].values() for v in n.get("outputs", {}).values() if v["kind"] == "file"]
            if params["path"] not in paths:
                raise ValueError("只能查看本次运行的输入和产出。")
            return {"content": Path(params["path"]).read_text(errors="replace")[:150000]}
        raise ValueError("不支持的自动化操作。")
