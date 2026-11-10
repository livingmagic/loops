"""Run both existing suites once, including Python workers started by Node."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'coverage'


def main():
    # The checkout must use runtime/.venv's Python, where test tooling is installed.
    if Path(sys.prefix).resolve() != (ROOT / 'runtime/.venv').resolve():
        raise SystemExit('Run with runtime/.venv/bin/python scripts/run-coverage.py')
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / 'python').mkdir(parents=True)
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'runtime'), 'COVERAGE_PROCESS_START': str(ROOT / '.coveragerc'),
           'COVERAGE_RCFILE': str(ROOT / '.coveragerc'),
           'COVERAGE_FILE': str(OUTPUT / 'python/.coverage'),
           'LOOPS_COVERAGE_SOURCE': str(ROOT / 'runtime/loops_runtime')}
    hook = Path(sysconfig.get_path('purelib')) / 'loops_ci_coverage.pth'
    if hook.exists():
        raise SystemExit(f'Remove stale coverage hook before retrying: {hook}')
    hook.write_text('import coverage; coverage.process_startup()\n')
    try:
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'runtime/tests', '-v'], cwd=ROOT, env=env, check=True)
        subprocess.run(['npm', 'run', 'test:coverage'], cwd=ROOT / 'desktop', env=env, check=True)
    finally:
        hook.unlink()
    subprocess.run([sys.executable, '-m', 'coverage', 'combine', str(OUTPUT / 'python')], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, '-m', 'coverage', 'json', '-o', str(OUTPUT / 'python/coverage.json')], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, '-m', 'coverage', 'report'], cwd=ROOT, env=env, check=True)


if __name__ == '__main__':
    main()
