from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.governance import (
    Approval,
    ApprovalStatusLiteral,
    ApproverLiteral,
    Decision,
    DecisionBody,
)
from app.schemas.guardrails import ActionTierLiteral
from app.services import approvals, decisions
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["approvals"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/approvals", response_model=Page[Approval])
def list_approvals(
    ctx: CompanyCtx,
    db: DbSession,
    status: ApprovalStatusLiteral | None = None,
    approver: ApproverLiteral | None = None,
    tier: ActionTierLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = approvals.list_approvals(
        db, ctx, limit=limit, cursor=cursor, status=status, approver=approver, tier=tier
    )
    return {"items": items, "next_cursor": nxt}


@router.get("/approvals/{approval_id}", response_model=Approval)
def get_approval(approval_id: str, ctx: CompanyCtx, db: DbSession):
    return approvals.get_approval(db, ctx, approval_id)


@router.post("/approvals/{approval_id}/approve", response_model=Approval)
def approve(approval_id: str, ctx: CompanyCtx, db: DbSession, body: DecisionBody | None = None):
    return approvals.decide(db, ctx, approval_id, "approve", body.note if body else None)


@router.post("/approvals/{approval_id}/reject", response_model=Approval)
def reject(approval_id: str, ctx: CompanyCtx, db: DbSession, body: DecisionBody | None = None):
    return approvals.decide(db, ctx, approval_id, "reject", body.note if body else None)


@router.post("/approvals/{approval_id}/request_changes", response_model=Approval)
def request_changes(
    approval_id: str, ctx: CompanyCtx, db: DbSession, body: DecisionBody | None = None
):
    return approvals.decide(db, ctx, approval_id, "request_changes", body.note if body else None)


@router.get("/decisions", response_model=Page[Decision])
def list_decisions(
    ctx: CompanyCtx,
    db: DbSession,
    related_task_id: str | None = None,
    related_project_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = decisions.list_decisions(
        db,
        ctx,
        limit=limit,
        cursor=cursor,
        related_task_id=related_task_id,
        related_project_id=related_project_id,
    )
    return {"items": items, "next_cursor": nxt}
