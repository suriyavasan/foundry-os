"""Plain text, canonical URLs and hashes for radar items.

Everything an item stores passed through here. The text is the most hostile input the company reads,
so it is reduced to plain text: HTML removed, entities unescaped, control / zero-width / bidi
characters removed, whitespace collapsed, length capped, and the ``<untrusted`` / ``</untrusted``
sequences neutralised exactly as the runtime's ``wrap_untrusted`` does, so a stored item can never
close or fake an ``<untrusted>`` block later.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from app.radar import MAX_URL

_BLOCK_TAGS = frozenset(
    {
        "p",
        "br",
        "div",
        "li",
        "ul",
        "ol",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "table",
        "hr",
        "section",
        "article",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "template", "noscript", "head", "svg", "iframe"})
# Zero-width and bidirectional-control characters (category Cf, plus the invisible separators).
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­؜᠎ᅟᅠㅤ]")
_WS = re.compile(r"\s+")
_NON_PRINTABLE = frozenset({"Cc", "Cs", "Co", "Cn", "Zl", "Zp"})
_UNTRUSTED = re.compile(r"(?i)<(\s*/?\s*)untrusted")
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "ref",
        "ref_src",
        "ref_url",
        "mc_cid",
        "mc_eid",
        "igshid",
        "_hsenc",
        "_hsmi",
        "mkt_tok",
    }
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(value: str) -> str:
    """The text content of ``value`` (tags dropped, script/style/head content dropped, entities
    unescaped). Text without markup passes through unchanged apart from entity decoding."""
    if "<" not in value and "&" not in value:
        return value
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:  # malformed markup: fall back to a blunt tag strip
        return html.unescape(re.sub(r"<[^>]*>", " ", value))
    return "".join(parser.parts)


def plain_text(value: object) -> str:
    """Plain, single-spaced text (no length cap, ``<untrusted`` left alone): what injection
    detection looks at. ``""`` for ``None``."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = strip_html(text)
    text = html.unescape(text)  # a second pass: "&amp;lt;" is not markup but is still an entity
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE.sub("", text)
    # Control and other non-printable characters become spaces; all whitespace collapses below.
    text = "".join(" " if unicodedata.category(ch) in _NON_PRINTABLE else ch for ch in text)
    return _WS.sub(" ", text).strip()


def neutralize_untrusted(text: str) -> str:
    """Make ``<untrusted`` / ``</untrusted`` unusable as tags, exactly as the runtime's
    ``wrap_untrusted`` does, so a stored item cannot close or fake a block."""
    text = re.sub(r"(?i)<\s*/\s*untrusted", r"<\\/untrusted", text)
    return re.sub(r"(?i)<\s*untrusted", "< untrusted", text)


def cap_text(text: str, max_len: int) -> str:
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "\u2026"
    return text


def clean_text(value: object, max_len: int) -> str:
    """Plain text safe to store: see :func:`plain_text`, neutralised, capped at ``max_len``."""
    return cap_text(neutralize_untrusted(plain_text(value)), max_len)


def canonical_url(url: object) -> str | None:
    """A stable form of ``url`` for deduplication, or ``None`` when it is not an http(s) URL.

    Lower-case scheme and host, default port dropped, fragment dropped, ``utm_*`` and other tracking
    parameters dropped, query sorted, a trailing slash on the path removed (the root stays ``/``).
    """
    if not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw or len(raw) > 4 * MAX_URL:
        return None
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if parts.username or parts.password:
        return None
    scheme = parts.scheme.lower()
    host = host.lower().rstrip(".")
    netloc = host
    if ":" in host:  # IPv6 literal
        netloc = f"[{host}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        low = key.lower()
        if low.startswith("utm_"):
            continue
        if low in _TRACKING_PARAMS:
            continue
        query_pairs.append((key, value))
    query_pairs.sort()
    canonical = urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))
    if len(canonical) > MAX_URL:
        return None
    return canonical


def https_url(url: object) -> str | None:
    """``url`` when it is an absolute https URL (used for image URLs: never fetched by the UI)."""
    canonical = canonical_url(url)
    if canonical is None or not canonical.startswith("https://"):
        return None
    return canonical


def url_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    """Lower-case words only: punctuation, case and spacing do not make two titles different."""
    return " ".join(re.findall(r"[^\W_]+", title.lower()))


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def parse_datetime(value: object) -> datetime | None:
    """A timezone-aware UTC datetime from an RFC 2822 / ISO 8601 string or a unix timestamp."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,11}(\.\d+)?", text):
        return parse_datetime(float(text))
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
