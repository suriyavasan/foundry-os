"""``company_radar_ui_search``: design references from a free Mobbin alternative (docs/specs/m16 3).

The control plane asks One Page Love's public MCP endpoint (``search_inspiration``, or
``search_sections`` when a page section is named; ``image_mode: markdown`` so screenshots come back
as public URLs instead of base64) and falls back to Siteinspire's public MCP (``search_sites``).
Both are plain JSON-RPC ``tools/call`` POSTs without a key; One Page Love answers as a one-line
server-sent event, Siteinspire as plain JSON, and this module reads either.

* The query and the server's IP go to a third party, which says it logs them (owner decision 5).
  A guardrail ``deny`` rule on the action ``radar.ui_search`` turns the tool off.
* Results are references (title, live URL, permalink, hero copy, a screenshot URL for
  ``vision_analyze``); the screenshots are never fetched or stored here, and agents are told to keep
  links and notes, not copies. Text is returned in ``content`` (UNTRUSTED).
* Cached 24 hours per query in this process (a restart empties the cache). Bounded at 200 entries.
* NOT verified against the live services in this repository (no real network in tests); the parser
  accepts JSON or markdown in the tool result and degrades to "unavailable" instead of guessing.
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.radar import access
from app.radar import fetcher as fetch_mod
from app.radar.normalize import canonical_url, cap_text, https_url, neutralize_untrusted, plain_text
from app.schemas.radar import AgentUiSearchIn
from app.services.agent_auth import AgentRunContext

OPL_URL = "https://api.onepagelove.com/mcp"
SITEINSPIRE_URL = "https://www.siteinspire.com/api/mcp"
CACHE_TTL = timedelta(hours=24)
CACHE_MAX = 200
MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
ACCEPT = "application/json, text/event-stream"
MAX_CONTENT = 600

_cache: OrderedDict[tuple[str, str, str, int], tuple[datetime, dict[str, Any]]] = OrderedDict()
_lock = threading.Lock()

# Replaced by tests: the transport used for both services (production: the SSRF guard).
transport_factory: Callable[[], httpx.BaseTransport] | None = None


class _Failure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _transport() -> httpx.BaseTransport:
    return (transport_factory or fetch_mod.transport_factory)()


def _rpc(url: str, tool: str, arguments: dict[str, Any]) -> Any:
    """One ``tools/call``; returns the decoded tool result (JSON, else its text)."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": ACCEPT,
        "User-Agent": fetch_mod.user_agent("ui-search"),
    }
    try:
        client = httpx.Client(transport=_transport(), follow_redirects=False, timeout=TIMEOUT)
        with client, client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code != 200:
                raise _Failure(f"HTTP {response.status_code}")
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise _Failure("response too large")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise _Failure(type(exc).__name__) from exc
    text = b"".join(chunks).decode("utf-8", errors="replace")
    message = _message(text)
    if message is None or "error" in message and message["error"]:
        raise _Failure("error response")
    return _tool_result(message.get("result"))


def _message(text: str) -> dict[str, Any] | None:
    """The JSON-RPC message of a plain JSON body or of a server-sent-event stream."""
    text = text.strip()
    candidates = [text]
    candidates += [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _tool_result(result: Any) -> Any:
    if isinstance(result, dict) and result.get("isError"):
        raise _Failure("tool error")
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        texts = [
            c.get("text", "")
            for c in result["content"]
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t)
        try:
            return json.loads(joined)
        except ValueError:
            return joined
    return result


# ---------------------------------------------------------------- reading results

_MD_LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]+)\)")
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\((https://[^)\s]+)\)")


def _first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = d.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "sites", "websites", "sections", "data"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def _copy(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "name", "headline", "subtext", "subheadline", "description", "tagline"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    ctas = row.get("ctas") or row.get("cta")
    if isinstance(ctas, list):
        parts.append("CTAs: " + ", ".join(str(c) for c in ctas[:4]))
    elif isinstance(ctas, str):
        parts.append(f"CTA: {ctas}")
    for key in ("categories", "tags", "style", "genre", "type"):
        value = row.get(key)
        if isinstance(value, list) and value:
            parts.append(f"{key}: " + ", ".join(str(v) for v in value[:6]))
        elif isinstance(value, str) and value:
            parts.append(f"{key}: {value}")
    text = " | ".join(dict.fromkeys(plain_text(p) for p in parts if p))
    return neutralize_untrusted(cap_text(text, MAX_CONTENT))


def _reference(source: str, row: dict[str, Any]) -> dict[str, Any] | None:
    live = canonical_url(_first(row, "url", "live_url", "liveUrl", "website", "site_url", "link"))
    permalink = canonical_url(
        _first(row, "permalink", "gallery_url", "galleryUrl", "page", "slug_url")
    )
    shot = https_url(
        _first(
            row, "screenshot_url", "screenshotUrl", "screenshot", "image", "image_url", "thumbnail"
        )
    )
    content = _copy(row)
    if not content or not (live or permalink):
        return None
    return {
        "source": source,
        "live_url": live,
        "permalink": permalink,
        "screenshot_url": shot,
        "content": content,
    }


def _from_markdown(source: str, text: str) -> list[dict[str, Any]]:
    """A tool result that is markdown: each link becomes a reference; an image on the same or the
    next line is its screenshot."""
    out: list[dict[str, Any]] = []
    lines = text.splitlines()
    for n, line in enumerate(lines):
        match = _MD_LINK.search(line)
        if not match or line.lstrip().startswith("!"):
            continue
        shot = _MD_IMAGE.search(line) or (
            _MD_IMAGE.search(lines[n + 1]) if n + 1 < len(lines) else None
        )
        ref = _reference(
            source,
            {
                "title": match.group(1),
                "url": match.group(2),
                "screenshot_url": shot.group(1) if shot else None,
            },
        )
        if ref:
            out.append(ref)
    return out


def parse_results(source: str, data: Any, limit: int) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in _rows(data):
        ref = _reference(source, row)
        if ref:
            refs.append(ref)
    if not refs and isinstance(data, str):
        refs = _from_markdown(source, data)
    seen: set[str] = set()
    unique = []
    for ref in refs:
        key = ref["live_url"] or ref["permalink"] or ""
        if key and key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique[:limit]


# ---------------------------------------------------------------- the two services


def _opl(data: AgentUiSearchIn) -> list[dict[str, Any]]:
    if data.section:
        args: dict[str, Any] = {
            "section_type": data.section,
            "query": data.query,
            "limit": data.limit,
            "image_mode": "markdown",
        }
        tool = "search_sections"
    else:
        args = {"query": data.query, "limit": data.limit, "image_mode": "markdown"}
        tool = "search_inspiration"
    return parse_results("onepagelove", _rpc(OPL_URL, tool, args), data.limit)


def _siteinspire(data: AgentUiSearchIn) -> list[dict[str, Any]]:
    query = f"{data.section} {data.query}" if data.section else data.query
    args = {"query": query, "limit": data.limit}
    return parse_results("siteinspire", _rpc(SITEINSPIRE_URL, "search_sites", args), data.limit)


def _cache_key(rc: AgentRunContext, data: AgentUiSearchIn) -> tuple[str, str, str, int]:
    return (
        rc.ctx.company_id,
        " ".join(data.query.lower().split()),
        (data.section or "").lower(),
        data.limit,
    )


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def search(
    db: Session, rc: AgentRunContext, data: AgentUiSearchIn, *, now: datetime | None = None
) -> dict[str, Any]:
    """Design references for ``data.query``. Never raises for a service failure: the answer says
    ``available: false`` and why, so the designer falls back to reading competitor sites."""
    access.require(db, rc.agent, access.UI, "search design inspiration")
    now = now or utcnow()
    key = _cache_key(rc, data)
    with _lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL:
            _cache.move_to_end(key)
            return {**hit[1], "cached": True}
    notes: list[str] = []
    for source, call in (("onepagelove", _opl), ("siteinspire", _siteinspire)):
        try:
            results = call(data)
        except _Failure as exc:
            notes.append(f"{source}: {exc.reason}")
            continue
        if results:
            answer = {
                "available": True,
                "source": source,
                "cached": False,
                "note": ("Fallback service used (the first one had no answer)" if notes else None),
                "results": results,
            }
            with _lock:
                _cache[key] = (now, answer)
                _cache.move_to_end(key)
                while len(_cache) > CACHE_MAX:
                    _cache.popitem(last=False)
            return answer
        notes.append(f"{source}: no results")
    return {
        "available": False,
        "source": None,
        "cached": False,
        "note": "No design references were found (" + "; ".join(notes) + "). Read competitor "
        "sites with company_radar_read instead.",
        "results": [],
    }
