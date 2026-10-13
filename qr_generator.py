import secrets
import qrcode
from config import INSTANCE
from db import get_all_students, transaction, validate_student
from qr_tokens import create_qr_token


def generate_qr(student):
    validate_student(
        student["full_name"], student["section"], student["lrn"], student["email"]
    )
    # Obtain the current nonce from the trusted database, not the caller.
    with transaction() as cur:
        cur.execute("SELECT qr_nonce FROM students WHERE lrn=%s", (student["lrn"],))
        row = cur.fetchone()
    if not row:
        raise ValueError("Student not found")
    token = create_qr_token(row[0])
    directory = INSTANCE / "qrcodes"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (student["lrn"] + ".png")
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4
    )
    qr.add_data(token, optimize=0)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").convert("RGB").save(path)
    path.chmod(0o600)
    return str(path)


def revoke_qr(lrn):
    with transaction() as cur:
        cur.execute(
            "UPDATE students SET qr_nonce=%s WHERE lrn=%s",
            (secrets.token_urlsafe(32), lrn),
        )
        if cur.rowcount != 1:
            raise ValueError("Student not found")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rotate", metavar="LRN", help="Revoke and replace one student QR"
    )
    args = parser.parse_args()
    if args.rotate:
        revoke_qr(args.rotate)
    for student in get_all_students():
        if not args.rotate or student["lrn"] == args.rotate:
            print(generate_qr(student))
