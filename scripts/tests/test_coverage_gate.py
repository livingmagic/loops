import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('coverage_gate', Path(__file__).parents[1] / 'check-coverage.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CoverageGateTests(unittest.TestCase):
    def test_changed_lines_include_added_files_and_ignore_deleted_hunks(self):
        diff = '+++ b/runtime/loops_runtime/new.py\n@@ -0,0 +1,2 @@\n+x\n+y\n@@ -4 +5,0 @@\n-x\n'
        self.assertEqual(gate.changed_lines(diff), {'runtime/loops_runtime/new.py': {1, 2}})

    def test_uncovered_new_logic_is_blocked_even_when_total_baseline_passes(self):
        records = {'a.py': ({1, 2, 3, 4}, {1, 2, 3, 4, 5}, 2, 2)}
        _, errors = gate.evaluate(records, {'a.py'}, {'lines': 80, 'branches': 100}, {'a.py': {5}}, 80)
        self.assertTrue(any('Changed executable lines' in e for e in errors))
        self.assertTrue(any('a.py: 5' in e for e in errors))

    def test_deleting_tests_reduces_total_coverage_and_blocks_merge(self):
        records = {'a.py': ({1}, {1, 2}, 0, 2)}
        _, errors = gate.evaluate(records, {'a.py'}, {'lines': 80, 'branches': 70}, {}, 80)
        self.assertEqual(len(errors), 2)

    def test_completely_unloaded_file_cannot_disappear_from_denominator(self):
        _, errors = gate.evaluate({}, {'missing.py'}, {'lines': 0, 'branches': 0}, {}, 80)
        self.assertTrue(any('missing.py' in e for e in errors))

    def test_new_code_is_evaluated_independently_of_covered_old_code(self):
        records = {'old.py': (set(range(1, 100)), set(range(1, 100)), 1, 1), 'new.py': (set(), {1}, 0, 0)}
        _, errors = gate.evaluate(records, set(records), {'lines': 90, 'branches': 90}, {'new.py': {1}}, 80)
        self.assertTrue(any('Changed executable lines: 0/1' in e for e in errors))

    def test_non_executable_changes_do_not_require_fabricated_tests(self):
        records = {'a.py': ({2}, {2}, 0, 0)}
        metrics, errors = gate.evaluate(records, {'a.py'}, {'lines': 100, 'branches': 100}, {'a.py': {1}}, 80)
        self.assertFalse(errors)
        self.assertEqual(metrics['changedExecutableLines'], 0)


if __name__ == '__main__':
    unittest.main()
