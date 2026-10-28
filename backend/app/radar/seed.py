"""The 100 seed sources (``seed_sources.json``) and how they become ``radar_sources`` rows.

The JSON is the research output of M16 (checked once with curl and ``urllib.robotparser`` on
2026-10-27). This module only maps it:

* a source is **enabled** when the control plane can fetch it (a feed or a documented JSON API,
  robots.txt allowing the exact path, no token and no owner-chosen value needed);
* a ``search_engine_only`` source is **enabled but never fetched**: it is a ``site:`` query template
  handed to agents, who reach it through ``web_search`` (snippets only);
* everything else is seeded **disabled with a reason** (a token is needed, the terms restrict
  automated use, the owner must pick a handle, it was unreachable when checked).

Re-seeding never overwrites a row the owner edited and never switches a source on or off.

Importing this module is cheap (the migration uses it): models are imported inside the functions.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.radar import (
    DAILY_INTERVAL_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    SEED_VERSION,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SEED_FILE = Path(__file__).with_name("seed_sources.json")

CATEGORIES = (
    "social",
    "forum",
    "news",
    "blog",
    "launch site",
    "review site",
    "UI inspiration",
    "research",
    "changelog",
)
ACCESS_METHODS = ("rss", "atom", "json_api", "json_api_free_token", "search_engine_only")
# Every adapter name a seed (or an owner) may use; ``app.radar.adapters`` implements them.
ADAPTER_NAMES = (
    "feed",
    "hn_algolia",
    "lobsters_json",
    "devto_api",
    "github_releases",
    "github_search",
    "github_issues",
    "stackexchange",
    "arxiv",
    "hf_daily_papers",
    "hf_papers",
    "semantic_scholar",
    "bsky_search",
    "mastodon_tag",
    "github_graphql",
    "producthunt_graphql",
    "search_engine",
)
KEYWORD_PLACEHOLDERS = ("query", "tag", "topic")
BUILTIN_PLACEHOLDERS = (*KEYWORD_PLACEHOLDERS, "since_unix", "yyyy-mm-dd")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_\-]*)\}")


@dataclass(frozen=True)
class SeedSource:
    slug: str
    name: str
    url: str
    category: str
    access_method: str
    adapter: str
    fetch_url: str | None
    search_template: str | None
    query_mode: str
    keep_unmatched: bool
    enabled: bool
    disabled_reason: str | None
    interval_seconds: int
    tos_note: str
    rate_note: str
    template_values: tuple[str, ...]


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:80] or "source"


def placeholders(template: str | None) -> list[str]:
    """Names of the ``{placeholders}`` in a URL template, in order, without duplicates."""
    if not template:
        return []
    return list(dict.fromkeys(_PLACEHOLDER.findall(template)))


def custom_placeholders(template: str | None) -> list[str]:
    """Placeholders the owner has to give a value for (everything but the built-in ones)."""
    return [p for p in placeholders(template) if p not in BUILTIN_PLACEHOLDERS]


def uses_keywords(template: str | None) -> bool:
    return any(p in KEYWORD_PLACEHOLDERS for p in placeholders(template))


def _disabled_reason(text: str | None) -> str:
    low = (text or "").lower()
    if low.startswith("search_engine_only"):
        return "search_only"
    if low.startswith("needs_token"):
        return "needs_token"
    if low.startswith("template needs"):
        return "needs_values"
    if low.startswith("not reachable"):
        return "unreachable"
    return "terms"


def _search_template(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.rstrip("/")
    scope = host
    if host.endswith("reddit.com") and path.startswith("/r/"):
        scope = f"{host}{path}"
    return f'site:{scope} "{{query}}"'


def _interval(category: str, adapter: str) -> int:
    if category in ("research", "review site", "UI inspiration"):
        return DAILY_INTERVAL_SECONDS
    return DEFAULT_INTERVAL_SECONDS


def _map(entry: dict[str, Any]) -> SeedSource:
    adapter = entry["adapter"]
    raw_fetch = (entry.get("fetch_url") or "").strip()
    # Two descriptive entries append the GraphQL query after the URL; keep the URL only.
    fetch_url = raw_fetch.split(" ")[0] if raw_fetch and raw_fetch != "-" else None
    search_only = adapter == "search_engine"
    if search_only:
        fetch_url = None
    seed_enabled = bool(entry.get("seed_enabled"))
    enabled = seed_enabled or search_only
    reason: str | None = None
    if not enabled:
        reason = _disabled_reason(entry.get("seed_disabled_reason"))
    category = entry["category"]
    values = tuple(entry.get("template_values") or ())
    return SeedSource(
        slug=slugify(entry["name"]),
        name=entry["name"][:160],
        url=entry["url"][:500],
        category=category,
        access_method=entry["access_method"],
        adapter=adapter,
        fetch_url=fetch_url,
        search_template=_search_template(entry["url"]) if search_only else None,
        query_mode="per_keyword" if uses_keywords(fetch_url) else "none",
        keep_unmatched=category in ("changelog", "UI inspiration"),
        enabled=enabled and (search_only or fetch_url is not None),
        disabled_reason=reason,
        interval_seconds=_interval(category, adapter),
        tos_note=(entry.get("tos_automated") or "")[:300],
        rate_note=(entry.get("rate_limit") or "")[:300],
        template_values=values,
    )


@lru_cache(maxsize=1)
def load_seed_sources() -> tuple[SeedSource, ...]:
    """The validated seed list. Raises ``ValueError`` when the file is inconsistent (a test runs
    this, so a bad edit fails CI instead of a company's first start)."""
    raw = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    sources = tuple(_map(entry) for entry in raw)
    slugs = [s.slug for s in sources]
    if len(set(slugs)) != len(slugs):
        dup = sorted({s for s in slugs if slugs.count(s) > 1})
        raise ValueError(f"duplicate seed slugs: {dup}")
    for s in sources:
        if s.category not in CATEGORIES:
            raise ValueError(f"{s.slug}: unknown category {s.category!r}")
        if s.access_method not in ACCESS_METHODS:
            raise ValueError(f"{s.slug}: unknown access method {s.access_method!r}")
        if s.adapter not in ADAPTER_NAMES:
            raise ValueError(f"{s.slug}: unknown adapter {s.adapter!r}")
        if s.fetch_url and not s.fetch_url.startswith("https://"):
            raise ValueError(f"{s.slug}: a seed fetch URL must be https")
        if s.enabled and custom_placeholders(s.fetch_url):
            raise ValueError(f"{s.slug}: enabled but its template needs owner values")
    return sources


def source_columns(seed: SeedSource) -> dict[str, Any]:
    """Column values of a ``radar_sources`` row for this seed (no id, company or timestamps)."""
    return {
        "slug": seed.slug,
        "name": seed.name,
        "url": seed.url,
        "category": seed.category,
        "access_method": seed.access_method,
        "adapter": seed.adapter,
        "fetch_url": seed.fetch_url,
        "search_template": seed.search_template,
        "query_mode": seed.query_mode,
        "keep_unmatched": seed.keep_unmatched,
        "enabled": seed.enabled,
        "origin": "seed",
        "seed_version": SEED_VERSION,
        "edited_by_owner": False,
        "interval_seconds": seed.interval_seconds,
        "tos_note": seed.tos_note,
        "rate_note": seed.rate_note,
        "robots_override": False,
        "template_values": {},
        "consecutive_failures": 0,
        "disabled_reason": seed.disabled_reason,
        "items_total": 0,
    }


@dataclass
class SeedReport:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


# Descriptive fields a re-seed may refresh on an unedited seed row. ``enabled`` and the failure
# bookkeeping are never touched: the owner and the fetcher own them.
_REFRESHABLE = (
    "name",
    "url",
    "category",
    "access_method",
    "adapter",
    "fetch_url",
    "search_template",
    "query_mode",
    "keep_unmatched",
    "tos_note",
    "rate_note",
)


def seed_company(db: Session, company_id: str, *, now: datetime | None = None) -> SeedReport:
    """Insert the seed sources a company does not have yet; refresh unedited seed rows whose
    ``seed_version`` is older. Idempotent. No commit (the caller's transaction owns it)."""
    from sqlalchemy import select

    from app.core.time import utcnow
    from app.models.radar import RadarSource

    now = now or utcnow()
    existing = {
        row.slug: row
        for row in db.scalars(select(RadarSource).where(RadarSource.company_id == company_id))
    }
    report = SeedReport()
    for seed in load_seed_sources():
        cols = source_columns(seed)
        row = existing.get(seed.slug)
        if row is None:
            db.add(RadarSource(company_id=company_id, **cols))
            report.inserted += 1
        elif (
            row.origin == "seed"
            and not row.edited_by_owner
            and (row.seed_version or 0) < SEED_VERSION
        ):
            for name in _REFRESHABLE:
                setattr(row, name, cols[name])
            row.seed_version = SEED_VERSION
            row.updated_at = now
            report.updated += 1
        else:
            report.skipped += 1
    db.flush()
    return report
