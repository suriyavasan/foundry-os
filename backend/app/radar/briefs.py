"""Radar briefs: what an agent concluded from a batch of items (docs/specs/m16 section 5.2).

A brief is an index row (summary, themes with item ids) plus a body stored as an ordinary artifact
(kind ``report``; ``design`` for a UI board). One brief per (company, kind, UTC day): a second one
is a 409 ``brief_exists`` and changes nothing. Citing an item marks it ``used``; the agent's
per-item relevance (0..100) is stored on the item.

The summary and themes are written by an agent from outside text, so every agent-facing view of
them goes through ``content`` (wrapped as untrusted by the runtime), never as a bare field.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.principal import CompanyContext
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.radar import BriefKind, RadarBrief, RadarIdea, RadarItem
from app.models.run import TaskRun
from app.radar import access
from app.radar.normalize import neutralize_untrusted, plain_text
from app.schemas.radar import AgentBriefCreate
from app.services import artifacts, audit, events, policy, secrets_scan
from app.services.agent_auth import AgentRunContext
from app.services.pagination import paginate
from app.storage.base import ArtifactStorage

WEEKLY_KINDS = (BriefKind.SELF_CRITIQUE.value, BriefKind.UI_INSPIRATION.value)
TITLES = {
    BriefKind.DAILY.value: "Radar brief",
    BriefKind.SELF_CRITIQUE.value: "Self-critique",
    BriefKind.UI_INSPIRATION.value: "UI inspiration board",
}


class BriefExists(Conflict):
    code = "brief_exists"
    default_message = "A brief of this kind was already written today"


def _get(db: Session, company_id: str, brief_id: str) -> RadarBrief:
    row = db.scalar(
        select(RadarBrief).where(RadarBrief.id == brief_id, RadarBrief.company_id == company_id)
    )
    if row is None:
        raise NotFound("Brief not found")
    return row


def get_brief(db: Session, ctx: CompanyContext, brief_id: str) -> RadarBrief:
    return _get(db, ctx.company_id, brief_id)


def list_briefs(
    db: Session,
    ctx: CompanyContext,
    *,
    kind: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[RadarBrief], str | None]:
    stmt = select(RadarBrief).where(RadarBrief.company_id == ctx.company_id)
    if kind:
        stmt = stmt.where(RadarBrief.kind == kind)
    return paginate(db, stmt, RadarBrief, limit=limit, cursor=cursor, descending=True)


def last_brief_at(db: Session, company_id: str, kind: str | None = None) -> datetime | None:
    stmt = select(RadarBrief.created_at).where(RadarBrief.company_id == company_id)
    if kind:
        stmt = stmt.where(RadarBrief.kind == kind)
    return db.scalar(stmt.order_by(RadarBrief.created_at.desc()).limit(1))


def period_start_for(db: Session, company_id: str, kind: str, now: datetime) -> datetime:
    """The start of the window a new brief covers: the end of the previous brief of its kind, else
    a day (a week for the weekly kinds) back."""
    previous = db.scalar(
        select(RadarBrief.period_end)
        .where(RadarBrief.company_id == company_id, RadarBrief.kind == kind)
        .order_by(RadarBrief.period_end.desc())
        .limit(1)
    )
    if previous is not None and previous < now:
        return previous
    return now - (timedelta(days=7) if kind in WEEKLY_KINDS else timedelta(days=1))


def serialize(db: Session, rows: list[RadarBrief]) -> list[dict[str, Any]]:
    names = (
        dict(
            db.execute(
                select(Agent.id, Agent.name).where(
                    Agent.id.in_({r.author_agent_id for r in rows if r.author_agent_id})
                )
            ).all()
        )
        if any(r.author_agent_id for r in rows)
        else {}
    )
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "author_agent_id": r.author_agent_id,
            "author_name": names.get(r.author_agent_id or ""),
            "task_id": r.task_id,
            "artifact_id": r.artifact_id,
            "summary": r.summary,
            "themes": [
                {
                    "title": t.get("title", ""),
                    "kind": t.get("kind", "opportunity"),
                    "item_ids": list(t.get("item_ids") or []),
                    "why": t.get("why"),
                }
                for t in (r.themes or [])
            ],
            "item_count": r.item_count,
            "ideas_created": r.ideas_created,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def cited_item_ids(row: RadarBrief) -> list[str]:
    seen: dict[str, None] = {}
    for theme in row.themes or []:
        for item_id in theme.get("item_ids") or []:
            seen.setdefault(item_id, None)
    return list(seen)


def theme_texts(rows: list[RadarBrief]) -> list[dict[str, str]]:
    """Agent-facing themes (``content`` is UNTRUSTED): used by the self-critique packet."""
    out = []
    for row in rows:
        for theme in row.themes or []:
            text = f"{theme.get('title', '')}: {theme.get('why') or ''}".strip(": ")
            out.append(
                {
                    "brief_id": row.id,
                    "kind": str(theme.get("kind", "")),
                    "content": neutralize_untrusted(text)[:600],
                }
            )
    return out


def create_brief(
    db: Session,
    rc: AgentRunContext,
    storage: ArtifactStorage,
    max_bytes: int,
    data: AgentBriefCreate,
) -> tuple[RadarBrief, int]:
    """``POST /radar/briefs`` (agent). Returns ``(brief, items marked used)``."""
    ctx = rc.ctx
    access.require(db, rc.agent, access.BRIEF, "write radar briefs")
    limits = policy.load_policy(db, ctx.company_id)
    if not limits.radar_enabled:
        raise Conflict(
            "The market radar is switched off for this company",
            details={"reason": "radar_disabled"},
        )
    if data.kind == BriefKind.SELF_CRITIQUE.value and not limits.self_critique_enabled:
        raise Conflict(
            "The weekly self-critique is switched off for this company",
            details={"reason": "self_critique_disabled"},
        )
    now = utcnow()
    day: date = now.date()
    exists = db.scalar(
        select(RadarBrief.id).where(
            RadarBrief.company_id == ctx.company_id,
            RadarBrief.kind == data.kind,
            RadarBrief.period_day == day,
        )
    )
    if exists:
        raise BriefExists(
            f"A {data.kind} brief was already written today [id {exists}]; nothing was changed",
            details={"existing_brief_id": exists},
        )

    cited: dict[str, None] = {}
    for theme in data.themes:
        for item_id in theme.item_ids:
            cited.setdefault(item_id, None)
    relevance = {r.item_id: r.relevance for r in data.relevance}
    wanted = list({*cited, *relevance})
    items: dict[str, RadarItem] = {}
    if wanted:
        items = {
            i.id: i
            for i in db.scalars(
                select(RadarItem).where(
                    RadarItem.company_id == ctx.company_id, RadarItem.id.in_(wanted)
                )
            )
        }
        missing = [i for i in wanted if i not in items]
        if missing:
            raise ValidationFailed(
                f"Radar items not found in this company: {', '.join(missing[:5])}",
                details=[{"loc": ["body", "themes"], "msg": "unknown item id", "ids": missing[:5]}],
            )

    task_id = data.task_id or rc.run.task_id
    artifact_id = data.artifact_id
    if artifact_id:
        found = db.scalar(
            select(Artifact.id).where(
                Artifact.id == artifact_id, Artifact.company_id == ctx.company_id
            )
        )
        if found is None:
            raise ValidationFailed("That artifact does not exist in this company")
    elif data.body:
        body = secrets_scan.scrub(data.body) or data.body
        artifact, _version = artifacts.create_or_version(
            db,
            ctx,
            storage,
            artifacts.ArtifactInput(
                title=f"{TITLES[data.kind]}: {day.isoformat()}",
                kind="design" if data.kind == BriefKind.UI_INSPIRATION.value else "report",
                mime_type="text/markdown",
                chunks=[body.encode("utf-8")],
                task_id=task_id,
            ),
            max_bytes=max_bytes,
            run=rc.run,
        )
        artifact_id = artifact.id

    run_started = db.scalar(select(TaskRun.created_at).where(TaskRun.id == rc.run.id)) or now
    created_before = int(
        db.scalar(
            select(func.count(RadarIdea.id)).where(
                RadarIdea.company_id == ctx.company_id,
                RadarIdea.created_by_agent_id == rc.agent.id,
                RadarIdea.created_at >= run_started,
            )
        )
        or 0
    )
    row = RadarBrief(
        company_id=ctx.company_id,
        kind=data.kind,
        period_start=period_start_for(db, ctx.company_id, data.kind, now),
        period_end=now,
        period_day=day,
        author_agent_id=rc.agent.id,
        task_id=task_id,
        artifact_id=artifact_id,
        summary=neutralize_untrusted(plain_text(secrets_scan.scrub(data.summary) or data.summary))[
            :2000
        ],
        themes=[
            {
                "title": neutralize_untrusted(plain_text(t.title))[:160],
                "kind": t.kind,
                "item_ids": list(t.item_ids),
                "why": neutralize_untrusted(plain_text(t.why))[:500] if t.why else None,
            }
            for t in data.themes
        ],
        item_count=len(cited) or len(relevance),
        ideas_created=created_before,
        created_at=now,
    )
    db.add(row)
    marked = 0
    for item_id, item in items.items():
        if item_id in relevance:
            item.relevance = relevance[item_id]
        if item_id in cited and item.status in ("new", "seen"):
            item.status = "used"
            marked += 1
    try:
        db.flush()
    except IntegrityError as exc:  # a concurrent run wrote today's brief first
        db.rollback()
        raise BriefExists("A brief of this kind was already written today") from exc
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="radar.brief.created",
        target_type="radar_brief",
        target_id=row.id,
        summary=f"Wrote the {data.kind.replace('_', ' ')} radar brief",
        data={
            "kind": row.kind,
            "item_count": row.item_count,
            "themes": len(row.themes),
            "artifact_id": artifact_id,
        },
    )
    events.emit(db, ctx.company_id, "radar.brief.created", {"brief_id": row.id, "kind": row.kind})
    db.commit()
    return row, marked
