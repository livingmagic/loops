#!/bin/sh
set -eu
cd "$(dirname "$0")"
LOOPS_SETUP_PYTHON="${LOOPS_PYTHON_BOOTSTRAP:-python3.13}"
"$LOOPS_SETUP_PYTHON" -m venv .venv
.venv/bin/python -m pip install --index-url "${LOOPS_PIP_INDEX_URL:-https://pypi.org/simple}" -r requirements.lock
