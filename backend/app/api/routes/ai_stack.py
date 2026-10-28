"""M17: the company's AI stack. Owners/admins write; members and viewers read; agents get 403."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import CompanyCtx, DbSession, get_cipher
from app.schemas.ai_setup import ClaudeLoginRequest, ClaudeLoginStatus
from app.schemas.ai_stack import (
    AiConnectionCreate,
    AiConnectionOut,
    AiConnectionPatch,
    AiOrder,
    AiStackOut,
)
from app.services import ai_stack

router = APIRouter(prefix="/companies/{company_id}/ai-stack", tags=["ai-stack"])


@router.get("", response_model=AiStackOut)
def get_stack(request: Request, ctx: CompanyCtx, db: DbSession):
    """Connections in order, readiness, the share summary and the options that can be added."""
    return ai_stack.list_stack(db, ctx, request.app.state.cli_env)


@router.post("", response_model=AiConnectionOut, status_code=201)
def add_connection(body: AiConnectionCreate, request: Request, ctx: CompanyCtx, db: DbSession):
    return ai_stack.add(db, ctx, body, request.app.state.cli_env)


# before /{connection_id}: "order" is not an id
@router.put("/order", response_model=AiStackOut)
def put_order(body: AiOrder, request: Request, ctx: CompanyCtx, db: DbSession):
    return ai_stack.reorder(db, ctx, body.ids, request.app.state.cli_env)


@router.patch("/{connection_id}", response_model=AiConnectionOut)
def patch_connection(
    connection_id: str, body: AiConnectionPatch, request: Request, ctx: CompanyCtx, db: DbSession
):
    return ai_stack.patch(db, ctx, connection_id, body, request.app.state.cli_env)


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str, request: Request, ctx: CompanyCtx, db: DbSession):
    ai_stack.remove(db, ctx, connection_id, request.app.state.cli_env)
    return Response(status_code=204)


@router.post("/{connection_id}/test", response_model=AiConnectionOut)
async def test_connection(connection_id: str, request: Request, ctx: CompanyCtx, db: DbSession):
    """API: the provider's live test for the model. CLI: a tiny headless prompt, own auth."""
    state = request.app.state
    return await run_in_threadpool(
        lambda: ai_stack.test(
            db,
            ctx,
            connection_id,
            cipher=get_cipher(request),
            cli_env=state.cli_env,
            adapters=state.runtimes,
            transport=getattr(state, "provider_transport", None),
        )
    )


@router.get("/{connection_id}/login", response_model=ClaudeLoginStatus)
def get_login(
    connection_id: str, request: Request, ctx: CompanyCtx, db: DbSession, refresh: int = 0
):
    """Plan sign-in state from the CLI's own status command (cached 30 s; `?refresh=1` forces)."""
    return ai_stack.login_status(
        db, ctx, connection_id, request.app.state.cli_env, refresh=bool(refresh)
    )


@router.post("/{connection_id}/login", response_model=ClaudeLoginStatus, status_code=202)
def post_login(
    connection_id: str,
    request: Request,
    ctx: CompanyCtx,
    db: DbSession,
    body: ClaudeLoginRequest | None = None,
):
    """Start the CLI's own browser sign-in in the background (Cursor ignores `method`)."""
    state = request.app.state
    return ai_stack.start_login(
        db,
        ctx,
        connection_id,
        state.cli_env,
        state.session_factory,
        method=(body or ClaudeLoginRequest()).method,
    )


@router.delete("/{connection_id}/login", response_model=ClaudeLoginStatus)
def delete_login(connection_id: str, request: Request, ctx: CompanyCtx, db: DbSession):
    """Run the CLI's own logout in this connection's home."""
    return ai_stack.logout(db, ctx, connection_id, request.app.state.cli_env)
