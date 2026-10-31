"""M23 company intensity: one control for how hard the company works (docs/specs/m23-intensity.md).

A level (1-5) writes a preset into the *pace* fields of the company policy in one audited change.
It never touches a budget or spend limit, purchasing bands, approval rules, guardrails, headcount
or the AI stack. Owner/admin humans only (agents are refused by ``require_human_admin``).

Plan-only stacks (every enabled AI connection is a personal plan): levels 4-5 need ``confirm`` and a
new company whose stack turns out plan-only starts at level 2 (``apply_plan_only_default``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.models.policy import CompanyPolicy
from app.services import audit, events, policy

DEFAULT_LEVEL = 3
PLAN_ONLY_DEFAULT_LEVEL = 2
CHANGED_ACTION = "policy.intensity_changed"

NAMES = {1: "Conserve", 2: "Light", 3: "Balanced", 4: "Busy", 5: "Continuous"}

PLAN_WARNING = (
    "Personal plans assume ordinary individual use; high intensity may hit usage limits "
    "- add an API key for heavy autonomy."
)

# The policy fields a level controls (all pace; none is a budget). Rows of the spec that have no
# policy field are skipped: the market radar's cadence is fixed in its built-in routines.
FIELDS = (
    "heartbeat_interval_seconds",
    "max_concurrent_runs",
    "max_planning_runs_per_hour",
    "initiative_enabled",
    "initiative_interval_seconds",
    "meetings_enabled",
    "max_meetings_per_company_per_day",
    "self_check_enabled",
)
_COLUMNS = FIELDS

# At level 1 the initiative and meetings are off; their numeric companions keep the level-2 values
# (inert while the switch is off) so that re-applying a level is deterministic.
PRESETS: dict[int, dict[str, Any]] = {
    1: dict(zip(_COLUMNS, (7200, 1, 2, False, 43_200, False, 4, False), strict=True)),
    2: dict(zip(_COLUMNS, (3600, 2, 4, True, 43_200, True, 4, True), strict=True)),
    3: dict(zip(_COLUMNS, (1800, 2, 6, True, 21_600, True, 12, True), strict=True)),
    4: dict(zip(_COLUMNS, (900, 3, 10, True, 10_800, True, 20, True), strict=True)),
    5: dict(zip(_COLUMNS, (300, 4, 15, True, 3_600, True, 30, True), strict=True)),
}


class IntensityConfirmRequired(Conflict):
    code = "intensity_confirm_required"
    default_message = PLAN_WARNING + " Confirm to continue."


def preset(level: int) -> dict[str, Any]:
    return dict(PRESETS[level])


def effective(row: CompanyPolicy) -> dict[str, Any]:
    return {f: getattr(row, f) for f in FIELDS}


def plan_only(db: Session, company_id: str) -> bool:
    """True when the company has at least one enabled AI connection and every one is a personal plan
    (Claude Code / Cursor signed in with a plan)."""
    from app.services import ai_stack

    ai_stack.ensure_bootstrapped(db, company_id)
    rows = [r for r in ai_stack._rows(db, company_id) if r.enabled]
    return bool(rows) and all(ai_stack.is_plan(r) for r in rows)


def view(db: Session, company_id: str, row: CompanyPolicy) -> dict[str, Any]:
    only = plan_only(db, company_id)
    level = row.intensity if row.intensity in PRESETS else DEFAULT_LEVEL
    return {
        "level": level,
        "name": NAMES[level],
        "custom": bool(row.intensity_custom),
        "preset": preset(level),
        "effective": effective(row),
        "plan_only": only,
        "warning": PLAN_WARNING if only else None,
    }


def get(db: Session, ctx: CompanyContext) -> dict[str, Any]:
    row = policy.load_policy(db, ctx.company_id)
    out = view(db, ctx.company_id, row)
    db.commit()  # persists a lazily created policy row or the first stack bootstrap
    return out


def _apply(db: Session, company_id: str, row: CompanyPolicy, level: int) -> dict[str, Any]:
    """Write the preset into the policy (no commit). Returns the before/after values of the fields
    that changed. Reserved management slots stay >= 1 and below the concurrency cap (when > 1)."""
    target = PRESETS[level]
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field, value in target.items():
        if getattr(row, field) != value:
            before[field] = getattr(row, field)
            after[field] = value
            setattr(row, field, value)
    if row.max_concurrent_runs > 1:
        slots = min(max(row.management_reserved_slots, 1), row.max_concurrent_runs - 1)
        if slots != row.management_reserved_slots:
            before["management_reserved_slots"] = row.management_reserved_slots
            after["management_reserved_slots"] = slots
            row.management_reserved_slots = slots
    if "heartbeat_interval_seconds" in after:
        from app.services import builtin_schedules

        builtin_schedules.sync_heartbeat_interval(
            db, company_id, row.heartbeat_interval_seconds, None
        )
    return {"before": before, "after": after}


def _record(
    db: Session,
    company_id: str,
    principal: Principal | None,
    row: CompanyPolicy,
    *,
    level_before: int,
    custom_before: bool,
    diff: dict[str, Any],
    confirmed: bool = False,
    reason: str | None = None,
) -> None:
    data = {
        "level_before": level_before,
        "level_after": row.intensity,
        "custom_before": custom_before,
        "before": diff["before"],
        "after": diff["after"],
        "confirmed": confirmed,
    }
    if reason:
        data["reason"] = reason
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action=CHANGED_ACTION,
        target_type="policy",
        target_id=row.id,
        summary=(
            f"Company intensity set to {row.intensity} ({NAMES[row.intensity]})"
            + (f": {reason}" if reason else "")
        ),
        data=data,
    )
    events.emit(
        db, company_id, "policy.updated", {"changed": ["intensity", *sorted(diff["after"])]}
    )


def set_level(
    db: Session, ctx: CompanyContext, level: int, confirm: bool = False
) -> dict[str, Any]:
    require_human_admin(ctx, "change the company intensity")
    row = policy.load_policy(db, ctx.company_id)
    if level >= 4 and plan_only(db, ctx.company_id) and not confirm:
        raise IntensityConfirmRequired()
    level_before, custom_before = row.intensity, bool(row.intensity_custom)
    diff = _apply(db, ctx.company_id, row, level)
    row.intensity = level
    row.intensity_custom = False
    row.updated_at = utcnow()
    # Always audited, also when nothing changes: an explicit choice (even of the default level) is
    # what stops the plan-only default from moving the company later.
    _record(
        db,
        ctx.company_id,
        ctx.principal,
        row,
        level_before=level_before,
        custom_before=custom_before,
        diff=diff,
        confirmed=confirm,
    )
    out = view(db, ctx.company_id, row)
    db.commit()
    return out


def note_manual_edit(row: CompanyPolicy, changed: list[str]) -> bool:
    """A pace field was edited through the normal policy update: the company is now Custom when a
    changed field's value differs from the current level's preset. True when it set the flag."""
    target = PRESETS.get(row.intensity, PRESETS[DEFAULT_LEVEL])
    if row.intensity_custom:
        return False
    if any(f in target and getattr(row, f) != target[f] for f in changed):
        row.intensity_custom = True
        return True
    return False


def apply_plan_only_default(db: Session, company_id: str, principal: Principal | None) -> bool:
    """Rule: when an AI connection is saved and the stack is now plan-only, a company that is still
    at the untouched default (level 3, not custom, and no intensity change ever audited) moves to
    level 2. No commit. A company that chose a level itself, even level 3, is never moved."""
    row = policy.load_policy(db, company_id)
    if row.intensity != DEFAULT_LEVEL or row.intensity_custom:
        return False
    touched = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.company_id == company_id, AuditEvent.action == CHANGED_ACTION
        )
    )
    if touched or not plan_only(db, company_id):
        return False
    diff = _apply(db, company_id, row, PLAN_ONLY_DEFAULT_LEVEL)
    row.intensity = PLAN_ONLY_DEFAULT_LEVEL
    row.updated_at = utcnow()
    _record(
        db,
        company_id,
        principal,
        row,
        level_before=DEFAULT_LEVEL,
        custom_before=False,
        diff=diff,
        reason="the AI stack is a personal plan only",
    )
    return True
