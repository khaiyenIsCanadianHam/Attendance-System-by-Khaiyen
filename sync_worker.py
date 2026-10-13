"""One roster polling worker per instance; only teacher-approved Forms rows enroll."""

import json
import hashlib
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import INSTANCE
from db import get_all_students
from fetch_students import run_sync
from qr_generator import generate_qr
from qr_tokens import create_qr_token

logger = logging.getLogger("hanami.sync")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def source():
    return hashlib.sha256(os.environ.get("STUDENT_SHEET_URL", "").encode()).hexdigest()


def write_status(status):
    status = dict(status, source=source())
    with tempfile.NamedTemporaryFile(mode="w", dir=INSTANCE, delete=False) as file:
        temporary = Path(file.name)
        json.dump(status, file)
    try:
        os.replace(temporary, INSTANCE / "sync-status.json")
    finally:
        temporary.unlink(missing_ok=True)


def read_status():
    if not os.environ.get("STUDENT_SHEET_URL"):
        return {"state": "disabled", "last_success": None}
    try:
        with (INSTANCE / "sync-status.json").open() as file:
            status = json.load(file)
        if status.get("source") != source():
            return {"state": "waiting", "last_success": None}
        if status.get("last_attempt"):
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(status["last_attempt"])
            ).total_seconds()
            if age > max(120, int(os.environ.get("STUDENT_SYNC_INTERVAL", "30")) * 3):
                status["state"] = "stale"
        return status
    except (FileNotFoundError, ValueError):
        return {"state": "waiting", "last_success": None}


def sync_once():
    previous = read_status()
    attempted = timestamp()
    try:
        imported = run_sync(google_forms=True, collect_pending=True)
        generated = 0
        for student in get_all_students():
            revision = INSTANCE / "qrcodes" / f"{student['lrn']}.revision"
            expected = hashlib.sha256(
                create_qr_token(student["qr_nonce"]).encode()
            ).hexdigest()
            if (
                not (INSTANCE / "qrcodes" / f"{student['lrn']}.png").exists()
                or not revision.exists()
                or revision.read_text() != expected
            ):
                generate_qr(student)
                generated += 1
        status = {
            "state": "ok",
            "last_attempt": attempted,
            "last_success": timestamp(),
            "imported": imported,
            "generated": generated,
        }
        write_status(status)
        return status
    except Exception:
        # Avoid saving/logging private Sheet URLs, roster data, or credentials.
        write_status(
            {
                "state": "error",
                "last_attempt": attempted,
                "last_success": previous.get("last_success"),
                "error": "Roster sync failed. Check Sheet access, headers, and approved rows.",
            }
        )
        raise


@contextmanager
def worker_lock():
    with (INSTANCE / "sync.lock").open("a+b") as file:
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            file.write(b"0")
            file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def run_worker(stop=None):
    if not os.environ.get("STUDENT_SHEET_URL"):
        raise ValueError("Set STUDENT_SHEET_URL before starting automatic sync")
    interval = int(os.environ.get("STUDENT_SYNC_INTERVAL", "30"))
    if not 10 <= interval <= 3600:
        raise ValueError("STUDENT_SYNC_INTERVAL must be between 10 and 3600 seconds")
    stop = stop or threading.Event()
    with worker_lock():
        while not stop.is_set():
            try:
                sync_once()
            except Exception:
                logger.warning(
                    "Roster sync failed; check Sheet sharing, headers, and approvals. Retrying next cycle."
                )
            stop.wait(interval)


if __name__ == "__main__":
    import signal

    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    run_worker(stop)
