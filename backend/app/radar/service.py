"""Owner and agent operations on radar sources and keywords (thin routes call these).

Rules (AGENTS.md): every query is filtered by ``ctx.company_id`` (a row of another company is a
404); authorization lives here, not only in the routes; every state change writes an ``AuditEvent``
in the same transaction. Agents can never change ``radar_*`` policy, owner sources,
``robots_override`` or owner / derived keywords. Callers commit.

Ideas, briefs, the digest and the page reader are WP-2 (``app/radar/ideas.py`` and friends).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import netguard
from app.core.errors import Conflict, Forbidden, NotFound, RateLimited, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.guardrails import ratelimit
from app.models.radar import (
    ItemStatus,
    KeywordKind,
    KeywordOrigin,
    RadarItem,
    RadarKeyword,
    RadarSource,
    SourceOrigin,
)
from app.radar import (
    FETCH_NOW_PER_HOUR,
    MAX_AGENT_KEYWORDS,
    MAX_AGENT_SOURCES,
    MAX_ENABLED_KEYWORDS,
    MAX_INTERVAL_SECONDS,
    MAX_QUERIES_PER_SOURCE,
    MIN_INTERVAL_SECONDS,
    RADAR_FETCH_NOW_KEY,
    RADAR_SOURCE_TESTS_KEY,
    TEST_FETCH_ITEMS,
    TEST_FETCHES_PER_HOUR,
)
from app.radar import adapters as adapter_registry
from app.radar import jobs as radar_jobs
from app.radar import keywords as keywords_mod
from app.radar import seed as seed_mod
from app.radar.normalize import canonical_url, clean_text
from app.services import audit, policy
from app.services.pagination import paginate

# Fields of a source that ``update_source`` accepts (everything else is fetcher state).
SOURCE_UPDATABLE = (
    "name",
    "url",
    "category",
    "fetch_url",
    "search_template",
    "query_mode",
    "keep_unmatched",
    "enabled",
    "interval_seconds",
    "template_values",
    "robots_override",
)
# Changing only these never marks a seed row as edited (a re-seed may still refresh the rest).
_STATE_ONLY = frozenset({"enabled"})
KEYWORD_UPDATABLE = ("term", "kind", "weight", "enabled")
AGENT_KEYWORD_KINDS = (KeywordKind.TOPIC.value, KeywordKind.COMPETITOR.value)


@dataclass(frozen=True)
class SourceInput:
    """A source an owner (or an agent, for a feed) adds. ``adapter`` defaults to ``feed``."""

    name: str
    url: str
    fetch_url: str
    category: str = "blog"
    adapter: str = "feed"
    interval_seconds: int = 21_600
    query_mode: str = "none"
    keep_unmatched: bool = False
    template_values: dict[str, str] = field(default_factory=dict)
    robots_override: bool = False  # owner only; 403 for agents


@dataclass(frozen=True)
class KeywordInput:
    term: str
    kind: str = "topic"  # product | competitor | topic | negative (agents: topic | competitor)
    weight: int = 3  # 1..5


@dataclass(frozen=True)
class ProbeResult:
    """What ``probe_source`` returns: nothing is stored."""

    source_id: str | None
    url: str
    robots: str  # allowed | disallowed | missing | unreachable | override | not_checked
    http_status: int | None
    status: str  # a ``FetchStatus`` value
    items: list[dict[str, Any]]  # at most 5: title, url, excerpt, published_at, kind
    requests: int
    error: str | None


def _invalid(field_name: str, message: str) -> ValidationFailed:
    return ValidationFailed(message, details=[{"loc": ["body", field_name], "msg": message}])


def _session_factory(db: Session) -> sessionmaker[Session]:
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


# ------------------------------------------------------------------ status


def get_status(db: Session, ctx: CompanyContext) -> dict[str, Any]:
    """``GET /radar``: ``enabled``, source counts per category and state, items by status, the last
    fetch time and the sources currently failing. Any member or agent of the company may read."""
    now = utcnow()
    limits = policy.load_policy(db, ctx.company_id)
    cid = ctx.company_id
    by_category: dict[str, dict[str, int]] = {}
    for category, enabled, count in db.execute(
        select(RadarSource.category, RadarSource.enabled, func.count(RadarSource.id))
        .where(RadarSource.company_id == cid)
        .group_by(RadarSource.category, RadarSource.enabled)
    ):
        bucket = by_category.setdefault(category, {"enabled": 0, "disabled": 0})
        bucket["enabled" if enabled else "disabled"] += int(count)
    items_by_status = {s.value: 0 for s in ItemStatus}
    for status, count in db.execute(
        select(RadarItem.status, func.count(RadarItem.id))
        .where(RadarItem.company_id == cid)
        .group_by(RadarItem.status)
    ):
        items_by_status[status] = int(count)
    last_fetch = db.scalar(
        select(func.max(RadarSource.last_fetched_at)).where(RadarSource.company_id == cid)
    )
    failing = list(
        db.scalars(
            select(RadarSource)
            .where(RadarSource.company_id == cid, RadarSource.consecutive_failures > 0)
            .order_by(RadarSource.consecutive_failures.desc(), RadarSource.name)
            .limit(10)
        )
    )
    searchable = db.scalar(
        select(func.count(RadarSource.id)).where(
            RadarSource.company_id == cid,
            RadarSource.enabled.is_(True),
            RadarSource.adapter == "search_engine",
        )
    )
    fetchable = db.scalar(
        select(func.count(RadarSource.id)).where(
            RadarSource.company_id == cid,
            RadarSource.enabled.is_(True),
            RadarSource.adapter != "search_engine",
        )
    )
    return {
        "enabled": bool(limits.radar_enabled),
        "sources": {
            "total": sum(c["enabled"] + c["disabled"] for c in by_category.values()),
            "fetched": int(fetchable or 0),
            "search_only": int(searchable or 0),
            "by_category": by_category,
        },
        "items": items_by_status,
        "last_fetch_at": last_fetch,
        "requests_today": radar_jobs.daily_fetches_used(db, cid, now),
        "max_requests_per_day": int(limits.radar_max_fetches_per_day),
        "keywords_enabled": keywords_mod.enabled_count(db, cid),
        "failing_sources": [
            {
                "id": s.id,
                "slug": s.slug,
                "name": s.name,
                "last_status": s.last_status,
                "consecutive_failures": s.consecutive_failures,
                "last_error": s.last_error,
            }
            for s in failing
        ],
    }


# ------------------------------------------------------------------ sources


def list_sources(
    db: Session,
    ctx: CompanyContext,
    *,
    category: str | None = None,
    enabled: bool | None = None,
    origin: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[RadarSource], str | None]:
    stmt = select(RadarSource).where(RadarSource.company_id == ctx.company_id)
    if category is not None:
        stmt = stmt.where(RadarSource.category == category)
    if enabled is not None:
        stmt = stmt.where(RadarSource.enabled.is_(enabled))
    if origin is not None:
        stmt = stmt.where(RadarSource.origin == origin)
    return paginate(db, stmt, RadarSource, limit=limit, cursor=cursor, descending=False)


def get_source(db: Session, ctx: CompanyContext, source_id: str) -> RadarSource:
    """404 for a source of another company."""
    source = db.scalar(
        select(RadarSource).where(
            RadarSource.id == source_id, RadarSource.company_id == ctx.company_id
        )
    )
    if source is None:
        raise NotFound("Radar source not found")
    return source


def _unique_slug(db: Session, company_id: str, name: str) -> str:
    base = seed_mod.slugify(name)
    taken = set(
        db.scalars(
            select(RadarSource.slug).where(
                RadarSource.company_id == company_id, RadarSource.slug.like(f"{base}%")
            )
        )
    )
    if base not in taken:
        return base
    n = 2
    while f"{base[:75]}-{n}" in taken:
        n += 1
    return f"{base[:75]}-{n}"


def _check_public_url(url: str, field_name: str, *, resolve: bool) -> str:
    """http(s), no credentials, and (``resolve``) every address public: the SSRF guard."""
    url = (url or "").strip()
    if len(url) > 1000:
        raise _invalid(field_name, "is too long (max 1000 characters)")
    try:
        netguard.check_url(url, require_resolution=resolve, field=field_name)
    except netguard.UnsafeUrl as exc:
        raise ValidationFailed(exc.message, details=exc.details) from exc
    return url


def _check_interval(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid("interval_seconds", "must be a whole number of seconds")
    if not MIN_INTERVAL_SECONDS <= value <= MAX_INTERVAL_SECONDS:
        raise _invalid(
            "interval_seconds",
            f"must be between {MIN_INTERVAL_SECONDS} (1 hour) and {MAX_INTERVAL_SECONDS} seconds",
        )
    return value


def _check_values(fetch_url: str | None, values: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise _invalid("template_values", "must be an object of strings")
    clean = {str(k)[:40]: str(v).strip()[:120] for k, v in values.items() if str(v).strip()}
    needed = seed_mod.custom_placeholders(fetch_url)
    missing = [n for n in needed if n not in clean]
    if missing:
        raise _invalid("template_values", f"needs a value for: {', '.join(missing)}")
    return {k: v for k, v in clean.items() if k in needed}


def _probe_collect(
    db: Session,
    company_id: str,
    spec: radar_jobs.SourceSpec,
    *,
    first_only: bool = True,
) -> radar_jobs.Collected:
    # The fetcher writes host state and request counts through connections of its own. A write
    # lock held by this request's transaction (a guardrail event, say) would stall them on SQLite,
    # so end this transaction first. Callers have made no half-finished change at this point.
    db.commit()
    factory = _session_factory(db)
    contact = policy.load_policy(db, company_id).radar_contact_email
    terms = [
        k.term
        for k in db.scalars(
            select(RadarKeyword)
            .where(RadarKeyword.company_id == company_id, RadarKeyword.enabled.is_(True))
            .order_by(RadarKeyword.weight.desc(), RadarKeyword.term)
        )
        if k.kind != "negative"
    ][:MAX_QUERIES_PER_SOURCE]
    fetcher = radar_jobs.make_fetcher(
        factory, company_id, utcnow, contact_email=contact, require_enabled=False
    )
    try:
        return radar_jobs.collect(fetcher, spec, terms, utcnow(), first_only=first_only)
    finally:
        fetcher.close()


def create_source(db: Session, ctx: CompanyContext, data: SourceInput) -> RadarSource:
    """Owner/admin: origin ``owner``. An agent (``radar.source.add``): origin ``agent``, adapter
    ``feed`` only, validated by a test fetch, at most 20 agent sources, enabled only when
    ``radar_agent_sources`` is true (else stored disabled as a suggestion)."""
    agent = ctx.principal.is_agent
    if not agent:
        require_human_admin(ctx, "add radar sources")
    name = clean_text(data.name, 160)
    if not name:
        raise _invalid("name", "is required")
    if data.category not in seed_mod.CATEGORIES:
        raise _invalid("category", f"must be one of: {', '.join(seed_mod.CATEGORIES)}")
    adapter_name = data.adapter or "feed"
    if agent:
        adapter_name = "feed"  # an agent adds RSS / Atom / JSON Feed, nothing that needs code
    if adapter_name not in adapter_registry.ADAPTERS:
        raise _invalid("adapter", f"unknown adapter {adapter_name!r}")
    adapter = adapter_registry.ADAPTERS[adapter_name]
    if not adapter.fetchable:
        raise _invalid("adapter", f"{adapter_name} sources are not fetched by the control plane")
    if agent and data.robots_override:
        raise Forbidden("Agents cannot override robots.txt")
    interval = _check_interval(data.interval_seconds)
    page_url = _check_public_url(data.url or data.fetch_url, "url", resolve=False)
    fetch_url = _check_public_url(data.fetch_url, "fetch_url", resolve=agent)
    values = _check_values(fetch_url, data.template_values)
    per_keyword = seed_mod.uses_keywords(fetch_url)
    limits = policy.load_policy(db, ctx.company_id)
    enabled = True
    disabled_reason: str | None = None
    if agent:
        count = db.scalar(
            select(func.count(RadarSource.id)).where(
                RadarSource.company_id == ctx.company_id, RadarSource.origin == SourceOrigin.AGENT
            )
        )
        if int(count or 0) >= MAX_AGENT_SOURCES:
            raise Conflict(
                f"Agents may add at most {MAX_AGENT_SOURCES} sources",
                details={"code": "agent_source_limit"},
            )
        spec = radar_jobs.SourceSpec(
            id=None,
            slug="candidate",
            category=data.category,
            adapter="feed",
            fetch_url=fetch_url,
            query_mode="per_keyword" if per_keyword else "none",
            template_values=values,
        )
        probe = _probe_collect(db, ctx.company_id, spec)
        entries = sum(len(entries) for _, entries in probe.batches)
        if probe.status not in ("ok", "not_modified") or not entries:
            reason = probe.error or ("the feed has no entries" if not entries else probe.status)
            raise _invalid("fetch_url", f"is not a readable feed: {reason}")
        if not limits.radar_agent_sources:
            enabled, disabled_reason = False, "suggestion"
    source = RadarSource(
        company_id=ctx.company_id,
        slug=_unique_slug(db, ctx.company_id, name),
        name=name,
        url=page_url,
        category=data.category,
        access_method="atom" if "atom" in fetch_url.lower() else "rss",
        adapter=adapter_name,
        fetch_url=fetch_url,
        query_mode="per_keyword" if per_keyword else "none",
        keep_unmatched=bool(data.keep_unmatched),
        enabled=enabled,
        origin=SourceOrigin.AGENT if agent else SourceOrigin.OWNER,
        edited_by_owner=False,
        interval_seconds=interval,
        robots_override=bool(data.robots_override) and not agent,
        template_values=values,
        disabled_reason=disabled_reason,
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(source)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.source.created",
        target_type="radar_source",
        target_id=source.id,
        summary=(
            f"Added radar source {source.name}"
            + (" (suggested by an agent, off until you enable it)" if not enabled else "")
        ),
        data={
            "slug": source.slug,
            "origin": source.origin,
            "enabled": enabled,
            "robots_override": source.robots_override,
            "host": urlsplit(fetch_url).hostname,
        },
    )
    return source


def update_source(
    db: Session, ctx: CompanyContext, source_id: str, changes: Mapping[str, Any]
) -> RadarSource:
    """Owner/admin only (agents: 403). Marks a seed row ``edited_by_owner``. ``robots_override``
    only on sources the owner added, audited. Enabling a source clears its failure state."""
    require_human_admin(ctx, "change radar sources")
    source = get_source(db, ctx, source_id)
    unknown = sorted(set(changes) - set(SOURCE_UPDATABLE))
    if unknown:
        raise _invalid(unknown[0], "cannot be changed")
    changed: list[str] = []

    def put(name: str, value: Any) -> None:
        if getattr(source, name) != value:
            setattr(source, name, value)
            changed.append(name)

    if "name" in changes:
        name = clean_text(changes["name"], 160)
        if not name:
            raise _invalid("name", "is required")
        put("name", name)
    if "url" in changes:
        put("url", _check_public_url(str(changes["url"]), "url", resolve=False))
    if "category" in changes:
        if changes["category"] not in seed_mod.CATEGORIES:
            raise _invalid("category", f"must be one of: {', '.join(seed_mod.CATEGORIES)}")
        put("category", changes["category"])
    if "fetch_url" in changes:
        if changes["fetch_url"] is None:
            raise _invalid("fetch_url", "cannot be cleared")
        fetch_url = _check_public_url(str(changes["fetch_url"]), "fetch_url", resolve=False)
        put("fetch_url", fetch_url)
        put("query_mode", "per_keyword" if seed_mod.uses_keywords(fetch_url) else "none")
    if "search_template" in changes:
        template = changes["search_template"]
        put("search_template", clean_text(template, 300) or None if template else None)
    if "query_mode" in changes:
        if changes["query_mode"] not in ("none", "per_keyword"):
            raise _invalid("query_mode", "must be 'none' or 'per_keyword'")
        put("query_mode", changes["query_mode"])
    if "keep_unmatched" in changes:
        put("keep_unmatched", bool(changes["keep_unmatched"]))
    if "interval_seconds" in changes:
        put("interval_seconds", _check_interval(changes["interval_seconds"]))
    if "template_values" in changes:
        put("template_values", _check_values(source.fetch_url, changes["template_values"]))
    if "robots_override" in changes:
        wanted = bool(changes["robots_override"])
        if wanted and source.origin != SourceOrigin.OWNER:
            raise Conflict(
                "robots.txt can only be overridden on a source you added yourself",
                details={"code": "robots_override_not_allowed"},
            )
        put("robots_override", wanted)
    if "enabled" in changes:
        wanted = bool(changes["enabled"])
        if wanted:
            adapter = adapter_registry.ADAPTERS.get(source.adapter)
            if adapter is not None and adapter.requires_token:
                raise Conflict(
                    "This source needs an API token, which is not supported yet",
                    details={"code": "needs_token"},
                )
            _check_values(source.fetch_url, source.template_values or {})
            if not source.enabled:
                source.consecutive_failures = 0
                source.disabled_reason = None
                source.last_error = None
                source.last_status = None
                source.next_fetch_at = None  # due at once
        elif source.enabled:
            source.disabled_reason = "owner"
        put("enabled", wanted)
    if not changed:
        return source
    if source.origin == SourceOrigin.SEED and set(changed) - _STATE_ONLY:
        source.edited_by_owner = True
    source.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.source.updated",
        target_type="radar_source",
        target_id=source.id,
        summary=f"Updated radar source {source.name}: {', '.join(changed)}",
        data={
            "changed": changed,
            **({"robots_override": source.robots_override} if "robots_override" in changed else {}),
            **({"enabled": source.enabled} if "enabled" in changed else {}),
        },
    )
    db.flush()
    return source


def delete_source(db: Session, ctx: CompanyContext, source_id: str) -> None:
    """Owner/admin only, non-seed sources only (409 for a seed: disable it instead)."""
    require_human_admin(ctx, "delete radar sources")
    source = get_source(db, ctx, source_id)
    if source.origin == SourceOrigin.SEED:
        raise Conflict(
            "Seed sources cannot be deleted; switch them off instead",
            details={"code": "seed_source"},
        )
    for item in db.scalars(
        select(RadarItem).where(
            RadarItem.company_id == ctx.company_id, RadarItem.source_id == source.id
        )
    ):
        if item.status == ItemStatus.USED:
            item.source_id = None  # a brief or idea still cites it
        else:
            db.delete(item)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.source.deleted",
        target_type="radar_source",
        target_id=source.id,
        summary=f"Deleted radar source {source.name}",
        data={"slug": source.slug, "origin": source.origin},
    )
    db.delete(source)
    db.flush()


def _spend(db: Session, ctx: CompanyContext, key: str, limit: int, what: str) -> None:
    """Count one use of a per-company hourly allowance or raise 429."""
    now = utcnow()
    window = ratelimit.window_for(db, ctx.company_id, key, now, None)
    if window.used >= limit:
        err = RateLimited(f"Too many {what}; try again later")
        if window.resets_at is not None:
            err.headers = {
                "Retry-After": str(max(1, int((window.resets_at - now).total_seconds())))
            }
        raise err
    ratelimit.record(db, ctx.company_id, None, (key,), now)
    db.flush()


def probe_source(
    db: Session, ctx: CompanyContext, source_id: str, *, now: Any | None = None
) -> ProbeResult:
    """Owner/admin: one fetch without storing; at most 5 parsed items, the robots verdict and the
    HTTP status. Rate limited to 10 per hour per company (429)."""
    require_human_admin(ctx, "test radar sources")
    source = get_source(db, ctx, source_id)
    _spend(db, ctx, RADAR_SOURCE_TESTS_KEY, TEST_FETCHES_PER_HOUR, "source tests")
    db.commit()  # the allowance is spent whatever the fetch does; free the write lock
    spec = radar_jobs.SourceSpec.of(source)
    if source.adapter == "search_engine":
        return ProbeResult(
            source.id,
            source.search_template or "",
            "not_checked",
            None,
            "config_error",
            [],
            0,
            "Agents read this source through web search; the control plane never fetches it",
        )
    collected = _probe_collect(db, ctx.company_id, spec)
    items: list[dict[str, Any]] = []
    for _, entries in collected.batches:
        for entry in entries:
            if len(items) >= TEST_FETCH_ITEMS:
                break
            canonical = canonical_url(entry.url)
            if canonical is None:
                continue
            items.append(
                {
                    "title": clean_text(entry.title, 300),
                    "url": canonical,
                    "excerpt": clean_text(entry.excerpt, 300),
                    "published_at": entry.published_at,
                    "kind": entry.kind,
                }
            )
    return ProbeResult(
        source_id=source.id,
        url=source.fetch_url or "",
        robots=collected.robots or "not_checked",
        http_status=collected.http_status,
        status=collected.status,
        items=items,
        requests=collected.requests,
        error=collected.error,
    )


def reseed(db: Session, ctx: CompanyContext) -> dict[str, int]:
    """Owner/admin: insert seed sources the company lacks; never overwrites an edited row."""
    require_human_admin(ctx, "re-seed radar sources")
    report = seed_mod.seed_company(db, ctx.company_id)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.sources.reseeded",
        target_type="radar",
        target_id=None,
        summary=f"Re-seeded radar sources: {report.inserted} added, {report.updated} refreshed",
        data={"inserted": report.inserted, "updated": report.updated, "skipped": report.skipped},
    )
    return {"inserted": report.inserted, "updated": report.updated, "skipped": report.skipped}


def enqueue_fetch_now(db: Session, ctx: CompanyContext) -> dict[str, Any]:
    """Owner/admin: enqueue a ``radar_fetch`` job now (6 per hour per company, 429)."""
    require_human_admin(ctx, "start a radar fetch")
    limits = policy.load_policy(db, ctx.company_id)
    if not limits.radar_enabled:
        raise Conflict(
            "The radar is off; turn it on in the policy first", details={"code": "radar_off"}
        )
    now = utcnow()
    _spend(db, ctx, RADAR_FETCH_NOW_KEY, FETCH_NOW_PER_HOUR, "fetch-now requests")
    job = radar_jobs.enqueue_now(db, ctx.company_id, now)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.fetch.requested",
        target_type="radar",
        target_id=None,
        summary="Requested a radar fetch now",
        data={"job_id": job.id},
    )
    return {"job_id": job.id, "queued": True}


# ------------------------------------------------------------------ keywords


def list_keywords(
    db: Session,
    ctx: CompanyContext,
    *,
    kind: str | None = None,
    origin: str | None = None,
    enabled: bool | None = None,
) -> list[RadarKeyword]:
    stmt = select(RadarKeyword).where(RadarKeyword.company_id == ctx.company_id)
    if kind is not None:
        stmt = stmt.where(RadarKeyword.kind == kind)
    if origin is not None:
        stmt = stmt.where(RadarKeyword.origin == origin)
    if enabled is not None:
        stmt = stmt.where(RadarKeyword.enabled.is_(enabled))
    return list(db.scalars(stmt.order_by(RadarKeyword.weight.desc(), RadarKeyword.term)))


def _get_keyword(db: Session, ctx: CompanyContext, keyword_id: str) -> RadarKeyword:
    row = db.scalar(
        select(RadarKeyword).where(
            RadarKeyword.id == keyword_id, RadarKeyword.company_id == ctx.company_id
        )
    )
    if row is None:
        raise NotFound("Radar keyword not found")
    return row


def _check_term(db: Session, ctx: CompanyContext, term: str, *, own_id: str | None = None) -> str:
    cleaned = keywords_mod.clean_term(clean_text(term, 80))
    if len(cleaned) < 2:
        raise _invalid("term", "must be at least 2 characters")
    taken = db.scalar(
        select(RadarKeyword.id).where(
            RadarKeyword.company_id == ctx.company_id,
            func.lower(RadarKeyword.term) == cleaned.lower(),
            RadarKeyword.id != (own_id or ""),
        )
    )
    if taken is not None:
        raise Conflict("That keyword already exists", details={"code": "duplicate_keyword"})
    return cleaned


def _check_weight(weight: Any) -> int:
    if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 5:
        raise _invalid("weight", "must be a whole number from 1 to 5")
    return weight


def _check_room(db: Session, ctx: CompanyContext) -> None:
    if keywords_mod.enabled_count(db, ctx.company_id) >= MAX_ENABLED_KEYWORDS:
        raise Conflict(
            f"At most {MAX_ENABLED_KEYWORDS} keywords can be enabled",
            details={"code": "keyword_limit"},
        )


def create_keyword(db: Session, ctx: CompanyContext, data: KeywordInput) -> RadarKeyword:
    """Owner/admin: origin ``owner``. Agent: origin ``agent``, kind ``topic``/``competitor`` only,
    at most 15 agent keywords. 40 enabled keywords per company. ``lower(term)`` is unique per
    company (409)."""
    agent = ctx.principal.is_agent
    if not agent:
        require_human_admin(ctx, "add radar keywords")
    kinds = [k.value for k in KeywordKind]
    if data.kind not in kinds:
        raise _invalid("kind", f"must be one of: {', '.join(kinds)}")
    if agent and data.kind not in AGENT_KEYWORD_KINDS:
        raise Forbidden("Agents may add topic and competitor keywords only")
    weight = _check_weight(data.weight)
    term = _check_term(db, ctx, data.term)
    if agent:
        count = db.scalar(
            select(func.count(RadarKeyword.id)).where(
                RadarKeyword.company_id == ctx.company_id,
                RadarKeyword.origin == KeywordOrigin.AGENT,
            )
        )
        if int(count or 0) >= MAX_AGENT_KEYWORDS:
            raise Conflict(
                f"Agents may add at most {MAX_AGENT_KEYWORDS} keywords",
                details={"code": "agent_keyword_limit"},
            )
    _check_room(db, ctx)
    row = RadarKeyword(
        company_id=ctx.company_id,
        term=term,
        kind=data.kind,
        origin=KeywordOrigin.AGENT if agent else KeywordOrigin.OWNER,
        weight=weight,
        enabled=True,
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.keyword.created",
        target_type="radar_keyword",
        target_id=row.id,
        summary=f"Added radar keyword '{term}' ({data.kind})",
        data={"term": term, "kind": data.kind, "origin": row.origin, "weight": weight},
    )
    return row


def _agent_may_touch(ctx: CompanyContext, row: RadarKeyword) -> None:
    if row.origin != KeywordOrigin.AGENT:
        raise Forbidden("Agents cannot change keywords the owner added or the platform derived")


def update_keyword(
    db: Session, ctx: CompanyContext, keyword_id: str, changes: Mapping[str, Any]
) -> RadarKeyword:
    """Owner/admin: any keyword. Agent: only keywords an agent added, never an owner or derived
    keyword (403)."""
    agent = ctx.principal.is_agent
    if not agent:
        require_human_admin(ctx, "change radar keywords")
    row = _get_keyword(db, ctx, keyword_id)
    if agent:
        _agent_may_touch(ctx, row)
    unknown = sorted(set(changes) - set(KEYWORD_UPDATABLE))
    if unknown:
        raise _invalid(unknown[0], "cannot be changed")
    changed: list[str] = []
    if "term" in changes:
        term = _check_term(db, ctx, str(changes["term"]), own_id=row.id)
        if term != row.term:
            row.term = term
            changed.append("term")
    if "kind" in changes:
        kinds = [k.value for k in KeywordKind]
        if changes["kind"] not in kinds:
            raise _invalid("kind", f"must be one of: {', '.join(kinds)}")
        if agent and changes["kind"] not in AGENT_KEYWORD_KINDS:
            raise Forbidden("Agents may use topic and competitor keywords only")
        if changes["kind"] != row.kind:
            row.kind = changes["kind"]
            changed.append("kind")
    if "weight" in changes:
        weight = _check_weight(changes["weight"])
        if weight != row.weight:
            row.weight = weight
            changed.append("weight")
    if "enabled" in changes:
        wanted = bool(changes["enabled"])
        if wanted and not row.enabled:
            _check_room(db, ctx)
        if wanted != row.enabled:
            row.enabled = wanted
            changed.append("enabled")
    if not changed:
        return row
    row.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.keyword.updated",
        target_type="radar_keyword",
        target_id=row.id,
        summary=f"Updated radar keyword '{row.term}': {', '.join(changed)}",
        data={"changed": changed},
    )
    db.flush()
    return row


def delete_keyword(db: Session, ctx: CompanyContext, keyword_id: str) -> None:
    """Owner/admin: any keyword. Agent: only agent-origin keywords."""
    agent = ctx.principal.is_agent
    if not agent:
        require_human_admin(ctx, "delete radar keywords")
    row = _get_keyword(db, ctx, keyword_id)
    if agent:
        _agent_may_touch(ctx, row)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.keyword.deleted",
        target_type="radar_keyword",
        target_id=row.id,
        summary=f"Deleted radar keyword '{row.term}'",
        data={"term": row.term, "origin": row.origin},
    )
    db.delete(row)
    db.flush()


def derive_keywords(db: Session, ctx: CompanyContext) -> list[RadarKeyword]:
    """Owner/admin: (re)derive keywords from the company profile (deterministic, no model). Adds
    missing ``derived`` keywords, never touches owner/agent ones or re-enables a disabled one."""
    require_human_admin(ctx, "derive radar keywords")
    return keywords_mod.apply_derived(db, ctx.company_id, principal=ctx.principal)


__all__ = [
    "KEYWORD_UPDATABLE",
    "SOURCE_UPDATABLE",
    "KeywordInput",
    "ProbeResult",
    "SourceInput",
    "create_keyword",
    "create_source",
    "delete_keyword",
    "delete_source",
    "derive_keywords",
    "enqueue_fetch_now",
    "get_source",
    "get_status",
    "list_keywords",
    "list_sources",
    "probe_source",
    "reseed",
    "update_keyword",
    "update_source",
]
