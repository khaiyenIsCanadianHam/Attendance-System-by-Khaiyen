"""Optional SMTP delivery, invoked explicitly by an operator."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def send_qr(email, qr_path, student_name):
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    host = os.environ.get("SMTP_HOST")
    if not all((user, password, host)):
        raise RuntimeError(
            "Configure SMTP_HOST, SMTP_USER and SMTP_PASSWORD before sending email"
        )
    message = EmailMessage()
    message["From"], message["To"] = user, email
    message["Subject"] = "Your Attendance QR Code"
    message.set_content(
        f"Hello {student_name},\n\nPresent the attached QR to your teacher. Do not share it."
    )
    message.add_attachment(
        Path(qr_path).read_bytes(),
        maintype="image",
        subtype="png",
        filename="attendance-qr.png",
    )
    with smtplib.SMTP_SSL(
        host,
        int(os.environ.get("SMTP_PORT", "465")),
        timeout=30,
        context=ssl.create_default_context(),
    ) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)
