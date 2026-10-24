"""Guardrails (owner API): rules, the event log, rate-limit status and an agent's effective
permissions. Rules are editable by humans only; agents (even with a run token) get 403."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import CompanyCtx, DbSession
from app.core.errors import Forbidden
from app.core.time import utcnow
from app.guardrails import log, permissions, ratelimit, rules
from app.schemas.common import Page
from app.schemas.guardrails import (
    ActionTierLiteral,
    EffectivePermissions,
    GuardrailEvent,
    GuardrailEventDecisionLiteral,
    GuardrailRule,
    GuardrailRuleCreate,
    GuardrailRuleUpdate,
    RateLimitList,
)
from app.services.common import get_agent_or_404
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["guardrails"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/guardrails/rules", response_model=Page[GuardrailRule])
def list_rules(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, next_cursor = rules.list_rules(db, ctx, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.post("/guardrails/rules", response_model=GuardrailRule, status_code=201)
def create_rule(body: GuardrailRuleCreate, ctx: CompanyCtx, db: DbSession):
    return rules.create_rule(db, ctx, body)


@router.get("/guardrails/rules/{rule_id}", response_model=GuardrailRule)
def get_rule(rule_id: str, ctx: CompanyCtx, db: DbSession):
    return rules.get_rule(db, ctx, rule_id)


@router.patch("/guardrails/rules/{rule_id}", response_model=GuardrailRule)
def update_rule(rule_id: str, body: GuardrailRuleUpdate, ctx: CompanyCtx, db: DbSession):
    return rules.update_rule(db, ctx, rule_id, body)


@router.delete("/guardrails/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, ctx: CompanyCtx, db: DbSession):
    rules.delete_rule(db, ctx, rule_id)
    return Response(status_code=204)


@router.get("/guardrails/events", response_model=Page[GuardrailEvent])
def list_events(
    ctx: CompanyCtx,
    db: DbSession,
    decision: GuardrailEventDecisionLiteral | None = None,
    tier: ActionTierLiteral | None = None,
    agent_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot read the guardrail log")
    items, next_cursor = log.list_events(
        db, ctx, limit=limit, cursor=cursor, decision=decision, tier=tier, agent_id=agent_id
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/guardrails/rate-limits", response_model=RateLimitList)
def rate_limits(ctx: CompanyCtx, db: DbSession, agent_id: str | None = None):
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot read rate-limit status")
    if agent_id:
        get_agent_or_404(db, ctx.company_id, agent_id, allow_gone=True)
    windows = ratelimit.status(db, ctx.company_id, utcnow(), agent_id)
    return {"items": [permissions.rate_status(w) for w in windows], "next_cursor": None}


@router.get("/agents/{agent_id}/effective-permissions", response_model=EffectivePermissions)
def effective_permissions(agent_id: str, ctx: CompanyCtx, db: DbSession):
    return permissions.effective_permissions(db, ctx, agent_id)
