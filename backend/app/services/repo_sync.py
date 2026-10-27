"""The sync loop, PR state machine and engine hooks of GitHub-backed projects (M14 step 0:
contracts; WP-B implements, except the few small functions marked "real" below).

See docs/specs/m14-github-backed-projects.md sections 3.3 to 3.7. In short, per repository every
``poll_interval_seconds`` (or when poked): lease -> read GitHub once into a ``SyncSnapshot`` ->
git fetch / integrate under the project lock -> ``repo_maintenance.process`` -> advance the pull
request -> update the binding. Every system GitHub write passes ``repo_gate.authorize``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.crypto import CredentialCipher
from app.core.time import utcnow
from app.integrations.base import IntegrationHttp
from app.models.agent import Agent
from app.models.repository import ProjectRepository
from app.models.run import Job
from app.models.work import Task
from app.services import jobs
from app.services.repo_errors import not_built

if TYPE_CHECKING:
    from app.engine.context import EngineContext
    from app.integrations.context import IntegrationContext

JOB_CLONE = "repo_clone"
JOB_SYNC = "repo_sync"
JOB_KINDS = (JOB_CLONE, JOB_SYNC)
SYNC_LEASE_SECONDS = 600
SYNC_WRITE_SPACING_SECONDS = 1.0

WorkspaceKind = Literal["project", "review", "upstream_merge", "run_dir"]


@dataclass
class RepoEnv:
    """What the sync loop needs to talk to GitHub. The API process builds it from its
    ``IntegrationContext``; the worker from its ``EngineContext`` (which has no integration
    context of its own: it carries ``integration_http``, a ``IntegrationHttp`` the engine's test
    fixtures replace)."""

    settings: Settings
    cipher: CredentialCipher
    http: IntegrationHttp
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = utcnow

    @classmethod
    def from_integrations(cls, ic: IntegrationContext) -> RepoEnv:
        return cls(settings=ic.settings, cipher=ic.cipher, http=ic.http, sleep=ic.sleep)

    @classmethod
    def from_engine(cls, ctx: EngineContext) -> RepoEnv:
        http = getattr(ctx, "integration_http", None) or IntegrationHttp()
        return cls(
            settings=ctx.settings,
            cipher=CredentialCipher(ctx.keys),
            http=http,
            now=ctx.clock,
        )


@dataclass
class SyncSnapshot:
    """Every GitHub API read of one sync, taken before any git work (ETags and the rate limit are
    handled once, in the client). ``None`` means "not read this time" (304, rate-limit budget,
    capability missing), never "empty"."""

    taken_at: datetime
    repo: dict[str, Any] = field(default_factory=dict)  # repo meta (renamed branch? archived?)
    protection: dict[str, Any] | None = None  # refreshed every 6 h
    pull: dict[str, Any] | None = None  # the open Foundry PR (GitHub's view)
    reviews: list[dict[str, Any]] | None = None
    review_comments: list[dict[str, Any]] | None = None
    checks: list[dict[str, Any]] | None = None  # normalized checks of the PR head
    default_checks: list[dict[str, Any]] | None = None  # normalized checks of the default head
    issues: list[dict[str, Any]] | None = None  # issues (no PRs) since last_issues_sync_at
    alerts: list[dict[str, Any]] | None = None  # refreshed hourly
    rate_remaining: int | None = None
    rate_reset_at: datetime | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkspaceSpec:
    """How a task's workspace is prepared (``project_workspaces.prepare``). The M12 defaults are
    ``WorkspaceSpec(kind="project", branch="main")`` for a work task of a local project."""

    kind: WorkspaceKind = "project"
    branch: str = "main"
    ref: str | None = None  # a commit to check out detached (PR review at review_sha)
    read_only: bool = False
    scan_secrets: bool = False  # remote-backed: run the push gate before the merge
    protected_paths: tuple[str, ...] = ()
    max_file_bytes: int = 0  # 0 = no per-file limit
    repository_id: str | None = None
    upstream_sha: str | None = None  # upstream_merge: the origin commit to merge
    conflict_files: tuple[str, ...] = ()  # upstream_merge: files that must be resolved


def workspace_spec_for(db: Session, task: Task) -> WorkspaceSpec | None:
    """Called by ``executor`` before ``prepare``: ``repo:onboard:`` -> read-only review of the work
    branch; ``github:pr:`` -> read-only review at ``review_sha``; ``repo:upstream:`` ->
    ``upstream_merge``; a project with an active or disconnected binding -> its work branch with
    ``scan_secrets``; otherwise None (the M12 defaults). Step 0: always None (no existing path
    changes)."""
    return None


def dispatch_hold(db: Session, task: Task) -> str | None:
    """Called by the dispatcher: a reason to skip ``task`` this tick (``repository_not_ready``
    while the binding is pending / creating / cloning; ``codebase_onboarding`` while the onboarding
    task runs and ``task`` is a work task), else None. Step 0: always None."""
    return None


def actor_for(db: Session, binding: ProjectRepository) -> Agent:
    """Who system GitHub operations are attributed to: the project's active lead, else the AI
    Co-founder."""
    raise not_built()


def request_sync(db: Session, repository_id: str, reason: str, now: datetime | None = None) -> bool:
    """Real: make the repository's next sync due now (``next_sync_at = now``). Returns False for an
    unknown id. Does not check tenancy (the caller did) and does not commit. WP-B adds webhook
    debouncing (``max(now, last_synced_at + 10 s)``)."""
    row = db.get(ProjectRepository, repository_id)
    if row is None:
        return False
    row.next_sync_at = now or utcnow()
    return True


def enqueue_clone(db: Session, binding: ProjectRepository, now: datetime | None = None) -> Job:
    """Real: queue the ``repo_clone`` job that creates (origin ``created``) or clones the
    repository. Idempotent per binding. Does not commit."""
    return jobs.enqueue(
        db,
        kind=JOB_CLONE,
        payload={"repository_id": binding.id},
        company_id=binding.company_id,
        run_after=now,
        dedupe_key=f"repo_clone:{binding.id}",
        max_attempts=3,
    )


def enqueue_due(session_factory: sessionmaker[Session], now: datetime) -> int:
    """Called by ``Scheduler.tick`` every ``repo_sync_tick_seconds``: enqueue a ``repo_sync`` job
    (``dedupe_key=repo_sync:<id>:<next_sync_at>``) for every active binding whose sync is due.
    Step 0: enqueues nothing."""
    return 0


def run_job(ctx: EngineContext, job: jobs.LeasedJob) -> None:
    """Worker handler of ``repo_clone`` and ``repo_sync`` jobs (with a lease-extension thread:
    a clone can outlast ``LEASE_SECONDS``)."""
    raise not_built()


def sync_repository(env: RepoEnv, session_factory: sessionmaker[Session], repository_id: str):
    """One full sync of one repository (section 3.7)."""
    raise not_built()
