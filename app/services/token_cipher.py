"""
Encrypts/decrypts OAuth tokens before they touch the database.

Storing raw access/refresh tokens in plaintext means a single DB leak
hands an attacker live write access to every connected YouTube channel.
We derive a Fernet key from APP_SECRET_KEY so no extra secret management
is needed beyond what's already required.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class TokenCipher:
    def __init__(self):
        settings = get_settings()
        self._fernet = Fernet(_derive_fernet_key(settings.app_secret_key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
