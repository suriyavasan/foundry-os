"""Market radar, owner API (M16): ``/api/companies/{cid}/radar``. Thin routes: the logic lives in
``app/radar/``. Members and viewers read; owners and admins write; agents use the agent API
(``/api/agent/v1/radar``) and get 403 here. Non-members get 404."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import CompanyCtx, DbSession
from app.core.errors import Forbidden
from app.core.principal import CompanyContext
from app.models.radar import RadarSource as RadarSourceRow
from app.radar import briefs, digest, ideas
from app.radar import service as radar_service
from app.schemas.common import Page
from app.schemas.radar import (
    RadarBrief,
    RadarBriefDetail,
    RadarFetchNowResult,
    RadarIdea,
    RadarIdeaCreate,
    RadarIdeaDecision,
    RadarItem,
    RadarItemUpdate,
    RadarKeyword,
    RadarKeywordCreate,
    RadarKeywordUpdate,
    RadarProbeResult,
    RadarReseedResult,
    RadarSource,
    RadarSourceCreate,
    RadarSourceUpdate,
    RadarStatus,
)
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def human_ctx(ctx: CompanyCtx) -> CompanyContext:
    """The owner routes are for people. An agent that holds a run token is refused here (it has
    the agent API); the services enforce the same for every write."""
    if ctx.principal.is_agent:
        raise Forbidden("Agents use the radar tools (agent API), not the owner API")
    return ctx


HumanCtx = Annotated[CompanyContext, Depends(human_ctx)]
router = APIRouter(prefix="/companies/{company_id}/radar", tags=["radar"])


def source_out(row: RadarSourceRow) -> dict:
    """A source row as the ``RadarSource`` schema (adds ``read_via``)."""
    data = {name: getattr(row, name) for name in RadarSource.model_fields if name != "read_via"}
    data["read_via"] = "web_search" if row.adapter == "search_engine" else "fetch"
    return data


# ---------------------------------------------------------------- status


@router.get("", response_model=RadarStatus)
def get_status(ctx: HumanCtx, db: DbSession):
    return digest.status(db, ctx)


# ---------------------------------------------------------------- sources


@router.get("/sources", response_model=Page[RadarSource])
def list_sources(
    ctx: HumanCtx,
    db: DbSession,
    category: str | None = None,
    enabled: bool | None = None,
    origin: Literal["seed", "owner", "agent"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = radar_service.list_sources(
        db, ctx, category=category, enabled=enabled, origin=origin, cursor=cursor, limit=limit
    )
    return {"items": [source_out(r) for r in rows], "next_cursor": nxt}


@router.post("/sources", response_model=RadarSource, status_code=201)
def create_source(body: RadarSourceCreate, ctx: HumanCtx, db: DbSession):
    row = radar_service.create_source(
        db,
        ctx,
        radar_service.SourceInput(
            name=body.name,
            url=body.url,
            fetch_url=body.fetch_url,
            category=body.category,
            adapter=body.adapter,
            interval_seconds=body.interval_seconds,
            query_mode=body.query_mode,
            keep_unmatched=body.keep_unmatched,
            template_values=dict(body.template_values),
            robots_override=body.robots_override,
        ),
    )
    db.commit()
    return source_out(row)


@router.post("/sources/reseed", response_model=RadarReseedResult)
def reseed_sources(ctx: HumanCtx, db: DbSession):
    counts = radar_service.reseed(db, ctx)
    db.commit()
    return {"counts": counts}


@router.get("/sources/{source_id}", response_model=RadarSource)
def get_source(source_id: str, ctx: HumanCtx, db: DbSession):
    return source_out(radar_service.get_source(db, ctx, source_id))


@router.patch("/sources/{source_id}", response_model=RadarSource)
def update_source(source_id: str, body: RadarSourceUpdate, ctx: HumanCtx, db: DbSession):
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    row = radar_service.update_source(db, ctx, source_id, changes)
    db.commit()
    return source_out(row)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: str, ctx: HumanCtx, db: DbSession):
    radar_service.delete_source(db, ctx, source_id)
    db.commit()


@router.post("/sources/{source_id}/test", response_model=RadarProbeResult)
def test_source(source_id: str, ctx: HumanCtx, db: DbSession):
    """One fetch without storing anything: at most 5 parsed items, the robots verdict and the HTTP
    status. 10 per hour."""
    return radar_service.probe_source(db, ctx, source_id)


@router.post("/fetch-now", response_model=RadarFetchNowResult, status_code=202)
def fetch_now(ctx: HumanCtx, db: DbSession):
    result = radar_service.enqueue_fetch_now(db, ctx)
    db.commit()
    return {
        "enqueued": bool(result.get("queued", result.get("enqueued", True))),
        "job_id": result.get("job_id"),
        "message": result.get("message"),
    }


# ---------------------------------------------------------------- keywords


@router.get("/keywords", response_model=Page[RadarKeyword])
def list_keywords(
    ctx: HumanCtx,
    db: DbSession,
    kind: Literal["product", "competitor", "topic", "negative"] | None = None,
    origin: Literal["derived", "owner", "agent"] | None = None,
    enabled: bool | None = None,
):
    rows = radar_service.list_keywords(db, ctx, kind=kind, origin=origin, enabled=enabled)
    return {"items": rows, "next_cursor": None}


@router.post("/keywords", response_model=RadarKeyword, status_code=201)
def create_keyword(body: RadarKeywordCreate, ctx: HumanCtx, db: DbSession):
    row = radar_service.create_keyword(
        db,
        ctx,
        radar_service.KeywordInput(term=body.term, kind=body.kind, weight=body.weight),
    )
    db.commit()
    return row


@router.post("/keywords/derive", response_model=Page[RadarKeyword])
def derive_keywords(ctx: HumanCtx, db: DbSession):
    """Add the keywords the company profile implies (deterministic, no model); never touches
    owner or agent keywords."""
    rows = radar_service.derive_keywords(db, ctx)
    db.commit()
    return {"items": rows, "next_cursor": None}


@router.patch("/keywords/{keyword_id}", response_model=RadarKeyword)
def update_keyword(keyword_id: str, body: RadarKeywordUpdate, ctx: HumanCtx, db: DbSession):
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    row = radar_service.update_keyword(db, ctx, keyword_id, changes)
    db.commit()
    return row


@router.delete("/keywords/{keyword_id}", status_code=204)
def delete_keyword(keyword_id: str, ctx: HumanCtx, db: DbSession):
    radar_service.delete_keyword(db, ctx, keyword_id)
    db.commit()


# ---------------------------------------------------------------- items


@router.get("/items", response_model=Page[RadarItem])
def list_items(
    ctx: HumanCtx,
    db: DbSession,
    source: str | None = None,
    category: str | None = None,
    status: Literal["new", "seen", "used", "dismissed"] | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    flagged: bool | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = digest.list_items(
        db,
        ctx,
        source=source,
        category=category,
        status=status,
        q=q,
        flagged=flagged,
        limit=limit,
        cursor=cursor,
    )
    return {"items": digest.serialize_items(db, rows), "next_cursor": nxt}


@router.patch("/items/{item_id}", response_model=RadarItem)
def update_item(item_id: str, body: RadarItemUpdate, ctx: HumanCtx, db: DbSession):
    row = digest.set_item_status(db, ctx, item_id, body.status)
    return digest.serialize_items(db, [row])[0]


# ---------------------------------------------------------------- briefs


@router.get("/briefs", response_model=Page[RadarBrief])
def list_briefs(
    ctx: HumanCtx,
    db: DbSession,
    kind: Literal["daily", "self_critique", "ui_inspiration"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = briefs.list_briefs(db, ctx, kind=kind, limit=limit, cursor=cursor)
    return {"items": briefs.serialize(db, rows), "next_cursor": nxt}


@router.get("/briefs/{brief_id}", response_model=RadarBriefDetail)
def get_brief(brief_id: str, ctx: HumanCtx, db: DbSession):
    row = briefs.get_brief(db, ctx, brief_id)
    out = briefs.serialize(db, [row])[0]
    item_rows = digest.items_by_ids(db, ctx.company_id, briefs.cited_item_ids(row)[:60])
    out["items"] = digest.serialize_items(db, item_rows)
    out["ideas"] = ideas.serialize(db, ideas.ideas_of_brief(db, row))
    return out


# ---------------------------------------------------------------- ideas


@router.get("/ideas", response_model=Page[RadarIdea])
def list_ideas(
    ctx: HumanCtx,
    db: DbSession,
    status: Literal["open", "accepted", "rejected", "merged", "done", "expired"] | None = None,
    kind: Literal["fix", "feature", "ui", "positioning", "research"] | None = None,
    origin: Literal["radar", "self_critique", "ui_inspiration", "owner"] | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = ideas.list_ideas(
        db, ctx, status=status, kind=kind, origin=origin, limit=limit, cursor=cursor
    )
    return {"items": ideas.serialize(db, rows), "next_cursor": nxt}


@router.post("/ideas", response_model=RadarIdea, status_code=201)
def create_idea(body: RadarIdeaCreate, ctx: HumanCtx, db: DbSession):
    return ideas.serialize(db, [ideas.create_as_owner(db, ctx, body)])[0]


@router.get("/ideas/{idea_id}", response_model=RadarIdea)
def get_idea(idea_id: str, ctx: HumanCtx, db: DbSession):
    return ideas.serialize(db, [ideas.get_idea(db, ctx, idea_id)])[0]


@router.patch("/ideas/{idea_id}", response_model=RadarIdea)
def decide_idea(idea_id: str, body: RadarIdeaDecision, ctx: HumanCtx, db: DbSession):
    """Accept into a project (creates its work task), reject, merge into another idea, or reopen a
    rejected or expired one."""
    return ideas.serialize(db, [ideas.decide_as_owner(db, ctx, idea_id, body)])[0]
