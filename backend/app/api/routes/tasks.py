from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.work import (
    CompleteTaskRequest,
    DependencyCreate,
    ReviewBody,
    RunCancelBody,
    RunEventPage,
    Task,
    TaskCreate,
    TaskKindOutLiteral,
    TaskReasonBody,
    TaskRun,
    TaskStatusLiteral,
    TaskUpdate,
)
from app.services import runs, tasks
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["tasks"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _one(db, task) -> Task:
    return tasks.serialize_tasks(db, [task])[0]


@router.get("/tasks", response_model=Page[Task])
def list_tasks(
    ctx: CompanyCtx,
    db: DbSession,
    status: TaskStatusLiteral | None = None,
    project_id: str | None = None,
    assignee_agent_id: str | None = None,
    assignee_ref: str | None = None,
    parent_task_id: str | None = None,
    kind: TaskKindOutLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = tasks.list_tasks(
        db,
        ctx,
        limit=limit,
        cursor=cursor,
        status=status,
        project_id=project_id,
        assignee_agent_id=assignee_agent_id,
        assignee_ref=assignee_ref,
        parent_task_id=parent_task_id,
        kind=kind,
    )
    return {"items": tasks.serialize_tasks(db, items), "next_cursor": nxt}


@router.post("/tasks", response_model=Task, status_code=201)
def create_task(body: TaskCreate, response: Response, ctx: CompanyCtx, db: DbSession):
    task, created = tasks.create_task(db, ctx, body)
    if not created:  # idempotent replay
        response.status_code = 200
    return _one(db, task)


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.get_task(db, ctx, task_id))


@router.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: str, body: TaskUpdate, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.update_task(db, ctx, task_id, body))


@router.post("/tasks/{task_id}/complete", response_model=Task)
def complete_task(
    task_id: str, ctx: CompanyCtx, db: DbSession, body: CompleteTaskRequest | None = None
):
    """A person completes a task assigned to them (comment and artifacts optional). Agents
    complete their own tasks through the agent API."""
    body = body or CompleteTaskRequest()
    return _one(
        db,
        tasks.complete_task_as_member(
            db, ctx, task_id, comment=body.comment, artifact_ids=body.artifact_ids
        ),
    )


@router.post("/tasks/{task_id}/cancel", response_model=Task)
def cancel_task(task_id: str, ctx: CompanyCtx, db: DbSession, body: TaskReasonBody | None = None):
    return _one(db, tasks.cancel_task(db, ctx, task_id, body.reason if body else None))


@router.post("/tasks/{task_id}/block", response_model=Task)
def block_task(task_id: str, ctx: CompanyCtx, db: DbSession, body: TaskReasonBody | None = None):
    return _one(db, tasks.block_task(db, ctx, task_id, body.reason if body else None))


@router.post("/tasks/{task_id}/unblock", response_model=Task)
def unblock_task(task_id: str, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.unblock_task(db, ctx, task_id))


@router.post("/tasks/{task_id}/retry", response_model=Task)
def retry_task(task_id: str, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.retry_task(db, ctx, task_id))


@router.post("/tasks/{task_id}/review", response_model=Task)
def review_task(task_id: str, body: ReviewBody, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.review_task(db, ctx, task_id, body.decision, body.note))


@router.post("/tasks/{task_id}/dependencies", response_model=Task, status_code=201)
def add_dependency(task_id: str, body: DependencyCreate, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.add_dependency(db, ctx, task_id, body.depends_on_task_id))


@router.delete("/tasks/{task_id}/dependencies/{dep_id}", response_model=Task)
def remove_dependency(task_id: str, dep_id: str, ctx: CompanyCtx, db: DbSession):
    return _one(db, tasks.remove_dependency(db, ctx, task_id, dep_id))


# ---------------------------------------------------------------- runs


@router.get("/tasks/{task_id}/runs", response_model=Page[TaskRun])
def list_task_runs(
    task_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = runs.list_runs_for_task(db, ctx, task_id, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}


@router.get("/runs/{run_id}", response_model=TaskRun)
def get_run(run_id: str, ctx: CompanyCtx, db: DbSession):
    return runs.get_run(db, ctx, run_id)


@router.get("/runs/{run_id}/events", response_model=RunEventPage)
def list_run_events(
    run_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    items, nxt = runs.list_run_events(db, ctx, run_id, after_seq=after_seq, limit=limit)
    return {"items": items, "next_cursor": nxt}


@router.post("/runs/{run_id}/cancel", response_model=TaskRun)
def cancel_run(run_id: str, ctx: CompanyCtx, db: DbSession, body: RunCancelBody | None = None):
    return runs.cancel_run(db, ctx, run_id, body.reason if body else None)
