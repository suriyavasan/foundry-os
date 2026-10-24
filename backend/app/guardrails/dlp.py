"""Outbound DLP (docs/specs/m6 section 4): scan what an agent wants to send outside the company.

Findings carry a kind, a severity, a description and a count -- NEVER the matched text, so they can
be shown to a reviewer, stored on the approval and logged without leaking the very thing they
caught. What is scanned: credential-shaped strings, the exact secrets Company OS itself holds (run
tokens, the owner token, stored credential values), e-mail addresses and phone numbers beyond the
allowlist (the recipients and human-configured domains), and verbatim passages from verified
company memory tagged internal.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.autonomy import MemoryEntry
from app.models.enums import MemoryStatus
from app.services.secrets_scan import _PATTERNS as _BASE_PATTERNS

INTERNAL_TAGS = frozenset({"internal", "internal-only", "confidential", "private"})
MIN_SECRET_LEN = 8
SHINGLE_WORDS = 8

_EXTRA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("stripe key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    # M7: a personal API token (the cleartext is never stored, so only its shape can be matched)
    ("company os api token", re.compile(r"\bcos_pat_[A-Za-z0-9_\-]{20,}")),
    (
        "bearer credential",
        re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=\-]{12,}"),
    ),
    ("connection string", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:[^\s/@]{3,}@[^\s]+")),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?)?\d{2,4}[\s.\-]\d{3,4}[\s.\-]\d{3,4}(?![\w])"
    r"|(?<![\w.])\+\d{8,15}(?![\w])"
)


@dataclass(frozen=True)
class Finding:
    kind: str  # secret | own_token | credential | email | phone | internal_memory
    severity: str  # low | medium | high
    description: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "description": self.description,
            "count": self.count,
        }


def _norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def internal_snippets(db: Session, company_id: str) -> list[list[str]]:
    """Word lists of verified memory entries tagged internal."""
    rows = db.scalars(
        select(MemoryEntry).where(
            MemoryEntry.company_id == company_id, MemoryEntry.status == MemoryStatus.VERIFIED
        )
    )
    out: list[list[str]] = []
    for row in rows:
        tags = {str(t).lower() for t in (row.tags or [])}
        if tags & INTERNAL_TAGS:
            words = _norm_words(f"{row.title or ''} {row.body}")
            if len(words) >= 3:
                out.append(words)
    return out


def _contains_passage(text_words: list[str], passage: list[str]) -> bool:
    size = min(SHINGLE_WORDS, len(passage))
    if size < 3 or len(text_words) < size:
        return False
    joined = " ".join(text_words)
    return any(" ".join(passage[i : i + size]) in joined for i in range(0, len(passage) - size + 1))


def scan_text(
    text: str,
    *,
    own_secrets: Iterable[str] = (),
    allowed_emails: Iterable[str] = (),
    allowed_domains: Iterable[str] = (),
    internal: Iterable[list[str]] = (),
) -> list[Finding]:
    """Findings for ``text``; empty when it is clean."""
    if not text:
        return []
    findings: list[Finding] = []

    own = 0
    for secret in own_secrets:
        if secret and len(secret) >= MIN_SECRET_LEN and secret in text:
            own += text.count(secret)
    if own:
        findings.append(
            Finding(
                "own_token",
                "high",
                "A token, key or stored credential of this company appears in the text",
                own,
            )
        )

    counts: dict[str, int] = {}
    for name, pattern in (*_BASE_PATTERNS, *_EXTRA_PATTERNS):
        n = len(pattern.findall(text))
        if n:
            counts[name] = n
    own_tokens = counts.pop("company os run token", 0) + counts.pop("company os api token", 0)
    if own_tokens:
        findings.append(
            Finding(
                "own_token",
                "high",
                "A Company OS run or owner token appears in the text",
                own_tokens,
            )
        )
    if counts:
        findings.append(
            Finding(
                "secret",
                "high",
                "Credential-shaped strings: " + ", ".join(sorted(counts)),
                sum(counts.values()),
            )
        )

    allowed_e = {e.lower() for e in allowed_emails}
    allowed_d = {d.lower().lstrip("@") for d in allowed_domains}
    stray = 0
    for match in _EMAIL.findall(text):
        email = match.lower()
        if email in allowed_e or email.rsplit("@", 1)[-1] in allowed_d:
            continue
        stray += 1
    if stray:
        findings.append(Finding("email", "medium", "E-mail addresses outside the allowlist", stray))

    phones = len(_PHONE.findall(text))
    if phones:
        findings.append(Finding("phone", "medium", "Phone numbers in the text", phones))

    words = _norm_words(text)
    leaked = sum(1 for passage in internal if _contains_passage(words, passage))
    if leaked:
        findings.append(
            Finding(
                "internal_memory",
                "high",
                "Passages from company memory marked internal-only",
                leaked,
            )
        )
    return findings


def extract_outbound(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The outbound message of a provider action: channel, recipient(s), subject and body."""
    params = payload.get("params") if isinstance(payload.get("params"), dict) else payload
    to = params.get("to")
    if isinstance(to, list):
        to = ", ".join(str(t) for t in to)
    return {
        "channel": str(params.get("channel") or action.split(".", 1)[0]),
        "to": str(to) if to else None,
        "subject": str(params["subject"]) if params.get("subject") else None,
        "body": str(params.get("body") or params.get("text") or params.get("content") or ""),
    }


def recipients(outbound: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(emails, domains) the message is addressed to: mentioning the recipient is not a leak."""
    emails: list[str] = []
    domains: list[str] = []
    for part in re.split(r"[,;\s]+", outbound.get("to") or ""):
        part = part.strip().strip("<>")
        if "@" in part:
            emails.append(part.lower())
        elif part:
            domains.append(part.lower())
    return emails, domains


def scan_outbound(
    db: Session,
    company_id: str,
    outbound: dict[str, Any],
    *,
    own_secrets: Iterable[str] = (),
    allowed_domains: Iterable[str] = (),
    extra_text: str = "",
) -> list[Finding]:
    emails, domains = recipients(outbound)
    text = "\n".join(
        part
        for part in (outbound.get("to"), outbound.get("subject"), outbound.get("body"), extra_text)
        if part
    )
    return scan_text(
        text,
        own_secrets=own_secrets,
        allowed_emails=emails,
        allowed_domains=[*domains, *allowed_domains],
        internal=internal_snippets(db, company_id),
    )
