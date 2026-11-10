"""Real local worker processes and SDK frames; no provider keys or model calls."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from loops_runtime import loops_ai


class ModelProtocolTests(unittest.TestCase):
    def test_sdk_validates_resources_and_translates_host_results_and_errors(self):
        with patch.object(loops_ai, '_channel', None):
            for model, reasoning in [('', 'low'), ('id', ''), (None, 'low')]:
                with self.assertRaises(ValueError): loops_ai.complete('hello', model=model, reasoning=reasoning)
            with self.assertRaisesRegex(RuntimeError, '节点'): loops_ai.complete('hello', model='id', reasoning='low')
        for response, expected in [('{"result":{"text":"answer"}}\n', 'answer'), ('{"error":"blocked"}\n', RuntimeError), ('', RuntimeError)]:
            channel = io.StringIO()
            with patch.object(loops_ai, '_channel', channel), patch.object(loops_ai, '_replies', io.StringIO(response)):
                if expected is RuntimeError:
                    with self.assertRaises(RuntimeError): loops_ai.complete('hello', model='id', reasoning='low')
                else: self.assertEqual(loops_ai.complete('hello', model='id', reasoning='low'), expected)
            frame = json.loads(channel.getvalue())
            self.assertEqual(frame['op'], 'complete'); self.assertEqual(frame['model'], 'id')

    def worker(self, script, responses='{}\n', values=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker_input.py'; path.write_text(script)
            return subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'loops_runtime/model_worker.py'), str(path)],
                                  input=responses, capture_output=True, text=True,
                                  env={**os.environ, 'LOOPS_VALUE_OUTPUT': '1' if values else '0'}, timeout=15)

    def test_real_worker_separates_logs_model_rpc_and_data_output(self):
        result = self.worker("import loops_ai\nprint('ordinary log')\nresult = {'text': loops_ai.complete('hello', model='local', reasoning='low')}\n", '{"result":{"text":"test answer"}}\n{}\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([f['op'] for f in frames], ['complete', 'output'])
        self.assertEqual(frames[1]['value'], {'text': 'test answer'})
        self.assertIn('ordinary log', result.stderr)
        self.assertNotIn('ordinary log', result.stdout)

    def test_real_worker_rejects_missing_nonfinite_oversize_or_refused_results(self):
        for script, responses, message in [('print("no result")', '{}\n', '赋给 result'),
                                           ('result = float("nan")', '{}\n', 'Out of range'),
                                           ('result = "x" * 900001', '{}\n', '900 KB'),
                                           ('result = 1', '{"error":"output rejected"}\n', 'output rejected')]:
            with self.subTest(message=message):
                result = self.worker(script, responses)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        self.assertEqual(self.worker('print("only logs")', '', values=False).returncode, 0)
