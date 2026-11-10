import unittest

from loops_runtime.script_worker import Context, evidence_field, observed


class ScriptWorkerTests(unittest.TestCase):
    def test_projected_fields_keep_their_original_read_paths(self):
        original = observed({'payload': {'items': [{'body/part': '完整正文', 'media~type': 'html'}]}}, 'read-1')
        metadata = observed({'payload': {'name': '实际名称'}}, 'read-2')
        view = {'text': str(original['payload']['items'][0]['body/part']),
                'title': metadata['payload']['name'], 'format': 'html'}
        self.assertEqual(evidence_field(view, '/text')[:2], ('read-1', '/payload/items/0/body~1part'))
        self.assertEqual(evidence_field(view, '/title')[:2], ('read-2', '/payload/name'))
        self.assertEqual(evidence_field(original['payload'], '/items/0/media~0type')[:2],
                         ('read-1', '/payload/items/0/media~0type'))
        self.assertIs(metadata['payload']['name'].strip(), metadata['payload']['name'])
        spaced = observed('  原始文字\n', 'read-space', '/body')
        self.assertEqual(evidence_field(spaced.strip(), '')[:2], ('read-space', '/body'))
        # A text transformation is not a proven projection, even if it looks similar.
        view['text'] = view['text'].replace('正文', '资料')
        self.assertEqual(evidence_field(view, '/text')[:2], (None, None))



class ScriptProtocolTests(unittest.TestCase):
    def context(self, responses, **data):
        import io
        channel = io.StringIO()
        return Context(data, channel, io.StringIO(responses)), channel

    def test_rpc_frames_preserve_parameters_and_surface_host_errors(self):
        import json
        ctx, channel = self.context('{"result": "accepted"}\n{"error": "denied"}\n')
        self.assertEqual(ctx.emit('key', {'name': '甲'}, {'offset': 3}), 'accepted')
        self.assertEqual(json.loads(channel.getvalue()), {'op': 'emit', 'eventKey': 'key', 'inputs': {'name': '甲'}, 'checkpoint': {'offset': 3}})
        with self.assertRaisesRegex(RuntimeError, 'denied'):
            ctx.log('message')
        with self.assertRaisesRegex(RuntimeError, '断开'):
            ctx.log('message')

    def test_request_receipt_and_file_inputs_keep_their_typed_contracts(self):
        import json
        import tempfile
        from pathlib import Path
        ctx, channel = self.context('{"result": true}\n' * 2)
        self.assertTrue(ctx.request('https://example.invalid', connection='test', method='POST', body={'a': 1}))
        self.assertTrue(ctx.receipt('local', status='accepted', count=2))
        frames = [json.loads(line) for line in channel.getvalue().splitlines()]
        self.assertEqual(frames[0]['headers'], {})
        self.assertEqual(frames[1]['receipt'], {'target': 'local', 'status': 'accepted', 'count': 2})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.txt'; path.write_text('你好')
            ctx.inputEvidence = {'file': {'kind': 'file'}, 'text': {'kind': 'text'}}
            ctx.inputs = {'file': str(path), 'text': '文本'}
            self.assertEqual(ctx.read_text('file'), '你好')
            self.assertEqual(ctx.read_bytes('file'), '你好'.encode())
            self.assertEqual(ctx.read_text('text'), '文本')
            self.assertEqual(ctx.read_bytes('text'), '文本'.encode())
            self.assertEqual(ctx.file(path), {'kind': 'file', 'path': str(path.resolve())})
            self.assertEqual(ctx.text(3), {'kind': 'text', 'value': '3'})

    def test_delivery_tracks_actual_fields_and_confirms_only_after_verification(self):
        import json
        replies = '{"result":{"_evidenceId":"read-1","body":"正文","name":"标题","format":"html"}}\n{"result":{"matched":true}}\n{"result":true}\n'
        ctx, channel = self.context(replies, verification={'expectedTitle': '标题', 'titlePolicy': 'target'})
        readback = ctx.connector('docs', 'fetch', {'id': 'test'})
        self.assertTrue(ctx.deliver_document('report', target='https://example.invalid/doc', content=readback['body'], actual_title=readback['name'], actual_format=readback['format']))
        frames = [json.loads(line) for line in channel.getvalue().splitlines()]
        self.assertEqual([f['op'] for f in frames], ['connector', 'verify_document', 'receipt'])
        self.assertEqual(frames[1]['contentPath'], '/body')
        self.assertEqual(frames[1]['titlePath'], '/name')
        self.assertEqual(frames[1]['formatPath'], '/format')
        self.assertEqual(frames[1]['evidenceId'], 'read-1')
        failed, channel = self.context('{"error":"content mismatch"}\n')
        with self.assertRaisesRegex(RuntimeError, 'content mismatch'):
            failed.deliver_document('report', readback, target='local', content_path='/body', title_path='/name', format_path='/format')
        self.assertEqual(len(channel.getvalue().splitlines()), 1, 'Failed verification must never create a receipt.')

    def test_forged_and_untraceable_fields_are_rejected_before_rpc(self):
        ctx, channel = self.context('', verification={'expectedTitle': 'title'})
        readback = observed({'body': 'content', 'title': 'title'}, 'origin')
        with self.assertRaisesRegex(ValueError, '目标名称'):
            ctx.deliver_document('report', target='local', content=readback['body'])
        with self.assertRaisesRegex(ValueError, '同一次'):
            ctx.deliver_document('report', readback, target='local', content=observed('other', 'another'))
        with self.assertRaisesRegex(ValueError, '不一致'):
            ctx.deliver_document('report', readback, target='local', content=readback['body'], content_path='/title')
        with self.assertRaisesRegex(ValueError, '需要提供'):
            ctx.deliver_document('report', target='local', content_path='/body')
        with self.assertRaisesRegex(ValueError, '实际正文'):
            ctx.deliver_document('report', {}, target='local')
        with self.assertRaisesRegex(ValueError, 'JSON Pointer'):
            evidence_field(readback, 'body')
        with self.assertRaisesRegex(ValueError, '来源'):
            ctx.verify_document('report', {'_evidenceId': 'forged', 'body': readback['body']}, content_path='/body')
        self.assertEqual(channel.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
