"""Role templates: built-in (code) plus company-custom (table). Resolution, validation, creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Role
from app.models.enums import AgentKind, RoleFamily
from app.models.skill import RoleTemplate, Skill
from app.roles.loader import (
    RESERVED_FAMILIES,
    RoleTemplateSpec,
    TemplateError,
    builtin_templates,
    parse_template,
)
from app.schemas.hr import RoleTemplateCreate
from app.services import audit, events, hr_access


@dataclass(frozen=True)
class TemplateView:
    spec: RoleTemplateSpec
    builtin: bool
    id: str | None = None
    created_by_type: str | None = None
    created_by_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        s = self.spec
        return {
            "slug": s.slug,
            "family": s.family.value,
            "title": s.title,
            "description": s.description,
            "default_kind": s.default_kind.value,
            "default_department": s.default_department,
            "instructions": s.instructions,
            "default_toolsets": list(s.default_toolsets),
            "default_skill_slugs": list(s.default_skill_slugs),
            "required_skill_tags": list(s.required_skill_tags),
            "builtin": self.builtin,
            "id": self.id,
            "created_by_type": self.created_by_type,
            "created_by_id": self.created_by_id,
        }


def _from_row(row: RoleTemplate) -> TemplateView:
    spec = RoleTemplateSpec(
        slug=row.slug,
        family=RoleFamily(row.family),
        title=row.title,
        description=row.description,
        default_kind=AgentKind(row.default_kind),
        default_department=row.default_department,
        instructions=row.instructions,
        default_toolsets=tuple(row.default_toolsets),
        default_skill_slugs=tuple(row.default_skill_slugs),
        required_skill_tags=tuple(row.required_skill_tags),
    )
    return TemplateView(spec, False, row.id, row.created_by_type, row.created_by_id)


def list_templates(db: Session, company_id: str) -> list[TemplateView]:
    views = [TemplateView(spec, True) for spec in builtin_templates().values()]
    rows = db.scalars(
        select(RoleTemplate)
        .where(RoleTemplate.company_id == company_id)
        .order_by(RoleTemplate.created_at, RoleTemplate.id)
    )
    return views + [_from_row(r) for r in rows]


def find_template(db: Session, company_id: str, slug: str) -> TemplateView | None:
    spec = builtin_templates().get(slug)
    if spec is not None:
        return TemplateView(spec, True)
    row = db.scalar(
        select(RoleTemplate).where(RoleTemplate.company_id == company_id, RoleTemplate.slug == slug)
    )
    return _from_row(row) if row else None


def get_template(db: Session, company_id: str, slug: str) -> TemplateView:
    view = find_template(db, company_id, slug)
    if view is None:
        raise NotFound("Role template not found")
    return view


def list_for(db: Session, ctx: CompanyContext) -> list[TemplateView]:
    """Templates visible to the caller: any human member; agents only if HR or the founder."""
    if ctx.principal.is_agent:
        hr_access.require_hr_or_founder(db, ctx, "list role templates")
    return list_templates(db, ctx.company_id)


def create_template(db: Session, ctx: CompanyContext, data: RoleTemplateCreate) -> TemplateView:
    """Create a company-custom template. Humans (owner/admin) or an HR/founder agent (checked
    by the caller). Agent-authored templates cannot reach privileged toolsets or reserved
    families: a template must never be a way to hand out more power than its author has."""
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    if ctx.principal.is_agent:
        hr_access.require_hr_or_founder(db, ctx, "create role templates")
    elif not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can create role templates")

    try:
        spec = parse_template(
            {
                **data.model_dump(),
                "default_toolsets": tuple(data.default_toolsets),
                "default_skill_slugs": tuple(data.default_skill_slugs),
                "required_skill_tags": tuple(t.strip().lower() for t in data.required_skill_tags),
            }
        )
    except TemplateError as exc:
        raise ValidationFailed(str(exc)) from exc
    if ctx.principal.is_agent:
        privileged = spec.privileged_toolsets()
        if privileged:
            raise Forbidden(
                f"Agents cannot create templates with privileged toolsets: {', '.join(privileged)}"
            )
        if spec.family in RESERVED_FAMILIES or spec.default_kind == AgentKind.FOUNDER:
            raise Forbidden("Agents cannot create founder or HR role templates")
    if spec.default_kind == AgentKind.FOUNDER:
        raise ValidationFailed("Custom templates cannot use the founder kind")
    if find_template(db, ctx.company_id, spec.slug) is not None:
        raise Conflict(f"A role template with slug {spec.slug!r} already exists")

    known = set(
        db.scalars(
            select(Skill.slug).where(
                Skill.company_id == ctx.company_id, Skill.slug.in_(list(spec.default_skill_slugs))
            )
        )
    )
    unknown = [s for s in spec.default_skill_slugs if s not in known]
    if unknown:
        raise ValidationFailed(
            f"Unknown skills in the company library: {', '.join(unknown)}",
            details=[{"loc": ["body", "default_skill_slugs"], "msg": "unknown skill"}],
        )

    row = RoleTemplate(
        company_id=ctx.company_id,
        slug=spec.slug,
        family=spec.family.value,
        title=spec.title,
        description=spec.description,
        default_kind=spec.default_kind.value,
        default_department=spec.default_department,
        instructions=spec.instructions,
        default_toolsets=list(spec.default_toolsets),
        default_skill_slugs=list(spec.default_skill_slugs),
        required_skill_tags=list(spec.required_skill_tags),
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="role_template.created",
        target_type="role_template",
        target_id=row.id,
        summary=f"Created role template {row.slug} ({row.title})",
        data={"slug": row.slug, "family": row.family, "toolsets": row.default_toolsets},
    )
    events.emit(db, ctx.company_id, "role_template.created", {"slug": row.slug})
    db.commit()
    return _from_row(row)
