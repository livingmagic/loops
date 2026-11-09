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
from .connectors import AuthorizationRequired

TERMINAL = {"success", "failed", "unknown", "stopped", "interrupted", "authorization_required"}


def document_output_format(output):
    # These extensions declare a markup language. A .txt container is ambiguous
    # and may legitimately contain literal text, Markdown or another language.
    return {'.md': 'markdown', '.markdown': 'markdown', '.html': 'html', '.htm': 'html'}.get(Path(output.get('path', '')).suffix.lower())


def output_keys(definition):
    return {ref: ref.removeprefix("${steps.").removesuffix("}").replace(".outputs.", ".")
            for ref in definition.get("finalOutputs", [])}


class Automations:
    def __init__(self, store, runner, notify, connectors=None):
        self.store, self.runner, self.notify = store, runner, notify
        self.connectors = connectors
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
            task["sourceStatus"] = "authorization_required" if task.get("blockedConnector") else "stopped"
            if task.get('connectionWait', {}).get('status') == 'resuming':
                task['connectionWait']['status'] = 'waiting'
            self.put("automation_tasks", task)
        for run in self.runs():
            self.publish_run_feedback(run)

    def put(self, table, value):
        if table == 'automation_runs' and value['status'] not in TERMINAL:
            value['feedbackSent'] = False
        self.db.execute(f"INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], json.dumps(value, ensure_ascii=False)))
        self.db.commit()
        if table == 'automation_runs':
            self.publish_run_feedback(value)
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
            runs.append({**run, "nodeLabels": labels, "nodeRevisions": {key: value["revision"] for key, value in version["loops"].items()}, "outputLabels": output_labels, "inputLabels": input_labels,
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

    def message(self, task_id, role, text, **extra):
        task = self.task(task_id)
        if role not in {"user", "assistant", "error"} or not isinstance(text, str):
            raise ValueError("对话消息无效。")
        if role == 'user':
            task.pop('connectionWait', None)
        task.setdefault("messages", []).append({"id": uid("message"), "role": role, "text": self.redact(text), "createdAt": now(), **extra})
        self.put("automation_tasks", task)
        self.changed(task_id)
        return True

    def report_run_in_conversation(self, task_id, run_id, *, progress=False):
        run = self.run(run_id)
        if run['taskId'] != task_id or run['status'] == 'sample':
            raise ValueError('只能反馈本任务的实际试跑。')
        if run.get('reportToConversation') and not progress:
            return
        task = self.task(task_id)
        if run['status'] not in TERMINAL:
            state = {'queued': '排队中', 'waiting': '等待 Loop 空闲', 'running': '正在执行', 'stopping': '正在停止'}.get(run['status'], '处理中')
            message = f"本次试跑{state}（版本 {run['revision']}），完成或失败后会在这里自动更新结果。" if progress else f"已启动本次试跑（版本 {run['revision']}），完成或失败后会在这里自动更新结果。"
            task.setdefault('messages', []).append({'id': uid('message'), 'role': 'assistant', 'source': 'execution', 'kind': 'run_progress',
                'runId': run_id, 'text': message, 'createdAt': now()})
            self.put('automation_tasks', task)
        run['reportToConversation'] = True
        self.put('automation_runs', run)
        self.changed(task_id)

    def publish_run_feedback(self, run):
        if not run.get('reportToConversation') or run.get('feedbackSent') or run['status'] not in TERMINAL:
            return
        task = self.task(run['taskId'], deleted=True)
        run['feedbackSent'] = True
        status = {'success': '已完成', 'failed': '失败', 'unknown': '结果待核对', 'stopped': '已停止',
                  'interrupted': '已中断', 'authorization_required': '等待重新授权'}[run['status']]
        text = f"本次试跑{status}（版本 {run['revision']}）。"
        if run.get('error'):
            text += '\n\n' + self.redact(run['error'])[:3000]
        spec = self.version(run['taskId'], run['revision'])['spec']
        titles = {sink['id']: sink.get('title') or '输出' for sink in spec['outputs']}
        for node_id, node in run['nodes'].items():
            receipt = node.get('receipt')
            if not receipt or receipt.get('status') not in {'accepted', 'delivered'}:
                continue
            target = receipt['target']
            target = 'https:' + target if target.startswith('//') else target
            parsed = urllib.parse.urlparse(target)
            label = '已送达' if receipt['status'] == 'delivered' else '目标已接收'
            text += '\n\n' + titles.get(node_id, '输出') + '：' + label + '。'
            if parsed.scheme in {'https', 'http'} and parsed.netloc and not parsed.username and not parsed.password:
                text += f' [查看结果](<{target}>)'
            else:
                text += '\n' + target
        if not task.get('deletedAt'):
            task.setdefault('messages', []).append({'id': uid('message'), 'role': 'assistant', 'source': 'execution', 'kind': 'run_status',
                'runId': run['id'], 'status': run['status'], 'text': text, 'createdAt': now()})
        # Feedback and its delivery marker commit together; restart must not
        # duplicate the message or replay the external operation.
        with self.db:
            self.db.execute('UPDATE automation_runs SET data=? WHERE id=?', (json.dumps(run, ensure_ascii=False), run['id']))
            self.db.execute('UPDATE automation_tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False), task['id']))
        self.changed(task['id'])

    def wait_for_connectors(self, task_id, connector_ids):
        from .connector_presets import presets
        task = self.task(task_id)
        if task['enabled']:
            raise ValueError('请先暂停任务，再继续搭建。')
        if not isinstance(connector_ids, list) or not 1 <= len(connector_ids) <= 30 or any(not isinstance(key, str) for key in connector_ids):
            raise ValueError('请指定需要连接的服务。')
        known = set(self.connectors.items) | {p['id'] for p in presets()}
        if any(key not in known for key in connector_ids):
            raise ValueError('服务尚未添加，请先添加插件后继续搭建。')
        ids = list(dict.fromkeys(connector_ids))
        if self.connectors_ready(ids):
            return {'waiting': False, 'message': '这些服务已连接，可以继续搭建。'}
        task['connectionWait'] = {'id': uid('connector_wait'), 'connectorIds': ids, 'status': 'waiting', 'createdAt': now()}
        self.put('automation_tasks', task)
        self.changed(task_id)
        return {'waiting': True, 'message': '已保留需求，所需服务全部连接后自动继续搭建草稿。'}

    def connectors_ready(self, ids):
        return all(key in self.connectors.items and self.connectors.items[key]['enabled']
                   and self.connectors.items[key]['status'] == 'connected' for key in ids)

    def claim_connection_resume(self, task_id, wait_id):
        task = self.task(task_id, deleted=True)
        wait = task.get('connectionWait')
        if task.get('deletedAt') or not wait or wait['id'] != wait_id or wait['status'] != 'waiting' or task['enabled'] or not self.connectors_ready(wait['connectorIds']):
            return None
        wait['status'] = 'resuming'
        self.put('automation_tasks', task)
        self.changed(task_id)
        return copy.deepcopy(wait)

    def clear_connection_wait(self, task_id, wait_id=None):
        task = self.task(task_id)
        if task.get('connectionWait') and (wait_id is None or task['connectionWait']['id'] == wait_id):
            task.pop('connectionWait')
            self.put('automation_tasks', task)
            self.changed(task_id)
        return True

    def update_draft(self, task_id, spec):
        task = self.task(task_id)
        if task["enabled"]:
            raise ValueError("请先暂停自动化任务，再修改流程。")
        normalized, _ = self.check_spec(spec)
        if task.get('connectionWait', {}).get('status') == 'waiting':
            task.pop('connectionWait')
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
        text = self.connectors.redact(value) if self.connectors else str(value)
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
        grants = value.get("connectors", [])
        if not isinstance(grants, list) or len(grants) > 30:
            raise ValueError("插件授权需要列表，最多 30 项。")
        seen_connectors = set()
        for grant in grants:
            if not isinstance(grant, dict) or not isinstance(grant.get("id"), str) or grant["id"] in seen_connectors or not isinstance(grant.get("operations"), list) or not grant["operations"] or any(not isinstance(o, str) for o in grant["operations"]):
                raise ValueError("每项插件授权需要唯一 id 和允许调用的 operations 列表。")
            if not self.connectors:
                raise ValueError("插件服务不可用。")
            self.connectors.entry(grant["id"])
            seen_connectors.add(grant["id"])
        if self.connectors:
            self.connectors.require_connected([grant['id'] for grant in grants])
        source, nodes, outputs = value.get("source", {}), value.get("nodes", []), value.get("outputs", [])
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 30 or not isinstance(outputs, list) or len(outputs) > 20:
            raise ValueError("请连接 1 到 30 个 Loop，输出目标最多 20 个。")
        snapshots, available, ids, formats = {}, {}, set(), {}
        self.check_script(source, "输入", requires_sample=True, grants=grants)
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
            revision = node.get("revision", loop["savedRevision"])
            if revision is None:
                raise ValueError(f"请先保存 Loop「{loop['title']}」。")
            snapshot = self.store.version(loop["id"], revision)
            definition = snapshot["definition"]
            snapshot.setdefault("title", definition["title"])
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
            declared = declared_outputs(definition)
            formats.update({f'{key}.{name}': document_output_format(declared[ref]) for ref, name in output_keys(definition).items()})
        for sink in outputs:
            if not isinstance(sink, dict) or not isinstance(sink.get("bindings", {}), dict):
                raise ValueError("输出节点需要有效配置与输入映射。")
            key = sink.get("id", "")
            if not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or key in ids or key == "input":
                raise ValueError("输出节点标识必须唯一且有效。")
            ids.add(key)
            self.check_script(sink, "输出", grants=grants)
            verification = sink.get('verification')
            if verification is not None:
                if isinstance(verification, dict):
                    input_name = verification.get('input')
                    original_format = formats.get(sink.get('bindings', {}).get(input_name)) if isinstance(input_name, str) else None
                    if original_format:
                        if verification.get('expectedFormat') is None:
                            verification['expectedFormat'] = original_format
                        elif verification['expectedFormat'] != original_format:
                            raise ValueError(f'已保存的原报告格式为 {original_format}，与 expectedFormat 冲突；请区分原报告格式与目标回读格式。')
                if (not isinstance(verification, dict) or verification.get('kind') != 'document'
                    or verification.get('input') not in sink.get('bindings', {})
                    or verification.get('expectedFormat') not in {'text', 'markdown', 'html'}
                    or verification.get('titlePolicy') not in {'preserve', 'target'}):
                    raise ValueError('文档验收规则需要绑定输入、明确格式和有效标题规则。')
                if verification.get('expectedTitle') is not None and (not isinstance(verification['expectedTitle'], str) or not verification['expectedTitle'].strip()):
                    raise ValueError('文档目标名称需要非空文本。')
                if verification['titlePolicy'] == 'target' and not verification.get('expectedTitle'):
                    raise ValueError('允许替换文档级标题时必须声明用户要求的目标名称。')
            import ast
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {'verify_document', 'deliver_document'} for n in ast.walk(ast.parse(sink['script']))) and verification is None:
                raise ValueError('使用文档校验前，请在输出 verification 中声明用户要求的固定验收规则。')
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

    def set_loop_version(self, task_id, node_id, revision):
        task = self.task(task_id)
        if task["enabled"]:
            raise ValueError("请先暂停自动化任务，再切换 Loop 版本。")
        if type(revision) is not int or revision < 1:
            raise ValueError("请选择有效的流程版本。")
        spec = copy.deepcopy(task.get("draftSpec") or task.get("spec"))
        node = next((n for n in (spec or {}).get("nodes", []) if n["id"] == node_id), None)
        if not node:
            raise ValueError("找不到这个 Loop 节点。")
        if node.get("revision") == revision:
            return self.detail(task_id)
        node["revision"] = revision
        # Validate the whole chain before changing the draft. This includes
        # downstream references to outputs removed in the selected version.
        try:
            return self.update_draft(task_id, spec)
        except ValueError as error:
            raise ValueError(f"无法切换版本：{error} 当前配置已保留；可通过对话调整输入和输出映射。") from error

    @staticmethod
    def check_ref(ref, available):
        if not isinstance(ref, str) or not (ref.startswith("input.") and IDENTIFIER.fullmatch(ref[6:]) or ref in available):
            raise ValueError(f"输入映射引用不存在或顺序不正确：{ref}")

    @staticmethod
    def check_script(spec, label, *, requires_sample=False, grants=()):
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
        context_names = {n.args.args[0].arg for n in tree.body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name in {'run', 'sample'} and n.args.args}
        import symtable
        module_symbols = symtable.symtable(source, f'{label}.py', 'exec')
        for name in context_names & set(module_symbols.get_identifiers()):
            symbol = module_symbols.lookup(name)
            if symbol.is_referenced() and not symbol.is_assigned() and not symbol.is_imported():
                raise ValueError(f'{label}脚本在函数外使用了运行参数 {name}；请将这些配置读取放入 run/sample 函数，执行器不会提供模块全局上下文。')
        allowed = {g['id']: set(g['operations']) for g in grants}
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'deliver_document' and isinstance(call.func.value, ast.Name)
                and call.func.value.id in context_names and (spec.get('verification') or {}).get('expectedTitle')
                and not any(k.arg in {'actual_title', 'title', 'title_path', None} for k in call.keywords)):
                raise ValueError('文档输出要求目标名称：请增加实际名称回读并传 actual_title 或 title_path，不能只声明配置中的目标名称。')
            if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute)
                    or call.func.attr != 'connector' or not isinstance(call.func.value, ast.Name)
                    or call.func.value.id not in context_names):
                continue
            keywords = {k.arg: k.value for k in call.keywords}
            key = call.args[0] if call.args else keywords.get('connector_id')
            operation = call.args[1] if len(call.args) > 1 else keywords.get('operation')
            # Dynamic calls remain subject to the same runtime grant check.
            # Catch definite literal mismatches before saving or any IO.
            if all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in (key, operation)):
                if operation.value not in allowed.get(key.value, set()):
                    raise ValueError(f'{label}脚本第 {call.lineno} 行调用 {key.value} / {operation.value}，但 connectors 未声明此操作；请同步修正脚本和固定授权后保存。')
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
        task.pop('connectionWait', None)
        task.update(title=spec["title"], revision=task["revision"] + 1, spec=spec, draftSpec=None, updatedAt=now(), sourceStatus="stopped", sourceError="")
        # Version definitions and scripts are immutable, including their saved Loop snapshots.
        connector_versions = self.connectors.snapshot([g["id"] for g in spec.get("connectors", [])]) if self.connectors else {}
        connector_contracts = self.connectors.snapshot_contracts(spec.get('connectors', [])) if self.connectors else {}
        self.db.execute("INSERT INTO automation_versions VALUES (?,?,?)", (task["id"], task["revision"], json.dumps({"spec": spec, "loops": loops, "connectorVersions": connector_versions, 'connectorContracts': connector_contracts}, ensure_ascii=False)))
        self.put("automation_tasks", task)
        self.changed(task["id"])
        return self.detail(task["id"])

    async def startup(self):
        if self.started:
            return
        self.started = True
        self.pump_task = asyncio.create_task(self.pump())
        for task in self.list():
            if task["enabled"] and not task.get("blockedConnector"):
                self.start_source(task["id"])
        self.wake.set()

    async def pump(self):
        while not self.closing:
            await self.wake.wait()
            self.wake.clear()
            for task in self.list():
                if task["id"] in self.jobs or task.get("blockedConnector"):
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
            if task.get("blockedConnector"):
                connector = self.connectors.entry(task["blockedConnector"])
                if not connector["enabled"]:
                    raise ValueError("请先重新连接授权失效的插件。")
                task.pop("blockedConnector", None)
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
                except AuthorizationRequired as error:
                    task = self.task(task_id)
                    task.update(sourceStatus="authorization_required", sourceError=self.redact(error))
                    self.put("automation_tasks", task)
                    self.changed(task_id)
                    break
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
                owned = self.store.root / "automations"
                # Generated process/run files are scoped to the application's data
                # root. Its ancestors may be a Codex worktree; they are not credentials.
                parts = path.relative_to(owned).parts if path.is_relative_to(owned) else path.parts
                if not path.is_file() or path.stat().st_size > 10_000_000 or any(p.startswith(".env") or p in {".ssh", ".aws", ".codex", "connections.local.json"} for p in parts):
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
        if run["taskId"] != task_id or run["status"] not in {"failed", "unknown", "interrupted", "stopped", "authorization_required"}:
            raise ValueError("只能继续本任务失败或中断的运行。")
        if not confirm:
            raise ValueError("请先核对已发生的外部操作，再确认从失败处继续。")
        task = self.task(task_id)
        if task.get("blockedConnector"):
            if not self.connectors.entry(task["blockedConnector"])["enabled"]:
                raise ValueError("请先重新连接授权失效的插件。")
            task.pop("blockedConnector", None)
            self.put("automation_tasks", task)
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
                    output = declared_outputs(result['definition'])[ref]
                    path = Path(step['outputFiles'][output_id]) if output_id in step.get('outputFiles', {}) else (
                        Path(result['directory']) / 'results' / step_id / f'{output_id}.json'
                        if output.get('kind') == 'value' else Path(step['outputs'][output_id]))
                    if output.get('kind') == 'value' and output_id not in step.get('outputFiles', {}):
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(step['outputs'][output_id], ensure_ascii=False))
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
        except AuthorizationRequired as error:
            run = self.run(run_id)
            if current_key in run["nodes"]:
                run["nodes"][current_key].update(status="authorization_required", error=self.redact(error), endedAt=now())
            run.update(status="authorization_required", error=self.redact(error), endedAt=now())
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
        data = {"config": script_spec.get("config", {}), "inputs": normalized, "inputEvidence": inputs or {}, "checkpoint": task.get("checkpoint"), "workspace": str(directory), "delivery_key": f"{run_id}:{node_id}" if run_id else None,
                'verification': script_spec.get('verification')}
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
        observed_calls = {}
        errors, connector_auth_error = [], None
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
            nonlocal sample_count, received, connector_auth_error
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
                        if (script_spec.get('verification') or {}).get('kind') == 'document' and not run['nodes'][node_id].get('verification', {}).get('matched'):
                            raise ValueError('固定的文档验收尚未通过，不能确认投递。')
                        if run['nodes'][node_id].get('verification', {}).get('matched') is False:
                            raise ValueError('正文校验未通过，不能确认投递。')
                        if run["nodes"][node_id].get("receipt"):
                            raise ValueError("每个输出节点只能确认一次投递。")
                        run["nodes"][node_id]["receipt"] = json.loads(self.redact(json.dumps(receipt, ensure_ascii=False)))
                        self.put("automation_runs", run)
                        self.changed(task_id)
                        result = True
                    elif op == 'verify_document' and role == 'output':
                        from .document_verification import pointer, verify_document
                        verification = {'ok': False, 'matched': False, 'version': 1, 'inputName': message.get('inputName'), 'evidenceId': message.get('evidenceId')}
                        try:
                            observed = observed_calls.get(message.get('evidenceId'))
                            if not observed or observed['effect'] != 'read':
                                raise ValueError('校验必须使用本输出步骤实际只读调用的返回值。')
                            name = message.get('inputName')
                            contract = script_spec.get('verification')
                            if not contract:
                                raise ValueError('此任务未声明文档验收规则，请重新搭建并保存。')
                            declared = (contract['input'], contract['expectedFormat'], contract.get('expectedTitle'), contract['titlePolicy'])
                            requested = (name, message.get('expectedFormat'), message.get('expectedTitle'), message.get('titlePolicy'))
                            if requested != declared:
                                raise ValueError('脚本校验参数与固定验收规则不一致，不能擅自放宽或更换期望。')
                            original = (inputs or {}).get(name)
                            if not original:
                                raise ValueError('校验原文需要引用本节点已绑定的输入。')
                            self.verify_values({name: original})
                            expected = Path(original['path']).read_text() if original['kind'] == 'file' else original['value']
                            response = observed['result']
                            verification['fields'] = {'content': {'evidenceId': message.get('evidenceId'), 'path': message['contentPath']}}
                            def read_field(path, evidence_id=None):
                                origin = observed_calls.get(evidence_id or message.get('evidenceId'))
                                if not origin or origin['effect'] != 'read':
                                    raise ValueError('验收字段必须来自本输出步骤实际只读调用。')
                                return pointer(origin['result'], path)
                            for field in ('format', 'title'):
                                if message.get(field + 'Path'):
                                    verification['fields'][field] = {
                                        'evidenceId': message.get(field + 'EvidenceId') or message.get('evidenceId'),
                                        'path': message[field + 'Path']}
                            actual = pointer(response, message['contentPath'])
                            options = {'expected_format': message.get('expectedFormat', 'markdown'),
                                       'actual_format': read_field(message['formatPath'], message.get('formatEvidenceId')) if message.get('formatPath') else message.get('actualFormat', 'text'),
                                       'expected_title': message.get('expectedTitle'),
                                       'actual_title': read_field(message['titlePath'], message.get('titleEvidenceId')) if message.get('titlePath') else None,
                                       'title_policy': message.get('titlePolicy', 'preserve'), 'version': message.get('version')}
                            verification.update(verify_document(expected, actual, **options))
                        except Exception as error:
                            verification['error'] = self.redact(str(error))[:500]
                            raise
                        finally:
                            verification['ok'] = verification['matched']
                            run = self.run(run_id)
                            run['nodes'][node_id]['verification'] = verification
                            self.put('automation_runs', run)
                            self.changed(task_id)
                        if not verification['matched']:
                            raise ValueError('回读的正文内容或顺序与交付要求不一致，已停止确认投递。')
                        result = verification
                    elif op == "request":
                        result = await asyncio.to_thread(self.http_request, message)
                    elif op == "connector":
                        key, operation = message.get("connectorId"), message.get("operation")
                        grant = next((g for g in version["spec"].get("connectors", []) if g["id"] == key), None)
                        if not grant or operation not in grant["operations"]:
                            raise ValueError("此版本自动化未授权这项插件操作，请先修改并保存流程。")
                        call_key = message.get("callKey", "")
                        if not isinstance(call_key, str) or len(call_key) > 500:
                            raise ValueError("插件调用标识需要不超过 500 字的字符串。")
                        evidence = {"at": now(), "connectorId": key, "operation": operation, "callKey": call_key,
                                    "arguments": message.get("arguments", {}), "revision": version.get("connectorVersions", {}).get(key)}
                        contract = version.get('connectorContracts', {}).get(key, {}).get(operation, {})
                        operation_effect = contract.get('effect')
                        if not operation_effect:
                            operation_effect = next((o.get('effect') for o in self.connectors.entry(key)['manifest'].get('operations', []) if o['name'] == operation), None)
                        try:
                            result = await self.connectors.call(key, operation, message.get("arguments", {}), role=role,
                                expected_revision=version.get("connectorVersions", {}).get(key),
                                expected_contract=version.get('connectorContracts', {}).get(key, {}).get(operation),
                                delivery_key=f"{run_id}:{node_id}:{operation}:{call_key}" if run_id else "")
                            evidence_id = uid('connector_call')
                            result['_evidenceId'] = evidence_id
                            # Only document outputs consume readback snapshots. Service
                            # inputs may poll indefinitely; their audit is already on disk.
                            if role == 'output' and (script_spec.get('verification') or {}).get('kind') == 'document' and operation_effect == 'read':
                                observed_calls[evidence_id] = {'result': copy.deepcopy(result), 'effect': operation_effect}
                            evidence.update(status="returned", result=result)
                        except AuthorizationRequired as error:
                            evidence.update(status="authorization_required", error=self.redact(error))
                            connector_auth_error = error
                            task = self.task(task_id)
                            task["blockedConnector"] = key
                            self.put("automation_tasks", task)
                            self.changed(task_id)
                            raise
                        except Exception as error:
                            evidence.update(status="unconfirmed", error=self.redact(error))
                            raise
                        finally:
                            with (directory / "connector-calls.jsonl").open("a") as handle:
                                handle.write(self.redact(json.dumps(evidence, ensure_ascii=False)) + "\n")
                    else:
                        raise ValueError("当前脚本不能执行这个操作。")
                    reply = {"result": result}
                except Exception as error:
                    reply = {"error": self.redact(error)}
                process.stdin.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())
                await process.stdin.drain()
                if connector_auth_error:
                    # A script may catch RuntimeError and keep polling or emit fallback
                    # data. Authorization loss stops the process at the host boundary.
                    raise connector_auth_error
            await process.wait()
            await error_task
            if connector_auth_error:
                raise connector_auth_error
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
        if method == 'automation_script_sdk':
            from .script_worker import sdk_contract
            return sdk_contract()
        await self.startup()
        task_id = params.get("taskId")
        if method == "automation_new_conversation":
            return self.new_conversation(params.get("seedLoopId"))
        if method == "automation_message":
            return self.message(task_id, params["role"], params["text"], **params.get("extra", {}))
        if method == 'automation_wait_for_connectors':
            return self.wait_for_connectors(task_id, params['connectorIds'])
        if method == 'automation_connection_waits':
            return [{'taskId': t['id'], **t['connectionWait']} for t in self.list()
                    if t.get('connectionWait', {}).get('status') == 'waiting' and not t['enabled']]
        if method == 'automation_claim_connector_resume':
            return self.claim_connection_resume(task_id, params['waitId'])
        if method == 'automation_clear_connection_wait':
            return self.clear_connection_wait(task_id, params.get('waitId'))
        if method == "automation_update_draft":
            return self.update_draft(task_id, params["spec"])
        if method == "automation_set_loop_version":
            return self.set_loop_version(task_id, params.get("nodeId"), params.get("revision"))
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
                    try:
                        snapshot = self.store.version(loop["id"], item["revision"])
                    except ValueError:
                        continue
                    definition = snapshot["definition"]
                    if definition.get("schemaVersion") != 2 or "finalOutputs" not in definition:
                        continue
                    declared = declared_outputs(definition)
                    versions.append({"revision": item["revision"], "savedAt": item["savedAt"], "title": snapshot.get("title", definition["title"]), "inputs": definition["inputs"], "outputs": [
                        {"key": key, "label": declared[ref]["label"], **({'format': fmt} if (fmt := document_output_format(declared[ref])) else {})}
                        for ref, key in output_keys(definition).items()]})
                if versions:
                    catalog.append({"id": loop["id"], "title": loop["title"], "savedRevision": loop["savedRevision"], "versions": versions})
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
            spec, _ = self.check_spec(spec)
            if self.connectors:
                await self.connectors.prepare_contracts(spec.get('connectors', []))
            return self.save(spec, task_id)
        if method == "automation_enable":
            return await self.enable(task_id, params["enabled"])
        if method == "automation_sample":
            return await self.sample(task_id)
        if method == "automation_run":
            self.require_saved(task_id)
            if params.get("runId"):
                accepted = self.replay(task_id, params["runId"])
            else:
                accepted = self.accept(task_id, uid("manual"), params.get("inputs", {}), manual=True)
            if params.get('reportToConversation'):
                self.report_run_in_conversation(task_id, accepted['runId'])
            return accepted
        if method == 'automation_report_run':
            self.report_run_in_conversation(task_id, params['runId'], progress=True)
            return self.run(params['runId'])
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
            task.pop('connectionWait', None)
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
