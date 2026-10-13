"""Explicit QR generation and optional delivery; no automatic student emailing."""

import argparse
import time
from db import get_all_students
from qr_generator import generate_qr
from email_sender import send_qr

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-email", action="store_true")
    args = parser.parse_args()
    for student in get_all_students():
        path = generate_qr(student)
        print(path)
        if args.send_email:
            send_qr(student["email"], path, student["full_name"])
            time.sleep(3)
