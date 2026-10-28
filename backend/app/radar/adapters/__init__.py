"""Adapter registry: how each source type is requested and parsed.

An adapter is a parser (bytes to :class:`RawEntry` list) plus the facts the fetch job needs: whether
the control plane may fetch it at all (``search_engine`` never is), whether it needs a token, and
how the ``{placeholders}`` of its URL template are filled.

Placeholders (docs/specs/m16 section 5.2): ``{query}`` (a keyword, URL-encoded), ``{tag}`` (a
keyword slug), ``{topic}`` (a keyword), ``{since_unix}``, ``{yyyy-mm-dd}``; any other placeholder
is a value the owner chose (``template_values``), for example a Substack publication or a repo.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import quote

from app.radar import MAX_QUERIES_PER_SOURCE
from app.radar.adapters import json_apis
from app.radar.parsers import ParseResult, parse_feed
from app.radar.seed import placeholders


@dataclass(frozen=True)
class ParseContext:
    category: str
    slug: str
    now: datetime
    fetch_url: str


@dataclass(frozen=True)
class Adapter:
    name: str
    parse: Callable[[bytes, ParseContext], ParseResult]
    is_json: bool = False
    fetchable: bool = True
    requires_token: bool = False
    default_kind: str | None = None  # None = by the source's category
    date_offset_days: int = 7  # what ``{yyyy-mm-dd}`` means: now minus this many days


def _feed(body: bytes, ctx: ParseContext) -> ParseResult:
    return parse_feed(body)


def _never(body: bytes, ctx: ParseContext) -> ParseResult:  # pragma: no cover - guarded earlier
    raise RuntimeError("search_engine sources are never fetched")


ADAPTERS: dict[str, Adapter] = {
    a.name: a
    for a in (
        Adapter("feed", _feed),
        Adapter("hn_algolia", json_apis.hn_algolia, is_json=True),
        Adapter("lobsters_json", json_apis.lobsters_json, is_json=True),
        Adapter("devto_api", json_apis.devto_api, is_json=True),
        Adapter("github_releases", json_apis.github_releases, is_json=True, default_kind="release"),
        Adapter("github_search", json_apis.github_search, is_json=True),
        Adapter("github_issues", json_apis.github_issues, is_json=True, default_kind="question"),
        Adapter("stackexchange", json_apis.stackexchange, is_json=True, default_kind="question"),
        Adapter("arxiv", json_apis.arxiv, default_kind="paper"),
        Adapter(
            "hf_daily_papers",
            json_apis.hf_daily_papers,
            is_json=True,
            default_kind="paper",
            date_offset_days=0,
        ),
        Adapter("hf_papers", json_apis.hf_papers, is_json=True, default_kind="paper"),
        Adapter("semantic_scholar", json_apis.semantic_scholar, is_json=True, default_kind="paper"),
        Adapter("bsky_search", json_apis.bsky_search, is_json=True),
        Adapter("mastodon_tag", json_apis.mastodon_tag, is_json=True),
        Adapter(
            "github_graphql",
            json_apis.token_required,
            is_json=True,
            requires_token=True,
            fetchable=False,
        ),
        Adapter(
            "producthunt_graphql",
            json_apis.token_required,
            is_json=True,
            requires_token=True,
            fetchable=False,
        ),
        Adapter("search_engine", _never, fetchable=False),
    )
}

_CATEGORY_KIND = {
    "UI inspiration": "design",
    "changelog": "release",
    "research": "paper",
    "review site": "review",
    "news": "story",
    "launch site": "story",
}


def get(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise KeyError(f"unknown radar adapter {name!r}") from None


def default_kind(category: str, adapter: Adapter) -> str:
    return adapter.default_kind or _CATEGORY_KIND.get(category, "post")


def keyword_slug(term: str) -> str:
    """``{tag}`` form of a keyword: lower-case letters and digits only (Mastodon / dev.to tags)."""
    return re.sub(r"[^a-z0-9]", "", term.lower())


@dataclass(frozen=True)
class UrlPlan:
    urls: list[str]
    # The keyword each URL asks for (parallel to ``urls``); ``None`` for a one-fetch source.
    terms: list[str | None] = field(default_factory=list)
    error: str | None = None  # set when the template cannot be filled (a CONFIG_ERROR)
    no_keywords: bool = False  # a per-keyword source and nothing to ask for


def build_urls(
    template: str | None,
    *,
    per_keyword: bool,
    terms: list[str],
    values: dict[str, str],
    now: datetime,
    since: datetime,
    adapter: Adapter,
) -> UrlPlan:
    """The request URLs for one source: one URL, or one per keyword (at most six)."""
    if not template:
        return UrlPlan([], error="the source has no fetch URL")
    names = placeholders(template)
    custom = [n for n in names if n not in ("query", "tag", "topic", "since_unix", "yyyy-mm-dd")]
    missing = [n for n in custom if not str(values.get(n, "")).strip()]
    if missing:
        return UrlPlan([], error=f"missing value for {{{missing[0]}}}")
    base = template.replace(">", "%3E").replace("<", "%3C")
    fixed: dict[str, str] = {n: quote(str(values[n]).strip(), safe="") for n in custom}
    fixed["since_unix"] = str(int(since.timestamp()))
    fixed["yyyy-mm-dd"] = (now - timedelta(days=adapter.date_offset_days)).strftime("%Y-%m-%d")

    def fill(text: str, keyword: str | None) -> str | None:
        local = dict(fixed)
        if keyword is not None:
            local["query"] = quote(keyword, safe="")
            local["topic"] = quote(keyword, safe="")
            slug = keyword_slug(keyword)
            if not slug and "tag" in names:
                return None
            local["tag"] = slug
        return re.sub(
            r"\{([A-Za-z_][A-Za-z0-9_\-]*)\}", lambda m: local.get(m.group(1), m.group(0)), text
        )

    uses_keywords = any(n in ("query", "tag", "topic") for n in names)
    if not uses_keywords:
        return UrlPlan([fill(base, None) or base], [None])
    if not per_keyword:
        return UrlPlan(
            [], error="the URL has a keyword placeholder but the source is not per-keyword"
        )
    urls: list[str] = []
    used: list[str | None] = []
    for term in terms:
        url = fill(base, term)
        if url and url not in urls:
            urls.append(url)
            used.append(term)
        if len(urls) >= MAX_QUERIES_PER_SOURCE:
            break
    return UrlPlan(urls, used, no_keywords=not urls)
