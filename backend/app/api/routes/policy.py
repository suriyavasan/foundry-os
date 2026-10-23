from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CompanyCtx, DbSession
from app.schemas.policy import Policy, PolicyUpdate
from app.services import policy

router = APIRouter(prefix="/companies/{company_id}/policy", tags=["policy"])


@router.get("", response_model=Policy)
def get_policy(ctx: CompanyCtx, db: DbSession):
    return policy.get_policy(db, ctx)


@router.patch("", response_model=Policy)
def update_policy(body: PolicyUpdate, ctx: CompanyCtx, db: DbSession):
    return policy.update_policy(db, ctx, body)
