"""AI setup: since M17 only the readiness gate's view of the AI stack (``/ai-stack`` changes it)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CompanyCtx, DbSession
from app.core.errors import Gone
from app.schemas.ai_setup import AiSetupStatus
from app.services import ai_setup

router = APIRouter(prefix="/companies/{company_id}/ai-setup", tags=["ai-setup"])


@router.get("", response_model=AiSetupStatus)
def get_ai_setup(request: Request, ctx: CompanyCtx, db: DbSession):
    """Is this company's AI ready? Derived from the AI stack, plus the coding CLIs on this machine."""
    return ai_setup.status(db, ctx, request.app.state.cli_env)


@router.put("", status_code=410, include_in_schema=False)
def put_ai_setup(ctx: CompanyCtx):
    """Removed in M17."""
    raise Gone("Use /ai-stack to manage the company's AI")
