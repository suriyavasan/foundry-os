"""Skills & HR owner API (``/api/companies/{cid}/...``). Thin: rules live in the services."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import CompanyCtx, DbSession, Skills
from app.schemas.common import Page
from app.schemas.hr import RoleTemplate, RoleTemplateCreate
from app.schemas.skills import (
    AgentSkill,
    CatalogItem,
    Skill,
    SkillDetail,
    SkillFileContent,
    SkillInstall,
    SkillInstallResult,
    SkillNote,
    SkillRefs,
    SkillSeedAccepted,
    SkillSeedResult,
)
from app.services import role_templates, skills
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["skills"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def install_result(result: skills.ImportResult) -> SkillInstallResult:
    return SkillInstallResult(
        skill=Skill.model_validate(result.skill),
        outcome=result.outcome,  # type: ignore[arg-type]
        quarantined=result.quarantined,
        scan=result.scan,
    )


def agent_skill_out(link) -> AgentSkill:
    return AgentSkill(
        skill=Skill.model_validate(link.skill),
        assigned_by_type=link.assigned_by_type,
        assigned_by_id=link.assigned_by_id,
        assigned_at=link.created_at,
    )


@router.get("/skills", response_model=Page[Skill])
def list_skills(
    ctx: CompanyCtx,
    db: DbSession,
    status: str | None = None,
    source_kind: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = skills.list_skills(
        db,
        ctx,
        limit=limit,
        cursor=cursor,
        status=status,
        source_kind=source_kind,
        tag=tag,
        q=q,
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/skills/catalog", response_model=Page[CatalogItem])
def skill_catalog(
    ctx: CompanyCtx,
    db: DbSession,
    env: Skills,
    q: str | None = None,
    source: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = skills.catalog(
        db, ctx, env, q=q, source=source, limit=limit, cursor=cursor
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/skills/install", response_model=SkillInstallResult)
def install_skill(
    body: SkillInstall, response: Response, ctx: CompanyCtx, db: DbSession, env: Skills
):
    result = skills.install_skill(db, ctx, env, body.source_ref, slug=body.slug)
    response.status_code = 201 if result.outcome == "installed" else 200
    return install_result(result)


@router.post("/skills/seed", response_model=SkillSeedAccepted | SkillSeedResult)
def seed_library(
    response: Response, ctx: CompanyCtx, db: DbSession, env: Skills, wait: bool = False
):
    """Queue the library seeding job (202); ``?wait=true`` runs it inline and returns the result."""
    if wait:
        return skills.seed_now(db, ctx, env).__dict__
    job_id, status = skills.enqueue_seed(db, ctx)
    response.status_code = 202
    return {"job_id": job_id, "status": status}


@router.get("/skills/{skill_id}", response_model=SkillDetail)
def get_skill(skill_id: str, ctx: CompanyCtx, db: DbSession, env: Skills):
    detail = skills.skill_detail(db, ctx, env, skill_id)
    out = SkillDetail.model_validate(
        {
            **Skill.model_validate(detail["skill"]).model_dump(),
            "scan_report": detail["scan_report"],
            "frontmatter": detail["frontmatter"],
            "files": detail["files"],
            "assigned_agents": detail["assigned_agents"],
        }
    )
    return out


@router.get("/skills/{skill_id}/files/{path:path}", response_model=SkillFileContent)
def get_skill_file(skill_id: str, path: str, ctx: CompanyCtx, db: DbSession, env: Skills):
    return skills.read_skill_file(db, ctx, env, skill_id, path)


@router.post("/skills/{skill_id}/archive", response_model=Skill)
def archive_skill(skill_id: str, ctx: CompanyCtx, db: DbSession, body: SkillNote | None = None):
    return skills.archive_skill(db, ctx, skill_id, body.note if body else None)


@router.post("/skills/{skill_id}/unquarantine", response_model=Skill)
def unquarantine_skill(
    skill_id: str, ctx: CompanyCtx, db: DbSession, body: SkillNote | None = None
):
    return skills.unquarantine_skill(db, ctx, skill_id, body.note if body else None)


@router.get("/agents/{agent_id}/skills", response_model=Page[AgentSkill])
def list_agent_skills(agent_id: str, ctx: CompanyCtx, db: DbSession):
    rows = skills.list_agent_skills(db, ctx, agent_id)
    return {"items": [agent_skill_out(r) for r in rows], "next_cursor": None}


@router.post("/agents/{agent_id}/skills", response_model=Page[AgentSkill])
def assign_agent_skills(agent_id: str, body: SkillRefs, ctx: CompanyCtx, db: DbSession):
    rows = skills.assign_skills(db, ctx, agent_id, skill_ids=body.skill_ids, slugs=body.slugs)
    return {"items": [agent_skill_out(r) for r in rows], "next_cursor": None}


@router.delete("/agents/{agent_id}/skills/{skill_ref}", status_code=204)
def remove_agent_skill(agent_id: str, skill_ref: str, ctx: CompanyCtx, db: DbSession):
    skills.unassign_skill(db, ctx, agent_id, skill_ref)
    return Response(status_code=204)


@router.get("/role-templates", response_model=Page[RoleTemplate])
def list_role_templates(ctx: CompanyCtx, db: DbSession):
    views = role_templates.list_for(db, ctx)
    return {"items": [v.as_dict() for v in views], "next_cursor": None}


@router.post("/role-templates", response_model=RoleTemplate, status_code=201)
def create_role_template(body: RoleTemplateCreate, ctx: CompanyCtx, db: DbSession):
    return role_templates.create_template(db, ctx, body).as_dict()
