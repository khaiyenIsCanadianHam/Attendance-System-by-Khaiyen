# Attendance System

A teacher-operated web application for recording student attendance with QR codes.
Teachers can check students in, record time-outs, monitor active attendance, and
export attendance reports. The application uses Flask and runs with SQLite by
default; PostgreSQL is available for deployments that need a separate database.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Step-by-step setup](#step-by-step-setup)
- [Daily use](#daily-use)
- [Account and QR maintenance](#account-and-qr-maintenance)
- [Optional integrations](#optional-integrations)
- [Migrating an existing PostgreSQL installation](#migrating-an-existing-postgresql-installation)
- [Deployment and security](#deployment-and-security)
- [Configuration reference](#configuration-reference)
- [Testing and troubleshooting](#testing-and-troubleshooting)

## Features

| Feature | What it does |
| --- | --- |
| Teacher login | Restricts the dashboard, attendance actions, and reports to provisioned teachers. |
| Roster import | Imports approved student records from CSV, validates all rows first, and skips existing LRNs. |
| QR generation | Creates a private QR image for each student without embedding their personal details. |
| Camera scanning | Checks a student in after the server verifies their QR credential against the roster. |
| Attendance dashboard | Displays stored attendance, the active student count, and live durations; reloads records after a page refresh. |
| Time-out controls | Closes one student's active record or all active records. |
| CSV reports | Exports attendance history, check-in/out timestamps, and completed attendance durations. |
| QR revocation | Replaces a lost or copied QR credential so the old code stops working. |
| Audit records | Records which teacher scanned or timed out a student, with UTC timestamps. |
| Optional integrations | Supports PostgreSQL, an approved Google Sheets CSV export, and explicit SMTP delivery of QR images. |

Security controls include CSRF protection, login throttling, protected session
cookies, password reset session invalidation, safe student-name rendering, CSV
formula protection, and database enforcement of one active record per student.

**How attendance works:** an operator approves the roster and issues QR codes. A
teacher logs in, verifies the student's identity, and scans their code. The server
records attendance; students cannot register teacher accounts or submit attendance
directly. A QR code can still be copied, so identity verification remains necessary.

## Requirements

- Python **3.12 or later** and pip; the application was tested with Python 3.12 on Linux.
- A modern browser and camera for scanning.
- Internet access to install Python dependencies.
- An operator-approved roster with a **12-digit LRN** and email address for each student.

PostgreSQL, Google Sheets, SMTP, Node.js, and Chromium are **optional**. You do not
need them for the default SQLite setup. Camera access requires HTTPS or a
browser-recognized localhost secure context; remote HTTP access does not support it.

## Step-by-step setup

### 1. Open the project folder

Extract the project archive or open your existing repository checkout. In a
terminal, change to the folder containing `server.py`, `requirements.lock`, and
this README. Run the following commands from that folder.

If you are upgrading an existing installation, back up its database and private
keys first. Follow the [migration instructions](#migrating-an-existing-postgresql-installation)
for the original PostgreSQL schema rather than initializing over it.

### 2. Create and activate a virtual environment

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

The remaining commands use `python` from this environment. If PowerShell blocks
activation, use `.\.venv\Scripts\python.exe` in place of `python`; activation is
not required when you use the interpreter's full path.

### 3. Install the dependencies

```bash
python -m pip install -r requirements.lock
python -m pip check
```

`requirements.lock` pins the application and its dependency versions. The check
should report `No broken requirements found`. No npm installation is needed to run
the dashboard: the QR decoder is included locally.

### 4. Initialize the database

```bash
python -m flask --app server init-db
```

Without `DATABASE_URL`, this creates SQLite storage in `.instance/`. This private
folder also holds generated application keys and QR images. Initialization is
repeatable and preserves existing records. For a new PostgreSQL installation, set
its connection configuration before running this command.

### 5. Create a teacher account

```bash
python -m flask --app server create-teacher your_username
```

Replace `your_username` with a username containing 3–100 letters, digits, periods,
underscores, or hyphens. Enter and confirm a password of 12–256 characters at the
private prompt. There is no default account or public registration page. Do not
put the password in command-line arguments or source files.

### 6. Prepare and import the roster

Save an approved UTF-8 CSV file, for example `roster.csv`, with these exact headers:

```csv
full_name,section,lrn,email
Example Student,Section A,123456789012,student@example.test
```

Replace the sample with real approved records. Keep LRNs as text so leading zeros
are preserved. Quote fields containing commas. Every LRN must contain exactly
12 digits, and each student must have a valid email address.

```bash
python fetch_students.py roster.csv
```

The importer validates the entire roster before inserting students. Reimporting
an existing LRN skips it; this command does not update its stored details.

### 7. Generate and distribute QR images

```bash
python qr_generator.py
```

Images are saved under `.instance/qrcodes/`, named by LRN. Distribute each image
privately to its student. Do not upload the database, keys, roster, or QR images
to a public repository. QR generation does not send email automatically.

### 8. Start the local server

**Linux/macOS:**

```bash
ATTENDANCE_LOCAL_HTTP=1 python -m gunicorn --workers 2 --bind 127.0.0.1:5000 server:app
```

**Windows PowerShell:**

```powershell
$env:ATTENDANCE_LOCAL_HTTP = "1"
python server.py
```

Keep the terminal running. On the same machine, open `http://127.0.0.1:5000` in
your browser and sign in with the account from step 5. Debug mode is disabled.
The HTTP override is for local development only; production requires HTTPS.

### 9. Check the installation

In a second terminal, open the project folder and run:

```bash
python scripts/smoke.py
```

This checks the running login form, local browser scripts, and that attendance and
report endpoints reject unauthenticated requests. Then use the dashboard's
**Start Scanner** button and allow camera access. Verify the student's identity
before scanning their QR.

On Linux/macOS, `bash scripts/setup.sh` is a shortcut for dependency installation
and database initialization. `ATTENDANCE_LOCAL_HTTP=1 bash scripts/start.sh`
starts the local server after setup.

## Daily use

1. Start the server and log in as a teacher.
2. Select **Start Scanner** and allow the browser to use the camera.
3. Verify each student's identity, then present their QR to the camera.
4. Review the student's name and attendance record. A second scan while they are
   already checked in is rejected, including concurrent duplicate requests.
5. Select **Time Out** beside a student, or **Time Out All** to close every active record.
6. Select **Stop and Download Report** to stop the camera and download the CSV.
7. Log out when finished, especially on a shared device.

Stopping the scanner does **not** time students out. Use the time-out controls to
close active records. Attendance persists across restarts, and the CSV contains
all stored attendance history rather than only the current scanning session.
The dashboard shows local browser times; records and audit events use UTC timestamps.

## Account and QR maintenance

Run these commands from the project folder using your virtual environment.

**Create another teacher:**

```bash
python -m flask --app server create-teacher another_username
```

**Reset a teacher's password:**

```bash
python -m flask --app server reset-teacher-password your_username
```

The command prompts privately and invalidates that teacher's existing sessions.
Removing a teacher through an authorized database administrator also revokes access.

**Revoke and replace a lost or copied QR:**

```bash
python qr_generator.py --rotate 123456789012
```

Replace the sample LRN with the affected student's LRN and distribute the replacement
image. Regenerating images without `--rotate` does not revoke existing credentials.

**Backups:** securely back up `.instance/` for SQLite installations. With PostgreSQL,
back up the database and the private application keys. Keep the keys persistent
across restarts and identical across workers. Losing the QR key invalidates QR
codes; replacing the session key logs teachers out.

## Optional integrations

### PostgreSQL

Create an empty database and a dedicated application role, then supply
`DATABASE_URL` through secure runtime configuration. Run:

```bash
python -m flask --app server init-db
```

Remote PostgreSQL connections require `sslmode=verify-full` and a trusted root
certificate. Restrict database access and role privileges. The source contains
no default database password. Use the migration procedure below for an existing
installation with the original schema.

### Google Sheets roster import

Set `STUDENT_SHEET_URL` to the HTTPS CSV export of an **operator-controlled Google
Sheet**, then explicitly run:

```bash
python fetch_students.py
```

The URL must use `docs.google.com`. Review student submissions before approving
them for the roster. There is no background polling or automatic trust in the old
public form. Local CSV import works without Google Sheets access.

### Email QR delivery

Configure `SMTP_HOST`, `SMTP_USER`, and `SMTP_PASSWORD` securely. `SMTP_PORT`
defaults to `465`. Then explicitly run:

```bash
python app.py --send-email
```

This generates and emails QR images for the stored roster using
certificate-verified SMTP TLS. Running `python app.py` without the flag only
creates images. Avoid repeating delivery unless you intend to email the roster
again. Real SMTP delivery and a live Google Sheet were not tested in this environment.

## Migrating an existing PostgreSQL installation

The old `databaseTables` instructions were malformed. Use this migration instead:

1. Stop the old application and take a verified PostgreSQL backup.
2. Configure `DATABASE_URL` for the existing database securely.
3. Run the migration with the timezone used by the original machine:

   ```bash
   python -m flask --app server migrate-legacy --timezone Asia/Manila --yes
   ```

4. Generate replacement QR images with `python qr_generator.py` and replace all old codes.
5. Reset potentially exposed teacher passwords and restore service with HTTPS and new private keys.

The migration preserves teachers, roster records, and attendance history. It adds
revocable QR credentials, converts naive timestamps to timezone-aware timestamps,
and enforces one active attendance record per student. It is repeatable.

Invalid roster data, missing timestamps, unknown students, or duplicate active
records cause migration to refuse rather than delete or silently rewrite records.
Resolve these issues under operator review after backup, then rerun. Migration was
tested on a representative original schema; your live database has not been changed.

## Deployment and security

For production, run the application behind an HTTPS reverse proxy on a supported
Linux server. Bind Gunicorn to loopback, keep its port and the database private,
and configure exact application hostnames through `ATTENDANCE_TRUSTED_HOSTS`.
Leave `ATTENDANCE_LOCAL_HTTP` unset so cookies remain Secure, HttpOnly, and
SameSite=Strict. Sessions expire after eight hours without sliding renewal.

Configure HSTS and IP rate limits at the HTTPS proxy. The application intentionally
does not trust client-supplied forwarded IP or host headers. Its database-backed
login limits allow 10 attempts per username and 50 per source address per
15-minute window, across cookie resets and multiple workers. Proxy-level limits
are needed when upstream requests share the proxy's address.

QR credentials use a random 256-bit roster identifier and a 128-bit HMAC tag.
The server obtains student identity from the database and rejects forged,
unknown, revoked, and legacy codes. All state-changing web requests require CSRF
protection. Browser-supplied attendance rosters are not accepted.

**Before reusing the original installation:** revoke the exposed Gmail app password,
replace the archived TLS private key/certificate, retire the old encryption/session
keys, and replace old QR images. Removing credentials from source does not revoke
them at their provider.

Teachers share access to the roster and attendance history; there is no per-class
permission partition. QR codes can be photographed or shared, so teacher identity
checks, strong passwords, locked devices, backups, and restricted operator access
remain necessary. No software can guarantee that students cannot exploit it.

## Configuration reference

Set configuration in your shell or secure deployment settings. `.env.example`
documents the names; the application does **not** automatically load `.env` files.
Never commit actual credentials.

| Variable | Purpose / default |
| --- | --- |
| `ATTENDANCE_INSTANCE` | Private persistent storage; defaults to `.instance/` beside the source. |
| `DATABASE_URL` | PostgreSQL connection configuration; omitted uses SQLite. |
| `ATTENDANCE_LOCAL_HTTP` | Set to `1` only for local HTTP development; omit for production. |
| `ATTENDANCE_TRUSTED_HOSTS` | Comma-separated exact application hostnames. |
| `ATTENDANCE_SESSION_KEY` | Optional private override of the generated session key; at least 32 random characters. |
| `ATTENDANCE_QR_KEY` | Optional private override; URL-safe base64 encoding of 32 random bytes. |
| `STUDENT_SHEET_URL` | Approved Google Sheets HTTPS CSV export; optional. |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` | Optional SMTP settings for explicit email delivery. |
| `SMTP_PORT` | SMTP TLS port; defaults to `465`. |
| `ATTENDANCE_WORKERS` | Worker count for `scripts/start.sh`; defaults to `2`. |
| `ATTENDANCE_BIND` | Bind address for `scripts/start.sh`; defaults to `127.0.0.1:5000`. |

## Testing and troubleshooting

### Core checks

With the virtual environment active:

```bash
python -m unittest discover -s tests -v
python -m pip check
```

The core suite uses temporary SQLite storage and generated credentials, leaving
the real roster untouched. It covers authentication, CSRF, QR validation/revocation,
concurrent duplicates, attendance actions, audit records, imports, session
invalidation, CSV protection, remote database TLS enforcement, and mocked SMTP.

If Node.js is installed, check the browser scripts with:

```bash
node --check static/js/scanner.js
node --check static/js/qr-camera.js
```

### Optional integration tests (Linux)

**PostgreSQL**, using an isolated local Unix-socket server:

```bash
python -m pip install --target /tmp/attendance-pgtools pgserver==0.1.4
ATTENDANCE_PGTOOLS=/tmp/attendance-pgtools python tests/postgres_integration.py -v
```

**Chromium**, with Node.js and Chromium installed at `/usr/bin/chromium` (or set
`CHROMIUM_PATH` to your executable):

```bash
npm install --prefix /tmp/attendance-browser --cache /tmp/attendance-npm-cache playwright@1.58.2
ATTENDANCE_PLAYWRIGHT=/tmp/attendance-browser/node_modules/playwright python tests/run_browser.py
```

The browser test starts and stops a temporary two-worker server, decodes 20 distinct
QR images, and uses Chromium's fake camera to check recovery, safe rendering,
reloads, time-out, report downloads, and logout. Physical mobile cameras and a
production HTTPS proxy were not tested.

The QR decoder is vendored jsQR 1.4.0. Its MIT license and package provenance are
in `static/vendor/`; application Python versions are pinned in `requirements.lock`.

### Common problems

| Problem | What to check |
| --- | --- |
| Missing Python modules | Activate `.venv` or use its Python executable, then install `requirements.lock`. |
| Database tables missing | Run `python -m flask --app server init-db` with the same configuration as the server. |
| Unable to log in | Create a teacher account or reset its password. There is no default account. |
| Too many login attempts | Wait for the 15-minute limit window to expire. |
| Camera unavailable | Use HTTPS or localhost, allow camera permission, and check whether another application is using the camera. |
| Login does not persist over local HTTP | Use `ATTENDANCE_LOCAL_HTTP=1` only for local development. Production must use HTTPS. |
| Invalid or revoked QR | Issue the current QR image; original archive-era codes are intentionally rejected. |
| Student already checked in | Close the active record with Time Out before scanning another check-in. |
| Roster rejected | Check the exact CSV headers, 12-digit LRNs, nonempty names/sections, and valid email addresses. |
| Existing roster details unchanged | Import skips existing LRNs; it is not a roster update command. |
| Remote PostgreSQL connection refused | Configure certificate-verified TLS and trusted root certificates. |
| Email delivery fails | Verify secure SMTP settings, provider app-password requirements, and network access. |
