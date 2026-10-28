"""``company_radar_read``: a control-plane page reader for every agent (docs/specs/m16 F1/L4).

Agents can search but could not read a page (Hermes' ``web_extract`` is never granted: the keyless
backend cannot extract). The control plane reads it instead, through the radar's own fetcher:
SSRF-guarded transport (no private, link-local or metadata address, every redirect re-checked, at
most 3), robots.txt honoured, per-host spacing, a 1 MB body cap, HTML only, the daily request
budget. What comes back is plain text: title, description, headings and the main text (12,000
characters at most), up to 10 absolute ``https`` image URLs for ``vision_analyze`` and ``og:image``.

Everything textual is returned in ``content`` (UNTRUSTED: the runtime wraps it) and checked for
instruction-like phrases. A page that belongs to a radar item flagged for such text is read only
when the agent names that item explicitly.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import (
    AppError,
    Forbidden,
    LimitExceeded,
    RateLimited,
    UpstreamError,
    ValidationFailed,
)
from app.core.time import utcnow
from app.models.radar import FetchStatus, RadarItem
from app.radar import MAX_PAGE_BYTES, access, jobs
from app.radar import fetcher as fetch_mod
from app.radar.normalize import (
    canonical_url,
    cap_text,
    https_url,
    neutralize_untrusted,
    plain_text,
    url_hash,
)
from app.services import email_identity, policy
from app.services.agent_auth import AgentRunContext

MAX_TEXT_CHARS = 12_000
MAX_IMAGES = 10
MAX_HEADINGS = 20
PAGE_ACCEPT = "text/html, application/xhtml+xml;q=0.9, */*;q=0.1"
_BLOCKS = frozenset(
    [
        "p",
        "div",
        "section",
        "article",
        "main",
        "li",
        "ul",
        "ol",
        "br",
        "tr",
        "td",
        "th",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "header",
        "footer",
        "nav",
        "aside",
        "form",
        "figure",
        "figcaption",
        "dd",
        "dt",
        "dl",
        "hr",
    ]
)
_SKIPPED = frozenset({"script", "style", "template", "noscript", "svg", "iframe", "head", "canvas"})
_CHROME = frozenset({"nav", "footer", "aside", "form", "header"})


class RobotsRefused(Forbidden):
    code = "robots_disallowed"
    default_message = "robots.txt does not allow reading this page"


class _PageParser(HTMLParser):
    """Title, description, headings, image URLs and the readable text of one HTML page."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.description = ""
        self.og_image: str | None = None
        self.headings: list[str] = []
        self.images: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._chrome = 0
        self._in_title = False
        self._heading: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:description") and not self.description:
                self.description = a.get("content", "")
            elif key == "og:image" and self.og_image is None:
                self.og_image = a.get("content") or None
            return
        if tag == "title":
            self._in_title = True
        if tag in _SKIPPED:
            if tag != "head":  # head holds title and meta; keep parsing inside it
                self._skip += 1
            return
        if tag in _CHROME:
            self._chrome += 1
        if tag in ("h1", "h2", "h3"):
            self._heading = []
        if tag == "img" and len(self.images) < MAX_IMAGES * 3:
            src = a.get("src") or a.get("data-src") or ""
            if src and not src.startswith("data:"):
                self.images.append(urljoin(self.base_url, src))
        if tag in _BLOCKS and not self._skip:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIPPED and tag != "head":
            self._skip = max(0, self._skip - 1)
            return
        if tag in _CHROME:
            self._chrome = max(0, self._chrome - 1)
        if tag in ("h1", "h2", "h3") and self._heading is not None:
            heading = plain_text(" ".join(self._heading))
            if heading and len(self.headings) < MAX_HEADINGS:
                self.headings.append(heading[:200])
            self._heading = None
        if tag in _BLOCKS and not self._skip:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._heading is not None:
            self._heading.append(data)
        if not self._chrome:
            self.text.append(data)


def _decode(body: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip().strip("\"'")
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def extract(html_text: str, base_url: str) -> dict[str, Any]:
    """Parse one HTML document into the fields ``read_page`` returns (no network)."""
    parser = _PageParser(base_url)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # malformed markup: keep whatever was parsed
        pass
    # Text: block boundaries become newlines; collapse each line, drop blanks.
    raw = "".join(parser.text)
    lines = [plain_text(line) for line in raw.split("\n")]
    body = "\n".join(line for line in lines if line)
    images: list[str] = []
    for src in ([parser.og_image] if parser.og_image else []) + parser.images:
        url = https_url(urljoin(base_url, src)) if src else None
        if url and url not in images:
            images.append(url)
        if len(images) >= MAX_IMAGES:
            break
    og = https_url(urljoin(base_url, parser.og_image)) if parser.og_image else None
    return {
        "title": plain_text(parser.title)[:300],
        "description": plain_text(parser.description)[:500],
        "headings": parser.headings,
        "text": body,
        "images": images,
        "og_image": og,
    }


def _compose(page: dict[str, Any]) -> tuple[str, bool]:
    head = []
    if page["title"]:
        head.append(f"Title: {page['title']}")
    if page["description"]:
        head.append(f"Description: {page['description']}")
    if page["headings"]:
        head.append("Headings:\n" + "\n".join(f"- {h}" for h in page["headings"]))
    prefix = "\n".join(head)
    room = max(0, MAX_TEXT_CHARS - len(prefix) - 2)
    text = page["text"]
    truncated = len(text) > room
    content = f"{prefix}\n\n{text[:room]}".strip() if prefix else text[:room]
    return neutralize_untrusted(cap_text(content, MAX_TEXT_CHARS)), truncated


def _check_flagged_item(
    db: Session, company_id: str, url: str, item_id: str | None
) -> RadarItem | None:
    """A radar item already stored for this URL that was flagged: reading it needs its id."""
    canonical = canonical_url(url)
    named = None
    if item_id:
        named = db.scalar(
            select(RadarItem).where(RadarItem.id == item_id, RadarItem.company_id == company_id)
        )
        if named is None:
            raise ValidationFailed("That radar item does not exist in this company")
    if canonical is None:
        return named
    known = db.scalar(
        select(RadarItem).where(
            RadarItem.company_id == company_id, RadarItem.url_hash == url_hash(canonical)
        )
    )
    if known is not None and known.injection_flags and (named is None or named.id != known.id):
        raise Forbidden(
            "This page belongs to a radar item flagged for instruction-like text. Read it only "
            "if you really need it, by passing that item's id as item_id",
            details={"reason": "flagged_item", "item_id": known.id},
        )
    return named


def _raise_for(result: fetch_mod.FetchResult) -> None:
    status = result.status
    if status == FetchStatus.ROBOTS_DISALLOWED.value:
        raise RobotsRefused(result.error or None, details={"url": result.final_url})
    if status == FetchStatus.BLOCKED_DESTINATION.value:
        raise ValidationFailed(
            "That address is not a public web page (private, local and metadata addresses are "
            "refused)",
            details={"reason": status},
        )
    if status == FetchStatus.RATE_LIMITED.value:
        raise RateLimited(
            "The site asked us to slow down",
            details={"retry_after_seconds": result.retry_after_seconds, "reason": status},
        )
    raise UpstreamError(
        f"The page could not be read ({status.replace('_', ' ')}"
        + (f", HTTP {result.http_status}" if result.http_status else "")
        + ")",
        details={"reason": status, "http_status": result.http_status},
    )


def read_page(
    db: Session,
    session_factory: sessionmaker[Session],
    rc: AgentRunContext,
    url: str,
    item_id: str | None = None,
    *,
    clock: Any = utcnow,
) -> dict[str, Any]:
    """Read one public page. Raises ``AppError`` subclasses; never returns a raw response."""
    ctx = rc.ctx
    access.require(db, rc.agent, access.PAGE, "read web pages")
    canonical = canonical_url(url)
    if canonical is None:
        raise ValidationFailed("url must be a public http(s) address without credentials")
    _check_flagged_item(db, ctx.company_id, url, item_id)
    contact = policy.load_policy(db, ctx.company_id).radar_contact_email
    db.commit()  # end the transaction (the gate's rows): the fetch below takes its own sessions
    try:
        with jobs.make_fetcher(
            session_factory, ctx.company_id, clock, contact_email=contact, require_enabled=False
        ) as fetcher:
            result = fetcher.fetch(
                canonical,
                accept=PAGE_ACCEPT,
                max_bytes=MAX_PAGE_BYTES,
                allowed_types=fetch_mod.HTML_TYPES,
            )
    except fetch_mod.HostBusy as exc:
        wait = max(1, int((exc.until - clock()).total_seconds()))
        raise RateLimited(
            "That site is busy; try again shortly",
            details={"retry_after_seconds": min(wait, 3600), "reason": "host_busy"},
        ) from exc
    except fetch_mod.BudgetExhausted as exc:
        raise LimitExceeded(
            "The company's daily radar request budget is spent",
            details={"limit": "radar_max_fetches_per_day"},
        ) from exc
    except fetch_mod.FetchAborted as exc:
        raise AppError("Fetching is paused (company paused or emergency stop)") from exc
    if not result.ok:
        _raise_for(result)
    base = result.final_url or canonical
    page = extract(_decode(result.body, result.content_type), base)
    content, truncated = _compose(page)
    flags = email_identity.detect_injection(content)
    return {
        "url": canonical,
        "final_url": base,
        "status": result.http_status or 200,
        "flagged": bool(flags),
        "flags": flags,
        "truncated": truncated,
        "og_image": page["og_image"],
        "images": page["images"],
        "content": content,
    }
