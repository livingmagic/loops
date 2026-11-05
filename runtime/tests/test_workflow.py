import asyncio
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from loops_runtime.bridge import Service


def flow(source=None):
    script = source or "import json,os\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text(Path(c['inputs']['source']).read_text().upper())\n"
    return {"schemaVersion": 2, "title": "文本处理", "goal": "转为大写", "completion": "处理后的文件存在", "finalOutputs": ["${steps.convert.outputs.report}"], "inputs": [{"id": "source", "label": "原文", "kind": "file", "required": True}],
            "scripts": {"scripts/process.py": script}, "steps": [{"id": "convert", "label": "转换", "instructions": "按脚本处理输入", "inputs": {"source": "${inputs.source}"},
            "execution": {"tool": "python", "script": "scripts/process.py"}, "outputs": [{"id": "report", "label": "转换结果", "path": "result.txt"}], "checks": [], "onFailure": {"action": "stop", "maxAttempts": 0}}]}


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(self.temp.name, lambda event: None, workspace_root=Path(self.temp.name) / "workspaces")
        self.loop = self.service.store.new()
        self.source = Path(self.temp.name) / "original.txt"
        self.source.write_text("first input")
        await self.service.handle("configure", {"loopId": self.loop["id"], "config": {"resources": [], "inputDefaults": {"source": str(self.source)}}})

    async def asyncTearDown(self):
        await self.service.handle("shutdown", {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def test_conversation_process_survives_updates_and_recovers_after_restart(self):
        loop_id = self.loop['id']
        process = {'status': 'running', 'startedAt': '2026-11-03T00:00:00+00:00', 'events': [{'type': 'tool', 'status': 'running', 'text': '读取'}]}
        await self.service.handle('message', {'loopId': loop_id, 'role': 'process', 'text': '', 'extra': {'id': 'progress-test', 'process': process}})
        process['current'] = '正在重试 1/5'
        await self.service.handle('message_update', {'loopId': loop_id, 'messageId': 'progress-test', 'process': process})
        self.service.store.recover()
        saved = self.service.store.get(loop_id)['messages'][0]['process']
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual(saved['current'], '正在重试 1/5')
        self.assertEqual(saved['events'][0]['status'], 'interrupted')
        self.assertGreater(saved['durationMs'], 0)

    async def test_command_rejects_structured_json_argument_before_launch(self):
        definition = flow("import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text('[1,2]')")
        definition['steps'][0]['outputs'][0]['kind'] = 'json'
        definition['steps'].append({'id': 'check', 'label': '检查数据', 'instructions': '校验数据', 'inputs': {},
            'execution': {'tool': 'command', 'command': ['python', '-c', 'pass', '${steps.convert.outputs.report}']},
            'outputs': [], 'checks': [{'kind': 'command', 'command': ['python', '-c', 'pass']}], 'onFailure': {'action': 'stop'}})
        await self.build(definition)
        result = await self.execute()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('JSON 数据请通过步骤 inputs 和 LOOPS_CONTEXT 读取', result['reason'])

    async def build(self, definition):
        return await self.service.handle("definition", {"loopId": self.loop["id"], "definition": definition})

    async def execute(self, values=None):
        run = await self.service.handle("start", {"loopId": self.loop["id"], "inputs": values or {}})
        await self.service.runner.tasks[run["id"]]
        return self.service.store.run(self.loop["id"], run["id"])

    async def test_variable_inputs_versioned_scripts_and_runs_remain_independent(self):
        built = await self.build(flow())
        first = await self.execute()
        saved = await self.service.handle("save", {"loopId": self.loop["id"]})
        self.assertEqual(first["status"], "success", first.get("reason"))
        self.assertEqual(Path(first["stepResults"][0]["outputs"]["report"]).read_text(), "FIRST INPUT")
        self.source.write_text("second input")
        changed = flow()
        changed["scripts"]["scripts/process.py"] = changed["scripts"]["scripts/process.py"].replace(".upper()", ".lower()")
        await self.build(changed)
        second = await self.execute()
        self.assertEqual(Path(second["stepResults"][0]["outputs"]["report"]).read_text(), "second input")
        self.assertEqual(self.service.store.run(self.loop["id"], first["id"]), first)
        self.assertEqual(Path(first["inputs"]["source"]).read_text(), "first input")
        self.assertIn(".upper()", (Path(saved["versions"][0]["path"]) / "scripts/process.py").read_text())
        self.assertEqual(saved["savedRevision"], built["revision"])

    async def test_missing_output_cannot_be_claimed_successful(self):
        await self.build(flow("print('all done')"))
        run = await self.execute()
        self.assertEqual(run["status"], "failed")
        self.assertIn("缺少产出", run["reason"])

    async def test_scripts_commands_and_checks_do_not_inherit_host_secrets(self):
        import sys
        secrets = {"DEEPSEEK_API_KEY": "test-only", "AWS_SECRET_ACCESS_KEY": "test-only",
                   "GH_TOKEN": "test-only", "UNRELATED_PRIVATE_VALUE": "test-only",
                   "PYTHONPATH": "/untrusted/module/path"}
        assertion = ("import os,json; from pathlib import Path; "
                     f"assert not set({list(secrets)!r}).intersection(os.environ); "
                     "assert os.environ.get('PATH'); "
                     "c=json.loads(os.environ['LOOPS_CONTEXT']); "
                     "Path(c['outputs']['report']).write_text('isolated')")
        for tool in ("python", "command"):
            with self.subTest(tool=tool):
                definition = flow(assertion)
                step = definition["steps"][0]
                if tool == "command":
                    step["execution"] = {"tool": "command", "command": [sys.executable, "-c", assertion]}
                step["checks"] = [{"kind": "command", "command": [sys.executable, "-c", assertion]}]
                await self.build(definition)
                with patch.dict(os.environ, secrets):
                    run = await self.execute()
                self.assertEqual(run["status"], "success", run.get("reason"))

    async def test_final_outputs_exclude_intermediates_and_preserve_earlier_deliverables(self):
        definition = flow()
        definition["steps"][0]["outputs"].append({"id": "cache", "label": "临时数据", "path": "final-looking.tmp"})
        definition["scripts"]["scripts/process.py"] += "Path(c['outputs']['cache']).write_text('intermediate')\n"
        definition["scripts"]["scripts/report.py"] = "import json,os\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['file']).write_text(Path(c['inputs']['data']).read_text()+' report')\n"
        definition["steps"].append({"id": "report", "label": "生成报告", "instructions": "从汇总生成报告", "inputs": {"data": "${steps.convert.outputs.report}"},
                                   "execution": {"tool": "python", "script": "scripts/report.py"}, "outputs": [{"id": "file", "label": "最终报告", "path": "report.txt"}], "checks": [], "onFailure": {"action": "stop"}})
        definition["finalOutputs"] = ["${steps.report.outputs.file}", "${steps.convert.outputs.report}"]
        built = await self.build(definition)
        run = await self.execute()
        saved = await self.service.handle("save", {"loopId": self.loop["id"]})
        self.assertEqual(run["status"], "success", run.get("reason"))
        self.assertEqual([item["title"] for item in run["artifacts"]], ["最终报告", "转换结果"])
        self.assertEqual([Path(item["path"]).name for item in run["intermediateArtifacts"]], ["final-looking.tmp", "execution.json"])
        self.assertEqual(Path(run["artifacts"][0]["path"]).read_text(), "FIRST INPUT report")
        for artifact in run["artifacts"] + run["intermediateArtifacts"]:
            result = await self.service.handle("artifact", {"loopId": self.loop["id"], "runId": run["id"], "path": artifact["path"]})
            self.assertTrue(result["content"])
        snapshot = json.loads((Path(saved["versions"][0]["path"]) / "loop.json").read_text())
        self.assertEqual(snapshot["definition"]["finalOutputs"], definition["finalOutputs"])
        definition["finalOutputs"] = ["${steps.report.outputs.file}"]
        await self.build(definition)
        self.assertEqual(self.service.store.run(self.loop["id"], run["id"]), run)
        self.assertEqual(saved["savedRevision"], built["revision"])

    async def test_final_output_contract_rejects_missing_unknown_and_duplicate_references(self):
        for selected in (None, "report.txt", ["${inputs.source}"], ["${steps.missing.outputs.report}"],
                         ["${steps.convert.outputs.missing}"], ["result.txt"], [{}],
                         ["${steps.convert.outputs.report}", "${steps.convert.outputs.report}"]):
            with self.subTest(selected=selected):
                definition = flow()
                definition["finalOutputs"] = selected
                with self.assertRaisesRegex(ValueError, "最终输出"):
                    await self.build(definition)
        definition = flow()
        definition.pop("finalOutputs")
        with self.assertRaisesRegex(ValueError, "最终输出"):
            await self.build(definition)
        self.assertIsNone(self.service.store.get(self.loop["id"])["definition"])

    async def test_no_file_delivery_keeps_process_files_in_execution_details(self):
        definition = flow()
        definition["finalOutputs"] = []
        await self.build(definition)
        run = await self.execute()
        self.assertEqual(run["status"], "success", run.get("reason"))
        self.assertEqual(run["artifacts"], [])
        self.assertEqual({Path(item["path"]).name for item in run["intermediateArtifacts"]}, {"result.txt", "execution.json"})

    async def test_incomplete_persisted_definitions_are_rejected_before_creating_a_run(self):
        await self.build(flow())
        old = self.service.store.get(self.loop["id"])
        old["definition"].pop("finalOutputs")
        self.service.store.put(old)
        with self.assertRaisesRegex(ValueError, "最终输出"):
            await self.execute()
        self.assertEqual(self.service.store.get(self.loop["id"])["runs"], [])
        self.assertNotIn("finalOutputs", self.service.store.get(self.loop["id"])["definition"])

    async def test_selected_workspace_and_default_parent(self):
        from unittest.mock import patch
        from loops_runtime.store import Store
        selected = Path(self.temp.name) / "chosen"
        selected.mkdir()
        marker = selected / "existing.txt"
        marker.write_text("keep")
        loop = await self.service.handle("new", {"workspace": str(selected)})
        self.assertEqual(loop["workdir"], str(selected.resolve()))
        self.assertEqual(marker.read_text(), "keep")
        with self.assertRaisesRegex(ValueError, "另一个 Loop"):
            await self.service.handle("new", {"workspace": str(selected)})
        with patch("pathlib.Path.home", return_value=Path(self.temp.name) / "home"):
            store = Store(Path(self.temp.name) / "isolated-db")
            try:
                self.assertFalse(store.workspace_root.exists())
                default = store.new()
                self.assertEqual(Path(default["workdir"]).parent, Path(self.temp.name) / "home" / "Loops")
            finally:
                store.db.close()

    async def test_user_rename_preserves_versions_and_survives_ai_definition_updates(self):
        await self.build(flow())
        trial = await self.execute()
        self.assertEqual(trial["status"], "success", trial.get("reason"))
        saved = await self.service.handle("save", {"loopId": self.loop["id"]})
        renamed = await self.service.handle("rename", {"loopId": self.loop["id"], "title": "  我的流程  "})
        self.assertEqual(renamed["title"], "我的流程")
        self.assertEqual(renamed["revision"], saved["revision"])
        self.assertEqual(renamed["versions"], saved["versions"])
        changed = flow()
        changed["title"] = "AI 新名称"
        updated = await self.build(changed)
        self.assertEqual(updated["title"], "我的流程")
        for invalid in (None, "", " ", "a\nb", "a" * 121):
            with self.assertRaises(ValueError):
                await self.service.handle("rename", {"loopId": self.loop["id"], "title": invalid})

    async def test_delete_and_restore_preserve_saved_flow_runs_files_and_session_across_restart(self):
        await self.build(flow())
        trial = await self.execute()
        self.assertEqual(trial["status"], "success", trial.get("reason"))
        saved = await self.service.handle("save", {"loopId": self.loop["id"]})
        await self.service.handle("message", {"loopId": self.loop["id"], "role": "user", "text": "保留这条搭建对话"})
        self.service.store.session(self.loop["id"], [{"role": "user", "content": "build"}])
        run = await self.execute()
        self.assertEqual(run["status"], "success", run.get("reason"))
        before = self.service.store.get(self.loop["id"])
        files = {str(path): path.read_bytes() for path in Path(before["workdir"]).rglob("*") if path.is_file()}
        await self.service.handle("delete", {"loopId": self.loop["id"]})
        self.assertEqual(await self.service.handle("list", {}), [])
        self.assertEqual((await self.service.handle("trash", {}))[0]["id"], self.loop["id"])
        for method, params in [("start", {}), ("save", {}), ("definition", {"definition": flow()})]:
            with self.assertRaisesRegex(ValueError, "回收站"):
                await self.service.handle(method, {"loopId": self.loop["id"], **params})
        restarted = Service(self.temp.name, lambda event: None, workspace_root=Path(self.temp.name) / "workspaces")
        try:
            self.assertEqual(await restarted.handle("list", {}), [])
            restored = await restarted.handle("restore", {"loopId": self.loop["id"]})
            self.assertEqual(restored, before)
            self.assertEqual(restored["savedRevision"], saved["savedRevision"])
            self.assertEqual(restarted.store.session(self.loop["id"]), [{"role": "user", "content": "build"}])
            self.assertEqual(files, {str(path): path.read_bytes() for path in Path(before["workdir"]).rglob("*") if path.is_file()})
        finally:
            await restarted.handle("shutdown", {})
            restarted.store.db.close()
        self.assertEqual(self.source.read_text(), "first input")
        again = await self.execute()
        self.assertEqual(again["status"], "success", again.get("reason"))
        self.assertEqual(again["number"], run["number"] + 1)

    async def test_active_runs_cannot_be_deleted_and_stopping_is_not_a_shortcut(self):
        before = self.service.store.get(self.loop["id"])
        for status in ("queued", "running", "correcting", "stopping"):
            loop = copy.deepcopy(before)
            loop["runs"] = [{"id": "active", "status": status}]
            self.service.store.put(loop)
            with self.assertRaisesRegex(ValueError, "先停止"):
                await self.service.handle("delete", {"loopId": loop["id"]})
            self.assertEqual(self.service.store.get(loop["id"])["runs"][0]["status"], status)
            self.assertEqual(await self.service.handle("trash", {}), [])
        self.service.store.put(before)

    async def test_failed_checks_retry_automatically_and_keep_failed_evidence(self):
        definition = flow("import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\np=Path('counter'); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\nPath(c['outputs']['report']).write_text(str(n))")
        step = definition["steps"][0]
        step["checks"] = [{"kind": "command", "label": "计数检查", "command": ["python", "-c", "from pathlib import Path; assert int(Path('result.txt').read_text()) > 1"], "expectedExitCode": 0}]
        step["onFailure"] = {"action": "retry", "maxAttempts": 1}
        await self.build(definition)
        run = await self.execute()
        self.assertEqual(run["status"], "success", run.get("reason"))
        self.assertEqual(run["corrections"], 1)
        self.assertEqual([item["status"] for item in run["stepResults"]], ["failed", "success"])
        self.assertTrue(any(not check["passed"] for check in run["checks"]))

    async def test_return_to_prior_step_recomputes_referenced_outputs(self):
        definition = flow("import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\np=Path('counter'); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\nPath(c['outputs']['report']).write_text(str(n))")
        definition["scripts"]["scripts/consume.py"] = "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nv=Path(c['inputs']['previous']).read_text(); assert int(v)>1\nPath(c['outputs']['report']).write_text(v)"
        second = copy.deepcopy(definition["steps"][0])
        second.update(id="consume", label="检查上步结果", inputs={"previous": "${steps.convert.outputs.report}"}, execution={"tool": "python", "script": "scripts/consume.py"}, outputs=[{"id": "report", "label": "最终结果", "path": "final.txt"}], onFailure={"action": "goto", "target": "convert", "maxAttempts": 1})
        definition["steps"].append(second)
        await self.build(definition)
        run = await self.execute()
        self.assertEqual(run["status"], "success", run.get("reason"))
        self.assertEqual(Path(run["stepResults"][-1]["outputs"]["report"]).read_text(), "2")
        self.assertEqual([item["stepId"] for item in run["stepResults"]], ["convert", "consume", "convert", "consume"])

    async def test_invalid_references_scripts_and_missing_inputs_rejected_before_execution(self):
        definition = flow()
        definition["steps"][0]["inputs"]["source"] = "${steps.later.outputs.file}"
        with self.assertRaises(ValueError):
            await self.build(definition)
        definition = flow()
        definition["scripts"] = {"../escape.py": "print(1)"}
        with self.assertRaises(ValueError):
            await self.build(definition)
        await self.build(flow())
        with self.assertRaises(ValueError):
            await self.service.handle("start", {"loopId": self.loop["id"], "inputs": {"source": "/missing/input"}})
        self.assertEqual(self.service.store.get(self.loop["id"])["runs"], [])

    async def test_codex_receives_resolved_inputs_and_readonly_reply_is_saved(self):
        definition = flow()
        step = definition["steps"][0]
        step["instructions"] = "分析 ${inputs.source}，上次失败：${failure}"
        step["execution"] = {"tool": "codex", "mode": "read-only", "session": "analysis", "responseOutput": "report"}
        prompts = []
        class ReadonlySession:
            def __init__(self, *args): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def ask(self, prompt, readonly=False):
                prompts.append((prompt, readonly))
                return "test-double output"
        self.service.runner.session_factory = ReadonlySession
        await self.build(definition)
        run = await self.execute()
        self.assertEqual(run["status"], "success", run.get("reason"))
        self.assertNotIn("${inputs.source}", prompts[0][0])
        self.assertIn(run["inputs"]["source"], prompts[0][0])
        self.assertTrue(prompts[0][1])
        self.assertEqual(Path(run["stepResults"][0]["outputs"]["report"]).read_text(), "test-double output")

    async def test_retry_keeps_an_upstream_file_shared_with_the_failed_step(self):
        definition = flow()
        second = copy.deepcopy(definition["steps"][0])
        second.update(id="append", label="追加", inputs={"source": "${steps.convert.outputs.report}"}, execution={"tool": "python", "script": "scripts/append.py"}, onFailure={"action": "retry", "maxAttempts": 1})
        definition["scripts"]["scripts/append.py"] = "import os,json\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\np=Path('counter'); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\nassert n>1\ntext=Path(c['inputs']['source']).read_text(); Path(c['outputs']['report']).write_text(text+' appended')"
        definition["steps"].append(second)
        await self.build(definition)
        run = await self.execute()
        self.assertEqual(run["status"], "success", run.get("reason"))
        self.assertEqual(Path(run["stepResults"][-1]["outputs"]["report"]).read_text(), "FIRST INPUT appended")

    async def test_step_commands_use_dynamic_ids_and_stop_is_terminal(self):
        await self.build(flow("import time; time.sleep(60)"))
        run = await self.service.handle("start", {"loopId": self.loop["id"]})
        task = self.service.runner.tasks[run["id"]]
        await asyncio.sleep(.1)
        self.service.runner.stop(self.loop["id"], run["id"])
        await task
        self.assertEqual(self.service.store.run(self.loop["id"], run["id"])["status"], "stopped")
        commands = await self.service.handle("commands", {"loopId": self.loop["id"], "runId": run["id"]})
        self.assertIn("convert", commands["steps"])


if __name__ == "__main__":
    unittest.main()
