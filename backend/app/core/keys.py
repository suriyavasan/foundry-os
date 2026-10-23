"""Signing key for agent run tokens.

``COMPANY_OS_SECRET_KEY`` wins. Otherwise a random key is generated once into
``<data_dir>/secret.key`` (mode 0600) and reused by every process sharing that directory.
The key is never logged or returned by any API.
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from app.core.config import Settings

KEY_FILENAME = "secret.key"


def _read_or_create(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        data = path.read_bytes().strip()
        if len(data) < 32:
            raise RuntimeError(f"{path} exists but is not a valid secret key") from None
        return data
    with os.fdopen(fd, "wb") as fh:
        key = secrets.token_hex(32).encode()
        fh.write(key)
    return key


class SecretKeyProvider:
    """Lazily resolves the signing key (so importing the app never touches the filesystem)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key: bytes | None = None
        self._lock = threading.Lock()

    def get(self) -> bytes:
        if self._key is None:
            with self._lock:
                if self._key is None:
                    configured = self._settings.secret_key
                    if configured is not None and configured.get_secret_value():
                        self._key = configured.get_secret_value().encode()
                    else:
                        path = self._settings.resolved_data_dir / KEY_FILENAME
                        self._key = _read_or_create(path)
        return self._key

    def __repr__(self) -> str:  # never expose the key
        return "SecretKeyProvider()"
