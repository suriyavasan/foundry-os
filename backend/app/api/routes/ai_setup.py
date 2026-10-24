"""AI setup (M10): the readiness gate and the work mode. Owner/admin write, any member reads."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CompanyCtx, DbSession
from app.schemas.ai_setup import AiSetupStatus, AiSetupUpdate
from app.services import ai_setup

router = APIRouter(prefix="/companies/{company_id}/ai-setup", tags=["ai-setup"])


@router.get("", response_model=AiSetupStatus)
def get_ai_setup(request: Request, ctx: CompanyCtx, db: DbSession):
    """Is this company's AI ready (a tested management model)? Plus the work mode and the coding
    CLIs on this machine."""
    return ai_setup.status(db, ctx, request.app.state.cli_env)


@router.put("", response_model=AiSetupStatus)
def put_ai_setup(body: AiSetupUpdate, request: Request, ctx: CompanyCtx, db: DbSession):
    """Select the management model and the work mode. 409 ``ai_not_ready`` (with
    ``details.reason``) when the provider's latest test did not pass, or a chosen CLI is not ready."""
    return ai_setup.update(db, ctx, body, request.app.state.cli_env)
