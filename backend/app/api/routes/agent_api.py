"""Agent API (``/api/agent/v1``): what a running agent may do, authenticated by its run token.

Everything is attributed to the agent and audited by the shared services. Policy, budgets,
permissions and the agent's own lifecycle are not reachable from here.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import DbSession, RunCtx, Skills, get_storage
from app.api.routes.skills import agent_skill_out, install_result
from app.api.uploads import read_artifact_input
from app.schemas.agent import Agent as AgentOut
from app.schemas.agent_api import (
    AgentContext,
    AgentTaskComplete,
    AgentTaskPatch,
    ProgressBody,
    ProgressOut,
)
from app.schemas.artifact import ArtifactDetail, ArtifactVersion
from app.schemas.common import Page
from app.schemas.governance import (
    AgentDecideBody,
    Approval,
    ApprovalCreate,
    ApprovalCreated,
    Decision,
    DecisionCreate,
)
from app.schemas.hr import (
    Colleague,
    HireCreate,
    HireResult,
    HiringRequestCreate,
    HiringRequestResult,
    RoleTemplate,
    RoleTemplateCreate,
)
from app.schemas.skills import (
    AgentSkill,
    CatalogItem,
    SkillAuthor,
    SkillInstall,
    SkillInstallResult,
    SkillRefs,
)
from app.schemas.work import ReviewBody, Task, TaskCreate
from app.services import (
    agent_actions,
    agent_context,
    approvals,
    artifacts,
    decisions,
    hiring,
    role_templates,
    skills,
    tasks,
)
from app.storage.base import ArtifactStorage

router = APIRouter(prefix="/agent/v1", tags=["agent-api"])


def _one(db, task) -> Task:
    return tasks.serialize_tasks(db, [task])[0]


@router.get("/context", response_model=AgentContext)
def get_context(rc: RunCtx, db: DbSession):
    return agent_context.build_context(db, rc)


@router.post("/tasks", response_model=Task, status_code=201)
def create_task(body: TaskCreate, response: Response, rc: RunCtx, db: DbSession):
    task, created = tasks.create_task_as_agent(db, rc.ctx, rc.run, body)
    if not created:
        response.status_code = 200
    return _one(db, task)


@router.patch("/tasks/{task_id}", response_model=Task)
def patch_task(task_id: str, body: AgentTaskPatch, rc: RunCtx, db: DbSession):
    task = tasks.agent_update_task(
        db,
        rc.ctx,
        rc.run,
        task_id,
        status=body.status,
        reason=body.reason,
        assignee_agent_id=body.assignee_agent_id,
        assignee_given="assignee_agent_id" in body.model_fields_set,
    )
    return _one(db, task)


@router.post("/tasks/{task_id}/complete", response_model=Task)
def complete_task(task_id: str, body: AgentTaskComplete, rc: RunCtx, db: DbSession):
    task = tasks.complete_task_as_agent(
        db,
        rc.ctx,
        rc.run,
        task_id,
        result_summary=body.result_summary,
        output_refs=body.output_refs,
    )
    return _one(db, task)


@router.post("/tasks/{task_id}/review", response_model=Task)
def review_task(task_id: str, body: ReviewBody, rc: RunCtx, db: DbSession):
    return _one(db, tasks.review_task(db, rc.ctx, task_id, body.decision, body.note))


@router.post("/artifacts", response_model=ArtifactDetail, status_code=201)
async def submit_artifact(
    request: Request,
    rc: RunCtx,
    db: DbSession,
    storage: ArtifactStorage = Depends(get_storage),
):
    """Multipart (``file`` + title/kind/task_id) or JSON ``{title, kind, content}``; pass
    ``artifact_id`` to add a version. ``task_id`` defaults to the current task."""
    max_bytes = request.app.state.settings.max_upload_bytes
    data = await read_artifact_input(request, max_bytes)
    if data.task_id is None and data.artifact_id is None:
        data = replace(data, task_id=rc.run.task_id)

    def work():
        artifact, _ = artifacts.create_or_version(
            db, rc.ctx, storage, data, max_bytes=max_bytes, run=rc.run
        )
        detail = ArtifactDetail.model_validate(artifact)
        detail.versions = [
            ArtifactVersion.model_validate(v) for v in artifacts.list_versions(db, artifact)
        ]
        return detail

    return await run_in_threadpool(work)


@router.post("/progress", response_model=ProgressOut, status_code=201)
def post_progress(body: ProgressBody, rc: RunCtx, db: DbSession):
    event = agent_actions.post_progress(db, rc, body.message)
    return ProgressOut(run_id=event.run_id, seq=event.seq)


@router.post("/decisions", response_model=Decision, status_code=201)
def record_decision(body: DecisionCreate, rc: RunCtx, db: DbSession):
    return decisions.create_decision(db, rc.ctx, body, default_task_id=rc.run.task_id)


@router.post("/approvals", response_model=ApprovalCreated, status_code=201)
def request_approval(body: ApprovalCreate, rc: RunCtx, db: DbSession):
    return approvals.create_approval(db, rc.ctx, body, default_task_id=rc.run.task_id)


@router.get("/approvals/{approval_id}", response_model=Approval)
def get_approval(approval_id: str, rc: RunCtx, db: DbSession):
    return agent_actions.get_approval_for_agent(db, rc, approval_id)


@router.post("/approvals/{approval_id}/decide", response_model=Approval)
def decide_approval(approval_id: str, body: AgentDecideBody, rc: RunCtx, db: DbSession):
    return approvals.decide(db, rc.ctx, approval_id, body.decision, body.note)


# ---------------------------------------------------------------- Milestone 4: skills & HR


@router.get("/agents", response_model=Page[Colleague])
def find_colleagues(
    rc: RunCtx,
    db: DbSession,
    skill_tags: str | None = None,
    family: str | None = None,
    status: str | None = None,
):
    """Colleague search (any agent). ``skill_tags``: comma-separated, all must match."""
    tags = [t for t in (skill_tags or "").split(",") if t.strip()]
    items = hiring.find_colleagues(db, rc.ctx, skill_tags=tags, family=family, status=status)
    return {"items": items, "next_cursor": None}


@router.get("/agents/{agent_id}/skills", response_model=Page[AgentSkill])
def agent_skills(agent_id: str, rc: RunCtx, db: DbSession):
    rows = skills.list_agent_skills(db, rc.ctx, agent_id)
    return {"items": [agent_skill_out(r) for r in rows], "next_cursor": None}


@router.get("/skills/catalog", response_model=Page[CatalogItem])
def skill_catalog(
    rc: RunCtx,
    db: DbSession,
    env: Skills,
    q: str | None = None,
    source: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
):
    items, next_cursor = skills.catalog(
        db, rc.ctx, env, q=q, source=source, limit=max(1, min(limit, 200)), cursor=cursor
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/skills/install", response_model=SkillInstallResult)
def install_skill(body: SkillInstall, response: Response, rc: RunCtx, db: DbSession, env: Skills):
    result = skills.install_skill(db, rc.ctx, env, body.source_ref, slug=body.slug)
    response.status_code = 201 if result.outcome == "installed" else 200
    return install_result(result)


@router.post("/skills/author", response_model=SkillInstallResult, status_code=201)
def author_skill(body: SkillAuthor, rc: RunCtx, db: DbSession, env: Skills):
    return install_result(skills.author_skill(db, rc.ctx, env, body))


@router.post("/agents/{agent_id}/skills", response_model=Page[AgentSkill])
def assign_skills(agent_id: str, body: SkillRefs, rc: RunCtx, db: DbSession):
    rows = skills.assign_skills(db, rc.ctx, agent_id, skill_ids=body.skill_ids, slugs=body.slugs)
    return {"items": [agent_skill_out(r) for r in rows], "next_cursor": None}


@router.delete("/agents/{agent_id}/skills/{skill_ref}", status_code=204)
def unassign_skill(agent_id: str, skill_ref: str, rc: RunCtx, db: DbSession):
    skills.unassign_skill(db, rc.ctx, agent_id, skill_ref)
    return Response(status_code=204)


@router.get("/role-templates", response_model=Page[RoleTemplate])
def list_role_templates(rc: RunCtx, db: DbSession):
    views = role_templates.list_for(db, rc.ctx)
    return {"items": [v.as_dict() for v in views], "next_cursor": None}


@router.post("/role-templates", response_model=RoleTemplate, status_code=201)
def create_role_template(body: RoleTemplateCreate, rc: RunCtx, db: DbSession):
    return role_templates.create_template(db, rc.ctx, body).as_dict()


@router.post("/hires", response_model=HireResult, status_code=201)
def hire(body: HireCreate, rc: RunCtx, db: DbSession):
    outcome = hiring.hire(db, rc.ctx, body)
    return HireResult(
        agent=AgentOut.model_validate(outcome.agent),
        auto_activated=outcome.auto_activated,
        approval_id=outcome.approval.id if outcome.approval else None,
        approver=outcome.approval.approver if outcome.approval else None,
        reasons_not_auto_activated=outcome.reasons,
        skills=outcome.skills,
        skipped_default_skills=outcome.skipped_default_skills,
    )


@router.post("/hiring-requests", response_model=HiringRequestResult)
def hiring_request(body: HiringRequestCreate, response: Response, rc: RunCtx, db: DbSession):
    task, created = hiring.request_hire(db, rc.ctx, body)
    response.status_code = 201 if created else 200
    return HiringRequestResult(hiring_task_id=task.id, created=created, status=task.status)
