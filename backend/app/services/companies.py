from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.models.agent import Agent
from app.models.company import Company, CompanyMember
from app.models.enums import AgentKind, AgentStatus, CompanyStatus, MemberRole
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.services import audit, policy
from app.services.pagination import paginate

UPDATABLE = ("name", "mission", "vision", "strategic_goal", "constraints", "timezone", "status")


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:100] or "company"


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    taken = set(db.scalars(select(Company.slug).where(Company.slug.like(f"{base}%"))))
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def create_company(db: Session, principal: Principal, data: CompanyCreate) -> Company:
    if not principal.is_human:
        raise Forbidden("Only human users can create companies")
    company = Company(
        name=data.name,
        slug=unique_slug(db, data.name),
        mission=data.mission,
        vision=data.vision,
        strategic_goal=data.strategic_goal,
        constraints=list(data.constraints),
        timezone=data.timezone,
        status=CompanyStatus.ACTIVE,
    )
    db.add(company)
    db.flush()
    db.add(CompanyMember(company_id=company.id, user_id=principal.id, role=MemberRole.OWNER))
    policy.create_default_policy(db, company.id)
    founder = Agent(
        company_id=company.id,
        name=data.founder.name,
        role_title="Founder & CEO",
        kind=AgentKind.FOUNDER,
        status=AgentStatus.ACTIVE,
        reports_to_human=True,
        model_provider=data.founder.model_provider,
        model_name=data.founder.model_name,
        runtime=data.founder.runtime,
        runtime_config={},
        tool_permissions=[],
    )
    db.add(founder)
    db.flush()
    audit.record(
        db,
        company_id=company.id,
        principal=principal,
        action="company.created",
        target_type="company",
        target_id=company.id,
        summary=f"Created company {company.name} with founder {founder.name}",
        data={"founder_agent_id": founder.id, "slug": company.slug},
    )
    try:
        db.commit()
    except IntegrityError as exc:  # slug race with a concurrent create
        db.rollback()
        raise Conflict("Could not allocate a unique company slug; retry") from exc
    return company


def list_companies(
    db: Session, principal: Principal, *, limit: int, cursor: str | None
) -> tuple[list[Company], str | None]:
    if principal.is_human:
        member_ids = select(CompanyMember.company_id).where(CompanyMember.user_id == principal.id)
        stmt = select(Company).where(Company.id.in_(member_ids))
    elif principal.company_id:
        stmt = select(Company).where(Company.id == principal.company_id)
    else:
        return [], None
    return paginate(db, stmt, Company, limit=limit, cursor=cursor, descending=False)


def get_company(db: Session, ctx: CompanyContext) -> Company:
    company = db.scalar(select(Company).where(Company.id == ctx.company_id))
    if company is None:
        raise NotFound("Company not found")
    return company


def update_company(db: Session, ctx: CompanyContext, data: CompanyUpdate) -> Company:
    require_human_admin(ctx, "update company settings")
    company = get_company(db, ctx)
    changed: list[str] = []
    for field in UPDATABLE:
        if field not in data.model_fields_set:
            continue
        new = getattr(data, field)
        if field == "constraints":
            new = list(new)
        if getattr(company, field) != new:
            setattr(company, field, new)
            changed.append(field)
    if "status" in changed:
        from app.services import control  # local import: control depends on this module

        control.apply_status_effects(db, ctx, company)
    if changed:
        audit.record(
            db,
            company_id=company.id,
            principal=ctx.principal,
            action="company.updated",
            target_type="company",
            target_id=company.id,
            summary=f"Updated company settings: {', '.join(changed)}",
            data={"changed": changed},
        )
        db.commit()
    return company
