import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
import click
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from config import SESSION_KEY
from db import init_db, transaction
from qr_tokens import verify_qr_token

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SESSION_KEY,
    MAX_CONTENT_LENGTH=8192,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=True,
    SESSION_REFRESH_EACH_REQUEST=False,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
# Explicit local-only HTTP override; deploy behind HTTPS with the default.
if os.environ.get("ATTENDANCE_LOCAL_HTTP") == "1":
    app.config["SESSION_COOKIE_SECURE"] = False
if os.environ.get("ATTENDANCE_TRUSTED_HOSTS"):
    app.config["TRUSTED_HOSTS"] = [
        host.strip() for host in os.environ["ATTENDANCE_TRUSTED_HOSTS"].split(",")
    ]
DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))
PUBLIC = {"home", "login", "static"}


def auth_stamp(password_hash):
    return hmac.new(
        SESSION_KEY.encode(), password_hash.encode(), hashlib.sha256
    ).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect():
    if request.endpoint is None:
        return
    if request.endpoint not in PUBLIC:
        if not session.get("user"):
            if request.endpoint == "dashboard":
                return redirect(url_for("home"))
            return jsonify(success=False, error="Authentication required"), 401
        with transaction() as cur:
            cur.execute(
                "SELECT password FROM teachers WHERE username=%s", (session["user"],)
            )
            teacher = cur.fetchone()
            if (
                not teacher
                or not isinstance(teacher[0], str)
                or not hmac.compare_digest(
                    session.get("auth", ""), auth_stamp(teacher[0])
                )
            ):
                session.clear()
                return jsonify(success=False, error="Authentication required"), 401
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.headers.get("X-CSRF-Token") or request.form.get(
            "csrf_token", ""
        )
        if not token or not hmac.compare_digest(
            token.encode(), session.get("csrf", "").encode()
        ):
            return jsonify(success=False, error="Invalid CSRF token"), 403


@app.after_request
def headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(self), microphone=(), geolocation=()"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.errorhandler(500)
def internal_error(error):
    return jsonify(success=False, error="Internal server error"), 500


@app.route("/")
def home():
    return render_template("login.html")


@app.post("/login")
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if len(username) > 100 or len(password) > 256:
        abort(400)
    # Shared DB counters, independent of cookies. Do not trust forwarded IP headers.
    buckets = [
        "ip:" + (request.remote_addr or "unknown"),
        "user:" + hashlib.sha256(username.encode()).hexdigest(),
    ]
    timestamp = time.time()
    with transaction() as cur:
        for bucket in buckets:
            cur.execute(
                "INSERT INTO login_limits (bucket,attempts,expires) VALUES (%s,1,%s) ON CONFLICT (bucket) DO UPDATE SET attempts=CASE WHEN login_limits.expires < %s THEN 1 ELSE login_limits.attempts+1 END, expires=CASE WHEN login_limits.expires < %s THEN %s ELSE login_limits.expires END RETURNING attempts",
                (bucket, timestamp + 900, timestamp, timestamp, timestamp + 900),
            )
            if cur.fetchone()[0] > (50 if bucket.startswith("ip:") else 10):
                return render_template(
                    "login.html", error="Too many attempts. Try again in 15 minutes."
                ), 429
        cur.execute("SELECT password FROM teachers WHERE username=%s", (username,))
        row = cur.fetchone()
        valid = check_password_hash(
            row[0] if row and isinstance(row[0], str) else DUMMY_HASH, password
        )
    if row and valid:
        session.clear()
        session["user"] = username
        session["auth"] = auth_stamp(row[0])
        session.permanent = True
        csrf_token()
        return redirect(url_for("dashboard"))
    return render_template("login.html", error="Invalid username or password"), 401


@app.route("/register", methods=["GET", "POST"])
def register():
    abort(403)


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def audit(cur, action, lrn=None):
    cur.execute(
        "INSERT INTO audit_log (actor,action,lrn,occurred_at) VALUES (%s,%s,%s,%s)",
        (session["user"], action, lrn, now()),
    )


@app.post("/scan")
def scan():
    token = payload().get("data")
    if not isinstance(token, str) or len(token) > 4096:
        abort(400)
    try:
        nonce = verify_qr_token(token)
    except ValueError:
        return jsonify(success=False, error="Invalid QR code"), 400
    with transaction() as cur:
        cur.execute(
            "SELECT full_name,section,lrn FROM students WHERE qr_nonce=%s", (nonce,)
        )
        row = cur.fetchone()
        if not row:
            return jsonify(success=False, error="Unknown or revoked QR code"), 400
        lrn = row[2]
        timestamp = now()
        cur.execute(
            "INSERT INTO attendance (full_name,section,lrn,time_in) VALUES (%s,%s,%s,%s) ON CONFLICT (lrn) WHERE time_out IS NULL DO NOTHING",
            (row[0], row[1], lrn, timestamp),
        )
        if cur.rowcount != 1:
            return jsonify(success=False, error="Student already checked in"), 409
        audit(cur, "scan", lrn)
    return jsonify(
        success=True,
        student={"full_name": row[0], "section": row[1], "lrn": lrn},
        time=timestamp,
    )


@app.post("/timeout")
def timeout_student():
    lrn = payload().get("lrn")
    if not isinstance(lrn, str) or not re.fullmatch(r"[0-9]{12}", lrn):
        abort(400)
    with transaction() as cur:
        cur.execute(
            "UPDATE attendance SET time_out=%s WHERE lrn=%s AND time_out IS NULL",
            (now(), lrn),
        )
        if not cur.rowcount:
            return jsonify(success=False, error="No active record found"), 404
        audit(cur, "timeout", lrn)
    return jsonify(success=True)


@app.post("/timeout_all")
def timeout_all():
    with transaction() as cur:
        cur.execute(
            "UPDATE attendance SET time_out=%s WHERE time_out IS NULL", (now(),)
        )
        audit(cur, "timeout_all")
    return jsonify(success=True)


@app.post("/save_attendance")
def save_attendance():
    # Attendance is recorded only from verified QR codes, never browser-supplied rosters.
    return jsonify(success=False, error="Use the authenticated QR scanner"), 410


def records():
    with transaction() as cur:
        cur.execute(
            "SELECT full_name,section,lrn,time_in,time_out FROM attendance ORDER BY time_in,id"
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        row = tuple(
            value.isoformat() if isinstance(value, datetime) else value for value in row
        )
        item = dict(zip(("full_name", "section", "lrn", "time_in", "time_out"), row))
        item["duration"] = (
            str(datetime.fromisoformat(row[4]) - datetime.fromisoformat(row[3]))
            if row[4]
            else ""
        )
        result.append(item)
    return result


@app.get("/get_attendance")
def get_attendance():
    return jsonify(records())


@app.get("/export_csv")
def export_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Full Name", "Section", "LRN", "Time In", "Time Out", "Duration"])
    for row in records():
        values = [
            row[k] or ""
            for k in ("full_name", "section", "lrn", "time_in", "time_out", "duration")
        ]
        writer.writerow(
            [
                "'" + v
                if v.lstrip().startswith(("=", "+", "-", "@"))
                or v.startswith(("\t", "\r", "\n"))
                else v
                for v in values
            ]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=attendance.csv"},
    )


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.cli.command("init-db")
def init_command():
    init_db()
    click.echo("Database initialized.")


@app.cli.command("create-teacher")
@click.argument("username")
@click.password_option(confirmation_prompt=True)
def create_teacher(username, password):
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]{3,100}", username)
        or not 12 <= len(password) <= 256
    ):
        raise click.ClickException(
            "Use a 3–100 character username and a password of 12–256 characters."
        )
    with transaction() as cur:
        cur.execute(
            "INSERT INTO teachers (username,password) VALUES (%s,%s)",
            (username, generate_password_hash(password)),
        )
    click.echo("Teacher created.")


@app.cli.command("migrate-legacy")
@click.option(
    "--timezone",
    default="Asia/Manila",
    show_default=True,
    help="Timezone of the old naive timestamps",
)
@click.option("--yes", is_flag=True, help="Confirm you have backed up the database")
def migrate_legacy(timezone, yes):
    """Preserve the legacy PostgreSQL roster/history, revoke the exposed QR codes."""
    if not yes:
        raise click.ClickException(
            "Back up your database first, then rerun with --yes."
        )
    from db import migrate_legacy_database

    migrate_legacy_database(timezone)
    click.echo(
        "Migration complete. Regenerate all QR codes with python qr_generator.py."
    )


@app.cli.command("reset-teacher-password")
@click.argument("username")
@click.password_option(confirmation_prompt=True)
def reset_teacher_password(username, password):
    if not 12 <= len(password) <= 256:
        raise click.ClickException("Password must have 12–256 characters.")
    with transaction() as cur:
        cur.execute(
            "UPDATE teachers SET password=%s WHERE username=%s",
            (generate_password_hash(password), username),
        )
        if cur.rowcount != 1:
            raise click.ClickException("Teacher not found.")
    click.echo("Password updated; existing sessions are invalidated.")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
