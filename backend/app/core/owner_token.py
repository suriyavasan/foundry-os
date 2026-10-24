"""The local owner credential (M3b-sec).

In ``COMPANY_OS_AUTH_MODE=local`` the owner is whoever holds ``<data_dir>/owner.token``: 32
random bytes (urlsafe), created at startup with mode 0600 and never logged. Agent processes run on
the same host but cannot read it (it lives outside their workspace jail and their environment never
contains it), so a request without a credential is no longer "the owner" -- it is a 401.

Owner credentials, checked in constant time:
  * browser: HttpOnly, SameSite=Strict cookie ``cos_owner`` set by ``GET /api/auth/local?token=``
  * scripts/CLI: ``Authorization: Bearer <owner token>``
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import stat
import sys
import threading
from pathlib import Path

from app.core.config import Settings

logger = logging.getLogger("company_os.auth")

OWNER_TOKEN_FILENAME = "owner.token"
OWNER_COOKIE = "cos_owner"
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 3600
MIN_TOKEN_LENGTH = 32


_TOKEN_QUERY = re.compile(r"([?&]token=)[^&\s\"']+")
# M7: an invitation token travels in the path of POST /api/invitations/<token>/accept
_INVITE_PATH = re.compile(r"(/invitations/)[A-Za-z0-9_\-]{16,}(/accept)")


def _mask(text: str) -> str:
    return _INVITE_PATH.sub(r"\1[redacted]\2", _TOKEN_QUERY.sub(r"\1[redacted]", text))


class RedactLoginToken(logging.Filter):
    """uvicorn's access log prints the request line, which for the login link contains the owner
    token. Mask the value so the token never reaches a log file or a shared terminal scrollback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_mask(a) if isinstance(a, str) else a for a in record.args)
        return True


def install_log_redaction() -> None:
    """Attach the filter to uvicorn's access logger (idempotent)."""
    for name in ("uvicorn.access", "uvicorn"):
        target = logging.getLogger(name)
        if not any(isinstance(f, RedactLoginToken) for f in target.filters):
            target.addFilter(RedactLoginToken())


class OwnerTokenProvider:
    """Lazily creates / reads the owner token. Importing the app never touches the filesystem."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._settings.resolved_data_dir / OWNER_TOKEN_FILENAME

    def ensure(self) -> str:
        """Create the token file if missing (0600, O_EXCL) and return the token."""
        with self._lock:
            if self._token is None:
                self._token = self._read_or_create()
            return self._token

    get = ensure

    def _read_or_create(self) -> str:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            token = path.read_text(encoding="utf-8").strip()
            if len(token) < MIN_TOKEN_LENGTH:
                raise RuntimeError(f"{path} exists but is not a valid owner token") from None
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                os.chmod(path, 0o600)
                logger.warning("owner.token was readable by other users; tightened to 0600")
            return token
        token = secrets.token_urlsafe(32)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
        return token

    def verify(self, candidate: str | None) -> bool:
        """Constant-time comparison; never raises on odd input."""
        if not candidate:
            return False
        try:
            return hmac.compare_digest(candidate.encode("utf-8"), self.ensure().encode("utf-8"))
        except (UnicodeError, OSError):
            return False

    def login_urls(self) -> list[str]:
        token = self.ensure()
        bases = [self._settings.public_url.rstrip("/")]
        for origin in self._settings.cors_origins:  # the Vite dev server proxies /api
            if origin.rstrip("/") not in bases:
                bases.append(origin.rstrip("/"))
        return [f"{base}/api/auth/local?token={token}" for base in bases[:2]]

    def announce(self) -> None:
        """Print the one-time login URL to the terminal -- never to the log files. When stderr is
        not a terminal (service managers, redirected output) nothing is printed; the token is in
        ``owner.token`` (override with COMPANY_OS_PRINT_LOGIN_URL=1)."""
        force = self._settings.print_login_url
        if force is False or (force is None and not sys.stderr.isatty()):
            logger.warning(
                "Owner login: read the token from %s (mode 0600) and open "
                "<public url>/api/auth/local?token=<token>, or send it as 'Authorization: Bearer'.",
                self.path,
            )
            return
        lines = ["", "  Company OS owner login (open this link in your browser; keep it secret):"]
        lines += [f"    {url}" for url in self.login_urls()]
        lines += [f"  The token is stored in {self.path} (mode 0600).", ""]
        print("\n".join(lines), file=sys.stderr, flush=True)

    def announce_setup(self) -> None:
        """Password mode, first run: tell the person at the terminal how to claim the install."""
        force = self._settings.print_login_url
        if force is False or (force is None and not sys.stderr.isatty()):
            logger.warning(
                "Company OS needs its first-run setup. Open the app and create your account; "
                "from another device you need the setup token in %s (mode 0600).",
                self.path,
            )
            return
        base = self._settings.public_url.rstrip("/")
        lines = [
            "",
            "  Company OS first-run setup: open the app and create your account.",
            f"    {base}",
            "  From another device you also need this setup token (not needed on this machine):",
            f"    {self.ensure()}",
            f"  It is stored in {self.path} (mode 0600).",
            "",
        ]
        print("\n".join(lines), file=sys.stderr, flush=True)

    def __repr__(self) -> str:  # never expose the token
        return "OwnerTokenProvider()"
