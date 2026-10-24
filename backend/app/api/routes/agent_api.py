"""Agent API (``/api/agent/v1``): what a running agent may do, authenticated by its run token.

Everything is attributed to the agent and audited by the shared services. Policy, budgets,
permissions and the agent's own lifecycle are not reachable from here.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import DbSession, RunCtx, Skills, get_storage
from app.api.guard import agent_guard, guard_env
from app.api.routes.skills import agent_skill_out, install_result
from app.api.uploads import read_artifact_input
from app.guardrails import engine as guard_engine
from app.guardrails import log as guard_log
from app.guardrails import peer, providers, registry
from app.models.company import Company
from app.schemas.agent import Agent as AgentOut
from app.schemas.agent_api import (
    AgentContext,
    AgentMember,
    AgentTaskComplete,
    AgentTaskPatch,
    ProgressBody,
    ProgressOut,
)
from app.schemas.artifact import ArtifactDetail, ArtifactVersion
from app.schemas.autonomy import (
    AgentNote,
    AgentState,
    AgentTaskAction,
    EscalationCreate,
    EscalationResult,
    FounderNote,
    HelpRequestCreate,
    HelpRequestResult,
    MemoryCreate,
    MemoryDecisionBody,
    MemoryEntry,
    NoteReply,
    NoteReplyCreate,
    NoteTriageIn,
)
from app.schemas.common import Page
from app.schemas.governance import (
    AgentDecideBody,
    Approval,
    ApprovalCreate,
    ApprovalCreated,
    Decision,
    DecisionCreate,
)
from app.schemas.guardrails import (
    FlagResult,
    InjectionFlag,
    PeerReviewDecision,
    ProviderActionRequest,
    ProviderActionResult,
    ToolCheck,
    ToolCheckResult,
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
from app.schemas.reflections import (
    AgentReflectionPost,
    AgentReflectionReply,
    AgentReflectionRevise,
    Reflection,
    ReflectionResponse,
)
from app.schemas.schedule import Schedule, ScheduleCreate, ScheduleUpdate
from app.schemas.skills import (
    AgentSkill,
    CatalogItem,
    SkillAuthor,
    SkillInstall,
    SkillInstallResult,
    SkillRefs,
)
from app.schemas.work import (
    Goal,
    GoalCreate,
    GoalUpdate,
    Project,
    ProjectCreate,
    ProjectUpdate,
    ReviewBody,
    Task,
    TaskCreate,
)
from app.services import (
    agent_actions,
    agent_context,
    approvals,
    artifacts,
    collab,
    decisions,
    goals,
    hiring,
    memory,
    notes,
    policy,
    projects,
    reflections,
    role_templates,
    schedules,
    skills,
    state,
    tasks,
    team,
)
from app.storage.base import ArtifactStorage

# Every route passes the guardrail gate first (registry lookup -> emergency stop -> rate limit ->
# tier decision); see app/api/guard.py.
router = APIRouter(prefix="/agent/v1", tags=["agent-api"], dependencies=[Depends(agent_guard)])


def _one(db, task) -> Task:
    return tasks.serialize_tasks(db, [task])[0]


@router.get("/context", response_model=AgentContext)
def get_context(rc: RunCtx, db: DbSession, storage: ArtifactStorage = Depends(get_storage)):
    return agent_context.build_context(db, rc, storage)


@router.get("/state", response_model=AgentState)
def get_state(rc: RunCtx, db: DbSession):
    """Live company state, sliced by the caller's role (founder: everything; lead: its projects;
    HR: team and hiring; others: their own tasks)."""
    return state.build_state(db, rc)


@router.get("/members", response_model=list[AgentMember])
def list_members(rc: RunCtx, db: DbSession):
    """The human colleagues, with their load: assign work with ``assignee_ref`` (lead/founder
    roles only). Names and titles only: never an email, username or credential."""
    return team.colleagues_for_agent(db, rc.ctx)


@router.post("/tasks", response_model=Task, status_code=201)
def create_task(body: TaskCreate, response: Response, rc: RunCtx, db: DbSession):
    task, created = tasks.create_task_as_agent(db, rc.ctx, rc.run, body)
    if not created:
        response.status_code = 200
    return _one(db, task)


@router.patch("/tasks/{task_id}", response_model=Task)
def patch_task(task_id: str, body: AgentTaskPatch, rc: RunCtx, db: DbSession):
    agent_id, member_id = body.assignee_agent_id, None
    given = "assignee_agent_id" in body.model_fields_set
    if body.assignee_ref:  # M7: agent:<id> | member:<id> (a person as colleague)
        agent_id, member_id = tasks.resolve_people(
            db,
            rc.ctx.company_id,
            agent_id=body.assignee_agent_id,
            ref=body.assignee_ref,
            label="Assignee",
            field="assignee_ref",
        )
        given = True
    task = tasks.agent_update_task(
        db,
        rc.ctx,
        rc.run,
        task_id,
        status=body.status,
        reason=body.reason,
        assignee_agent_id=agent_id,
        assignee_given=given,
        assignee_member_id=member_id,
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


# ------------------------------------------------------ Milestone 5: autonomous operating model


@router.post("/tasks/{task_id}/retry", response_model=Task)
def retry_task(task_id: str, body: AgentTaskAction, rc: RunCtx, db: DbSession):
    """Retry a failed task you manage (optionally with another assignee)."""
    task = tasks.retry_as_agent(
        db, rc.ctx, task_id, reason=body.reason, assignee_id=body.assignee_agent_id
    )
    return _one(db, task)


@router.post("/tasks/{task_id}/cancel", response_model=Task)
def cancel_task(task_id: str, body: AgentTaskAction, rc: RunCtx, db: DbSession):
    return _one(db, tasks.cancel_as_agent(db, rc.ctx, task_id, body.reason))


@router.post("/escalations", response_model=EscalationResult, status_code=201)
def escalate(body: EscalationCreate, rc: RunCtx, db: DbSession):
    return collab.escalate(db, rc, body)


@router.post("/help-requests", response_model=HelpRequestResult)
def request_help(body: HelpRequestCreate, response: Response, rc: RunCtx, db: DbSession):
    result = collab.request_help(db, rc, body)
    response.status_code = 201 if result.created else 200
    return result


@router.post("/goals", response_model=Goal, status_code=201)
def create_goal(body: GoalCreate, rc: RunCtx, db: DbSession):
    return goals.create_goal(db, rc.ctx, body)


@router.patch("/goals/{goal_id}", response_model=Goal)
def update_goal(goal_id: str, body: GoalUpdate, rc: RunCtx, db: DbSession):
    return goals.update_goal(db, rc.ctx, goal_id, body)


@router.post("/projects", response_model=Project, status_code=201)
def create_project(body: ProjectCreate, rc: RunCtx, db: DbSession):
    return projects.create_project(db, rc.ctx, body)


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: str, body: ProjectUpdate, rc: RunCtx, db: DbSession):
    return projects.update_project(db, rc.ctx, project_id, body)


@router.post("/schedules", response_model=Schedule, status_code=201)
def create_schedule(body: ScheduleCreate, rc: RunCtx, db: DbSession):
    return schedules.create_schedule(db, rc.ctx, body)


@router.patch("/schedules/{schedule_id}", response_model=Schedule)
def update_schedule(schedule_id: str, body: ScheduleUpdate, rc: RunCtx, db: DbSession):
    return schedules.update_schedule(db, rc.ctx, schedule_id, body)


@router.get("/notes", response_model=Page[AgentNote])
def list_notes(rc: RunCtx, db: DbSession, status: str | None = "new", limit: int = 20):
    """The Founder's notes, for the AI Co-founder (``status=new`` = unprocessed)."""
    return {
        "items": notes.agent_list(db, rc, status=status or None, limit=limit),
        "next_cursor": None,
    }


@router.post("/notes/{note_id}/triage", response_model=FounderNote)
def triage_note(note_id: str, body: NoteTriageIn, rc: RunCtx, db: DbSession):
    note = notes.agent_triage(db, rc, note_id, body)
    return notes.serialize(db, [note])[0]


@router.post("/notes/{note_id}/replies", response_model=NoteReply, status_code=201)
def reply_to_note(note_id: str, body: NoteReplyCreate, rc: RunCtx, db: DbSession):
    return notes.agent_reply(db, rc, note_id, body.body)


@router.post("/memory", response_model=MemoryEntry)
def propose_memory(body: MemoryCreate, response: Response, rc: RunCtx, db: DbSession):
    """Propose a memory entry (a lesson, a fact...); others see it once it is verified."""
    entry, created = memory.create_as_agent(
        db, rc.ctx, body, run_task_id=rc.run.task_id, run_id=rc.run.id
    )
    response.status_code = 201 if created else 200
    return entry


@router.post("/memory/{entry_id}/verify", response_model=MemoryEntry)
def verify_memory(entry_id: str, rc: RunCtx, db: DbSession, body: MemoryDecisionBody | None = None):
    return memory.decide(db, rc.ctx, entry_id, "verify", body.reason if body else None)


@router.post("/memory/{entry_id}/reject", response_model=MemoryEntry)
def reject_memory(entry_id: str, rc: RunCtx, db: DbSession, body: MemoryDecisionBody | None = None):
    return memory.decide(db, rc.ctx, entry_id, "reject", body.reason if body else None)


# ------------------------------------------------------ Milestone 6: guardrails & governance


@router.post("/guardrails/check-tool", response_model=ToolCheckResult)
def check_tool(body: ToolCheck, request: Request, rc: RunCtx, db: DbSession):
    """The runtime asks before it runs a tool that reaches outside (web search...). Raises 403 /
    429 / 202 like any guarded action; unknown tools are denied (fail closed)."""
    action = registry.tool_action(body.tool)
    verdict = guard_engine.check(
        db, rc.ctx, action, {"tool": body.tool}, run=rc.run, env=guard_env(request)
    )
    return ToolCheckResult(allowed=True, tool=body.tool, tier=verdict.tier)


@router.post("/guardrails/flags", response_model=FlagResult, status_code=201)
def flag_content(body: InjectionFlag, rc: RunCtx, db: DbSession):
    """The runtime saw an injection-style phrase in a tool result: logged, never auto-blocked."""
    source = f" ({body.source})" if body.source else ""
    guard_log.record(
        db,
        company_id=rc.ctx.company_id,
        agent_id=rc.agent.id,
        run_id=rc.run.id,
        task_id=rc.run.task_id,
        action="content.injection_flag",
        tier="external_read",
        decision="allow",
        reason=f"possible prompt injection in the result of {body.tool}{source}: "
        + ", ".join(body.categories[:10]),
    )
    db.commit()
    return FlagResult()


@router.post("/peer-reviews/{approval_id}/decide", response_model=Approval)
def decide_peer_review(approval_id: str, body: PeerReviewDecision, rc: RunCtx, db: DbSession):
    """The reviewer agent decides a peer review assigned to it (never its own work)."""
    return peer.decide(db, rc.ctx, approval_id, body.decision, body.note)


@router.post("/provider-actions", response_model=ProviderActionResult)
def provider_action(body: ProviderActionRequest, request: Request, rc: RunCtx, db: DbSession):
    """Ask the control plane to act with a stored credential (the agent never sees it)."""
    company = db.get(Company, rc.ctx.company_id)
    assert company is not None
    result = providers.execute(
        db,
        rc,
        request.app.state.cipher,
        request.app.state.provider_executors,
        body.model_dump(),
        limits=policy.load_policy(db, rc.ctx.company_id),
        company_name=company.name,
    )
    return ProviderActionResult(action=body.action, result=result)


# ------------------------------------------------------ Milestone 10: reflections


@router.post("/reflections", response_model=Reflection, status_code=201)
def post_reflection(body: AgentReflectionPost, response: Response, rc: RunCtx, db: DbSession):
    """The AI Co-founder posts a reflection for the Founder (kind observation, standard_proposal,
    retro, risk or praise). A duplicate title within a week returns the existing one (200)."""
    row, created = reflections.agent_post(db, rc, body)
    if not created:
        response.status_code = 200
    return reflections.serialize(db, [row])[0]


@router.post(
    "/reflections/{reflection_id}/replies", response_model=ReflectionResponse, status_code=201
)
def reply_reflection(reflection_id: str, body: AgentReflectionReply, rc: RunCtx, db: DbSession):
    reply = reflections.agent_reply(db, rc, reflection_id, body.body)
    return reflections.serialize_response(db, reply)


@router.post("/reflections/{reflection_id}/revise", response_model=Reflection)
def revise_reflection(reflection_id: str, body: AgentReflectionRevise, rc: RunCtx, db: DbSession):
    """Change the text of your own reflection (or its proposed standard) after the Founder's
    reaction. The status becomes ``revised``; a standard still needs the Founder's endorsement."""
    row = reflections.agent_revise(db, rc, reflection_id, body)
    return reflections.serialize(db, [row])[0]
