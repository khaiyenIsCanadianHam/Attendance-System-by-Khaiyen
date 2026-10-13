"""Optional PostgreSQL integration checks using an isolated local Unix-socket server."""

import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

# Test-only pgserver can be installed in a separate tools directory.
if os.environ.get("ATTENDANCE_PGTOOLS"):
    sys.path.insert(0, os.environ["ATTENDANCE_PGTOOLS"])
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pgserver

_temp = tempfile.TemporaryDirectory(prefix="attendance-postgres-")
os.environ["ATTENDANCE_INSTANCE"] = str(Path(_temp.name) / "instance")
from db import (
    init_db,
    insert_student,
    get_all_students,
    transaction,
    migrate_legacy_database,
)
from server import app
from qr_tokens import create_qr_token
from werkzeug.security import generate_password_hash


class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pgdata = Path(_temp.name) / "pgdata"
        pgdata.mkdir()
        # Keep tool runtime files in our writable temporary directory.
        pgserver.PostgresServer.runtime_path = Path(_temp.name) / "runtime"
        pgserver.PostgresServer.lock_path = Path(_temp.name) / "runtime.lock"
        import fasteners

        pgserver.PostgresServer._lock = fasteners.InterProcessLock(
            pgserver.PostgresServer.lock_path
        )
        cls.pg = pgserver.get_server(pgdata, cleanup_mode="stop")
        os.environ["DATABASE_URL"] = cls.pg.get_uri()
        cls.password = secrets.token_urlsafe(24)
        cls.password_hash = generate_password_hash(cls.password)
        app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.pg.cleanup()
        os.environ.pop("DATABASE_URL", None)

    def setUp(self):
        with transaction() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
        self.client = app.test_client()

    def seed(self):
        with transaction() as cur:
            cur.execute(
                "INSERT INTO teachers (username,password) VALUES (%s,%s)",
                ("teacher", self.password_hash),
            )
        insert_student("Student", "A", "123456789012", "test@example.test")

    def login(self):
        self.client.get("/", base_url="https://localhost")
        with self.client.session_transaction() as s:
            csrf = s["csrf"]
        response = self.client.post(
            "/login",
            data={"username": "teacher", "password": self.password, "csrf_token": csrf},
            base_url="https://localhost",
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as s:
            return s["csrf"]

    def post(self, path, data, csrf):
        return self.client.post(
            path,
            json=data,
            headers={"X-CSRF-Token": csrf},
            base_url="https://localhost",
        )

    def legacy_schema(self):
        with transaction() as cur:
            cur.execute(
                "CREATE TABLE teachers (id SERIAL PRIMARY KEY,username TEXT UNIQUE,password TEXT)"
            )
            cur.execute(
                "CREATE TABLE students (id SERIAL PRIMARY KEY,full_name TEXT,section TEXT,lrn TEXT UNIQUE,email TEXT)"
            )
            cur.execute(
                "CREATE TABLE attendance (id SERIAL PRIMARY KEY,full_name TEXT NOT NULL,section TEXT NOT NULL,lrn TEXT NOT NULL,time_in TIMESTAMP NOT NULL,time_out TIMESTAMP,CONSTRAINT unique_active_student UNIQUE(lrn,time_out))"
            )
            cur.execute(
                "INSERT INTO teachers (username,password) VALUES (%s,%s)",
                ("teacher", self.password_hash),
            )
            cur.execute(
                "INSERT INTO students (full_name,section,lrn,email) VALUES (%s,%s,%s,%s)",
                ("Student", "A", "123456789012", "test@example.test"),
            )
            cur.execute(
                "INSERT INTO attendance (full_name,section,lrn,time_in,time_out) VALUES ('Student','A','123456789012','2026-10-01 08:00:00','2026-10-01 09:00:00')"
            )

    def test_fresh_postgres_workflow(self):
        init_db()
        init_db()
        self.seed()
        csrf = self.login()
        student = get_all_students()[0]
        qr = create_qr_token(student["qr_nonce"])
        self.assertEqual(self.post("/scan", {"data": qr}, csrf).status_code, 200)
        self.assertEqual(self.post("/scan", {"data": qr}, csrf).status_code, 409)
        self.assertEqual(
            self.post("/timeout", {"lrn": student["lrn"]}, csrf).status_code, 200
        )
        rows = self.client.get("/get_attendance", base_url="https://localhost").json
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["time_out"])
        self.assertEqual(
            self.client.get("/export_csv", base_url="https://localhost").status_code,
            200,
        )

    def test_legacy_migration_preserves_history_and_roster(self):
        self.legacy_schema()
        with self.assertRaisesRegex(RuntimeError, "Legacy database"):
            init_db()
        migrate_legacy_database("Asia/Manila")
        nonce = get_all_students()[0]["qr_nonce"]
        migrate_legacy_database("Asia/Manila")
        self.assertEqual(get_all_students()[0]["qr_nonce"], nonce)
        self.login()
        rows = self.client.get("/get_attendance", base_url="https://localhost").json
        self.assertEqual(len(rows), 1)
        from datetime import datetime, timezone

        actual = datetime.fromisoformat(rows[0]["time_in"]).astimezone(timezone.utc)
        self.assertEqual(actual.hour, 0)
        self.assertEqual(rows[0]["duration"], "1:00:00")

    def test_legacy_duplicate_migration_rolls_back(self):
        self.legacy_schema()
        with transaction() as cur:
            for _ in range(2):
                cur.execute(
                    "INSERT INTO attendance (full_name,section,lrn,time_in) VALUES ('Student','A','123456789012','2026-10-02 08:00:00')"
                )
        with self.assertRaisesRegex(ValueError, "Duplicate active"):
            migrate_legacy_database("Asia/Manila")
        with transaction() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='students'"
            )
            self.assertNotIn("qr_nonce", {r[0] for r in cur.fetchall()})
            cur.execute("SELECT count(*) FROM attendance")
            self.assertEqual(cur.fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
