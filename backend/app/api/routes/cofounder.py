from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CompanyCtx, DbSession
from app.schemas.autonomy import CofounderSummary, CofounderWakeResult
from app.services import cofounder

router = APIRouter(prefix="/companies/{company_id}/cofounder", tags=["cofounder"])


@router.get("/summary", response_model=CofounderSummary)
def summary(ctx: CompanyCtx, db: DbSession):
    return cofounder.get_summary(db, ctx)


@router.post("/wake", response_model=CofounderWakeResult)
def wake(ctx: CompanyCtx, db: DbSession):
    return cofounder.wake(db, ctx)
