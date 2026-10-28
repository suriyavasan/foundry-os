"""Feed parsing: RSS 2.0, RSS 1.0 (RDF), Atom and JSON Feed into :class:`RawEntry` lists.

XML is parsed with ``defusedxml`` (no entity expansion, no external references): a feed is input
from the open internet and billion-laughs and XXE payloads are refused as :class:`ParseError`.
Nothing here touches the network or the database. Text fields are returned raw (possibly HTML);
``normalize`` reduces them to plain text before anything is stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.radar.normalize import https_url, parse_datetime

MAX_ENTRIES = 200
_RAW_TEXT_CAP = 20_000  # characters kept of a description/content before cleaning

NS_ATOM = "{http://www.w3.org/2005/Atom}"
NS_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
NS_RSS1 = "{http://purl.org/rss/1.0/}"
NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
NS_MEDIA = "{http://search.yahoo.com/mrss/}"

_IMG = re.compile(r"""(?is)<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""")


class ParseError(Exception):
    """The body is not a feed (or not JSON/XML at all). The message never contains the body."""


class NeedsToken(ParseError):
    """The adapter needs an API token the control plane does not have."""


@dataclass
class RawEntry:
    """One item as the source gave it, before normalisation."""

    url: str
    title: str
    excerpt: str = ""
    author: str | None = None
    published_at: datetime | None = None
    external_id: str | None = None
    kind: str | None = None  # None = the adapter's / category's default
    image_url: str | None = None
    engagement: dict[str, int] = field(default_factory=dict)


@dataclass
class ParseResult:
    entries: list[RawEntry]
    # A hint from the provider body itself (Stack Exchange ``backoff``): seconds to leave the host.
    backoff_seconds: int | None = None


def _text(element: Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip() if len(element) else (element.text or "").strip()


def _find_text(parent: Element, *names: str) -> str:
    for name in names:
        found = parent.find(name)
        if found is not None:
            value = _text(found)
            if value:
                return value
    return ""


def _first_image(parent: Element, html_blobs: list[str]) -> str | None:
    for child in parent.iter():
        tag = child.tag
        if tag in (f"{NS_MEDIA}content", f"{NS_MEDIA}thumbnail"):
            medium = child.get("medium") or ""
            ctype = child.get("type") or ""
            if (
                tag.endswith("thumbnail")
                or medium == "image"
                or ctype.startswith("image/")
                or not (medium or ctype)
            ):
                got = https_url(child.get("url"))
                if got:
                    return got
        elif tag == "enclosure" and (child.get("type") or "").startswith("image/"):
            got = https_url(child.get("url"))
            if got:
                return got
        elif tag == f"{NS_ATOM}link" and child.get("rel") == "enclosure":
            if (child.get("type") or "").startswith("image/"):
                got = https_url(child.get("href"))
                if got:
                    return got
    for blob in html_blobs:
        for match in _IMG.finditer(blob[:_RAW_TEXT_CAP]):
            got = https_url(match.group(1).strip())
            if got:
                return got
    return None


def _atom_link(entry: Element) -> str:
    fallback = ""
    for link in entry.findall(f"{NS_ATOM}link"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        rel = link.get("rel") or "alternate"
        if rel == "alternate":
            if (link.get("type") or "text/html").startswith(("text/html", "application/xhtml")):
                return href
            fallback = fallback or href
        elif not fallback and rel not in ("self", "enclosure", "edit", "replies", "related"):
            fallback = href
    return fallback


def _parse_rss_item(item: Element) -> RawEntry | None:
    title = _find_text(item, "title", f"{NS_RSS1}title", f"{NS_DC}title")
    link = _find_text(item, "link", f"{NS_RSS1}link")
    guid_el = item.find("guid")
    guid = _text(guid_el)
    permalink = guid_el is None or guid_el.get("isPermaLink", "true").lower() != "false"
    if not link and guid.startswith(("http://", "https://")) and permalink:
        link = guid
    if not link:
        link = _find_text(item, f"{NS_ATOM}link")
        if not link:
            for el in item.findall(f"{NS_ATOM}link"):
                link = (el.get("href") or "").strip()
                if link:
                    break
    if not title and not link:
        return None
    description = _find_text(item, "description", f"{NS_RSS1}description", f"{NS_DC}description")
    content = _find_text(item, f"{NS_CONTENT}encoded")
    excerpt = description or content
    author = _find_text(item, f"{NS_DC}creator", "author", f"{NS_DC}author")
    published = parse_datetime(_find_text(item, "pubDate", f"{NS_DC}date", "date"))
    return RawEntry(
        url=link,
        title=title or link,
        excerpt=excerpt[:_RAW_TEXT_CAP],
        author=author or None,
        published_at=published,
        external_id=guid or None,
        image_url=_first_image(item, [content, description]),
    )


def _parse_atom_entry(entry: Element) -> RawEntry | None:
    title = _find_text(entry, f"{NS_ATOM}title")
    link = _atom_link(entry)
    if not title and not link:
        return None
    summary = _find_text(entry, f"{NS_ATOM}summary")
    content = _find_text(entry, f"{NS_ATOM}content")
    author = ""
    author_el = entry.find(f"{NS_ATOM}author")
    if author_el is not None:
        author = _find_text(author_el, f"{NS_ATOM}name") or _text(author_el)
    published = parse_datetime(
        _find_text(entry, f"{NS_ATOM}published", f"{NS_ATOM}updated", f"{NS_DC}date")
    )
    return RawEntry(
        url=link,
        title=title or link,
        excerpt=(summary or content)[:_RAW_TEXT_CAP],
        author=author or None,
        published_at=published,
        external_id=_find_text(entry, f"{NS_ATOM}id") or None,
        image_url=_first_image(entry, [content, summary]),
    )


def _parse_xml(body: bytes) -> list[RawEntry]:
    try:
        root = SafeET.fromstring(body)
    except DefusedXmlException as exc:
        raise ParseError(f"unsafe XML refused ({type(exc).__name__})") from exc
    except (SafeET.ParseError, ValueError) as exc:
        raise ParseError("not well-formed XML") from exc
    tag = root.tag
    entries: list[RawEntry | None] = []
    if tag == "rss" or tag.endswith("}rss"):
        channel = root.find("channel")
        items = (channel if channel is not None else root).findall("item")
        entries = [_parse_rss_item(i) for i in items[:MAX_ENTRIES]]
    elif tag == f"{NS_RDF}RDF":
        items = root.findall(f"{NS_RSS1}item") or root.findall("item")
        entries = [_parse_rss_item(i) for i in items[:MAX_ENTRIES]]
    elif tag == f"{NS_ATOM}feed":
        entries = [_parse_atom_entry(e) for e in root.findall(f"{NS_ATOM}entry")[:MAX_ENTRIES]]
    elif tag == f"{NS_ATOM}entry":
        entries = [_parse_atom_entry(root)]
    else:
        raise ParseError("XML document is not an RSS, RDF or Atom feed")
    return [e for e in entries if e is not None]


def _json_authors(item: dict[str, Any]) -> str | None:
    authors = item.get("authors")
    if isinstance(authors, list) and authors and isinstance(authors[0], dict):
        name = authors[0].get("name")
        if isinstance(name, str):
            return name
    author = item.get("author")
    if isinstance(author, dict) and isinstance(author.get("name"), str):
        return author["name"]
    return None


def _parse_json_feed(data: Any) -> list[RawEntry]:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ParseError("JSON document is not a JSON Feed")
    out: list[RawEntry] = []
    for item in data["items"][:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("external_url") or ""
        title = item.get("title") or ""
        if not (isinstance(url, str) and url) and not (isinstance(title, str) and title):
            continue
        body = item.get("content_html") or item.get("content_text") or ""
        summary = item.get("summary")
        excerpt = summary if isinstance(summary, str) and summary else body
        out.append(
            RawEntry(
                url=url if isinstance(url, str) else "",
                title=title if isinstance(title, str) and title else str(url),
                excerpt=excerpt[:_RAW_TEXT_CAP] if isinstance(excerpt, str) else "",
                author=_json_authors(item),
                published_at=parse_datetime(
                    item.get("date_published") or item.get("date_modified")
                ),
                external_id=str(item["id"]) if item.get("id") is not None else None,
                image_url=https_url(item.get("image") or item.get("banner_image")),
            )
        )
    return out


def parse_feed(body: bytes) -> ParseResult:
    """RSS / RDF / Atom / JSON Feed to entries. Raises :class:`ParseError`."""
    stripped = body.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not stripped:
        raise ParseError("empty response")
    if stripped[:1] in (b"{", b"["):
        try:
            data = json.loads(stripped.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise ParseError("not valid JSON") from exc
        return ParseResult(_parse_json_feed(data))
    if stripped[:1] != b"<":
        raise ParseError("response is neither XML nor JSON")
    return ParseResult(_parse_xml(stripped))
