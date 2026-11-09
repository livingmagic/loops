import json
import tempfile
import unittest
from pathlib import Path

from loops_runtime.bridge import Service
from loops_runtime.store import Store
from test_workflow import flow


class VersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root / 'db', lambda _: None, workspace_root=self.root / 'workspaces')
        self.store = self.service.store
        self.loop = self.store.new()
        self.loop_id = self.loop['id']
        self.store.update_definition(self.loop_id, flow())
        self.original = await self.trial_and_save()

    async def run_version(self, **params):
        source = self.root / 'input.txt'
        source.write_text('Mixed Case')
        run = await self.service.handle('start', {'loopId': self.loop_id, 'inputs': {'source': str(source)}, **params})
        await self.service.runner.tasks[run['id']]
        return self.store.run(self.loop_id, run['id'])

    async def trial_and_save(self):
        run = await self.run_version()
        self.assertEqual(run['status'], 'success', run.get('reason'))
        return await self.service.handle('save', {'loopId': self.loop_id})

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.store.db.close()
        self.temp.cleanup()

    async def test_restore_copies_definition_scripts_and_configuration_without_rewriting_history(self):
        original_files = {p: p.read_bytes() for p in Path(self.original['versions'][0]['path']).rglob('*') if p.is_file()}
        changed = flow()
        changed['scripts']['scripts/process.py'] += '\n# second version\n'
        self.store.update_definition(self.loop_id, changed)
        self.store.configure(self.loop_id, {'resources': [], 'inputDefaults': {'source': 'new-input.txt'}, 'defaultAgentId': 'codex'})
        current = await self.trial_and_save()
        self.store.rename(self.loop_id, '我的流程')
        self.store.message(self.loop_id, 'user', '保留对话')
        restored = await self.service.handle('restore_version', {'loopId': self.loop_id, 'revision': 1, 'expectedRevision': current['revision']})
        self.assertEqual(restored['definition'], self.original['definition'])
        self.assertEqual(restored['config'], self.original['config'])
        self.assertEqual(restored['title'], '我的流程')
        self.assertEqual(restored['messages'][0]['text'], '保留对话')
        self.assertEqual(restored['revision'], current['revision'] + 1)
        self.assertEqual(restored['savedRevision'], current['savedRevision'])
        saved = await self.trial_and_save()
        self.assertEqual(saved['versions'][-1]['restoredFrom'], 1)
        self.assertNotIn('restoredFrom', saved)
        self.assertEqual(len(self.store.save(self.loop_id)['versions']), 3)
        for path, contents in original_files.items():
            self.assertEqual(path.read_bytes(), contents)
        reloaded = Store(self.store.root)
        try:
            self.assertEqual(reloaded.get(self.loop_id)['savedRevision'], saved['revision'])
            self.assertEqual(reloaded.version(self.loop_id, saved['revision'])['config'], self.original['config'])
        finally:
            reloaded.db.close()

    async def test_unsaved_and_concurrent_edits_require_explicit_replacement(self):
        changed = flow()
        changed['goal'] = '新的未保存目标'
        current = self.store.update_definition(self.loop_id, changed)
        with self.assertRaisesRegex(ValueError, '未保存'):
            self.store.restore_version(self.loop_id, 1)
        with self.assertRaisesRegex(ValueError, '流程已更新'):
            self.store.restore_version(self.loop_id, 1, expected_revision=1, discard_draft=True)
        self.assertEqual(self.store.get(self.loop_id), current)
        restored = self.store.restore_version(self.loop_id, 1, expected_revision=2, discard_draft=True)
        self.assertEqual(restored['revision'], 3)
        self.assertEqual(restored['savedRevision'], 1)
        self.assertEqual(restored['definition'], flow())

    async def test_invalid_or_missing_version_never_falls_back_or_changes_the_draft(self):
        for revision in (None, 0, -1, True, '1', 100):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                await self.service.handle('version', {'loopId': self.loop_id, 'revision': revision})
        snapshot = Path(self.original['versions'][0]['path']) / 'loop.json'
        snapshot.write_text('{broken')
        with self.assertRaisesRegex(ValueError, '缺失或损坏'):
            self.store.restore_version(self.loop_id, 1)
        self.assertEqual(self.store.get(self.loop_id), self.original)
        snapshot.unlink()
        self.assertNotIn(self.loop_id, [item['id'] for item in await self.service.handle('automation_catalog', {})])

    async def test_old_saved_snapshots_remain_readable_without_new_metadata(self):
        snapshot = Path(self.original['versions'][0]['path']) / 'loop.json'
        value = json.loads(snapshot.read_text())
        value.pop('title', None)
        snapshot.write_text(json.dumps(value))
        catalog = await self.service.handle('automation_catalog', {})
        self.assertEqual(catalog[0]['versions'][0]['title'], flow()['title'])
        self.assertEqual(catalog[0]['savedRevision'], 1)

    async def test_restore_records_a_new_draft_even_when_the_contents_are_identical(self):
        self.store.update_definition(self.loop_id, flow())
        await self.trial_and_save()
        restored = self.store.restore_version(self.loop_id, 1, expected_revision=2)
        self.assertEqual(restored['revision'], 3)
        self.assertEqual(restored['savedRevision'], 2)
        self.assertEqual(restored['restoredFrom'], 1)

    async def test_only_success_on_current_draft_allows_save_and_old_versions_run_independently(self):
        changed = flow()
        script = changed['scripts']['scripts/process.py']
        changed['scripts']['scripts/process.py'] = 'raise RuntimeError("trial failed")'
        self.store.update_definition(self.loop_id, changed)
        with self.assertRaisesRegex(ValueError, '试跑成功'):
            self.store.save(self.loop_id)
        failed = await self.run_version()
        self.assertEqual(failed['status'], 'failed')
        historic = await self.run_version(revision=1)
        self.assertEqual((historic['revision'], historic['mode'], historic['status']), (1, 'run', 'success'))
        self.assertEqual(Path(historic['artifacts'][0]['path']).read_text(), 'MIXED CASE')
        self.assertEqual(self.store.get(self.loop_id)['definition'], changed)
        with self.assertRaisesRegex(ValueError, '试跑成功'):
            self.store.save(self.loop_id)
        changed['scripts']['scripts/process.py'] = script.replace('.upper()', '.lower()')
        updated = self.store.update_definition(self.loop_id, changed)
        with self.assertRaisesRegex(ValueError, '流程已更新'):
            await self.run_version(expectedRevision=2)
        saved = await self.trial_and_save()
        self.assertEqual(saved['savedRevision'], updated['revision'])
