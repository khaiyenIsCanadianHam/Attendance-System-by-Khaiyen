"""SQLite for local development; PostgreSQL via DATABASE_URL for deployment."""

import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from config import INSTANCE


class Cursor:
    def __init__(self, cursor, postgres):
        self.cursor, self.postgres = cursor, postgres

    def execute(self, sql, params=()):
        self.cursor.execute(sql if self.postgres else sql.replace("%s", "?"), params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount


@contextmanager
def transaction():
    url = os.environ.get("DATABASE_URL")
    if url:
        import psycopg2

        from psycopg2.extensions import parse_dsn

        parameters = parse_dsn(url)
        hosts = parameters.get("host", "").split(",")
        remote = any(
            host
            and not host.startswith("/")
            and host not in ("localhost", "127.0.0.1", "::1")
            for host in hosts
        )
        if remote and parameters.get("sslmode") != "verify-full":
            raise RuntimeError(
                "Remote PostgreSQL requires sslmode=verify-full and a trusted root certificate"
            )
        conn = psycopg2.connect(url, connect_timeout=10)
    else:
        conn = sqlite3.connect(INSTANCE / "attendance.sqlite3", timeout=15)
        conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield Cursor(conn.cursor(), bool(url))
    finally:
        conn.close()


def init_db():
    identity = (
        "SERIAL PRIMARY KEY"
        if os.environ.get("DATABASE_URL")
        else "INTEGER PRIMARY KEY"
    )
    timestamp_type = "TIMESTAMPTZ" if os.environ.get("DATABASE_URL") else "TEXT"
    with transaction() as cur:
        if os.environ.get("DATABASE_URL"):
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='students'"
            )
            columns = {r[0] for r in cur.fetchall()}
            if columns and "qr_nonce" not in columns:
                raise RuntimeError(
                    "Legacy database detected. Back up the database, then run flask --app server migrate-legacy."
                )
        for sql in [
            "CREATE TABLE IF NOT EXISTS registration_requests (lrn TEXT NOT NULL, email TEXT NOT NULL, full_name TEXT NOT NULL, section TEXT NOT NULL, status TEXT NOT NULL, observed_at TEXT NOT NULL, PRIMARY KEY (lrn,email))",
            f"CREATE TABLE IF NOT EXISTS teachers (id {identity}, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS students (id {identity}, full_name TEXT NOT NULL, section TEXT NOT NULL, lrn TEXT UNIQUE NOT NULL, email TEXT NOT NULL, qr_nonce TEXT NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS attendance (id {identity}, full_name TEXT NOT NULL, section TEXT NOT NULL, lrn TEXT NOT NULL REFERENCES students(lrn), time_in {timestamp_type} NOT NULL, time_out {timestamp_type})",
            "CREATE UNIQUE INDEX IF NOT EXISTS unique_qr_nonce ON students(qr_nonce)",
            "CREATE UNIQUE INDEX IF NOT EXISTS one_active_attendance ON attendance(lrn) WHERE time_out IS NULL",
            "CREATE TABLE IF NOT EXISTS login_limits (bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires DOUBLE PRECISION NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS audit_log (id {identity}, actor TEXT NOT NULL, action TEXT NOT NULL, lrn TEXT, occurred_at TEXT NOT NULL)",
        ]:
            cur.execute(sql)


# Validate identifiers before storage, filename creation, or QR generation.
def validate_student(full_name, section, lrn, email):
    if not isinstance(lrn, str) or not re.fullmatch(r"[0-9]{12}", lrn):
        raise ValueError("LRN must contain exactly 12 digits")
    for value, limit in ((full_name, 150), (section, 100), (email, 254)):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > limit
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Invalid student details")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Invalid email address")


def insert_student(full_name, section, lrn, email):
    validate_student(full_name, section, lrn, email)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO students (full_name,section,lrn,email,qr_nonce) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (lrn) DO NOTHING",
            (
                full_name.strip(),
                section.strip(),
                lrn,
                email.strip(),
                secrets.token_urlsafe(32),
            ),
        )
        return cur.rowcount == 1


def get_all_students():
    with transaction() as cur:
        cur.execute(
            "SELECT full_name,section,lrn,email,qr_nonce FROM students ORDER BY lrn"
        )
        return [
            dict(zip(("full_name", "section", "lrn", "email", "qr_nonce"), row))
            for row in cur.fetchall()
        ]


def migrate_legacy_database(timezone):
    from zoneinfo import ZoneInfo

    ZoneInfo(timezone)  # Reject unknown timezones before changing the schema.
    if not os.environ.get("DATABASE_URL"):
        raise ValueError("Legacy migration requires PostgreSQL DATABASE_URL")
    with transaction() as cur:
        # Exclusive locks prevent writes between validation and index creation.
        cur.execute("LOCK TABLE teachers,students,attendance IN ACCESS EXCLUSIVE MODE")
        cur.execute("SELECT full_name,section,lrn,email FROM students")
        students = cur.fetchall()
        for row in students:
            validate_student(*row)
        cur.execute(
            "SELECT lrn FROM attendance WHERE time_out IS NULL GROUP BY lrn HAVING COUNT(*)>1"
        )
        if cur.fetchone():
            raise ValueError(
                "Duplicate active attendance exists. Resolve these records after backup before migrating."
            )
        cur.execute(
            "SELECT id FROM attendance WHERE time_in IS NULL OR lrn NOT IN (SELECT lrn FROM students) LIMIT 1"
        )
        if cur.fetchone():
            raise ValueError(
                "Attendance has missing timestamps or unknown students. Resolve after backup before migrating."
            )
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='students'"
        )
        if "qr_nonce" not in {r[0] for r in cur.fetchall()}:
            cur.execute("ALTER TABLE students ADD COLUMN qr_nonce TEXT")
            for row in students:
                cur.execute(
                    "UPDATE students SET qr_nonce=%s WHERE lrn=%s",
                    (secrets.token_urlsafe(32), row[2]),
                )
        cur.execute("ALTER TABLE students ALTER COLUMN qr_nonce SET NOT NULL")
        for column in ("time_in", "time_out"):
            cur.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='attendance' AND column_name=%s",
                (column,),
            )
            data_type = cur.fetchone()[0]
            if data_type == "timestamp without time zone":
                cur.execute(
                    f"ALTER TABLE attendance ALTER COLUMN {column} TYPE TIMESTAMPTZ USING {column} AT TIME ZONE %s",
                    (timezone,),
                )
            elif data_type not in ("timestamp with time zone",):
                raise ValueError("Unexpected legacy timestamp type")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS one_active_attendance ON attendance(lrn) WHERE time_out IS NULL"
        )
        cur.execute(
            "SELECT 1 FROM pg_constraint WHERE conrelid='attendance'::regclass AND conname='attendance_student_fk'"
        )
        if not cur.fetchone():
            cur.execute(
                "ALTER TABLE attendance ADD CONSTRAINT attendance_student_fk FOREIGN KEY (lrn) REFERENCES students(lrn)"
            )
    init_db()


def record_registration_requests(rows):
    from datetime import datetime, timezone

    observed = datetime.now(timezone.utc).isoformat()
    with transaction() as cur:
        for row in rows:
            cur.execute(
                "INSERT INTO registration_requests (lrn,email,full_name,section,status,observed_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (lrn,email) DO UPDATE SET full_name=excluded.full_name, section=excluded.section, status=excluded.status, observed_at=excluded.observed_at",
                (
                    row["lrn"],
                    row["email"],
                    row["full_name"],
                    row["section"],
                    row["status"],
                    observed,
                ),
            )
