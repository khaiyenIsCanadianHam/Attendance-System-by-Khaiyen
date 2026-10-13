#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/gunicorn --workers "${ATTENDANCE_WORKERS:-2}" --bind "${ATTENDANCE_BIND:-127.0.0.1:5000}" server:app
