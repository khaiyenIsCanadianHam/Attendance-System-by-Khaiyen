from cryptography.fernet import Fernet
from config import QR_KEY

cipher = Fernet(QR_KEY.encode())


def encrypt_data(data: str) -> bytes:
    return cipher.encrypt(data.encode())


def decrypt_data(token: bytes) -> str:
    return cipher.decrypt(token).decode()
