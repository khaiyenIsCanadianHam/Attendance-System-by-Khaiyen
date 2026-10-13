"""Private, persistent configuration. Never reuse keys shipped with an archive."""

import json
import os
import secrets
from pathlib import Path
from cryptography.fernet import Fernet

INSTANCE = Path(
    os.environ.get("ATTENDANCE_INSTANCE", Path(__file__).parent / ".instance")
)
INSTANCE.mkdir(mode=0o700, parents=True, exist_ok=True)
keyfile = INSTANCE / "keys.json"
# Publish fully written keys atomically; concurrent workers share one key set.
import tempfile

if not keyfile.exists():
    with tempfile.NamedTemporaryFile(mode="w", dir=INSTANCE, delete=False) as f:
        temporary = Path(f.name)
        json.dump(
            {"session": secrets.token_hex(32), "qr": Fernet.generate_key().decode()}, f
        )
        f.flush()
        os.fsync(f.fileno())
    try:
        try:
            os.link(temporary, keyfile)
        except FileExistsError:
            pass
    finally:
        temporary.unlink()
with keyfile.open() as f:
    keys = json.load(f)
SESSION_KEY = os.environ.get("ATTENDANCE_SESSION_KEY", keys["session"])
QR_KEY = os.environ.get("ATTENDANCE_QR_KEY", keys["qr"])

if len(SESSION_KEY) < 32:
    raise RuntimeError("ATTENDANCE_SESSION_KEY must contain at least 32 characters")
