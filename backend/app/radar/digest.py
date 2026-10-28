"""Reading the radar: the owner's feed and status, and the agent digest (spec m16, 5.5).

Owner side: item list / dismiss, ``RadarStatus``.

Agent side: ``build_digest`` returns the new matched items (best score first, at most 60), the
``site:`` query templates of search-engine-only sources, the open ideas (so an agent avoids
duplicates) and, for the weekly self-critique, a deterministic packet of the product's own record.

Rules that make this safe to hand to a model:

* Outside text only ever leaves in a field named ``content`` (title and excerpt of an item, an
  idea's text, a theme): the runtime wraps every ``content`` in ``<untrusted>`` tags.
* An item whose text trips the injection detector is marked ``flagged`` with the category names
  (never the phrases).
* The deterministic ``counts_line`` (numbers only) is what a routine's task description may carry.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BadRequest, NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.enums import AgentKind, AgentStatus, TaskKind, TaskStatus
from app.models.radar import (
    IdeaStatus,
    RadarBrief,
    RadarIdea,
    RadarItem,
    RadarKeyword,
    RadarSource,
)
from app.models.reflections import CompanyStandard
from app.models.repository import RepoItem
from app.models.work import Task
from app.radar import access, briefs, ideas
from app.radar.normalize import neutralize_untrusted
from app.services import audit, events, policy
from app.services.agent_auth import AgentRunContext
from app.services.pagination import decode_cursor, encode_cursor

DIGEST_MAX_ITEMS = 60
DIGEST_DEFAULT_ITEMS = 40
SEARCH_QUERIES_PER_DAY = 4
PAGE_READS_PER_RUN = 5
SEARCH_QUERIES_PER_SOURCE = 2
SEARCH_TEMPLATE_SOURCES = 40
CRITIQUE_WINDOW_DAYS = 7
UI_CATEGORY = "UI inspiration"
SEARCH_ADAPTER = "search_engine"
OPEN_STATUSES = (IdeaStatus.OPEN.value,)


# ================================================================ owner: items


def _flag_filter():
    return func.length(cast(RadarItem.injection_flags, String)) > 2


def _sources_by_id(db: Session, source_ids: set[str | None]) -> dict[str, RadarSource]:
    wanted = {s for s in source_ids if s}
    if not wanted:
        return {}
    return {s.id: s for s in db.scalars(select(RadarSource).where(RadarSource.id.in_(wanted)))}


def serialize_items(db: Session, rows: list[RadarItem]) -> list[dict[str, Any]]:
    sources = _sources_by_id(db, {r.source_id for r in rows})
    out = []
    for r in rows:
        src = sources.get(r.source_id or "")
        flags = list(r.injection_flags or [])
        out.append(
            {
                "id": r.id,
                "source_id": r.source_id,
                "source_slug": src.slug if src else None,
                "source_name": src.name if src else None,
                "category": src.category if src else None,
                "url": r.url,
                "title": r.title,
                "excerpt": r.excerpt,
                "author": r.author,
                "published_at": r.published_at,
                "fetched_at": r.fetched_at,
                "kind": r.kind,
                "image_url": r.image_url,
                "engagement": dict(r.engagement or {}),
                "also_seen_on": list(r.also_seen_on or []),
                "matched_keywords": list(r.matched_keywords or []),
                "match_score": r.match_score,
                "flagged": bool(flags),
                "injection_flags": flags,
                "status": r.status,
                "relevance": r.relevance,
            }
        )
    return out


def list_items(
    db: Session,
    ctx: CompanyContext,
    *,
    source: str | None = None,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
    flagged: bool | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[RadarItem], str | None]:
    stmt = select(RadarItem).where(RadarItem.company_id == ctx.company_id)
    if source or category:
        sub = select(RadarSource.id).where(RadarSource.company_id == ctx.company_id)
        if source:
            sub = sub.where(or_(RadarSource.id == source, RadarSource.slug == source))
        if category:
            sub = sub.where(RadarSource.category == category)
        stmt = stmt.where(RadarItem.source_id.in_(sub))
    if status:
        stmt = stmt.where(RadarItem.status == status)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(RadarItem.title).like(like), func.lower(RadarItem.excerpt).like(like))
        )
    if flagged is True:
        stmt = stmt.where(_flag_filter())
    elif flagged is False:
        stmt = stmt.where(~_flag_filter())
    if cursor:
        c_at, c_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                RadarItem.fetched_at < c_at,
                (RadarItem.fetched_at == c_at) & (RadarItem.id < c_id),
            )
        )
    rows = list(
        db.scalars(stmt.order_by(RadarItem.fetched_at.desc(), RadarItem.id.desc()).limit(limit + 1))
    )
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].fetched_at, rows[-1].id)
    return rows, next_cursor


def items_by_ids(db: Session, company_id: str, item_ids: list[str]) -> list[RadarItem]:
    if not item_ids:
        return []
    return list(
        db.scalars(
            select(RadarItem)
            .where(RadarItem.company_id == company_id, RadarItem.id.in_(item_ids))
            .order_by(RadarItem.match_score.desc(), RadarItem.id)
        )
    )


def get_item(db: Session, ctx: CompanyContext, item_id: str) -> RadarItem:
    row = db.scalar(
        select(RadarItem).where(RadarItem.id == item_id, RadarItem.company_id == ctx.company_id)
    )
    if row is None:
        raise NotFound("Item not found")
    return row


def set_item_status(db: Session, ctx: CompanyContext, item_id: str, status: str) -> RadarItem:
    """Owner/admin: dismiss an item (or bring a dismissed one back as ``new``)."""
    require_human_admin(ctx, "dismiss radar items")
    row = get_item(db, ctx, item_id)
    if status == "dismissed" and row.status == "dismissed":
        return row
    if status == "new" and row.status != "dismissed":
        raise BadRequest("Only a dismissed item can be restored")
    previous = row.status
    row.status = status
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.item.dismissed" if status == "dismissed" else "radar.item.restored",
        target_type="radar_item",
        target_id=row.id,
        summary=("Dismissed" if status == "dismissed" else "Restored") + " a radar item",
        data={"from": previous, "to": status},
    )
    events.emit(db, ctx.company_id, "radar.item.updated", {"item_id": row.id, "status": status})
    db.commit()
    return row


# ================================================================ owner: status


def _runner(db: Session, company_id: str) -> tuple[Agent | None, str]:
    """Who runs the daily routine: the active Market Researcher, else the active Co-founder."""
    researcher = db.scalar(
        select(Agent)
        .where(
            Agent.company_id == company_id,
            Agent.role_template == "market-researcher",
            Agent.status == AgentStatus.ACTIVE,
        )
        .order_by(Agent.created_at)
        .limit(1)
    )
    if researcher is not None:
        return researcher, "market_researcher"
    founder = db.scalar(
        select(Agent).where(
            Agent.company_id == company_id,
            Agent.kind == AgentKind.FOUNDER,
            Agent.status == AgentStatus.ACTIVE,
        )
    )
    return (founder, "cofounder") if founder is not None else (None, "none")


def active_market_researcher(db: Session, company_id: str) -> Agent | None:
    agent, role = _runner(db, company_id)
    return agent if role == "market_researcher" else None


def status(db: Session, ctx: CompanyContext) -> dict[str, Any]:
    """``GET /radar``: everything is counted from the database; nothing is invented."""
    cid = ctx.company_id
    limits = policy.load_policy(db, cid)
    sources = list(db.scalars(select(RadarSource).where(RadarSource.company_id == cid)))
    by_cat: dict[str, dict[str, int]] = {}
    item_counts = dict(
        db.execute(
            select(RadarItem.source_id, func.count(RadarItem.id))
            .where(RadarItem.company_id == cid)
            .group_by(RadarItem.source_id)
        ).all()
    )
    failing = []
    for s in sources:
        cat = by_cat.setdefault(s.category, {"sources": 0, "enabled": 0, "failing": 0, "items": 0})
        cat["sources"] += 1
        cat["enabled"] += 1 if s.enabled else 0
        cat["items"] += int(item_counts.get(s.id, 0))
        if s.consecutive_failures > 0 or s.disabled_reason in ("failing", "robots_disallowed"):
            cat["failing"] += 1
            failing.append(s)
    failing.sort(key=lambda s: (-s.consecutive_failures, s.name))
    statuses = dict(
        db.execute(
            select(RadarItem.status, func.count(RadarItem.id))
            .where(RadarItem.company_id == cid)
            .group_by(RadarItem.status)
        ).all()
    )
    flagged = int(
        db.scalar(
            select(func.count(RadarItem.id)).where(RadarItem.company_id == cid, _flag_filter())
        )
        or 0
    )
    open_ideas = int(
        db.scalar(
            select(func.count(RadarIdea.id)).where(
                RadarIdea.company_id == cid, RadarIdea.status == IdeaStatus.OPEN.value
            )
        )
        or 0
    )
    keywords = int(
        db.scalar(
            select(func.count(RadarKeyword.id)).where(
                RadarKeyword.company_id == cid, RadarKeyword.enabled.is_(True)
            )
        )
        or 0
    )
    last_fetch = max((s.last_fetched_at for s in sources if s.last_fetched_at), default=None)
    runner, runner_role = _runner(db, cid)
    enabled = [s for s in sources if s.enabled]
    searched = [s for s in enabled if s.adapter == SEARCH_ADAPTER]
    return {
        "available": True,
        "enabled": bool(limits.radar_enabled),
        "self_critique_enabled": bool(limits.self_critique_enabled),
        "sources_total": len(sources),
        "sources_enabled": len(enabled),
        "sources_fetched": len(enabled) - len(searched),
        "sources_search_only": len(searched),
        "categories": [{"category": c, **v} for c, v in sorted(by_cat.items())],
        "items": {
            "new": int(statuses.get("new", 0)),
            "seen": int(statuses.get("seen", 0)),
            "used": int(statuses.get("used", 0)),
            "dismissed": int(statuses.get("dismissed", 0)),
            "flagged": flagged,
        },
        "open_ideas": open_ideas,
        "ideas_accepted_this_week": ideas.accepted_this_week(db, cid),
        "keywords_enabled": keywords,
        "last_fetched_at": last_fetch,
        "last_brief_at": briefs.last_brief_at(db, cid),
        "failing_sources": [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "last_status": s.last_status,
                "last_error": s.last_error,
                "consecutive_failures": s.consecutive_failures,
                "disabled_reason": s.disabled_reason,
            }
            for s in failing[:10]
        ],
        "runner_agent_id": runner.id if runner else None,
        "runner_agent_name": runner.name if runner else None,
        "runner_role": runner_role,
        "search_queries_per_day": SEARCH_QUERIES_PER_DAY,
    }


# ================================================================ deterministic counts


def new_item_stats(db: Session, company_id: str, since: datetime | None = None) -> dict[str, int]:
    """Numbers only: new items (UI inspiration excluded), distinct sources, flagged ones."""
    stmt = (
        select(RadarItem.id, RadarItem.source_id, RadarItem.injection_flags)
        .join(RadarSource, RadarSource.id == RadarItem.source_id, isouter=True)
        .where(
            RadarItem.company_id == company_id,
            RadarItem.status == "new",
            or_(RadarSource.category.is_(None), RadarSource.category != UI_CATEGORY),
        )
    )
    if since is not None:
        stmt = stmt.where(RadarItem.fetched_at > since)
    rows = db.execute(stmt).all()
    return {
        "items": len(rows),
        "sources": len({r[1] for r in rows if r[1]}),
        "flagged": sum(1 for r in rows if r[2]),
    }


def counts_line(db: Session, company_id: str, since: datetime | None = None) -> str:
    """ "Radar: 37 new items from 18 sources, 4 flagged." Safe for task descriptions and digests:
    it carries counts and nothing from any item."""
    s = new_item_stats(db, company_id, since)
    open_ideas = int(
        db.scalar(
            select(func.count(RadarIdea.id)).where(
                RadarIdea.company_id == company_id, RadarIdea.status == IdeaStatus.OPEN.value
            )
        )
        or 0
    )
    return (
        f"Radar: {s['items']} new items from {s['sources']} sources, {s['flagged']} flagged; "
        f"{open_ideas} open ideas."
    )


def unseen_count_since_last_brief(db: Session, company_id: str, kind: str) -> int:
    since = briefs.last_brief_at(db, company_id, kind)
    return new_item_stats(db, company_id, since)["items"]


# ================================================================ agent: digest


def _search_templates(db: Session, company_id: str) -> list[dict[str, Any]]:
    keywords = list(
        db.scalars(
            select(RadarKeyword)
            .where(
                RadarKeyword.company_id == company_id,
                RadarKeyword.enabled.is_(True),
                RadarKeyword.kind != "negative",
            )
            .order_by(RadarKeyword.weight.desc(), RadarKeyword.created_at)
            .limit(SEARCH_QUERIES_PER_SOURCE)
        )
    )
    terms = [k.term.replace('"', "") for k in keywords]
    sources = list(
        db.scalars(
            select(RadarSource)
            .where(
                RadarSource.company_id == company_id,
                RadarSource.enabled.is_(True),
                RadarSource.adapter == SEARCH_ADAPTER,
                RadarSource.search_template.is_not(None),
            )
            .order_by(RadarSource.category, RadarSource.name)
            .limit(SEARCH_TEMPLATE_SOURCES)
        )
    )
    out = []
    for s in sources:
        template = s.search_template or ""
        if "{query}" in template:
            queries = [template.replace("{query}", t) for t in terms]
        else:
            queries = [template]
        out.append(
            {
                "source": s.slug,
                "name": s.name,
                "category": s.category,
                "queries": [
                    neutralize_untrusted(q)[:300] for q in queries[:SEARCH_QUERIES_PER_SOURCE]
                ],
            }
        )
    return out


def _item_view(r: RadarItem, src: RadarSource | None) -> dict[str, Any]:
    flags = list(r.injection_flags or [])
    return {
        "id": r.id,
        "source": src.slug if src else None,
        "source_name": src.name if src else None,
        "category": src.category if src else None,
        "url": r.url,
        "kind": r.kind,
        "published_at": r.published_at,
        "engagement": dict(r.engagement or {}),
        "matched_keywords": list(r.matched_keywords or []),
        "match_score": r.match_score,
        "flagged": bool(flags),
        "flags": flags,
        "image_url": r.image_url,
        "status": r.status,
        "content": neutralize_untrusted(f"{r.title}\n{r.excerpt}".strip()),
    }


def _caps(db: Session, company_id: str, now: datetime) -> dict[str, Any]:
    limits = policy.load_policy(db, company_id)
    c = ideas.caps(db, company_id, now)
    agent_keywords = int(
        db.scalar(
            select(func.count(RadarKeyword.id)).where(
                RadarKeyword.company_id == company_id, RadarKeyword.origin == "agent"
            )
        )
        or 0
    )
    agent_sources = int(
        db.scalar(
            select(func.count(RadarSource.id)).where(
                RadarSource.company_id == company_id, RadarSource.origin == "agent"
            )
        )
        or 0
    )
    from app.radar import MAX_AGENT_KEYWORDS, MAX_AGENT_SOURCES

    return {
        **c,
        "web_search_queries_per_day": SEARCH_QUERIES_PER_DAY,
        "page_reads_per_run": PAGE_READS_PER_RUN,
        "auto_accept": bool(limits.radar_auto_accept),
        "agent_keywords": agent_keywords,
        "agent_keywords_remaining": max(0, MAX_AGENT_KEYWORDS - agent_keywords),
        "agent_sources": agent_sources,
        "agent_sources_remaining": max(0, MAX_AGENT_SOURCES - agent_sources),
        "agent_sources_enabled_on_add": bool(limits.radar_agent_sources),
    }


def critique_packet(db: Session, company_id: str, now: datetime) -> dict[str, Any]:
    """The weekly self-critique's input: the product's own record, computed (no model)."""
    since = now - timedelta(days=CRITIQUE_WINDOW_DAYS)
    artifacts_ = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.company_id == company_id, Artifact.updated_at >= since)
            .order_by(Artifact.updated_at.desc())
            .limit(15)
        )
    )
    tasks_ = list(
        db.scalars(
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.kind == TaskKind.WORK,
                Task.updated_at >= since,
                or_(
                    Task.status == TaskStatus.FAILED,
                    Task.status == TaskStatus.REVISION_REQUESTED,
                    Task.revision_count > 0,
                ),
            )
            .order_by(Task.updated_at.desc())
            .limit(15)
        )
    )
    standards = list(
        db.scalars(
            select(CompanyStandard)
            .where(CompanyStandard.company_id == company_id, CompanyStandard.status == "active")
            .order_by(CompanyStandard.adopted_at)
            .limit(20)
        )
    )
    week_briefs = list(
        db.scalars(
            select(RadarBrief)
            .where(RadarBrief.company_id == company_id, RadarBrief.created_at >= since)
            .order_by(RadarBrief.created_at.desc())
            .limit(7)
        )
    )
    signals = list(
        db.scalars(
            select(RepoItem)
            .where(
                RepoItem.company_id == company_id,
                RepoItem.kind.in_(["issue", "check_failure"]),
                RepoItem.status.in_(["new", "tasked"]),
                RepoItem.created_at >= now - timedelta(days=30),
            )
            .order_by(RepoItem.created_at.desc())
            .limit(10)
        )
    )
    return {
        "window_days": CRITIQUE_WINDOW_DAYS,
        "artifacts": [
            {
                "id": a.id,
                "kind": a.kind,
                "task_id": a.task_id,
                "updated_at": a.updated_at,
                "content": neutralize_untrusted(a.title),
            }
            for a in artifacts_
        ],
        "tasks": [
            {
                "id": t.id,
                "status": t.status,
                "project_id": t.project_id,
                "revisions": t.revision_count,
                "updated_at": t.updated_at,
                "content": neutralize_untrusted(t.title),
            }
            for t in tasks_
        ],
        "standards": [{"id": s.id, "title": s.title, "statement": s.statement} for s in standards],
        "brief_themes": briefs.theme_texts(week_briefs)[:20],
        "repo_signals": [
            {
                "id": r.id,
                "repository_id": r.repository_id,
                "kind": r.kind,
                "number": r.number,
                "url": r.url,
                "content": neutralize_untrusted(
                    f"{r.title or ''}\n{(r.body_excerpt or '')[:400]}".strip()
                ),
            }
            for r in signals
        ],
    }


def build_digest(
    db: Session,
    rc: AgentRunContext,
    *,
    kind: str = "daily",
    category: str | None = None,
    since: datetime | None = None,
    status: str = "open",
    limit: int = DIGEST_DEFAULT_ITEMS,
) -> dict[str, Any]:
    """``GET /radar/digest``. Marks the returned ``new`` items ``seen`` (one commit)."""
    cid = rc.ctx.company_id
    access.require(db, rc.agent, access.READ, "read the radar digest")
    now = utcnow()
    limits = policy.load_policy(db, cid)
    limit = max(1, min(int(limit), DIGEST_MAX_ITEMS))
    if since is None:
        since = briefs.last_brief_at(db, cid, kind)
    items: list[dict[str, Any]] = []
    stats = new_item_stats(db, cid, since)
    if limits.radar_enabled and kind != "self_critique":
        stmt = (
            select(RadarItem)
            .join(RadarSource, RadarSource.id == RadarItem.source_id, isouter=True)
            .where(RadarItem.company_id == cid)
        )
        if status in ("new", "seen", "used"):
            stmt = stmt.where(RadarItem.status == status)
        elif status == "open":  # not yet cited: new, or seen in an earlier digest
            stmt = stmt.where(RadarItem.status.in_(["new", "seen"]))
        else:  # "all" (anything but dismissed)
            stmt = stmt.where(RadarItem.status != "dismissed")
        if since is not None:
            stmt = stmt.where(RadarItem.fetched_at > since)
        wanted_category = category or (UI_CATEGORY if kind == "ui_inspiration" else None)
        if wanted_category:
            stmt = stmt.where(RadarSource.category == wanted_category)
        else:
            stmt = stmt.where(
                or_(RadarSource.category.is_(None), RadarSource.category != UI_CATEGORY)
            )
        rows = list(
            db.scalars(
                stmt.order_by(RadarItem.match_score.desc(), RadarItem.fetched_at.desc()).limit(
                    limit
                )
            )
        )
        sources = _sources_by_id(db, {r.source_id for r in rows})
        items = [_item_view(r, sources.get(r.source_id or "")) for r in rows]
        for r in rows:
            if r.status == "new":
                r.status = "seen"
    open_rows = ideas.open_ideas(db, cid, 25)
    templates = (
        _search_templates(db, cid)
        if limits.radar_enabled and kind in ("daily", "ui_inspiration")
        else []
    )
    packet = critique_packet(db, cid, now) if kind == "self_critique" else None
    result = {
        "kind": kind,
        "generated_at": now,
        "since": since,
        "available": True,
        "enabled": bool(limits.radar_enabled),
        "items": items,
        "search_templates": templates,
        "open_ideas": ideas.agent_view(open_rows, now),
        "counts": {
            "new_matched_items": stats["items"],
            "sources_with_items": stats["sources"],
            "flagged_items": stats["flagged"],
            "open_ideas": len(open_rows),
            "items_returned": len(items),
        },
        "caps": _caps(db, cid, now),
        "packet": packet,
    }
    db.commit()
    return result


# ================================================================ routines


def skip_reason(db: Session, company_id: str, radar_kind: str, now: datetime) -> str | None:
    """Why a radar routine fires no task (no model run, no cost) right now, else None."""
    limits = policy.load_policy(db, company_id)
    if not limits.radar_enabled:
        return "the market radar is switched off"
    if radar_kind == "daily":
        if unseen_count_since_last_brief(db, company_id, "daily") == 0:
            return "no new radar items since the last brief"
    elif radar_kind == "triage":
        if not ideas.open_ideas(db, company_id, 1):
            return "no open radar ideas"
    elif radar_kind == "self_critique":
        if not limits.self_critique_enabled:
            return "the weekly self-critique is switched off"
        packet = critique_packet(db, company_id, now)
        if not (packet["artifacts"] or packet["tasks"] or packet["repo_signals"]):
            return "nothing happened this week to critique"
    return None


def strategy_line(db: Session, company_id: str) -> list[str]:
    """Numbers (and one fixed instruction) for the weekly strategy digest: no outside text."""
    limits = policy.load_policy(db, company_id)
    if not limits.radar_enabled:
        return []
    lines = [
        f"- {counts_line(db, company_id)} Ideas accepted this week: "
        f"{ideas.accepted_this_week(db, company_id)}."
    ]
    researcher = active_market_researcher(db, company_id)
    has_new_week = new_item_stats(db, company_id, utcnow() - timedelta(days=7))["items"] > 0
    if researcher is None and has_new_week:
        lines.append(
            "- no Market Researcher is active: if the radar had new items all week, request one "
            "hire with company_request_hire (template market-researcher)."
        )
    return lines
