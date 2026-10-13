"""Operator-only roster import. Never poll a public student-controlled form."""

import argparse
import csv
import io
import os
from urllib.parse import urlsplit
import requests
from db import insert_student, validate_student

FIELDS = ("full_name", "section", "lrn", "email")


def import_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
        raise ValueError("CSV requires full_name,section,lrn,email headers")
    rows = list(reader)
    if len(rows) > 10000:
        raise ValueError("Roster too large")
    # Validate the entire file before inserting any student.
    for row in rows:
        validate_student(*(row[k] for k in FIELDS))
    return sum(insert_student(*(row[k] for k in FIELDS)) for row in rows)


def run_sync():
    url = os.environ.get("STUDENT_SHEET_URL", "")
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "docs.google.com":
        raise ValueError(
            "Set STUDENT_SHEET_URL to an operator-controlled HTTPS Google Sheets CSV URL"
        )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("Roster too large")
    return import_csv(response.text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", nargs="?")
    args = parser.parse_args()
    if args.csv_file:
        with open(args.csv_file, encoding="utf-8-sig") as f:
            count = import_csv(f.read())
    else:
        count = run_sync()
    print(f"Imported {count} new students. Generate QR codes separately.")
