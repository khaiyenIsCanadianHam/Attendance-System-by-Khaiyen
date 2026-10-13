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
        for path in ("/get_attendance", "/export_csv", "/sync_status"):
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

    def test_forms_only_approved_responses_import(self):
        text = (
            "Timestamp,Email Address,full_name,section,lrn,approved\n"
            "today,approved@example.test,Approved,A,012345678901,TRUE\n"
            "today,invalid,Pending,B,bad-lrn,FALSE\n"
            "today,pending@example.test,Pending,B,012345678902,\n"
        )
        self.assertEqual(import_csv(text, google_forms=True), 1)
        students = {s["lrn"]: s for s in get_all_students()}
        self.assertEqual(students["012345678901"]["email"], "approved@example.test")
        self.assertNotIn("012345678902", students)
        self.assertEqual(import_csv(text, google_forms=True), 0)

    def test_forms_requires_teacher_approval_column(self):
        text = "Timestamp,Email Address,full_name,section,lrn\ntoday,test@example.test,Student,A,012345678901\n"
        with self.assertRaisesRegex(ValueError, "approved headers"):
            import_csv(text, google_forms=True)
        self.assertEqual(len(get_all_students()), 1)

    def test_forms_uses_collected_email_instead_of_student_email_question(self):
        text = "Email Address,email,full_name,section,lrn,approved\nverified@example.test,spoofed@example.test,Student,A,012345678901,TRUE\n"
        self.assertEqual(import_csv(text, google_forms=True), 1)
        student = next(s for s in get_all_students() if s["lrn"] == "012345678901")
        self.assertEqual(student["email"], "verified@example.test")

    def test_forms_invalid_approved_record_prevents_partial_import(self):
        text = "Email Address,full_name,section,lrn,approved\nvalid@example.test,Valid,A,012345678901,TRUE\ninvalid,Invalid,A,012345678902,TRUE\n"
        with self.assertRaises(ValueError):
            import_csv(text, google_forms=True)
        self.assertEqual(len(get_all_students()), 1)

    def test_forms_rejects_conflicting_approved_identities(self):
        text = "Email Address,full_name,section,lrn,approved\none@example.test,One,A,012345678901,TRUE\ntwo@example.test,Two,A,012345678901,TRUE\n"
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            import_csv(text, google_forms=True)
        self.assertEqual(len(get_all_students()), 1)

    def test_forms_rejects_ambiguous_approval_and_duplicate_headers(self):
        text = "Email Address,full_name,section,lrn,approved\nstudent@example.test,Student,A,012345678901,yes\n"
        with self.assertRaisesRegex(ValueError, "Approval must"):
            import_csv(text, google_forms=True)
        duplicate = "Email Address,full_name,section,lrn,approved,Approved\n"
        with self.assertRaisesRegex(ValueError, "duplicate headers"):
            import_csv(duplicate, google_forms=True)

    def test_shared_google_sheet_fetch_imports_approved_forms_rows(self):
        from unittest.mock import patch, MagicMock
        from fetch_students import run_sync

        url = "https://docs.google.com/spreadsheets/d/example/export?format=csv&gid=0"
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "text/csv; charset=utf-8"}
        response.iter_content.return_value = [
            b"Email Address,full_name,section,lrn,approved\n",
            b"verified@example.test,Student,A,012345678901,TRUE\n",
        ]
        with (
            patch.dict(os.environ, {"STUDENT_SHEET_URL": url}),
            patch("fetch_students.requests.get", return_value=response) as get,
        ):
            self.assertEqual(run_sync(google_forms=True), 1)
            get.assert_called_once_with(url, stream=True, timeout=(10, 30))
            response.raise_for_status.assert_called_once()

    def test_shared_sheet_html_permission_page_is_rejected(self):
        from unittest.mock import patch, MagicMock
        from fetch_students import run_sync

        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        with (
            patch.dict(
                os.environ,
                {
                    "STUDENT_SHEET_URL": "https://docs.google.com/spreadsheets/d/example/export?format=csv"
                },
            ),
            patch("fetch_students.requests.get", return_value=response),
        ):
            with self.assertRaisesRegex(ValueError, "HTML instead of CSV"):
                run_sync(google_forms=True)
        self.assertEqual(len(get_all_students()), 1)

    def test_shared_sheet_download_size_limit(self):
        from unittest.mock import patch, MagicMock
        from fetch_students import run_sync

        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "text/csv"}
        response.iter_content.return_value = [b"x" * 1_000_001, b"x" * 1_000_001]
        with (
            patch.dict(
                os.environ,
                {
                    "STUDENT_SHEET_URL": "https://docs.google.com/spreadsheets/d/example/export?format=csv"
                },
            ),
            patch("fetch_students.requests.get", return_value=response),
        ):
            with self.assertRaisesRegex(ValueError, "Roster too large"):
                run_sync(google_forms=True)
        self.assertEqual(len(get_all_students()), 1)

    def test_automatic_registration_stores_pending_without_enrolling(self):
        text = "Email Address,full_name,section,lrn,approved\npending@example.test,Pending,A,012345678901,FALSE\n"
        self.assertEqual(import_csv(text, google_forms=True, collect_pending=True), 0)
        self.assertEqual(len(get_all_students()), 1)
        with transaction() as cur:
            cur.execute("SELECT lrn,status FROM registration_requests")
            self.assertEqual(cur.fetchall(), [("012345678901", "pending")])
        self.assertEqual(
            import_csv(
                text.replace("FALSE", "TRUE"), google_forms=True, collect_pending=True
            ),
            1,
        )
        with transaction() as cur:
            cur.execute("SELECT status FROM registration_requests")
            self.assertEqual(cur.fetchone()[0], "approved")
        self.assertEqual(len(get_all_students()), 2)

    def test_sync_worker_generates_missing_qr_and_retries_failed_requests(self):
        from unittest.mock import patch
        import sync_worker
        from config import INSTANCE

        text = "Email Address,full_name,section,lrn,approved\nnew@example.test,New,A,012345678901,TRUE\n"

        def download(**kwargs):
            return import_csv(text, **kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "STUDENT_SHEET_URL": "https://docs.google.com/spreadsheets/d/test/export?format=csv"
                },
            ),
            patch("sync_worker.run_sync", side_effect=download),
        ):
            first = sync_worker.sync_once()
            self.assertEqual(first["imported"], 1)
            self.assertTrue((INSTANCE / "qrcodes" / "012345678901.png").exists())
            second = sync_worker.sync_once()
            self.assertEqual(second["imported"], 0)
            self.assertEqual(second["generated"], 0)
            with patch(
                "sync_worker.run_sync", side_effect=ValueError("PRIVATE_URL_OR_DATA")
            ):
                with self.assertRaises(ValueError):
                    sync_worker.sync_once()
            status = sync_worker.read_status()
            self.assertEqual(status["state"], "error")
            self.assertEqual(status["last_success"], second["last_success"])
            self.assertNotIn("PRIVATE_URL_OR_DATA", json.dumps(status))

    def test_sync_status_is_teacher_only_and_shows_counts(self):
        self.assertEqual(self.client.get("/sync_status").status_code, 401)
        self.login()
        response = self.client.get("/sync_status", base_url="https://localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["students"], 1)
        self.assertNotIn("source", response.json)

    def test_worker_continues_after_failure_and_uses_poll_interval(self):
        from unittest.mock import patch, MagicMock
        from sync_worker import run_worker

        stop = MagicMock()
        stop.is_set.side_effect = [False, False, True]
        with (
            patch.dict(
                os.environ,
                {
                    "STUDENT_SHEET_URL": "https://docs.google.com/spreadsheets/d/test/export?format=csv",
                    "STUDENT_SYNC_INTERVAL": "30",
                },
            ),
            patch(
                "sync_worker.sync_once", side_effect=[ValueError("failure"), {}]
            ) as sync,
            self.assertLogs("hanami.sync", level="WARNING"),
        ):
            run_worker(stop)
        self.assertEqual(sync.call_count, 2)
        self.assertEqual(
            [call.args for call in stop.wait.call_args_list], [(30,), (30,)]
        )

    def test_worker_singleton_and_recovery_after_release(self):
        from sync_worker import worker_lock

        with worker_lock():
            with self.assertRaises(OSError):
                with worker_lock():
                    pass
        with worker_lock():
            pass

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
