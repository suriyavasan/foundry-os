"""The Command Center (M10): slash commands and free text, every input gets an output."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.commands import CommandExchange, CommandRequest
from app.schemas.common import Page
from app.services import command_center
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["command-center"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.post("/command", response_model=CommandExchange, status_code=201)
def post_command(body: CommandRequest, ctx: CompanyCtx, db: DbSession):
    """``/command`` -> answered at once (status ``done``, with the human's own permissions); free text
    -> a Founder's note, status ``pending`` until the AI Co-founder answers. 409 ``ai_setup_required``
    until the AI is set up."""
    return command_center.submit(db, ctx, body.input)


@router.get("/command-center/feed", response_model=Page[CommandExchange])
def feed(ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None):
    items, nxt = command_center.list_feed(db, ctx, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}


@router.post("/command-center/{exchange_id}/retry", response_model=CommandExchange)
def retry(exchange_id: str, ctx: CompanyCtx, db: DbSession):
    return command_center.retry(db, ctx, exchange_id)
