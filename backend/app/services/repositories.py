"""Repository bindings: the owner-facing service of GitHub-backed projects (M14 step 0: contracts;
WP-C implements every body).

Rules every function keeps (AGENTS.md): tenancy by ``CompanyContext`` (a binding of another company
is a 404), authorization here and not only in the routes (owner/admin humans change things; any
human member may read; agents never use these endpoints), a state change writes its AuditEvent in
the same transaction, and the token never appears in a result, an audit row or a log.

Until WP-C replaces a body, the authorization checks below are real and the rest answers 501
``{available: false, milestone: "M14"}``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.principal import CompanyContext, require_human_admin
from app.integrations.context import IntegrationContext
from app.models.repository import ProjectRepository
from app.models.work import Project
from app.schemas import repository as schemas
from app.services.agent_auth import AgentRunContext
from app.services.repo_errors import not_built


def _readable(ctx: CompanyContext) -> None:
    """Any human member may read bindings (viewers included); agents may not (403)."""
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot use the repository endpoints")


def availability() -> dict[str, Any]:
    """``GET /repositories/status``: whether projects can be backed by a repository here (git and
    file locking present), with ``git_version``. Step 0: not built."""
    return {
        "available": False,
        "reason": "GitHub-backed projects are not built yet",
        "git_version": None,
        "milestone": "M14",
    }


def list_repositories(
    db: Session, ctx: CompanyContext, *, project_id: str | None, limit: int, cursor: str | None
) -> tuple[list[schemas.ProjectRepository], str | None]:
    _readable(ctx)
    raise not_built()


def get_repository(
    db: Session, ctx: CompanyContext, repository_id: str
) -> schemas.ProjectRepository:
    _readable(ctx)
    raise not_built()


def create_repository(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: schemas.RepositoryCreate
) -> schemas.ProjectRepository:
    """Create a repository (``mode=create``, an empty repo made through the API) or bind an
    existing one (``mode=connect``), creating or choosing the project, and enqueue the
    ``repo_clone`` job. Returns the binding in status ``creating`` / ``cloning``. Errors:
    ``repo_errors`` (409 / 422 / 501)."""
    require_human_admin(ctx, "connect a repository")
    raise not_built()


def update_repository(
    db: Session, ctx: CompanyContext, repository_id: str, data: schemas.RepositoryUpdate
) -> schemas.ProjectRepository:
    """Patch the policy (only the given fields; ``direct_push`` is refused while the base branch is
    protected), the default branch or ``is_company_default``. Audited with old/new values."""
    require_human_admin(ctx, "change a repository")
    raise not_built()


def request_sync_now(db: Session, ctx: CompanyContext, repository_id: str) -> None:
    require_human_admin(ctx, "sync a repository")
    raise not_built()


def pause(db: Session, ctx: CompanyContext, repository_id: str) -> schemas.ProjectRepository:
    require_human_admin(ctx, "pause a repository")
    raise not_built()


def resume(db: Session, ctx: CompanyContext, repository_id: str) -> schemas.ProjectRepository:
    require_human_admin(ctx, "resume a repository")
    raise not_built()


def disconnect(
    db: Session, ctx: CompanyContext, repository_id: str, *, confirm: bool
) -> schemas.ProjectRepository:
    """409 ``repository_busy`` while a Foundry PR is ``merging`` or a run of the project is
    active; the row and the clone are kept (``status = disconnected``)."""
    require_human_admin(ctx, "disconnect a repository")
    raise not_built()


def reset_integration(
    db: Session,
    ctx: CompanyContext,
    ic: IntegrationContext,
    repository_id: str,
    data: schemas.IntegrationReset,
) -> schemas.ProjectRepository:
    """Move the work branch to the remote work branch or the default branch (typed confirmation);
    the old head is kept as ``foundry/rescue-<UTC>``; refused while a run is active."""
    require_human_admin(ctx, "reset the integration branch")
    raise not_built()


def resume_pr_opening(
    db: Session, ctx: CompanyContext, repository_id: str
) -> schemas.ProjectRepository:
    require_human_admin(ctx, "resume pull requests")
    raise not_built()


def set_webhook(
    db: Session,
    ctx: CompanyContext,
    ic: IntegrationContext,
    repository_id: str,
    enabled: bool,
) -> schemas.ProjectRepository:
    """Enable (409 ``public_url_ephemeral`` unless a stable public URL exists) or disable the
    optional webhook that only pokes a sync."""
    require_human_admin(ctx, "change the webhook")
    raise not_built()


def list_pull_requests(
    db: Session, ctx: CompanyContext, repository_id: str, *, limit: int, cursor: str | None
) -> tuple[list[schemas.RepoPullRequest], str | None]:
    _readable(ctx)
    raise not_built()


def list_items(
    db: Session,
    ctx: CompanyContext,
    repository_id: str,
    *,
    kind: str | None,
    status: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[schemas.RepoItem], str | None]:
    """Items with ``untrusted: true`` and body excerpts only."""
    _readable(ctx)
    raise not_built()


def activity(
    db: Session, ctx: CompanyContext, repository_id: str, *, limit: int, cursor: str | None
):
    """Audit events with ``target_type="repository"`` and ``target_id=repository_id``, newest
    first (``AuditEvent`` rows; the route serializes them as ``Page[AuditEvent]``)."""
    _readable(ctx)
    raise not_built()


def receive_webhook(
    db: Session,
    ic: IntegrationContext,
    repository_id: str,
    headers: dict[str, str],
    body: bytes,
) -> dict[str, Any]:
    """``POST /webhooks/github/{repository_id}`` (public; HMAC ``X-Hub-Signature-256``). An unknown
    repository, no secret and a bad signature all raise the same 401 ``webhook_rejected``. A valid
    delivery only pokes a sync (``repo_sync.request_sync``); the payload is never trusted or used.
    Returns ``{"accepted": True}``."""
    raise not_built()


# ------------------------------------------------------------------ used by other services


def binding_for(db: Session, company_id: str, full_name: str) -> ProjectRepository | None:
    """The ACTIVE binding of ``owner/name`` in a company, else None (the executors' guard)."""
    raise not_built()


def agent_may_use(db: Session, rc: AgentRunContext, binding: ProjectRepository) -> bool:
    """The agent is the AI Co-founder, the lead of a project bound to ``binding``, or is running a
    task of such a project."""
    raise not_built()


def bind_for_agent(
    db: Session, rc: AgentRunContext, ic: IntegrationContext, params: dict[str, Any]
) -> dict[str, Any]:
    """``github.repo.create`` after its human approval: create the repository and bind it to the
    caller's project. Returns a token-free summary."""
    raise not_built()


def on_project_created(db: Session, ctx: CompanyContext, project: Project) -> None:
    """Called by ``projects.create_project``: with ``new_project_repository = company_repo`` the new
    project gets its own binding to the company's default repository (own work branch, own clone).
    No-op with ``local``."""
    raise not_built()
