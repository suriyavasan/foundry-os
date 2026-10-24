"""Value-level secret detection for free text that agents (or notes) could leak into company
memory or agent-visible fields. Deliberately conservative: it only matches well-known token
shapes and ``name = value`` assignments of obviously secret names."""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "json web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    ("company os run token", re.compile(r"\bcos_(?:run|owner)[._][A-Za-z0-9_.\-]{8,}")),
    (
        "secret assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token)"
            r"\s*[:=]\s*[\"']?[^\s\"']{8,}"
        ),
    ),
)
REDACTED = "[redacted]"


def find_secret(text: str | None) -> str | None:
    """Name of the first secret pattern found in ``text``, else None."""
    if not text:
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    return None


def scrub(text: str | None) -> str | None:
    """``text`` with every secret-looking span replaced by ``[redacted]``."""
    if not text:
        return text
    for _, pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text
