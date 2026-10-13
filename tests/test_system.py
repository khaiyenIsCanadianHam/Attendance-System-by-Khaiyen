import csv
import io
import json
import os
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Never touch the live roster or persistent application keys.
_temp = tempfile.TemporaryDirectory()
os.environ["ATTENDANCE_INSTANCE"] = _temp.name
os.environ.pop("DATABASE_URL", None)
os.environ.pop("ATTENDANCE_SESSION_KEY", None)
os.environ.pop("ATTENDANCE_QR_KEY", None)
from server import app, auth_stamp
from db import init_db, insert_student, get_all_students, transaction
from encryption import encrypt_data
from qr_tokens import create_qr_token
from fetch_students import import_csv
from qr_generator import generate_qr, revoke_qr
from werkzeug.security import generate_password_hash


class SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        path = Path(_temp.name) / "attendance.sqlite3"
        if path.exists():
            path.unlink()
        init_db()
        with transaction() as cur:
            cur.execute(
                "INSERT INTO teachers (username,password) VALUES (%s,%s)",
                ("teacher", self.password_hash),
            )
        insert_student(
            "Student One", "Section A", "123456789012", "student@example.test"
        )
        self.student = get_all_students()[0]
        self.token = self.qr()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def qr(self):
        return create_qr_token(self.student["qr_nonce"])

    def csrf(self, client=None):
        client = client or self.client
        client.get("/", base_url="https://localhost")
        with client.session_transaction() as session:
            return session["csrf"]

    def login(self, client=None):
        client = client or self.client
        response = client.post(
            "/login",
            data={
                "username": "teacher",
                "password": self.password,
                "csrf_token": self.csrf(client),
            },
            base_url="https://localhost",
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            return session["csrf"]

    def post(self, path, data=None, token=None, client=None):
        return (client or self.client).post(
            path,
            json=data or {},
            headers={"X-CSRF-Token": token or self.csrf()},
            base_url="https://localhost",
        )

    def test_every_data_endpoint_requires_teacher(self):
        for path in ("/scan", "/timeout", "/timeout_all", "/save_attendance"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path).status_code, 401)
        for path in ("/get_attendance", "/export_csv"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_public_registration_closed(self):
        self.assertEqual(self.client.get("/register").status_code, 401)
        token = self.login()
        self.assertEqual(
            self.client.get("/register", base_url="https://localhost").status_code, 403
        )
        self.assertEqual(self.post("/register", token=token).status_code, 403)

    def test_csrf_for_login_and_all_writes(self):
        self.client.get("/")
        self.assertEqual(
            self.client.post(
                "/login", data={"username": "teacher", "password": self.password}
            ).status_code,
            403,
        )
        self.login()
        for path in (
            "/scan",
            "/timeout",
            "/timeout_all",
            "/save_attendance",
            "/logout",
        ):
            self.assertEqual(
                self.client.post(
                    path, json={}, base_url="https://localhost"
                ).status_code,
                403,
            )

    def test_login_session_rotation_and_logout(self):
        before = self.csrf()
        token = self.login()
        self.assertNotEqual(before, token)
        self.assertEqual(
            self.client.get("/dashboard", base_url="https://localhost").status_code, 200
        )
        self.assertEqual(
            self.client.get("/logout", base_url="https://localhost").status_code, 405
        )
        self.assertEqual(self.post("/logout", token=token).status_code, 302)
        self.assertEqual(
            self.client.get(
                "/get_attendance", base_url="https://localhost"
            ).status_code,
            401,
        )

    def test_full_attendance_cycle_and_audit(self):
        token = self.login()
        first = self.post("/scan", {"data": self.token}, token)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["student"]["full_name"], "Student One")
        self.assertEqual(
            self.post("/scan", {"data": self.token}, token).status_code, 409
        )
        self.assertEqual(
            self.post("/timeout", {"lrn": self.student["lrn"]}, token).status_code, 200
        )
        self.assertEqual(
            self.post("/timeout", {"lrn": self.student["lrn"]}, token).status_code, 404
        )
        self.assertEqual(
            self.post("/scan", {"data": self.token}, token).status_code, 200
        )
        self.assertEqual(self.post("/timeout_all", token=token).status_code, 200)
        rows = self.client.get("/get_attendance", base_url="https://localhost").json
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["time_out"] for r in rows))
        with transaction() as cur:
            cur.execute("SELECT actor,action FROM audit_log")
            self.assertEqual(
                cur.fetchall(),
                [
                    ("teacher", "scan"),
                    ("teacher", "timeout"),
                    ("teacher", "scan"),
                    ("teacher", "timeout_all"),
                ],
            )

    def test_tampered_legacy_and_unknown_codes_rejected(self):
        csrf = self.login()
        legacy = encrypt_data(
            json.dumps({"lrn": self.student["lrn"], "full_name": "Forged"})
        ).decode()
        unknown = create_qr_token(secrets.token_urlsafe(32))
        for token in (
            self.token[:40] + "x" + self.token[41:],
            legacy,
            unknown,
            "garbage",
        ):
            self.assertEqual(self.post("/scan", {"data": token}, csrf).status_code, 400)
        self.assertEqual(
            self.client.get("/get_attendance", base_url="https://localhost").json, []
        )

    def test_student_identity_taken_from_database(self):
        csrf = self.login()
        response = self.post(
            "/scan",
            {
                "data": self.token,
                "student": {"full_name": "Forged", "lrn": "000000000000"},
            },
            csrf,
        )
        self.assertEqual(response.json["student"]["full_name"], "Student One")

    def test_revoked_qr_rejected_and_new_qr_accepted(self):
        csrf = self.login()
        revoke_qr(self.student["lrn"])
        self.assertEqual(
            self.post("/scan", {"data": self.token}, csrf).status_code, 400
        )
        self.student = get_all_students()[0]
        self.assertEqual(self.post("/scan", {"data": self.qr()}, csrf).status_code, 200)

    def test_client_roster_injection_disabled(self):
        csrf = self.login()
        self.assertEqual(
            self.post(
                "/save_attendance", {"records": [{"full_name": "Forged"}]}, csrf
            ).status_code,
            410,
        )
        self.assertEqual(
            self.client.get("/get_attendance", base_url="https://localhost").json, []
        )

    def test_bad_input_and_size_limit(self):
        csrf = self.login()
        for data in ({}, {"data": None}, {"data": 42}, {"data": "x" * 4097}):
            self.assertEqual(self.post("/scan", data, csrf).status_code, 400)
        self.assertEqual(
            self.post("/timeout", {"lrn": "' OR 1=1 --"}, csrf).status_code, 400
        )
        self.assertEqual(
            self.post("/scan", {"data": "x" * 9000}, csrf).status_code, 413
        )
        self.assertEqual(
            self.client.post(
                "/scan",
                data="[]",
                content_type="application/json",
                headers={"X-CSRF-Token": csrf},
                base_url="https://localhost",
            ).status_code,
            400,
        )

    def test_login_throttling_survives_new_cookie(self):
        for _ in range(10):
            client = app.test_client()
            response = client.post(
                "/login",
                data={
                    "username": "teacher",
                    "password": "wrong",
                    "csrf_token": self.csrf(client),
                },
                base_url="https://localhost",
            )
            self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.post(
                "/login",
                data={
                    "username": "teacher",
                    "password": self.password,
                    "csrf_token": self.csrf(),
                },
                base_url="https://localhost",
            ).status_code,
            429,
        )

    def test_deleted_teacher_session_rejected(self):
        self.login()
        with transaction() as cur:
            cur.execute("DELETE FROM teachers")
        self.assertEqual(
            self.client.get(
                "/get_attendance", base_url="https://localhost"
            ).status_code,
            401,
        )

    def test_password_change_invalidates_session(self):
        self.login()
        with transaction() as cur:
            cur.execute(
                "UPDATE teachers SET password=%s",
                (generate_password_hash(secrets.token_urlsafe(24)),),
            )
        self.assertEqual(
            self.client.get(
                "/get_attendance", base_url="https://localhost"
            ).status_code,
            401,
        )

    def test_session_cookie_and_security_headers(self):
        response = self.client.get("/", base_url="https://localhost")
        cookie = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(flag, cookie)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn(
            "frame-ancestors 'none'", response.headers["Content-Security-Policy"]
        )

    def test_csv_quoting_and_formula_injection(self):
        with transaction() as cur:
            cur.execute(
                "UPDATE students SET full_name=%s,section=%s", ("=1+1", 'A, "B"')
            )
        csrf = self.login()
        self.post("/scan", {"data": self.token}, csrf)
        response = self.client.get("/export_csv", base_url="https://localhost")
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(rows[1][0], "'=1+1")
        self.assertEqual(rows[1][1], 'A, "B"')
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_roster_all_rows_idempotence_and_validation(self):
        text = "full_name,section,lrn,email\nStudent Two,B,123456789013,two@example.test\nStudent Three,C,123456789014,three@example.test\n"
        self.assertEqual(import_csv(text), 2)
        self.assertEqual(import_csv(text), 0)
        bad = text.replace("123456789014", "../../file")
        with self.assertRaises(ValueError):
            import_csv(bad)
        self.assertEqual(len(get_all_students()), 3)

    def test_qr_image_and_private_keys(self):
        path = Path(generate_qr(self.student))
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((Path(_temp.name) / "keys.json").stat().st_mode & 0o777, 0o600)

    def test_concurrent_duplicate_scans(self):
        def scan_once(_):
            client = app.test_client()
            with client.session_transaction() as session:
                session["user"] = "teacher"
                session["csrf"] = "parallel"
                session["auth"] = auth_stamp(self.password_hash)
            return client.post(
                "/scan",
                json={"data": self.token},
                headers={"X-CSRF-Token": "parallel"},
                base_url="https://localhost",
            ).status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(scan_once, range(4)))
        self.assertEqual(sorted(codes), [200, 409, 409, 409])

    def test_optional_email_tls_and_attachment(self):
        from unittest.mock import patch
        from email_sender import send_qr

        path = generate_qr(self.student)
        settings = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_USER": "operator@example.test",
            "SMTP_PASSWORD": "test-only-placeholder",
        }
        with (
            patch.dict(os.environ, settings),
            patch("email_sender.smtplib.SMTP_SSL") as smtp,
        ):
            send_qr("student@example.test", path, "Student")
            self.assertEqual(smtp.call_args.args, ("smtp.example.test", 465))
            import ssl

            self.assertEqual(
                smtp.call_args.kwargs["context"].verify_mode, ssl.CERT_REQUIRED
            )
            self.assertTrue(smtp.call_args.kwargs["context"].check_hostname)
            connection = smtp.return_value.__enter__.return_value
            message = connection.send_message.call_args.args[0]
            self.assertEqual(message["To"], "student@example.test")
            self.assertEqual(len(list(message.iter_attachments())), 1)

    def test_email_missing_configuration_refuses_delivery(self):
        from unittest.mock import patch
        from email_sender import send_qr

        with patch.dict(
            os.environ, {"SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASSWORD": ""}
        ):
            with self.assertRaisesRegex(RuntimeError, "Configure SMTP"):
                send_qr("student@example.test", "missing.png", "Student")

    def test_non_ascii_csrf_is_rejected_safely(self):
        self.login()
        self.assertEqual(self.post("/timeout_all", token="é").status_code, 403)

    def test_remote_database_requires_verified_tls(self):
        from unittest.mock import patch

        with (
            patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://db.example.test/attendance?sslmode=prefer"
                },
            ),
            patch("psycopg2.connect") as connect,
        ):
            with self.assertRaisesRegex(RuntimeError, "sslmode=verify-full"):
                with transaction():
                    pass
            connect.assert_not_called()

    def test_init_db_repeatable(self):
        init_db()
        self.assertEqual(len(get_all_students()), 1)


if __name__ == "__main__":
    unittest.main()
