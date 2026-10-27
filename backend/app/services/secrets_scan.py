"""Value-level secret detection for free text that agents (or notes) could leak into company
memory or agent-visible fields. Deliberately conservative: it only matches well-known token
shapes and ``name = value`` assignments of obviously secret names.

M15 adds payment data: a **card number detector** (13-19 digits, optional spaces or dashes, passing
the Luhn check, not one repeated digit) that is redacted as ``[card number]``, plus Stripe and
Mercury key shapes. The card number is never needed by anything in the system (cards are issued and
revealed on the provider's own surface), so a number that appears anywhere is a leak to be removed.
"""

from __future__ import annotations

import re

PAN_REDACTED = "[card number]"
_PAN_RUN = re.compile(r"(?<![\w.\-])(?:\d[ \-]?){12,18}\d(?![\w\-])")


def luhn_valid(digits: str) -> bool:
    """The Luhn checksum of a string of decimal digits."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _is_card_number(run: str) -> bool:
    digits = re.sub(r"\D", "", run)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    return luhn_valid(digits)


class _CardNumberPattern:
    """Quacks like a compiled regex (``search`` / ``findall`` / ``sub``) so it can sit in the same
    pattern tables as the token shapes, including the outbound DLP that imports them."""

    def search(self, text: str) -> re.Match[str] | None:
        for match in _PAN_RUN.finditer(text):
            if _is_card_number(match.group()):
                return match
        return None

    def findall(self, text: str) -> list[str]:
        return [m.group() for m in _PAN_RUN.finditer(text) if _is_card_number(m.group())]

    def sub(self, repl: str, text: str) -> str:  # noqa: ARG002 - always the card-number marker
        return _PAN_RUN.sub(
            lambda m: PAN_REDACTED if _is_card_number(m.group()) else m.group(), text
        )


_CARD_NUMBER = _CardNumberPattern()


def contains_card_number(text: str | None) -> bool:
    return bool(text) and _CARD_NUMBER.search(text) is not None


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    # M15: Stripe's keys use underscores, so the ``sk-`` shape above never matched them.
    ("stripe key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("stripe webhook secret", re.compile(r"\bwhsec_[A-Za-z0-9]{16,}")),
    # Mercury API tokens (the ``secret-token:mercury_...`` form; the exact shape is unverified).
    ("mercury token", re.compile(r"\bsecret-token:mercury_[A-Za-z0-9_\-]{10,}")),
    ("card number", _CARD_NUMBER),  # type: ignore[arg-type]
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
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
