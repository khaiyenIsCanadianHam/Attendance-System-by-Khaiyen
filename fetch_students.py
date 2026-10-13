"""Operator-only roster import. Never poll a public student-controlled form."""

import argparse
import csv
import io
import os
from urllib.parse import urlsplit
import requests
from db import insert_student, validate_student

FIELDS = ("full_name", "section", "lrn", "email")


def import_csv(text, *, google_forms=False):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV requires a header row")
    if google_forms:
        # Google Forms stores the collected email separately from its questions.
        normalized = [name.strip().casefold() for name in reader.fieldnames]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Google Forms CSV has duplicate headers")
        headers = dict(zip(normalized, reader.fieldnames))
        required = {"full_name", "section", "lrn", "email address", "approved"}
        if not required.issubset(headers):
            raise ValueError(
                "Google Forms CSV requires full_name,section,lrn,Email Address,approved headers"
            )
    elif not set(FIELDS).issubset(reader.fieldnames):
        raise ValueError("CSV requires full_name,section,lrn,email headers")
    rows = list(reader)
    if len(rows) > 10000:
        raise ValueError("Roster too large")
    if google_forms:
        approved = []
        for row in rows:
            status = (row.get(headers["approved"]) or "").strip().casefold()
            if status not in ("", "false", "true"):
                raise ValueError(
                    "Approval must be TRUE, FALSE, or empty; use teacher-only checkboxes"
                )
            if status != "true":
                continue
            approved.append(
                {
                    "full_name": row.get(headers["full_name"]),
                    "section": row.get(headers["section"]),
                    "lrn": row.get(headers["lrn"]),
                    "email": row.get(headers["email address"]),
                }
            )
        rows = approved
        identities = {}
        for row in rows:
            previous = identities.setdefault(row["lrn"], row)
            if previous != row:
                raise ValueError(
                    "Conflicting approved responses for one LRN; review duplicates before import"
                )
    # Validate every approved record before inserting any student.
    for row in rows:
        validate_student(*(row[k] for k in FIELDS))
    return sum(insert_student(*(row[k] for k in FIELDS)) for row in rows)


def run_sync(*, google_forms=False):
    url = os.environ.get("STUDENT_SHEET_URL", "")
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "docs.google.com":
        raise ValueError(
            "Set STUDENT_SHEET_URL to an operator-controlled HTTPS Google Sheets CSV URL"
        )
    with requests.get(url, stream=True, timeout=(10, 30)) as response:
        response.raise_for_status()
        if "text/html" in response.headers.get("Content-Type", "").casefold():
            raise ValueError(
                "Sheet returned HTML instead of CSV. Check Viewer link access and the CSV export URL."
            )
        content = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            content.extend(chunk)
            if len(content) > 2_000_000:
                raise ValueError("Roster too large")
        text = content.decode("utf-8-sig")
    return import_csv(text, google_forms=google_forms)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", nargs="?")
    parser.add_argument(
        "--google-forms",
        action="store_true",
        help="Import a Forms response CSV using collected Email Address and teacher-only approved checkboxes",
    )
    args = parser.parse_args()
    if args.csv_file:
        with open(args.csv_file, encoding="utf-8-sig") as f:
            count = import_csv(f.read(), google_forms=args.google_forms)
    else:
        count = run_sync(google_forms=args.google_forms)
    print(f"Imported {count} new students. Generate QR codes separately.")
