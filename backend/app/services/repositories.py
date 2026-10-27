"""Repository bindings: the owner-facing service of GitHub-backed projects (M14).

Rules every function keeps (AGENTS.md): tenancy by ``CompanyContext`` (a binding of another company
is a 404), authorization here and not only in the routes (owner/admin humans change things; any
human member may read; agents never use these endpoints), a state change writes its AuditEvent in
the same transaction, and the token never appears in a result, an audit row or a log.

What this module does and does not do: it creates and edits the *binding* (policy, pause, resume,
disconnect, webhook, integration reset) and talks to the GitHub REST API only for the few requests a
binding needs at creation time (create or read the repository, push permission, protection). All
git work (clone, the first commit, fetch, push) runs in the worker (``repo_sync.run_job`` and
``repo_git``), reached through the ``repo_clone`` job queued here.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import re
import secrets
import shutil
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    Forbidden,
    NotAvailable,
    NotFound,
    ValidationFailed,
)
from app.core.principal import CompanyContext, Principal, require_human_admin
from app.core.time import utcnow
from app.integrations.base import ProviderError
from app.integrations.context import IntegrationContext
from app.integrations.github import GithubClient
from app.models.enums import AgentKind, AlertSeverity
from app.models.provider import Credential
from app.models.reachability import ConnectedAccount
from app.models.repository import ProjectRepository, RepoItem, RepoPullRequest
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.schemas import repository as schemas
from app.services import (
    activity as activity_service,
)
from app.services import (
    alerts,
    audit,
    connected_accounts,
    credentials,
    events,
    policy,
    reachability,
    repo_git,
    repo_sync,
)
from app.services import project_workspaces as pw
from app.services.agent_auth import AgentRunContext
from app.services.common import get_project_or_404
from app.services.email_identity import WebhookRejected
from app.services.pagination import paginate
from app.services.repo_errors import (
    MILESTONE,
    AdministrationPermissionMissing,
    BranchProtected,
    ConfirmPublicRequired,
    NoAccount,
    ProjectHasRepository,
    PublicUrlEphemeral,
    RepositoryArchived,
    RepositoryBusy,
    RepositoryExists,
    RepositoryNotAccessible,
    RepositoryTooLarge,
    TokenCannotPush,
)
from app.services.secrets_scan import find_secret

WEBHOOK_EVENTS = [
    "push",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "issues",
    "issue_comment",
    "check_suite",
    "check_run",
    "status",
    "workflow_run",
    "dependabot_alert",
]
WEBHOOK_PATH = "/api/webhooks/github/{repository_id}"
TERMINAL_PR_STAGES = ("merged", "closed")
# Status values in which the binding is (or will be) usable without the owner's help.
LIVE_STATUSES = ("pending", "creating", "cloning", "active", "paused", "auth_failed", "error")


# ------------------------------------------------------------------ small helpers


def _readable(ctx: CompanyContext) -> None:
    """Any human member may read bindings (viewers included); agents may not (403)."""
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot use the repository endpoints")


def _load(db: Session, company_id: str, repository_id: str) -> ProjectRepository:
    row = db.scalar(
        select(ProjectRepository).where(
            ProjectRepository.id == repository_id, ProjectRepository.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Repository not found")
    return row


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-") or "project"


def _emit(db: Session, row: ProjectRepository) -> None:
    events.emit(db, row.company_id, "repository.updated", {"repository_id": row.id})


def _audit(
    db: Session,
    row: ProjectRepository,
    principal: Principal | None,
    action: str,
    summary: str,
    data: dict[str, Any] | None = None,
) -> None:
    audit.record(
        db,
        company_id=row.company_id,
        principal=principal,
        action=action,
        target_type="repository",
        target_id=row.id,
        summary=summary,
        data={"full_name": row.full_name, "project_id": row.project_id, **(data or {})},
    )


@lru_cache(maxsize=1)
def _git_version() -> str | None:
    binary = shutil.which("git")
    if binary is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+(?:\.\d+)+)", out)
    return match.group(1) if match else None


def availability() -> dict[str, Any]:
    """``GET /repositories/status``: whether projects can be backed by a repository here (git and
    file locking present), with ``git_version``."""
    state = pw.status()
    return {
        "available": bool(state["available"]),
        "reason": state.get("reason"),
        "git_version": _git_version() if state["available"] else None,
        "milestone": MILESTONE,
    }


def _require_available() -> None:
    state = pw.status()
    if not state["available"]:
        raise NotAvailable(
            f"Repositories need git on this machine: {state.get('reason')}",
            milestone=MILESTONE,
            details={"reason": "git_unavailable"},
        )


# ------------------------------------------------------------------ serialization


def _serialize(db: Session, rows: list[ProjectRepository]) -> list[schemas.ProjectRepository]:
    if not rows:
        return []
    company_ids = {r.company_id for r in rows}
    names = {
        pid: name
        for pid, name in db.execute(
            select(Project.id, Project.name).where(
                Project.company_id.in_(company_ids), Project.id.in_({r.project_id for r in rows})
            )
        )
    }
    open_prs: dict[str, RepoPullRequest] = {}
    for pr in db.scalars(
        select(RepoPullRequest)
        .where(
            RepoPullRequest.repository_id.in_([r.id for r in rows]),
            RepoPullRequest.stage.notin_(TERMINAL_PR_STAGES),
        )
        .order_by(RepoPullRequest.created_at.desc(), RepoPullRequest.id.desc())
    ):
        open_prs.setdefault(pr.repository_id, pr)
    last_url = (reachability.load_url_state(db) or {}).get("last_url")
    out = []
    for row in rows:
        model = schemas.ProjectRepository.model_validate(row)
        model.project_name = names.get(row.project_id)
        enabled = bool(row.webhook_id)
        stale = bool(
            enabled
            and row.webhook_url
            and last_url
            and not row.webhook_url.startswith(str(last_url).rstrip("/") + "/")
        )
        model.webhook = schemas.WebhookState(
            enabled=enabled, url=row.webhook_url if enabled else None, stale=stale
        )
        pr = open_prs.get(row.id)
        model.open_pull_request = (
            schemas.RepoPullRequestSummary.model_validate(pr) if pr is not None else None
        )
        out.append(model)
    return out


def _one(db: Session, row: ProjectRepository) -> schemas.ProjectRepository:
    db.refresh(row)
    return _serialize(db, [row])[0]


# ------------------------------------------------------------------ reads


def list_repositories(
    db: Session, ctx: CompanyContext, *, project_id: str | None, limit: int, cursor: str | None
) -> tuple[list[schemas.ProjectRepository], str | None]:
    _readable(ctx)
    stmt = select(ProjectRepository).where(ProjectRepository.company_id == ctx.company_id)
    if project_id:
        stmt = stmt.where(ProjectRepository.project_id == project_id)
    rows, nxt = paginate(db, stmt, ProjectRepository, limit=limit, cursor=cursor, descending=False)
    return _serialize(db, rows), nxt


def get_repository(
    db: Session, ctx: CompanyContext, repository_id: str
) -> schemas.ProjectRepository:
    _readable(ctx)
    return _serialize(db, [_load(db, ctx.company_id, repository_id)])[0]


def list_pull_requests(
    db: Session, ctx: CompanyContext, repository_id: str, *, limit: int, cursor: str | None
) -> tuple[list[schemas.RepoPullRequest], str | None]:
    _readable(ctx)
    row = _load(db, ctx.company_id, repository_id)
    stmt = select(RepoPullRequest).where(
        RepoPullRequest.company_id == ctx.company_id, RepoPullRequest.repository_id == row.id
    )
    prs, nxt = paginate(db, stmt, RepoPullRequest, limit=limit, cursor=cursor, descending=True)
    return [schemas.RepoPullRequest.model_validate(p) for p in prs], nxt


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
    row = _load(db, ctx.company_id, repository_id)
    stmt = select(RepoItem).where(
        RepoItem.company_id == ctx.company_id, RepoItem.repository_id == row.id
    )
    if kind:
        stmt = stmt.where(RepoItem.kind == kind)
    if status:
        stmt = stmt.where(RepoItem.status == status)
    items, nxt = paginate(db, stmt, RepoItem, limit=limit, cursor=cursor, descending=True)
    return [schemas.RepoItem.model_validate(i) for i in items], nxt


def activity(
    db: Session, ctx: CompanyContext, repository_id: str, *, limit: int, cursor: str | None
):
    """Audit events with ``target_type="repository"`` and ``target_id=repository_id``, newest
    first."""
    _readable(ctx)
    row = _load(db, ctx.company_id, repository_id)
    return activity_service.list_activity(
        db, ctx, limit=limit, cursor=cursor, target_type="repository", target_id=row.id
    )


# ------------------------------------------------------------------ GitHub (creation time only)


def _account(db: Session, company_id: str, account_id: str) -> ConnectedAccount:
    account = db.scalar(
        select(ConnectedAccount).where(
            ConnectedAccount.id == account_id,
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.provider == "github",
            ConnectedAccount.revoked_at.is_(None),
        )
    )
    if account is None or account.status != "connected":
        raise NoAccount(details={"reason": "no_account", "connected_account_id": account_id})
    return account


def _client(db: Session, ic: IntegrationContext, account: ConnectedAccount) -> GithubClient:
    """A GithubClient for the account (close ``client.http`` when done). The token is decrypted
    here and lives only inside the client."""
    client = connected_accounts.client_for(db, ic, account)
    assert isinstance(client, GithubClient)
    client.web_url = ic.settings.github_web_url
    client._sleep = ic.sleep
    return client


def _reason(exc: ProviderError) -> str:
    return str((exc.details or {}).get("reason") or "")


def protection_summary(client: GithubClient, full_name: str, branch: str) -> dict[str, Any]:
    """What limits writes to ``branch``: ``protected`` (branch protection or any ruleset rule),
    ``require_pr``, ``required_checks`` (bool), ``linear_history``, ``required_signatures``,
    ``non_fast_forward``. Read-only; a failed read is an error for the caller to handle."""
    info = client.get_branch(full_name, branch)
    rules = client.branch_rules(full_name, branch)
    kinds = {str(r.get("type")) for r in rules if isinstance(r, dict)}
    return {
        "branch": branch,
        "protected": bool(info.get("protected")) or bool(kinds),
        "require_pr": "pull_request" in kinds or bool(info.get("protected")),
        "required_checks": "required_status_checks" in kinds,
        "linear_history": "required_linear_history" in kinds,
        "required_signatures": "required_signatures" in kinds,
        "non_fast_forward": "non_fast_forward" in kinds,
        "checked_at": utcnow().isoformat(),
    }


def _direct_push_allowed(protection: dict[str, Any]) -> bool:
    return not protection.get("protected") and not protection.get("required_signatures")


# ------------------------------------------------------------------ local repository handling


def _has_history(repo: Path) -> bool:
    """Whether the M12 project repository holds more than its initial empty commit."""
    binary = shutil.which("git")
    if binary is None:
        return True  # cannot tell: keep it
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1"}

    def run(*args: str) -> str | None:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, read-only
                [binary, "-C", str(repo), *args],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    count = run("rev-list", "--count", "--all")
    files = run("ls-tree", "-r", "--name-only", "HEAD")
    if count is None or files is None:
        return True
    return files != "" or int(count or 0) > 1


def _move_aside(repo: Path) -> Path:
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest = repo.parent / f"local-{stamp}"
    n = 1
    while dest.exists():
        dest = repo.parent / f"local-{stamp}-{n}"
        n += 1
    repo.rename(dest)
    return dest


def _project_run_active(db: Session, company_id: str, project_id: str) -> bool:
    return (
        db.scalar(
            select(func.count(TaskRun.id))
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.company_id == company_id,
                Task.project_id == project_id,
                TaskRun.status.in_(("starting", "running")),
            )
        )
        or 0
    ) > 0


def _unique_work_branch(
    db: Session, company_id: str, full_name: str, project: Project, requested: str | None
) -> str:
    taken = {
        b.lower()
        for b in db.scalars(
            select(ProjectRepository.work_branch).where(
                ProjectRepository.company_id == company_id,
                func.lower(ProjectRepository.full_name) == full_name.lower(),
                ProjectRepository.status != "disconnected",
                ProjectRepository.project_id != project.id,
            )
        )
    }
    if requested:
        if requested.lower() in taken:
            raise Conflict(
                "Another project of this company already works on that branch",
                details={"reason": "work_branch_taken", "work_branch": requested},
            )
        return requested
    base = f"{schemas.WORK_BRANCH_PREFIX}{_slug(project.name)}"
    if base.lower() not in taken:
        return base
    return f"{base}-{project.id[:6]}"


# ------------------------------------------------------------------ create / connect


def _policy_for(
    mode: str, has_workflows: bool, given: schemas.RepoPolicyInput
) -> schemas.RepoPolicy:
    base = schemas.default_policy(mode, has_workflows=has_workflows).model_dump()  # type: ignore[arg-type]
    base.update(given.model_dump(exclude_unset=True))
    try:
        return schemas.RepoPolicy.model_validate(base)
    except ValueError as exc:  # pragma: no cover - the inputs were validated one by one
        raise ValidationFailed(f"Invalid repository policy: {exc}") from exc


def create_repository(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: schemas.RepositoryCreate
) -> schemas.ProjectRepository:
    """Create a repository (``mode=create``, an empty repo made through the API) or bind an
    existing one (``mode=connect``), creating or choosing the project, and enqueue the
    ``repo_clone`` job. Returns the binding in status ``creating`` / ``cloning``. Errors:
    ``repo_errors`` (409 / 422 / 501)."""
    require_human_admin(ctx, "connect a repository")
    row = _create(db, ctx, ic, data)
    return _one(db, row)


def _create(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: schemas.RepositoryCreate
) -> ProjectRepository:
    """Shared by the owner endpoint and the approved agent action. Authorization was done by the
    caller. Commits on success; leaves nothing behind on failure (the local repository is moved
    back; a repository already created on GitHub is reported in the error details)."""
    company_id = ctx.company_id
    _require_available()
    if data.mode == "create" and data.visibility == "public" and not data.confirm_public:
        raise ConfirmPublicRequired()
    if data.description and find_secret(data.description):
        raise ValidationFailed("The description must not contain a secret")
    account = _account(db, company_id, data.connected_account_id)
    project_input = _project_input(data) if not data.project_id else None

    # -- the project ----------------------------------------------------------------------
    project: Project | None = None
    reuse: ProjectRepository | None = None
    if data.project_id:
        project = get_project_or_404(db, company_id, data.project_id)
        existing = db.scalar(
            select(ProjectRepository).where(
                ProjectRepository.company_id == company_id,
                ProjectRepository.project_id == project.id,
            )
        )
        if existing is not None and existing.status != "disconnected":
            raise ProjectHasRepository()
        reuse = existing
        if _project_run_active(db, company_id, project.id):
            raise RepositoryBusy("A run of this project is active; connect after it finishes")

    # -- the repository on GitHub ----------------------------------------------------------
    target = schemas.parse_repo_ref(data.full_name) if data.full_name else None
    owner = data.owner or (target[0] if target else None)
    name = data.name or (target[1] if target else None)
    client = _client(db, ic, account)
    try:
        login = str(client.identity().get("login") or "")
        owner = owner or login
        if not owner or not name:
            raise ValidationFailed("Give the repository as OWNER/NAME")
        full_name = f"{owner}/{name}"
        facts = (
            _create_remote(client, data, owner, name, login)
            if data.mode == "create"
            else _read_remote(client, data, full_name, ic)
        )
        capabilities = _capabilities(client, full_name, data.mode)
        token_expires_at = client.token_expires_at
        rate = client.last_rate
    finally:
        with contextlib.suppress(Exception):
            client.http.close()

    pol = _policy_for(data.mode, facts["has_workflows"], data.policy)
    if pol.merge_mode == "direct_push" and not _direct_push_allowed(facts["protection"]):
        raise BranchProtected(details={"branch": facts["default_branch"]})

    default_branch = facts["default_branch"]
    work_branch_requested = data.work_branch
    if work_branch_requested and work_branch_requested == default_branch:
        raise ValidationFailed("The work branch must differ from the default branch")

    # -- the local repository --------------------------------------------------------------
    moved: Path | None = None
    discard_moved = False
    publish = False
    repo_dir: Path | None = None
    if project is not None:
        repo_dir = pw.main_repo(ic.settings.workspaces_dir, company_id, project.id)
        if repo_dir.exists():
            history = _has_history(repo_dir)
            if data.publish_local and not facts["empty"]:
                raise ValidationFailed(
                    "publish_local needs an empty repository; this one already has commits"
                )
            if data.publish_local and history:
                publish = True
            else:
                moved = _move_aside(repo_dir)
                discard_moved = not history  # only the initial empty commit: nothing to keep
    elif data.publish_local:
        raise ValidationFailed("publish_local needs an existing project (project_id)")

    try:
        row = _bind(
            db,
            ctx,
            data=data,
            project_input=project_input,
            account=account,
            project=project,
            reuse=reuse,
            owner=owner,
            name=name,
            facts=facts,
            policy_=pol,
            capabilities=capabilities,
            publish=publish,
            moved=moved if not discard_moved else None,
            token_expires_at=token_expires_at,
            rate=rate,
            ic=ic,
            work_branch_requested=work_branch_requested,
        )
    except IntegrityError as exc:
        db.rollback()
        _restore(moved, repo_dir)
        raise ProjectHasRepository() from exc
    except BaseException:
        db.rollback()
        _restore(moved, repo_dir)
        raise
    if discard_moved and moved is not None:
        shutil.rmtree(moved, ignore_errors=True)
    return row


def _restore(moved: Path | None, repo_dir: Path | None) -> None:
    if moved is not None and repo_dir is not None and moved.exists() and not repo_dir.exists():
        with contextlib.suppress(OSError):
            moved.rename(repo_dir)


def _create_remote(
    client: GithubClient, data: schemas.RepositoryCreate, owner: str, name: str, login: str
) -> dict[str, Any]:
    try:
        created = client.create_repo(
            owner,
            name,
            private=data.visibility != "public",
            description=data.description,
            org=owner.lower() != login.lower(),
        )
    except ProviderError as exc:
        reason = _reason(exc)
        if (exc.details or {}).get("already_exists"):
            raise RepositoryExists(details={"full_name": f"{owner}/{name}"}) from exc
        if reason in ("forbidden", "not_accessible", "unauthorized"):
            raise AdministrationPermissionMissing() from exc
        if reason == "not_found":
            raise RepositoryNotAccessible(
                f"The token cannot create repositories for {owner}",
                details={"owner": owner},
            ) from exc
        raise
    return {
        "html_url": created.get("html_url") or f"https://github.com/{owner}/{name}",
        "visibility": "public" if not created.get("private", True) else "private",
        "default_branch": data.default_branch or "main",
        "empty": True,
        "has_workflows": False,
        "protection": {},
        "size_kb": 0,
    }


def _read_remote(
    client: GithubClient, data: schemas.RepositoryCreate, full_name: str, ic: IntegrationContext
) -> dict[str, Any]:
    try:
        info = client.get_repo(full_name)
    except ProviderError as exc:
        if _reason(exc) in ("not_found", "forbidden", "not_accessible", "unauthorized"):
            raise RepositoryNotAccessible(details={"full_name": full_name}) from exc
        raise
    if info.get("archived"):
        raise RepositoryArchived(details={"full_name": full_name})
    size_kb = int(info.get("size") or 0)
    if size_kb > ic.settings.repo_max_size_mb * 1024:
        raise RepositoryTooLarge(
            details={"size_kb": size_kb, "limit_mb": ic.settings.repo_max_size_mb}
        )
    if not client.can_push(full_name):
        raise TokenCannotPush(details={"missing_permissions": ["Contents: read and write"]})
    empty = client.is_empty(full_name)
    default_branch = data.default_branch or str(info.get("default_branch") or "main")
    protection: dict[str, Any] = {}
    has_workflows = False
    if not empty:
        if data.default_branch:
            try:
                client.get_branch(full_name, default_branch)
            except ProviderError as exc:
                if _reason(exc) == "not_found":
                    raise ValidationFailed(
                        f"The branch {default_branch!r} does not exist in {full_name}",
                        details=[{"loc": ["body", "default_branch"], "msg": "no such branch"}],
                    ) from exc
                raise
        with contextlib.suppress(ProviderError):
            protection = protection_summary(client, full_name, default_branch)
        has_workflows = client.path_exists(full_name, ".github/workflows", ref=default_branch)
    return {
        "html_url": info.get("html_url") or f"https://github.com/{full_name}",
        "visibility": str(
            info.get("visibility") or ("private" if info.get("private") else "public")
        ),
        "default_branch": default_branch,
        "empty": empty,
        "has_workflows": has_workflows,
        "protection": protection,
        "size_kb": size_kb,
    }


def _capabilities(client: GithubClient, full_name: str, mode: str) -> dict[str, Any]:
    from app.services import github_accounts

    return dict(github_accounts.probe_capabilities(client, full_name, mode=mode))  # type: ignore[arg-type]


def _bind(
    db: Session,
    ctx: CompanyContext,
    *,
    data: schemas.RepositoryCreate,
    project_input: Any,
    account: ConnectedAccount,
    project: Project | None,
    reuse: ProjectRepository | None,
    owner: str,
    name: str,
    facts: dict[str, Any],
    policy_: schemas.RepoPolicy,
    capabilities: dict[str, Any],
    publish: bool,
    moved: Path | None,
    token_expires_at: datetime | None,
    rate: Any,
    ic: IntegrationContext,
    work_branch_requested: str | None,
) -> ProjectRepository:
    from app.services import projects as projects_service

    company_id, principal = ctx.company_id, ctx.principal
    now = utcnow()
    full_name = f"{owner}/{name}"
    if project is None:
        project = projects_service.create_project(
            db, ctx, project_input, commit=False, bind_repository=False
        )
    work_branch = _unique_work_branch(db, company_id, full_name, project, work_branch_requested)
    if work_branch == facts["default_branch"]:
        raise ValidationFailed("The work branch must differ from the default branch")

    if data.mode == "create":
        origin = "published" if publish else ("created" if data.initialize else "connected")
        status = "creating" if origin != "connected" else "cloning"
    else:
        if publish:
            origin, status = "published", "creating"
        elif facts["empty"] and data.initialize:
            origin, status = "created", "creating"
        else:
            origin, status = "connected", "cloning"
    reason = None
    if moved is not None:
        reason = f"The previous local repository of this project was kept at {moved.name}"
    ctype, cid = ("human" if principal.is_human else "agent"), principal.id

    values: dict[str, Any] = dict(
        company_id=company_id,
        project_id=project.id,
        provider="github",
        auth_kind="pat",
        connected_account_id=account.id,
        owner=owner,
        name=name,
        full_name=full_name,
        html_url=facts["html_url"],
        remote_url=f"{ic.settings.github_web_url.rstrip('/')}/{full_name}.git",
        visibility=facts["visibility"],
        default_branch=facts["default_branch"],
        work_branch=work_branch,
        origin=origin,
        status=status,
        status_reason=reason,
        sync_state="idle",
        policy=policy_.model_dump(),
        capabilities=capabilities,
        protection=facts["protection"],
        etags={},
        next_sync_at=None,
        sync_started_at=None,
        sync_failures=0,
        token_expires_at=token_expires_at,
        rate_limit_remaining=getattr(rate, "remaining", None),
        rate_limit_reset_at=getattr(rate, "reset_at", None),
        last_fetched_default_sha=None,
        last_pushed_sha=None,
        integration_head_sha=None,
        webhook_id=None,
        webhook_url=None,
        webhook_secret_credential_id=None,
        onboarding_status="pending"
        if (not facts["empty"] and origin == "connected")
        else "not_needed",
        onboarding_task_id=None,
        is_company_default=False,
        pr_opening_paused=False,
        created_by_type=ctype,
        created_by_id=cid,
    )
    if reuse is not None:  # a disconnected binding: the project's row is reused (one per project)
        for key, value in values.items():
            setattr(reuse, key, value)
        reuse.updated_at = now
        row = reuse
    else:
        row = ProjectRepository(**values)
        db.add(row)
    db.flush()

    if data.company_default:
        _make_company_default(db, principal, row)
        limits = policy.load_policy(db, company_id)
        if limits.new_project_repository != "company_repo":
            limits.new_project_repository = "company_repo"
            limits.updated_at = now
            audit.record(
                db,
                company_id=company_id,
                principal=principal,
                action="policy.updated",
                target_type="policy",
                target_id=limits.id,
                summary="Updated company policy: new_project_repository",
                data={"changed": ["new_project_repository"], "via": "repository"},
            )
            events.emit(db, company_id, "policy.updated", {"changed": ["new_project_repository"]})

    action = {"created": "repository.created", "published": "repository.published"}.get(
        origin, "repository.connected"
    )
    _audit(
        db,
        row,
        principal,
        action,
        f"{'Created' if data.mode == 'create' else 'Connected'} repository {full_name} "
        f"for project {project.name}",
        {
            "mode": data.mode,
            "origin": origin,
            "visibility": row.visibility,
            "default_branch": row.default_branch,
            "work_branch": row.work_branch,
            "merge_mode": policy_.merge_mode,
            "company_default": row.is_company_default,
            "local_history_kept_as": moved.name if moved is not None else None,
        },
    )
    if moved is not None:
        alerts.raise_alert(
            db,
            company_id,
            kind="github_local_history_moved",
            severity=AlertSeverity.INFO,
            title=f"The local history of '{project.name}' was kept aside",
            body=f"{reason}. {full_name} replaced it; unrelated histories are never merged.",
            related_type="repository",
            related_id=row.id,
            dedupe_key=f"github_local_history_moved:{row.id}",
            wake_founder=False,
        )
    repo_sync.enqueue_clone(db, row, now)
    _emit(db, row)
    db.commit()
    return row


def _project_input(data: schemas.RepositoryCreate):
    from app.schemas.work import ProjectCreate

    return ProjectCreate(name=data.project_name, description=data.project_description)


def _make_company_default(db: Session, principal: Principal | None, row: ProjectRepository) -> None:
    others = db.scalars(
        select(ProjectRepository).where(
            ProjectRepository.company_id == row.company_id,
            ProjectRepository.is_company_default.is_(True),
            ProjectRepository.id != row.id,
        )
    )
    for other in others:
        other.is_company_default = False
        _audit(
            db,
            other,
            principal,
            "repository.policy_updated",
            f"{other.full_name} is no longer the company's default repository",
            {"changed": {"is_company_default": {"old": True, "new": False}}},
        )
    row.is_company_default = True


# ------------------------------------------------------------------ update


def update_repository(
    db: Session, ctx: CompanyContext, repository_id: str, data: schemas.RepositoryUpdate
) -> schemas.ProjectRepository:
    """Patch the policy (only the given fields; ``direct_push`` is refused while the base branch is
    protected), the default branch or ``is_company_default``. Audited with old/new values."""
    require_human_admin(ctx, "change a repository")
    row = _load(db, ctx.company_id, repository_id)
    if row.status == "disconnected":
        raise Conflict("This repository is disconnected; connect it again first")
    changes: dict[str, dict[str, Any]] = {}

    if data.policy is not None:
        current = dict(row.policy or {})
        given = data.policy.model_dump(exclude_unset=True)
        merged = schemas.RepoPolicy.model_validate({**current, **given}).model_dump()
        if (
            merged["merge_mode"] == "direct_push"
            and current.get("merge_mode") != "direct_push"
            and not _direct_push_allowed(row.protection or {})
        ):
            raise BranchProtected(details={"branch": row.default_branch})
        # compare against the full normalised policy so defaults count as unchanged
        before = schemas.RepoPolicy.model_validate(current).model_dump()
        for key, value in merged.items():
            if before.get(key) != value:
                changes[f"policy.{key}"] = {"old": before.get(key), "new": value}
        if changes:
            row.policy = merged

    if data.default_branch is not None and data.default_branch != row.default_branch:
        if data.default_branch == row.work_branch:
            raise ValidationFailed("The default branch must differ from the work branch")
        open_pr = db.scalar(
            select(func.count(RepoPullRequest.id)).where(
                RepoPullRequest.repository_id == row.id,
                RepoPullRequest.stage.notin_(TERMINAL_PR_STAGES),
            )
        )
        if open_pr:
            raise RepositoryBusy("A pull request is open; change the base branch after it ends")
        changes["default_branch"] = {"old": row.default_branch, "new": data.default_branch}
        row.default_branch = data.default_branch
        row.protection = {}  # stale: re-read by the next sync
        row.last_fetched_default_sha = None

    if data.is_company_default is not None and data.is_company_default != row.is_company_default:
        changes["is_company_default"] = {
            "old": row.is_company_default,
            "new": data.is_company_default,
        }
        if data.is_company_default:
            _make_company_default(db, ctx.principal, row)
        else:
            row.is_company_default = False

    if changes:
        row.updated_at = utcnow()
        _audit(
            db,
            row,
            ctx.principal,
            "repository.policy_updated",
            f"Changed settings of {row.full_name}: {', '.join(sorted(changes))}",
            {"changed": changes},
        )
        if "default_branch" in changes or any(
            k in changes for k in ("policy.poll_interval_seconds", "policy.merge_mode")
        ):
            repo_sync.request_sync(db, row.id, "settings", utcnow())
        _emit(db, row)
        db.commit()
    return _one(db, row)


# ------------------------------------------------------------------ lifecycle


def request_sync_now(db: Session, ctx: CompanyContext, repository_id: str) -> None:
    require_human_admin(ctx, "sync a repository")
    row = _load(db, ctx.company_id, repository_id)
    if row.status not in ("active", "auth_failed", "error"):
        raise Conflict(
            f"A repository in status {row.status!r} cannot be synced",
            details={"status": row.status},
        )
    repo_sync.request_sync(db, row.id, "manual", utcnow())
    db.commit()


def pause(db: Session, ctx: CompanyContext, repository_id: str) -> schemas.ProjectRepository:
    require_human_admin(ctx, "pause a repository")
    row = _load(db, ctx.company_id, repository_id)
    if row.status == "paused":
        return _one(db, row)
    if row.status in ("pending", "creating", "cloning", "disconnected"):
        raise Conflict(
            f"A repository in status {row.status!r} cannot be paused",
            details={"status": row.status},
        )
    previous = row.status
    row.status = "paused"
    row.status_reason = "Paused by an owner: no GitHub traffic; local work continues"
    row.updated_at = utcnow()
    _audit(
        db, row, ctx.principal, "repository.paused", f"Paused {row.full_name}", {"from": previous}
    )
    _emit(db, row)
    db.commit()
    return _one(db, row)


def resume(db: Session, ctx: CompanyContext, repository_id: str) -> schemas.ProjectRepository:
    require_human_admin(ctx, "resume a repository")
    row = _load(db, ctx.company_id, repository_id)
    if row.status == "active":
        return _one(db, row)
    if row.status != "paused":
        raise Conflict(
            f"A repository in status {row.status!r} cannot be resumed",
            details={"status": row.status},
        )
    now = utcnow()
    row.status = "active"
    row.status_reason = None
    row.sync_failures = 0
    row.updated_at = now
    repo_sync.request_sync(db, row.id, "resume", now)
    _audit(db, row, ctx.principal, "repository.resumed", f"Resumed {row.full_name}")
    _emit(db, row)
    db.commit()
    return _one(db, row)


def disconnect(
    db: Session, ctx: CompanyContext, repository_id: str, *, confirm: bool
) -> schemas.ProjectRepository:
    """409 ``repository_busy`` while a Foundry PR is ``merging`` or a run of the project is
    active; the row and the clone are kept (``status = disconnected``)."""
    require_human_admin(ctx, "disconnect a repository")
    row = _load(db, ctx.company_id, repository_id)
    if not confirm:
        raise ValidationFailed(
            "Disconnecting needs confirm: true",
            details=[{"loc": ["body", "confirm"], "msg": "must be true"}],
        )
    if row.status == "disconnected":
        return _one(db, row)
    merging = db.scalar(
        select(func.count(RepoPullRequest.id)).where(
            RepoPullRequest.repository_id == row.id, RepoPullRequest.stage == "merging"
        )
    )
    if merging or _project_run_active(db, row.company_id, row.project_id):
        raise RepositoryBusy()
    had_hook = bool(row.webhook_id)
    if row.webhook_secret_credential_id:
        secret = db.get(Credential, row.webhook_secret_credential_id)
        if secret is not None and secret.revoked_at is None:
            secret.revoked_at = utcnow()  # deliveries of the old hook are now rejected
    row.webhook_id = None
    row.webhook_url = None
    row.webhook_secret_credential_id = None
    row.status = "disconnected"
    row.status_reason = "Disconnected by an owner; the clone is kept and work continues locally"
    row.next_sync_at = None
    row.sync_started_at = None
    row.is_company_default = False
    row.updated_at = utcnow()
    _audit(
        db,
        row,
        ctx.principal,
        "repository.disconnected",
        f"Disconnected {row.full_name}",
        {"webhook_left_on_github": had_hook},
    )
    _emit(db, row)
    db.commit()
    return _one(db, row)


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
    row = _load(db, ctx.company_id, repository_id)
    if not data.confirm:
        raise ValidationFailed(
            "Resetting the integration branch needs confirm: true",
            details=[{"loc": ["body", "confirm"], "msg": "must be true"}],
        )
    if row.status in ("pending", "creating", "cloning", "disconnected"):
        raise Conflict(
            f"A repository in status {row.status!r} has no clone to reset",
            details={"status": row.status},
        )
    if _project_run_active(db, row.company_id, row.project_id):
        raise RepositoryBusy("A run of this project is active; reset after it finishes")
    rescue = f"{schemas.WORK_BRANCH_PREFIX}rescue-{utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    repo = pw.main_repo(ic.settings.workspaces_dir, row.company_id, row.project_id)
    try:
        new_head = repo_git.reset_integration(repo, row, to=data.to, rescue_name=rescue)
    except (pw.GitError, repo_git.GitNetworkError) as exc:
        raise Conflict(f"The reset failed: {exc}", details={"reason": "reset_failed"}) from exc
    now = utcnow()
    row.integration_head_sha = new_head
    row.sync_state = "idle"
    row.updated_at = now
    repo_sync.request_sync(db, row.id, "reset", now)
    _audit(
        db,
        row,
        ctx.principal,
        "repository.integration_reset",
        f"Reset the work branch of {row.full_name} to {data.to}",
        {"to": data.to, "rescue_branch": rescue, "new_head": (new_head or "")[:12]},
    )
    _emit(db, row)
    db.commit()
    return _one(db, row)


def resume_pr_opening(
    db: Session, ctx: CompanyContext, repository_id: str
) -> schemas.ProjectRepository:
    require_human_admin(ctx, "resume pull requests")
    row = _load(db, ctx.company_id, repository_id)
    if row.pr_opening_paused:
        now = utcnow()
        row.pr_opening_paused = False
        row.updated_at = now
        repo_sync.request_sync(db, row.id, "resume_opening", now)
        _audit(
            db,
            row,
            ctx.principal,
            "repository.pr_opening_resumed",
            f"Pull requests may be opened again on {row.full_name}",
        )
        _emit(db, row)
        db.commit()
    return _one(db, row)


# ------------------------------------------------------------------ webhook


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
    row = _load(db, ctx.company_id, repository_id)
    if row.status in ("pending", "creating", "cloning", "disconnected"):
        raise Conflict(
            f"A repository in status {row.status!r} cannot have a webhook",
            details={"status": row.status},
        )
    if enabled:
        return _enable_webhook(db, ctx, ic, row)
    return _disable_webhook(db, ctx, ic, row)


def _enable_webhook(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, row: ProjectRepository
) -> schemas.ProjectRepository:
    public = reachability.public_url(ic)
    if not public or reachability.tunnel_is_ephemeral(ic):
        raise PublicUrlEphemeral()
    url = public.rstrip("/") + WEBHOOK_PATH.format(repository_id=row.id)
    if row.webhook_id and row.webhook_url == url:
        return _one(db, row)
    account = _account(db, row.company_id, row.connected_account_id or "")
    secret = secrets.token_urlsafe(32)
    client = _client(db, ic, account)
    try:
        if row.webhook_id:  # stale: the public URL changed
            with contextlib.suppress(ProviderError):
                client.delete_hook(row.full_name, row.webhook_id)
        try:
            hook = client.create_hook(row.full_name, url=url, secret=secret, events=WEBHOOK_EVENTS)
        except ProviderError as exc:
            if _reason(exc) in ("forbidden", "not_accessible", "not_found"):
                raise Conflict(
                    "The token cannot manage webhooks on this repository "
                    "(it needs the Webhooks permission)",
                    details={
                        "reason": "webhook_permission_missing",
                        "missing_permissions": ["Webhooks: read and write"],
                    },
                ) from exc
            raise
    finally:
        with contextlib.suppress(Exception):
            client.http.close()
    now = utcnow()
    if row.webhook_secret_credential_id:
        old = db.get(Credential, row.webhook_secret_credential_id)
        if old is not None and old.revoked_at is None:
            old.revoked_at = now
    credential = Credential(
        company_id=row.company_id,
        name=f"GitHub webhook secret {row.id[:8]} {now.strftime('%Y%m%dT%H%M%S')}",
        kind="other",
        encrypted_value=ic.cipher.encrypt(secret),
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(credential)
    db.flush()
    row.webhook_id = str(hook.get("id"))[:32]
    row.webhook_url = url
    row.webhook_secret_credential_id = credential.id
    row.updated_at = now
    _audit(
        db,
        row,
        ctx.principal,
        "repository.webhook_enabled",
        f"Enabled the webhook of {row.full_name}",
        {"events": WEBHOOK_EVENTS},  # the URL and the secret stay out of the audit trail
    )
    _emit(db, row)
    db.commit()
    return _one(db, row)


def _disable_webhook(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, row: ProjectRepository
) -> schemas.ProjectRepository:
    if not row.webhook_id:
        return _one(db, row)
    deleted = False
    account = (
        db.get(ConnectedAccount, row.connected_account_id) if row.connected_account_id else None
    )
    if account is not None and account.status == "connected":
        client = _client(db, ic, account)
        try:
            client.delete_hook(row.full_name, row.webhook_id)
            deleted = True
        except ProviderError as exc:
            deleted = _reason(exc) == "not_found"
        finally:
            with contextlib.suppress(Exception):
                client.http.close()
    now = utcnow()
    if row.webhook_secret_credential_id:
        secret = db.get(Credential, row.webhook_secret_credential_id)
        if secret is not None and secret.revoked_at is None:
            secret.revoked_at = now
    row.webhook_id = None
    row.webhook_url = None
    row.webhook_secret_credential_id = None
    row.updated_at = now
    _audit(
        db,
        row,
        ctx.principal,
        "repository.webhook_disabled",
        f"Disabled the webhook of {row.full_name}",
        {"hook_deleted_on_github": deleted},
    )
    _emit(db, row)
    db.commit()
    return _one(db, row)


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
    row = db.get(ProjectRepository, repository_id)
    if row is None or not row.webhook_id or not row.webhook_secret_credential_id:
        raise WebhookRejected()
    try:
        secret = credentials.reveal(db, ic.cipher, row.company_id, row.webhook_secret_credential_id)
    except Exception as exc:  # revoked, missing or undecryptable: same answer
        raise WebhookRejected() from exc
    lowered = {k.lower(): v for k, v in headers.items()}
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = lowered.get("x-hub-signature-256", "")
    if not hmac.compare_digest(expected.encode(), given.encode()):
        raise WebhookRejected()
    event = lowered.get("x-github-event", "")
    if event != "ping" and row.status == "active":
        repo_sync.request_sync(db, row.id, "webhook", utcnow())
        db.commit()
    return {"accepted": True}


# ------------------------------------------------------------------ used by other services


def binding_for(db: Session, company_id: str, full_name: str) -> ProjectRepository | None:
    """The ACTIVE binding of ``owner/name`` in a company, else None (the executors' guard). When
    several projects share the repository the company default wins, then the oldest."""
    return db.scalar(
        select(ProjectRepository)
        .where(
            ProjectRepository.company_id == company_id,
            func.lower(ProjectRepository.full_name) == full_name.strip().lower(),
            ProjectRepository.status == "active",
        )
        .order_by(
            ProjectRepository.is_company_default.desc(),
            ProjectRepository.created_at,
            ProjectRepository.id,
        )
        .limit(1)
    )


def agent_may_use(db: Session, rc: AgentRunContext, binding: ProjectRepository) -> bool:
    """The agent is the AI Co-founder, the lead of a project bound to ``binding``'s repository, or
    is running a task of such a project."""
    if rc.ctx.company_id != binding.company_id:
        return False
    if rc.agent.kind == AgentKind.FOUNDER:
        return True
    project_ids = set(
        db.scalars(
            select(ProjectRepository.project_id).where(
                ProjectRepository.company_id == binding.company_id,
                func.lower(ProjectRepository.full_name) == binding.full_name.lower(),
                ProjectRepository.status != "disconnected",
            )
        )
    )
    project_ids.add(binding.project_id)
    if rc.task.project_id and rc.task.project_id in project_ids:
        return True
    return bool(
        db.scalar(
            select(func.count(Project.id)).where(
                Project.company_id == binding.company_id,
                Project.id.in_(project_ids),
                Project.lead_agent_id == rc.agent.id,
            )
        )
    )


def bind_for_agent(
    db: Session, rc: AgentRunContext, ic: IntegrationContext, params: dict[str, Any]
) -> dict[str, Any]:
    """``github.repo.create`` after its human approval: create the repository and bind it to the
    caller's project. Returns a token-free summary."""
    company_id = rc.ctx.company_id
    project_id = str(params.get("project_id") or rc.task.project_id or "")
    if not project_id:
        raise ValidationFailed("A repository belongs to a project: name the project_id")
    project = get_project_or_404(db, company_id, project_id)
    if rc.agent.kind != AgentKind.FOUNDER and project.lead_agent_id != rc.agent.id:
        raise Forbidden("Only the AI Co-founder or the project's lead can create its repository")
    name = str(params.get("name") or "").strip()
    owner = str(params.get("owner") or "").strip() or None
    if not name:
        raise ValidationFailed("A repository name is required")
    account_id = str(params.get("connected_account_id") or "") or None
    if account_id is None:
        account = connected_accounts.find_account(db, company_id, "github")
        if account is None:
            raise NoAccount()
        account_id = account.id
    visibility = "public" if str(params.get("visibility") or "private") == "public" else "private"
    try:
        data = schemas.RepositoryCreate(
            mode="create",
            connected_account_id=account_id,
            project_id=project.id,
            owner=owner,
            name=name,
            visibility=visibility,
            confirm_public=True,  # a human approved exactly this visibility
            description=(str(params.get("description") or "")[:350] or None),
            company_default=False,
        )
    except ValueError as exc:
        raise ValidationFailed(f"Invalid repository request: {exc}") from exc
    row = _create(db, rc.ctx, ic, data)
    return {
        "repository_id": row.id,
        "full_name": row.full_name,
        "html_url": row.html_url,
        "visibility": row.visibility,
        "status": row.status,
        "project_id": row.project_id,
        "work_branch": row.work_branch,
        "default_branch": row.default_branch,
    }


def on_project_created(db: Session, ctx: CompanyContext, project: Project) -> None:
    """Called by ``projects.create_project``: with ``new_project_repository = company_repo`` the new
    project gets its own binding to the company's default repository (own work branch, own clone).
    No-op with ``local``. The new binding shares the repository, so it takes no issue intake,
    alerts, releases or docs upkeep of its own (the default binding does that once). No commit."""
    if policy.load_policy(db, project.company_id).new_project_repository != "company_repo":
        return
    if not pw.status()["available"]:
        return
    default = db.scalar(
        select(ProjectRepository)
        .where(
            ProjectRepository.company_id == project.company_id,
            ProjectRepository.is_company_default.is_(True),
            ProjectRepository.status != "disconnected",
        )
        .order_by(ProjectRepository.created_at)
        .limit(1)
    )
    if default is None:
        return
    if db.scalar(
        select(ProjectRepository.id).where(
            ProjectRepository.company_id == project.company_id,
            ProjectRepository.project_id == project.id,
        )
    ):
        return
    shared = dict(default.policy or {})
    shared.update(
        issue_intake="off",
        security_alerts="off",
        releases="off",
        docs_upkeep=False,
        stale_branch_days=0,
    )
    now = utcnow()
    row = ProjectRepository(
        company_id=project.company_id,
        project_id=project.id,
        provider=default.provider,
        auth_kind=default.auth_kind,
        connected_account_id=default.connected_account_id,
        owner=default.owner,
        name=default.name,
        full_name=default.full_name,
        html_url=default.html_url,
        remote_url=default.remote_url,
        visibility=default.visibility,
        default_branch=default.default_branch,
        work_branch=_unique_work_branch(db, project.company_id, default.full_name, project, None),
        origin="connected",
        status="cloning",
        sync_state="idle",
        policy=schemas.RepoPolicy.model_validate(shared).model_dump(),
        capabilities=dict(default.capabilities or {}),
        protection=dict(default.protection or {}),
        etags={},
        onboarding_status="not_needed",
        is_company_default=False,
        pr_opening_paused=False,
        created_by_type="human" if ctx.principal.is_human else "agent",
        created_by_id=ctx.principal.id,
    )
    db.add(row)
    db.flush()
    _audit(
        db,
        row,
        ctx.principal,
        "repository.connected",
        f"Bound new project {project.name} to the company repository {row.full_name}",
        {
            "mode": "company_repo",
            "work_branch": row.work_branch,
            "shared_with": default.id,
            "default_branch": row.default_branch,
        },
    )
    repo_sync.enqueue_clone(db, row, now)
    _emit(db, row)
