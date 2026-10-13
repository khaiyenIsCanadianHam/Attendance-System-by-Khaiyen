"""Optional real Chromium integration check; requires Node and Playwright."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

with tempfile.TemporaryDirectory(prefix="attendance-browser-") as directory:
    os.environ["ATTENDANCE_INSTANCE"] = directory
    os.environ["ATTENDANCE_LOCAL_HTTP"] = "1"
    for key in ("DATABASE_URL", "ATTENDANCE_SESSION_KEY", "ATTENDANCE_QR_KEY"):
        os.environ.pop(key, None)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from db import init_db, insert_student, get_all_students, transaction
    from qr_tokens import create_qr_token
    from qr_generator import generate_qr, revoke_qr
    from werkzeug.security import generate_password_hash

    init_db()
    password = secrets.token_urlsafe(24)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO teachers (username,password) VALUES (%s,%s)",
            ("browserteacher", generate_password_hash(password)),
        )
    insert_student(
        '<img src=x onerror="window.pwned=1">',
        "Test",
        "123456789012",
        "browser@example.test",
    )
    student = get_all_students()[0]
    import shutil

    qrpaths = []
    qrtokens = []
    for index in range(20):
        revoke_qr(student["lrn"])
        student = get_all_students()[0]
        qrtokens.append(create_qr_token(student["qr_nonce"]))
        source = generate_qr(student)
        target = Path(directory) / f"qr-{index}.png"
        shutil.copyfile(source, target)
        qrpaths.append(str(target))
    qrpath = qrpaths[0]
    qr = create_qr_token(student["qr_nonce"])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    fixture = Path(directory) / "fixture.json"
    fixture.write_text(
        json.dumps(
            dict(
                password=password,
                qr=qr,
                qrpath=qrpath,
                qrpaths=qrpaths,
                qrtokens=qrtokens,
                base=base,
            )
        )
    )
    fixture.chmod(0o600)
    os.environ["ATTENDANCE_BROWSER_FIXTURE"] = str(fixture)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gunicorn",
            "--workers",
            "2",
            "--bind",
            f"127.0.0.1:{port}",
            "server:app",
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(base, timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server did not become ready")
        result = subprocess.run(["node", str(Path(__file__).with_name("browser.cjs"))])
        if result.returncode:
            raise SystemExit(result.returncode)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
