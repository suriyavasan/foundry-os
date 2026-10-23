"""Encryption for stored credentials (Fernet, from ``cryptography``).

The Fernet key is derived (HKDF-SHA256, fixed purpose label) from the same secret key the run
tokens use: ``COMPANY_OS_SECRET_KEY`` or the auto-generated ``<data_dir>/secret.key`` (0600).
Consequence, documented in the README: losing or replacing that key makes stored credentials
undecryptable (they must be re-entered); the ciphertext alone is useless.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.keys import SecretKeyProvider

_PURPOSE = b"company-os/credentials/v1"
_SALT = b"company-os"


class CredentialDecryptError(Exception):
    """The stored value cannot be decrypted with the current secret key."""


class CredentialCipher:
    def __init__(self, keys: SecretKeyProvider) -> None:
        self._keys = keys
        self._fernet: Fernet | None = None

    def _get(self) -> Fernet:
        if self._fernet is None:
            raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=_PURPOSE).derive(
                self._keys.get()
            )
            self._fernet = Fernet(base64.urlsafe_b64encode(raw))
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        return self._get().encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._get().decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            raise CredentialDecryptError("Stored credential cannot be decrypted") from None

    def __repr__(self) -> str:
        return "CredentialCipher()"
