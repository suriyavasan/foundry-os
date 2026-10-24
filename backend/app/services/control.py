"""Company kill switch: pause stops dispatch and cancels active runs; resume re-opens work."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import InvalidTransition
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.company import Company
from app.models.enums import CompanyStatus
from app.services import audit, companies, events, runs, safety, tasks


def apply_status_effects(db: Session, ctx: CompanyContext | None, company: Company) -> None:
    """Side effects of a status change already applied to ``company`` (no commit). ``ctx`` is
    None when the platform itself paused the company (the circuit breaker)."""
    principal = ctx.principal if ctx is not None else None
    if company.status == CompanyStatus.PAUSED:
        cancelled = runs.request_cancel_all(db, company.id, runs.CANCEL_COMPANY_PAUSED, principal)
        tasks.recompute_readiness(db, company.id, principal=principal)
        audit.record(
            db,
            company_id=company.id,
            principal=principal,
            action="company.paused",
            target_type="company",
            target_id=company.id,
            summary=f"Paused company; cancelling {cancelled} active run(s)",
            data={"runs_cancelled": cancelled},
        )
    else:
        tasks.recompute_readiness(db, company.id, principal=principal)
        safety.resolve_breaker_alert(db, company.id, principal)
        audit.record(
            db,
            company_id=company.id,
            principal=principal,
            action="company.resumed",
            target_type="company",
            target_id=company.id,
            summary="Resumed company",
            data={},
        )
    events.emit(db, company.id, "company.updated", {"status": company.status})


def pause_company(db: Session, ctx: CompanyContext) -> Company:
    require_human_admin(ctx, "pause the company")
    company = companies.get_company(db, ctx)
    if company.status == CompanyStatus.PAUSED:
        raise InvalidTransition("Company is already paused", details={"from": "paused"})
    company.status = CompanyStatus.PAUSED
    company.updated_at = utcnow()
    apply_status_effects(db, ctx, company)
    db.commit()
    return company


def resume_company(db: Session, ctx: CompanyContext) -> Company:
    require_human_admin(ctx, "resume the company")
    company = companies.get_company(db, ctx)
    if company.status == CompanyStatus.ACTIVE:
        raise InvalidTransition("Company is not paused", details={"from": "active"})
    company.status = CompanyStatus.ACTIVE
    company.updated_at = utcnow()
    apply_status_effects(db, ctx, company)
    db.commit()
    return company
