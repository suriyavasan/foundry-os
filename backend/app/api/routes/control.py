from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api import sse
from app.api.deps import CompanyCtx, DbSession
from app.schemas.company import Company
from app.schemas.usage import GroupByLiteral, UsageSummary
from app.services import control, usage

router = APIRouter(prefix="/companies/{company_id}", tags=["control"])


@router.post("/pause", response_model=Company)
def pause(ctx: CompanyCtx, db: DbSession):
    return control.pause_company(db, ctx)


@router.post("/resume", response_model=Company)
def resume(ctx: CompanyCtx, db: DbSession):
    return control.resume_company(db, ctx)


@router.get("/usage", response_model=UsageSummary, response_model_by_alias=True)
def get_usage(
    ctx: CompanyCtx,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from", max_length=40)] = None,
    to: Annotated[str | None, Query(max_length=40)] = None,
    group_by: GroupByLiteral = "agent",
):
    return usage.usage_report(db, ctx, from_=from_, to=to, group_by=group_by)


@router.get("/stream")
async def stream(
    request: Request,
    ctx: CompanyCtx,
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    """SSE feed of company events. Resume with ``Last-Event-ID`` or ``?after=<id>``; with
    neither, the stream starts at "now" (use ``?after=0`` to replay the retained history)."""
    factory = request.app.state.session_factory
    cursor = sse.resolve_cursor(after, last_event_id, sse.latest_id(factory, ctx.company_id))
    return StreamingResponse(
        sse.event_stream(factory, ctx.company_id, cursor, is_disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
