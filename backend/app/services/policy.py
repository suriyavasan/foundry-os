"""Company policy: the hard limits of an autonomous company. Only humans may change it."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.policy import DEFAULT_HUMAN_APPROVAL_KINDS, CompanyPolicy
from app.models.provider import ModelProvider
from app.schemas.policy import PolicyUpdate
from app.services import audit, events

UPDATABLE = (
    "autonomy_mode",
    "human_approval_required_for",
    "daily_budget_micros",
    "monthly_budget_micros",
    "max_concurrent_runs",
    "max_delegation_depth",
    "max_tasks_per_planning_cycle",
    "max_attempts_per_task",
    "max_revisions",
    "max_run_seconds",
    "max_headcount",
    "heartbeat_interval_seconds",
    "default_run_reservation_micros",
    "allowed_model_providers",
    "auto_activate_hires",
    "hire_default_monthly_budget_micros",
    "max_agent_monthly_budget_micros",
    "max_planning_runs_per_hour",
    "max_open_tasks",
    "circuit_breaker_failures",
    "default_model_provider_id",
    "default_model_name",
    "sandbox_backend",
    "budget_alert_thresholds",
    "model_routing",
    "allow_paid_fallback",
    "auto_spend_limit_micros",
    "stall_seconds",
    "max_tool_calls_per_run",
    "max_artifacts_per_run",
    "max_tokens_per_run",
    "ai_disclosure_enabled",
    "ai_disclosure_text",
)
_BUDGET_FIELDS = {"daily_budget_micros", "monthly_budget_micros", "default_run_reservation_micros"}
# Changing these re-evaluates tasks the dispatcher parked behind the paid-model approval.
_ROUTING_FIELDS = {"model_routing", "allow_paid_fallback", "auto_spend_limit_micros"}


def _check_default_model(
    db: Session, company_id: str, current: CompanyPolicy, data: PolicyUpdate
) -> None:
    """The company default model must be an enabled provider of this company (404 otherwise)."""
    given = data.model_fields_set
    if "default_model_provider_id" not in given or data.default_model_provider_id is None:
        return
    found = db.scalar(
        select(ModelProvider.id).where(
            ModelProvider.id == data.default_model_provider_id,
            ModelProvider.company_id == company_id,
            ModelProvider.enabled.is_(True),
        )
    )
    if found is None:
        raise NotFound("Model provider not found")


def create_default_policy(db: Session, company_id: str) -> CompanyPolicy:
    policy = CompanyPolicy(
        company_id=company_id, human_approval_required_for=list(DEFAULT_HUMAN_APPROVAL_KINDS)
    )
    db.add(policy)
    db.flush()
    return policy


def load_policy(db: Session, company_id: str) -> CompanyPolicy:
    """Policy for a company; created lazily for companies inserted outside the service."""
    policy = db.scalar(select(CompanyPolicy).where(CompanyPolicy.company_id == company_id))
    if policy is None:
        policy = create_default_policy(db, company_id)
    return policy


def get_policy(db: Session, ctx: CompanyContext) -> CompanyPolicy:
    policy = load_policy(db, ctx.company_id)
    db.commit()  # persists a lazily created row; no-op otherwise
    return policy


def update_policy(db: Session, ctx: CompanyContext, data: PolicyUpdate) -> CompanyPolicy:
    # Agents (including via their own run token) are rejected here: 403.
    require_human_admin(ctx, "change company policy")
    policy = load_policy(db, ctx.company_id)
    _check_default_model(db, ctx.company_id, policy, data)
    changed: list[str] = []
    for field in UPDATABLE:
        if field not in data.model_fields_set:
            continue
        new = getattr(data, field)
        if isinstance(new, list):
            new = list(new)
        if getattr(policy, field) != new:
            setattr(policy, field, new)
            changed.append(field)
    if changed:
        policy.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="policy.updated",
            target_type="policy",
            target_id=policy.id,
            summary=f"Updated company policy: {', '.join(changed)}",
            data={"changed": changed},
        )
        events.emit(db, ctx.company_id, "policy.updated", {"changed": changed})
        from app.services import tasks  # local import: tasks depends on policy

        if _BUDGET_FIELDS & set(changed):
            tasks.release_budget_blocked(db, ctx.company_id, ctx.principal)
        if "heartbeat_interval_seconds" in changed:
            from app.services import builtin_schedules

            builtin_schedules.sync_heartbeat_interval(
                db, ctx.company_id, policy.heartbeat_interval_seconds, ctx.principal
            )
        if _ROUTING_FIELDS & set(changed):
            tasks.release_budget_blocked(
                db, ctx.company_id, ctx.principal, codes=("paid_model_approval",)
            )
        if "allowed_model_providers" in changed:
            tasks.release_budget_blocked(
                db, ctx.company_id, ctx.principal, codes=("provider_not_allowed",)
            )
        db.commit()
    return policy
