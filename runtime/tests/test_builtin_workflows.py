import json
import tempfile
import unittest
from pathlib import Path

from loops_runtime.bridge import Service
from loops_runtime.store import Store


class BuiltinWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root, lambda _: None)

    async def asyncTearDown(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.temp.cleanup()

    async def restart(self):
        await self.service.handle('shutdown', {})
        self.service.store.db.close()
        self.service = Service(self.root, lambda _: None)

    def greeting(self):
        return next(loop for loop in self.service.store.list() if loop.get('builtin') == 'greeting')

    async def reply(self, text):
        loop = self.greeting()
        run = await self.service.handle('start', {'loopId': loop['id'], 'inputs': {'text': text}})
        await self.service.runner.tasks[run['id']]
        result = self.service.store.run(loop['id'], run['id'])
        self.assertEqual(result['status'], 'success', result.get('reason'))
        reply = result['stepResults'][-1]['outputs']['reply']
        self.assertEqual(json.loads(Path(result['artifacts'][0]['path']).read_text()), reply)
        return reply

    async def test_ready_to_publish_without_fake_history_or_automatic_sharing(self):
        loop = self.greeting()
        self.assertEqual(loop['title'], '打个招呼')
        self.assertEqual(loop['savedRevision'], loop['revision'])
        self.assertEqual(loop['runs'], [])
        self.assertEqual(loop['messages'], [])
        self.assertFalse(self.service.sharing.config['enabled'])
        self.assertEqual(self.service.sharing.catalog(), [])
        snapshot = self.service.store.version(loop['id'], loop['savedRevision'])
        self.assertEqual(snapshot['definition'], loop['definition'])
        self.assertIsNone(self.service.model_host)
        self.assertEqual(await self.reply('天王盖地虎'), '小鸡炖蘑菇')
        await self.service.handle('sharing_publish', {'loopId': loop['id']})
        self.assertEqual(self.service.sharing.catalog()[0]['name'], '打个招呼')
        self.assertFalse(self.service.sharing.config['enabled'])

    async def test_introduction_uses_current_host_name_and_other_inputs_are_random(self):
        await self.service.handle('sharing_settings', {'name': '测试电脑甲'})
        self.assertEqual(await self.reply('你好，我是小王'), '你好，我是测试电脑甲')
        await self.service.handle('sharing_settings', {'name': '测试电脑乙'})
        self.assertEqual(await self.reply('你好，我是Alice Chen'), '你好，我是测试电脑乙')
        replies = [await self.reply(text) for text in ('', 'hello', '你好，我是', '你好，我是   ', '天王盖地虎！', 'hello')]
        self.assertTrue(all(isinstance(reply, str) and len(reply) == 24 for reply in replies))
        self.assertEqual(len(set(replies)), len(replies))
        self.assertEqual(self.greeting()['savedRevision'], 1)

    async def test_restart_preserves_changes_and_deletion_without_reinstalling(self):
        loop = self.greeting()
        original = self.service.store.version(loop['id'], 1)
        self.service.store.rename(loop['id'], '我的打招呼')
        changed = loop['definition']
        changed['goal'] = '用户修改的目标'
        self.service.store.update_definition(loop['id'], changed)
        with self.assertRaisesRegex(ValueError, '试跑成功'):
            self.service.store.save(loop['id'])
        await self.restart()
        self.assertEqual(len(self.service.store.list()), 1)
        self.assertEqual(self.greeting()['title'], '我的打招呼')
        self.assertEqual(self.greeting()['definition']['goal'], '用户修改的目标')
        self.assertEqual(self.service.store.version(loop['id'], 1), original)
        self.service.store.delete(loop['id'])
        await self.restart()
        self.assertEqual(self.service.store.list(), [])
        self.assertEqual(self.service.store.list(deleted=True)[0]['id'], loop['id'])
        self.service.store.restore(loop['id'])
        self.assertEqual(self.greeting()['title'], '我的打招呼')

    async def test_upgrade_keeps_an_existing_user_flow_with_the_same_title(self):
        root = self.root / 'existing-data'
        old = Store(root)
        try:
            user_loop = old.rename(old.new()['id'], '打个招呼')
        finally:
            old.db.close()
        upgraded = Service(root, lambda _: None)
        try:
            self.assertEqual(upgraded.store.get(user_loop['id']), user_loop)
            self.assertEqual(len(upgraded.store.list()), 2)
            builtin = next(loop for loop in upgraded.store.list() if loop.get('builtin') == 'greeting')
            self.assertNotEqual(builtin['id'], user_loop['id'])
        finally:
            await upgraded.handle('shutdown', {})
            upgraded.store.db.close()


if __name__ == '__main__':
    unittest.main()
