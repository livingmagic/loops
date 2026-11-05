import copy
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .agent_clients import validate_agent_id, validate_model, initial_agent_id
from .definition import IDENTIFIER, validate, validate_model_resource, write_bundle


ACTIVE = {"queued", "running", "correcting", "stopping"}


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def workflow_source(definition):
    payload = repr(json.dumps(definition, ensure_ascii=False))
    return ("# Generated from the versioned Loops definition.\nimport json\n\n"
            f"DEFINITION = json.loads({payload})\n\n"
            "async def run(context):\n    return await context.run_workflow(DEFINITION)\n")


class Store:
    def __init__(self, root, workspace_root=None):
        self.root = Path(root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else self.root / "workspaces"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "loops.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS loops (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.commit()
        self.recover()

    def recover(self):
        for loop in self.list():
            changed = False
            for message in loop.get('messages', []):
                process = message.get('process')
                if process and process.get('status') == 'running':
                    process.update(status='interrupted', endedAt=now())
                    process['durationMs'] = max(0, int((datetime.fromisoformat(process['endedAt']) - datetime.fromisoformat(process['startedAt'])).total_seconds() * 1000))
                    for event in process.get('events', []):
                        if event.get('status') == 'running':
                            event['status'] = 'interrupted'
                    changed = True
            for run in loop["runs"]:
                if run["status"] in ACTIVE:
                    run.update(status="interrupted", endedAt=now(), reason="执行服务已重启，本次运行已中断；记录保留，可重新试跑。")
                    for call in run.get('commandCalls', []):
                        if call.get('status') == 'started':
                            call.update(status='interrupted', outcome='unknown', endedAt=now())
                    for call in run.get('modelCalls', []):
                        if call.get('status') == 'running':
                            call.update(status='interrupted', endedAt=now())
                    changed = True
            if changed:
                self.put(loop)

    def list(self, *, deleted=False):
        loops = [json.loads(row[0]) for row in self.db.execute("SELECT data FROM loops ORDER BY rowid DESC")]
        return [loop for loop in loops if bool(loop.get("deletedAt")) == deleted]

    def get(self, loop_id, *, include_deleted=False):
        row = self.db.execute("SELECT data FROM loops WHERE id=?", (loop_id,)).fetchone()
        if not row:
            raise ValueError("找不到这个 Loop。")
        loop = json.loads(row[0])
        if loop.get("deletedAt") and not include_deleted:
            raise ValueError("这个 Loop 已在回收站，请先恢复。")
        return loop

    def delete(self, loop_id):
        loop = self.get(loop_id)
        if any(run["status"] in ACTIVE for run in loop["runs"]):
            raise ValueError("请先停止本次运行，再删除 Loop。")
        loop["deletedAt"] = now()
        return self.put(loop)

    def restore(self, loop_id):
        loop = self.get(loop_id, include_deleted=True)
        loop.pop("deletedAt", None)
        return self.put(loop)

    def rename(self, loop_id, title):
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120 or any(ord(c) < 32 for c in title):
            raise ValueError("名称需要 1 到 120 个字符，不能包含换行。")
        loop = self.get(loop_id)
        loop["title"] = loop["userTitle"] = title.strip()
        return self.put(loop)

    def put(self, loop):
        self.db.execute("INSERT INTO loops VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (loop["id"], json.dumps(loop, ensure_ascii=False)))
        self.db.commit()
        return loop

    def new(self, workspace=None, agent_id=None):
        agent_id = initial_agent_id() if agent_id is None else agent_id
        validate_agent_id(agent_id)
        loop_id = uid("loop")
        workdir = Path(workspace).expanduser().resolve() if workspace else self.workspace_root / loop_id
        if any(Path(item["workdir"]) == workdir for item in self.list() + self.list(deleted=True)):
            raise ValueError("这个工作目录已属于另一个 Loop，请选择其他目录。")
        workdir.mkdir(parents=True, exist_ok=True)
        return self.put({"id": loop_id, "title": "新建 Loop", "workdir": str(workdir), "revision": 0,
                         "savedRevision": None, "definition": None, "versions": [], "messages": [], "runs": [],
                         "config": {"resources": [], "inputDefaults": {}, "defaultAgentId": agent_id}, "createdAt": now()})

    def configure(self, loop_id, values):
        loop = self.get(loop_id)
        if not isinstance(values, dict) or "resources" not in values or not isinstance(values["resources"], list):
            raise ValueError("资料配置需要 resources 列表和 inputDefaults 输入默认值。")
        resources = copy.deepcopy(values["resources"])
        ids = set()
        for resource in resources:
            key = resource.get("id", "")
            if not IDENTIFIER.fullmatch(key) or key in ids or not resource.get("label") or resource.get("kind") not in {"file", "directory", "git", "model"}:
                raise ValueError("资料需要唯一标识、名称和有效类型。")
            ids.add(key)
            if resource['kind'] == 'model':
                validate_model_resource(resource)
                continue
            path = Path(resource.get("path", "")).expanduser().resolve()
            if not path.exists() or (resource["kind"] == "file") != path.is_file() or any(part.startswith(".env") or part in {".ssh", ".codex"} for part in path.parts):
                raise ValueError("请选择可用的普通文件或文件夹。")
            if resource["kind"] == "git" and not (path / ".git").exists():
                raise ValueError("Git 资料需要有效的本地仓库。")
            resource["path"] = str(path)
        if sum(item["kind"] == "git" for item in resources) > 1:
            raise ValueError("当前每个 Loop 支持一个独立 Git 工作区。")
        configuration = {"resources": resources, "inputDefaults": copy.deepcopy(values.get("inputDefaults", {})),
                         "defaultAgentId": validate_agent_id(values.get("defaultAgentId", loop["config"].get("defaultAgentId", "codex")))}
        models = values.get('agentModels', loop['config'].get('agentModels', {}))
        if not isinstance(models, dict):
            raise ValueError('Agent 模型配置必须为对象。')
        if models:
            configuration['agentModels'] = {validate_agent_id(k): validate_model(v) for k, v in models.items()}
        efforts = values.get('agentReasoning', loop['config'].get('agentReasoning', {}))
        if not isinstance(efforts, dict):
            raise ValueError('Agent 思考强度配置必须为对象。')
        if efforts:
            configuration['agentReasoning'] = {validate_agent_id(k): validate_model(v) for k, v in efforts.items()}
        if configuration == loop["config"]:
            return loop
        if loop["definition"]:
            validate(loop["definition"], configuration)
            loop["revision"] += 1
        loop["config"] = configuration
        return self.put(loop)

    def update_definition(self, loop_id, definition, config=None):
        loop = self.get(loop_id)
        if config is not None:
            loop['config'] = copy.deepcopy(config)
        loop["definition"] = validate(definition, loop["config"])
        loop["revision"] += 1
        loop["title"] = loop.get("userTitle") or definition["title"].strip()
        self.write_draft(loop)
        return self.put(loop)

    @staticmethod
    def write_draft(loop):
        draft = Path(loop["workdir"]) / "draft"
        draft.mkdir(exist_ok=True)
        (draft / "loop.json").write_text(json.dumps(loop["definition"], ensure_ascii=False, indent=2))
        (draft / "workflow.py").write_text(workflow_source(loop["definition"]))
        write_bundle(draft, loop["definition"], loop['config'])

    def version(self, loop_id, revision):
        loop = self.get(loop_id)
        if type(revision) is not int or revision < 1:
            raise ValueError("请选择有效的流程版本。")
        version = next((v for v in loop["versions"] if v["revision"] == revision), None)
        if not version:
            raise ValueError(f"Loop「{loop['title']}」没有已保存的版本 {revision}。")
        try:
            snapshot = json.loads((Path(version["path"]) / "loop.json").read_text())
            if snapshot["revision"] != revision:
                raise ValueError("版本号不一致")
            snapshot["definition"] = validate(snapshot["definition"], snapshot["config"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Loop「{loop['title']}」的版本 {revision} 文件缺失或损坏，请恢复版本文件后重试。") from error
        return snapshot

    def restore_version(self, loop_id, revision, *, expected_revision=None, discard_draft=False):
        loop = self.get(loop_id)
        if expected_revision is not None and expected_revision != loop["revision"]:
            raise ValueError("流程已更新，请重新选择版本后再回退。")
        if loop["definition"] and loop["savedRevision"] != loop["revision"] and not discard_draft:
            raise ValueError("当前有未保存的修改，请确认替换后再恢复版本。")
        snapshot = self.version(loop_id, revision)
        loop.update(definition=snapshot["definition"], config=snapshot["config"],
                    revision=loop["revision"] + 1, restoredFrom=revision,
                    title=loop.get("userTitle") or snapshot["definition"]["title"])
        self.write_draft(loop)
        return self.put(loop)

    def save(self, loop_id, *, expected_revision=None):
        loop = self.get(loop_id)
        if expected_revision is not None and expected_revision != loop["revision"]:
            raise ValueError("流程已更新，请确认当前版本后再保存。")
        if not loop["definition"]:
            raise ValueError("请先通过对话搭建流程。")
        if loop["savedRevision"] == loop["revision"]:
            return loop
        if not any(run.get("revision") == loop["revision"] and run.get("mode") == "trial"
                   and run["status"] == "success" for run in loop["runs"]):
            raise ValueError("当前版本试跑成功后才能保存。")
        destination = Path(loop["workdir"]) / "versions" / str(loop["revision"])
        destination.mkdir(parents=True, exist_ok=False)
        snapshot = {"revision": loop["revision"], "title": loop["title"], "definition": loop["definition"], "config": loop["config"], "savedAt": now()}
        if loop.get("restoredFrom"):
            snapshot["restoredFrom"] = loop["restoredFrom"]
        (destination / "loop.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        (destination / "workflow.py").write_text(workflow_source(loop["definition"]))
        write_bundle(destination, loop["definition"], loop['config'])
        loop["savedRevision"] = loop["revision"]
        loop["versions"].append({key: value for key, value in snapshot.items() if key not in {"definition", "config"}} | {"path": str(destination)})
        loop.pop("restoredFrom", None)
        return self.put(loop)

    def message(self, loop_id, role, text, **extra):
        loop = self.get(loop_id)
        loop["messages"].append({"id": uid("message"), "role": role, "text": text, "createdAt": now(), **extra})
        return self.put(loop)

    def update_message(self, loop_id, message_id, process):
        loop = self.get(loop_id)
        message = next(m for m in loop['messages'] if m['id'] == message_id and m['role'] == 'process')
        message['process'] = process
        return self.put(loop)

    def session(self, loop_id, messages=None):
        if messages is not None:
            self.db.execute("INSERT INTO sessions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (loop_id, json.dumps(messages, ensure_ascii=False)))
            self.db.commit()
        row = self.db.execute("SELECT data FROM sessions WHERE id=?", (loop_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def run(self, loop_id, run_id):
        return next(run for run in self.get(loop_id)["runs"] if run["id"] == run_id)

    def update_run(self, loop_id, run_id, **values):
        loop = self.get(loop_id)
        run = next(run for run in loop["runs"] if run["id"] == run_id)
        run.update(values)
        self.put(loop)
        return run

    def event(self, loop_id, run_id, kind, text, **extra):
        run = self.run(loop_id, run_id)
        event = {"at": now(), "type": kind, "text": text, **extra}
        with (Path(run["directory"]) / "events.jsonl").open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.update_run(loop_id, run_id, events=(run.get("events", []) + [event])[-200:])
        return event
