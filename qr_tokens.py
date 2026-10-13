"""Compact authenticated QR credentials; no student personal details in the QR."""

import base64
import hashlib
import hmac
import re
from config import QR_KEY

_KEY = base64.urlsafe_b64decode(QR_KEY)
if len(_KEY) != 32:
    raise RuntimeError("QR key must contain 32 random bytes")


def create_qr_token(nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce):
        raise ValueError("Invalid QR nonce")
    message = "AT1:" + nonce
    # A 128-bit authentication tag plus a 256-bit random roster credential.
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(_KEY, message.encode(), hashlib.sha256).digest()[:16]
        )
        .decode()
        .rstrip("=")
    )
    return message + ":" + signature


def verify_qr_token(token):
    if not isinstance(token, str) or not re.fullmatch(
        r"AT1:[A-Za-z0-9_-]{43}:[A-Za-z0-9_-]{22}", token
    ):
        raise ValueError("Invalid QR code")
    nonce = token.split(":")[1]
    if not hmac.compare_digest(token, create_qr_token(nonce)):
        raise ValueError("Invalid QR code")
    return nonce
