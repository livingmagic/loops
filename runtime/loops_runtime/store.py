import copy
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .definition import IDENTIFIER, validate, write_bundle


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
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else Path.home() / "Loops"
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
            for run in loop["runs"]:
                if run["status"] in ACTIVE:
                    run.update(status="interrupted", endedAt=now(), reason="执行服务已重启，本次运行已中断；记录保留，可重新试跑。")
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

    def new(self, workspace=None):
        loop_id = uid("loop")
        workdir = Path(workspace).expanduser().resolve() if workspace else self.workspace_root / loop_id
        if any(Path(item["workdir"]) == workdir for item in self.list() + self.list(deleted=True)):
            raise ValueError("这个工作目录已属于另一个 Loop，请选择其他目录。")
        workdir.mkdir(parents=True, exist_ok=True)
        return self.put({"id": loop_id, "title": "新建 Loop", "workdir": str(workdir), "revision": 0,
                         "savedRevision": None, "definition": None, "versions": [], "messages": [], "runs": [],
                         "config": {"resources": [], "inputDefaults": {}}, "createdAt": now()})

    def configure(self, loop_id, values):
        loop = self.get(loop_id)
        if not isinstance(values, dict) or "resources" not in values or not isinstance(values["resources"], list):
            raise ValueError("资料配置需要 resources 列表和 inputDefaults 输入默认值。")
        resources = copy.deepcopy(values["resources"])
        ids = set()
        for resource in resources:
            key = resource.get("id", "")
            path = Path(resource.get("path", "")).expanduser().resolve()
            if not IDENTIFIER.fullmatch(key) or key in ids or not resource.get("label") or resource.get("kind") not in {"file", "directory", "git"}:
                raise ValueError("资料需要唯一标识、名称和有效类型。")
            if not path.exists() or (resource["kind"] == "file") != path.is_file() or any(part.startswith(".env") or part in {".ssh", ".codex"} for part in path.parts):
                raise ValueError("请选择可用的普通文件或文件夹。")
            if resource["kind"] == "git" and not (path / ".git").exists():
                raise ValueError("Git 资料需要有效的本地仓库。")
            resource["path"] = str(path)
            ids.add(key)
        if sum(item["kind"] == "git" for item in resources) > 1:
            raise ValueError("当前每个 Loop 支持一个独立 Git 工作区。")
        configuration = {"resources": resources, "inputDefaults": copy.deepcopy(values.get("inputDefaults", {}))}
        if configuration == loop["config"]:
            return loop
        if loop["definition"]:
            validate(loop["definition"], configuration)
            loop["revision"] += 1
        loop["config"] = configuration
        return self.put(loop)

    def update_definition(self, loop_id, definition):
        loop = self.get(loop_id)
        loop["definition"] = validate(definition, loop["config"])
        loop["revision"] += 1
        loop["title"] = loop.get("userTitle") or definition["title"].strip()
        draft = Path(loop["workdir"]) / "draft"
        draft.mkdir(exist_ok=True)
        (draft / "loop.json").write_text(json.dumps(loop["definition"], ensure_ascii=False, indent=2))
        (draft / "workflow.py").write_text(workflow_source(loop["definition"]))
        write_bundle(draft, loop["definition"])
        return self.put(loop)

    def save(self, loop_id):
        loop = self.get(loop_id)
        if not loop["definition"]:
            raise ValueError("请先通过对话搭建流程。")
        if loop["savedRevision"] == loop["revision"]:
            return loop
        destination = Path(loop["workdir"]) / "versions" / str(loop["revision"])
        destination.mkdir(parents=True, exist_ok=False)
        snapshot = {"revision": loop["revision"], "definition": loop["definition"], "config": loop["config"], "savedAt": now()}
        (destination / "loop.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        (destination / "workflow.py").write_text(workflow_source(loop["definition"]))
        write_bundle(destination, loop["definition"])
        loop["savedRevision"] = loop["revision"]
        loop["versions"].append({"revision": loop["revision"], "path": str(destination), "savedAt": snapshot["savedAt"]})
        return self.put(loop)

    def message(self, loop_id, role, text, **extra):
        loop = self.get(loop_id)
        loop["messages"].append({"id": uid("message"), "role": role, "text": text, "createdAt": now(), **extra})
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
