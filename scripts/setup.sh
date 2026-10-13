#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
PIP_CACHE_DIR="${TMPDIR:-/tmp}/attendance-pip-cache" .venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip check
.venv/bin/flask --app server init-db
