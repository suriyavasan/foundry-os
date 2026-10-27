"""GitHub-backed projects (M14): repository bindings of a company. Owner/admin humans change
things; any human member reads. Thin routes: everything is in ``app/services/repositories.py``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CompanyCtx, DbSession
from app.schemas import repository as schemas
from app.schemas.audit import AuditEvent
from app.schemas.common import Page
from app.services import github_accounts, repositories
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["repositories"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _ic(request: Request):
    return request.app.state.integrations


@router.get("/repositories/status", response_model=schemas.RepoAvailability)
def repositories_status(ctx: CompanyCtx):
    """Whether projects can be backed by a repository on this machine (git, file locking)."""
    return repositories.availability()


@router.post("/github/inspect", response_model=schemas.GithubInspectResult)
def inspect_github(
    body: schemas.GithubInspectRequest, request: Request, ctx: CompanyCtx, db: DbSession
):
    """What the connected GitHub token can do, and (optionally) what it can do on one repository.
    Never returns the token."""
    return github_accounts.inspect(db, ctx, _ic(request), body)


@router.get("/repositories", response_model=Page[schemas.ProjectRepository])
def list_repositories(
    ctx: CompanyCtx,
    db: DbSession,
    project_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = repositories.list_repositories(
        db, ctx, project_id=project_id, limit=limit, cursor=cursor
    )
    return {"items": items, "next_cursor": nxt}


@router.post("/repositories", response_model=schemas.ProjectRepository, status_code=201)
def create_repository(
    body: schemas.RepositoryCreate, request: Request, ctx: CompanyCtx, db: DbSession
):
    return repositories.create_repository(db, ctx, _ic(request), body)


@router.get("/repositories/{repository_id}", response_model=schemas.ProjectRepository)
def get_repository(repository_id: str, ctx: CompanyCtx, db: DbSession):
    return repositories.get_repository(db, ctx, repository_id)


@router.patch("/repositories/{repository_id}", response_model=schemas.ProjectRepository)
def update_repository(
    repository_id: str, body: schemas.RepositoryUpdate, ctx: CompanyCtx, db: DbSession
):
    return repositories.update_repository(db, ctx, repository_id, body)


@router.post(
    "/repositories/{repository_id}/sync", response_model=schemas.SyncQueued, status_code=202
)
def sync_repository(repository_id: str, ctx: CompanyCtx, db: DbSession):
    repositories.request_sync_now(db, ctx, repository_id)
    return schemas.SyncQueued()


@router.post("/repositories/{repository_id}/pause", response_model=schemas.ProjectRepository)
def pause_repository(repository_id: str, ctx: CompanyCtx, db: DbSession):
    return repositories.pause(db, ctx, repository_id)


@router.post("/repositories/{repository_id}/resume", response_model=schemas.ProjectRepository)
def resume_repository(repository_id: str, ctx: CompanyCtx, db: DbSession):
    return repositories.resume(db, ctx, repository_id)


@router.post("/repositories/{repository_id}/disconnect", response_model=schemas.ProjectRepository)
def disconnect_repository(
    repository_id: str, body: schemas.DisconnectBody, ctx: CompanyCtx, db: DbSession
):
    return repositories.disconnect(db, ctx, repository_id, confirm=body.confirm)


@router.post(
    "/repositories/{repository_id}/integration/reset", response_model=schemas.ProjectRepository
)
def reset_integration(
    repository_id: str,
    body: schemas.IntegrationReset,
    request: Request,
    ctx: CompanyCtx,
    db: DbSession,
):
    return repositories.reset_integration(db, ctx, _ic(request), repository_id, body)


@router.post(
    "/repositories/{repository_id}/pull-requests/resume-opening",
    response_model=schemas.ProjectRepository,
)
def resume_pr_opening(repository_id: str, ctx: CompanyCtx, db: DbSession):
    return repositories.resume_pr_opening(db, ctx, repository_id)


@router.put("/repositories/{repository_id}/webhook", response_model=schemas.ProjectRepository)
def set_webhook(
    repository_id: str,
    body: schemas.WebhookUpdate,
    request: Request,
    ctx: CompanyCtx,
    db: DbSession,
):
    return repositories.set_webhook(db, ctx, _ic(request), repository_id, body.enabled)


@router.get(
    "/repositories/{repository_id}/pull-requests", response_model=Page[schemas.RepoPullRequest]
)
def list_pull_requests(
    repository_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = repositories.list_pull_requests(db, ctx, repository_id, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}


@router.get("/repositories/{repository_id}/items", response_model=Page[schemas.RepoItem])
def list_items(
    repository_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    kind: schemas.ItemKind | None = None,
    status: schemas.ItemStatus | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = repositories.list_items(
        db, ctx, repository_id, kind=kind, status=status, limit=limit, cursor=cursor
    )
    return {"items": items, "next_cursor": nxt}


@router.get("/repositories/{repository_id}/activity", response_model=Page[AuditEvent])
def repository_activity(
    repository_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = repositories.activity(db, ctx, repository_id, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}
