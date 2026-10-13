#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/flask --app server init-db
if [[ -z "${STUDENT_SHEET_URL:-}" ]]; then
    exec .venv/bin/gunicorn --workers "${ATTENDANCE_WORKERS:-2}" --bind "${ATTENDANCE_BIND:-127.0.0.1:5000}" server:app
fi
# One separate polling worker, regardless of the number of web workers.
.venv/bin/python sync_worker.py &
sync_pid=$!
cleanup() {
    kill "$sync_pid" "${web_pid:-}" 2>/dev/null || true
    wait "$sync_pid" "${web_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
.venv/bin/gunicorn --workers "${ATTENDANCE_WORKERS:-2}" --bind "${ATTENDANCE_BIND:-127.0.0.1:5000}" server:app &
web_pid=$!
# A failed polling worker must not leave the app silently running without sync.
while kill -0 "$web_pid" 2>/dev/null && kill -0 "$sync_pid" 2>/dev/null; do
    sleep 1
done
exit 1
