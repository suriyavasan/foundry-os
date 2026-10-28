"""Company policy: the hard limits of an autonomous company. Only humans may change it."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.policy import (
    DEFAULT_HUMAN_APPROVAL_KINDS,
    MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS,
    MAX_PURCHASE_AUTO_APPROVE_MICROS,
    MAX_PURCHASE_COFOUNDER_LIMIT_MICROS,
    CompanyPolicy,
)
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
    "work_model_provider_id",
    "work_model_name",
    "cli_role_families",
    "cli_run_reservation_micros",
    "management_reserved_slots",
    "max_parallel_runs_per_agent",
    "max_work_tasks_per_project",
    "max_subtasks_per_task",
    "circuit_breaker_cooldown_seconds",
    "initiative_enabled",
    "initiative_interval_seconds",
    "project_auto_complete",
    "new_project_repository",
    # M15 purchasing (human-only; the ceilings are checked in ``_check_purchasing``)
    "purchasing_enabled",
    "purchase_auto_approve_micros",
    "purchase_agent_auto_monthly_micros",
    "purchase_cofounder_limit_micros",
    "purchase_cofounder_monthly_micros",
    "purchase_monthly_budget_micros",
    "purchase_recurring_requires_human",
    "purchase_new_vendor_requires_human",
    "purchase_tolerance_bps",
    "purchase_tolerance_min_micros",
    "purchase_card_ttl_hours",
    "purchase_checkout_member_id",
    "subscription_renewal_alert_days",
    # M16 radar (human-only)
    "radar_enabled",
    "radar_max_fetches_per_day",
    "radar_max_items_per_day",
    "radar_max_ideas_per_day",
    "radar_max_open_ideas",
    "radar_auto_accept",
    "radar_max_goals_per_week",
    "radar_agent_sources",
    "self_critique_enabled",
    "radar_contact_email",
)
PURCHASE_FIELDS = frozenset(f for f in UPDATABLE if f.startswith(("purchase", "subscription_")))
# What an AI may approve is capped in code, whatever the form sends (docs/specs/m15, section 5).
_PURCHASE_CEILINGS = {
    "purchase_auto_approve_micros": MAX_PURCHASE_AUTO_APPROVE_MICROS,
    "purchase_agent_auto_monthly_micros": MAX_PURCHASE_AGENT_AUTO_MONTHLY_MICROS,
    "purchase_cofounder_limit_micros": MAX_PURCHASE_COFOUNDER_LIMIT_MICROS,
}
RADAR_FIELDS = frozenset(f for f in UPDATABLE if f.startswith("radar_")) | {"self_critique_enabled"}
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


def _check_work_model(
    db: Session, company_id: str, current: CompanyPolicy, data: PolicyUpdate
) -> None:
    """The optional work model is an enabled provider of this company (404 otherwise) and always
    comes as a pair: provider and model name are both set or both empty (422)."""
    given = data.model_fields_set
    if not ({"work_model_provider_id", "work_model_name"} & given):
        return
    provider = (
        data.work_model_provider_id
        if "work_model_provider_id" in given
        else current.work_model_provider_id
    )
    name = data.work_model_name if "work_model_name" in given else current.work_model_name
    if bool(provider) != bool(name):
        raise ValidationFailed(
            "work_model_provider_id and work_model_name must be set together (or both cleared)",
            details=[{"loc": ["body", "work_model_name"], "msg": "set together with the provider"}],
        )
    if provider and "work_model_provider_id" in given:
        found = db.scalar(
            select(ModelProvider.id).where(
                ModelProvider.id == provider,
                ModelProvider.company_id == company_id,
                ModelProvider.enabled.is_(True),
            )
        )
        if found is None:
            raise NotFound("Model provider not found")


def _check_slots(current: CompanyPolicy, data: PolicyUpdate) -> None:
    """Reserved management slots must leave room for work: ``management_reserved_slots <
    max_concurrent_runs``. Checked when the request sets the slots (lowering ``max_concurrent_runs``
    alone is never refused; the dispatcher clamps the effective reservation to ``max - 1``)."""
    if "management_reserved_slots" not in data.model_fields_set:
        return
    max_runs = (
        data.max_concurrent_runs
        if "max_concurrent_runs" in data.model_fields_set
        else current.max_concurrent_runs
    )
    if data.management_reserved_slots >= max_runs:
        raise ValidationFailed(
            "management_reserved_slots must be lower than max_concurrent_runs",
            details=[
                {
                    "loc": ["body", "management_reserved_slots"],
                    "msg": f"must be lower than max_concurrent_runs ({max_runs})",
                }
            ],
        )


def _check_purchasing(
    db: Session, company_id: str, current: CompanyPolicy, data: PolicyUpdate
) -> None:
    """Ceilings on what an AI may approve (the schema checks them too; the service is the rule) and
    a checkout person who really is an active, assignable member of this company."""
    given = data.model_fields_set
    for field, ceiling in _PURCHASE_CEILINGS.items():
        value = getattr(data, field) if field in given else None
        if value is not None and value > ceiling:
            raise ValidationFailed(
                f"{field} cannot be above {ceiling} micro-USD (a code ceiling on what an AI may "
                "approve); the Founder can still approve any purchase by hand",
                details=[{"loc": ["body", field], "msg": f"must be at most {ceiling}"}],
            )
    member_id = data.purchase_checkout_member_id if "purchase_checkout_member_id" in given else None
    if member_id:
        from app.services import org  # local: org -> policy

        org.get_assignable_member(db, company_id, member_id)  # 404 / 422 / 409


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
    _check_work_model(db, ctx.company_id, policy, data)
    _check_slots(policy, data)
    _check_purchasing(db, ctx.company_id, policy, data)
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
        bought = sorted(PURCHASE_FIELDS & set(changed))
        if bought:  # its own line in the trail: who loosened or tightened what agents may buy
            audit.record(
                db,
                company_id=ctx.company_id,
                principal=ctx.principal,
                action="purchase_policy.updated",
                target_type="policy",
                target_id=policy.id,
                summary=f"Updated the purchasing policy: {', '.join(bought)}",
                data={"changed": bought, "values": {f: getattr(policy, f) for f in bought}},
            )
        radar = sorted(RADAR_FIELDS & set(changed))
        if radar:  # M16: its own line in the trail: who changed what the company reads and decides
            audit.record(
                db,
                company_id=ctx.company_id,
                principal=ctx.principal,
                action="radar_policy.updated",
                target_type="policy",
                target_id=policy.id,
                summary=f"Updated the market radar policy: {', '.join(radar)}",
                data={"changed": radar, "values": {f: getattr(policy, f) for f in radar}},
            )
        from app.services import tasks  # local import: tasks depends on policy

        if _BUDGET_FIELDS & set(changed):
            tasks.release_budget_blocked(db, ctx.company_id, ctx.principal)
        if "heartbeat_interval_seconds" in changed:
            from app.services import builtin_schedules

            builtin_schedules.sync_heartbeat_interval(
                db, ctx.company_id, policy.heartbeat_interval_seconds, ctx.principal
            )
        if "radar_enabled" in changed and policy.radar_enabled:  # M16 radar: provision the routines
            from app.services import builtin_schedules

            builtin_schedules.ensure_radar_schedules(db, ctx.company_id, ctx.principal)
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
