"""Fail closed on incomplete coverage, baseline regressions or uncovered PR lines."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
POLICY = '.github/coverage-policy.json'


def percentage(covered, total):
    return 100 * covered / total if total else 100


def changed_lines(diff):
    result, filename = {}, None
    for line in diff.splitlines():
        if line.startswith('+++ b/'):
            filename = line[6:]
        elif line.startswith('+++ '):
            filename = None
        elif filename and (match := re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)):
            start, count = int(match[1]), int(match[2] or 1)
            result.setdefault(filename, set()).update(range(start, start + count))
    return result


def python_data(root):
    report = json.loads((root / 'coverage/python/coverage.json').read_text())
    records = {}
    for name, data in report['files'].items():
        # Coverage's [paths] maps child cwd paths to the repository-relative source.
        filename = Path(name).as_posix()
        if filename.startswith('runtime/loops_runtime/'):
            hits = set(data['executed_lines'])
            all_lines = hits | set(data['missing_lines'])
            summary = data['summary']
            records[filename] = (hits, all_lines, summary['covered_branches'], summary['num_branches'])
    return records


def javascript_data(root):
    records, name, hits, lines, branches = {}, None, set(), set(), []
    for line in (root / 'coverage/javascript/lcov.info').read_text().splitlines():
        if line.startswith('SF:'):
            raw = Path(line[3:])
            raw = raw if raw.is_absolute() else root / 'desktop' / raw
            name = raw.resolve().relative_to(root.resolve()).as_posix()
            hits, lines, branches = set(), set(), []
        elif line.startswith('DA:'):
            number, count, *_ = line[3:].split(',')
            lines.add(int(number))
            if int(count) > 0:
                hits.add(int(number))
        elif line.startswith('BRDA:'):
            count = line.rsplit(',', 1)[1]
            branches.append(count != '-' and int(count) > 0)
        elif line == 'end_of_record':
            if name in records:
                raise ValueError(f'Duplicate coverage record: {name}')
            records[name] = (hits, lines, sum(branches), len(branches))
    return records


def inventory(root, language):
    if language == 'python':
        return {p.relative_to(root).as_posix() for p in (root / 'runtime/loops_runtime').rglob('*.py')}
    return {p.relative_to(root).as_posix() for directory, extensions in [('host', ['*.mjs']), ('src', ['*.js', '*.mjs'])]
            for extension in extensions for p in (root / 'desktop' / directory).rglob(extension)}


def evaluate(records, expected, thresholds, changes, minimum):
    errors = []
    missing = expected - records.keys()
    if not expected or missing:
        errors.append('Missing source coverage: ' + ', '.join(sorted(missing)))
    selected = [records[name] for name in expected & records.keys()]
    covered = sum(len(r[0]) for r in selected)
    total = sum(len(r[1]) for r in selected)
    branch_covered, branch_total = sum(r[2] for r in selected), sum(r[3] for r in selected)
    metrics = {'lines': percentage(covered, total), 'branches': percentage(branch_covered, branch_total)}
    for metric, actual in metrics.items():
        if actual < thresholds[metric]:
            errors.append(f'{metric}: {actual:.2f}% < required {thresholds[metric]}%')
    changed_total, changed_hit, uncovered = 0, 0, []
    for name in sorted(expected & records.keys()):
        hits, lines, *_ = records[name]
        executable = changes.get(name, set()) & lines
        changed_total += len(executable)
        changed_hit += len(executable & hits)
        if executable - hits:
            uncovered.append(f'{name}: ' + ','.join(map(str, sorted(executable - hits))))
    delta = percentage(changed_hit, changed_total)
    if delta < minimum:
        errors.append(f'Changed executable lines: {changed_hit}/{changed_total} ({delta:.2f}%) < {minimum}%\n' + '\n'.join(uncovered))
    return {**metrics, 'changedLines': delta, 'changedExecutableLines': changed_total}, errors


def git(*args):
    return subprocess.check_output(['git', '-c', 'core.quotePath=false', *args], cwd=ROOT, text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True, help='PR base commit; local use: origin/main')
    args = parser.parse_args()
    base = git('rev-parse', '--verify', args.base + '^{commit}').strip()
    policy = json.loads((ROOT / POLICY).read_text())
    values = [policy[language][metric] for language in ['python', 'javascript'] for metric in ['lines', 'branches']] + [policy['changedLines']]
    if any(type(value) not in (int, float) or not 0 <= value <= 100 for value in values):
        raise ValueError('Coverage thresholds must be numbers between 0 and 100.')
    tracked = git('ls-tree', '--name-only', base, '--', POLICY).strip()
    if tracked:
        previous = json.loads(git('show', f'{base}:{POLICY}'))
        if any(policy[k][m] < previous[k][m] for k in ['python', 'javascript'] for m in ['lines', 'branches']) or policy['changedLines'] < previous['changedLines']:
            raise ValueError('This PR lowers an existing coverage threshold.')
        for filename in ['.coveragerc', 'desktop/.c8rc.json']:
            if (ROOT / filename).read_text() != git('show', f'{base}:{filename}'):
                raise ValueError(f'Coverage scope changes require a dedicated policy review: {filename}')
    changes = changed_lines(git('diff', '--no-ext-diff', '--no-renames', '--unified=0', base, '--'))
    failures = []
    for language, load in [('python', python_data), ('javascript', javascript_data)]:
        metrics, errors = evaluate(load(ROOT), inventory(ROOT, language), policy[language], changes, policy['changedLines'])
        print(language + ': ' + json.dumps(metrics), flush=True)
        failures.extend(f'{language}: {error}' for error in errors)
    if failures:
        raise SystemExit('\n'.join(failures))
    print('Coverage gate passed.')


if __name__ == '__main__':
    main()
