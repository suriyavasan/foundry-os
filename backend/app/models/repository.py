"""M14 tables: GitHub-backed projects (docs/specs/m14-github-backed-projects.md section 6.1).

A ``ProjectRepository`` binds one project to one remote repository. The token is never stored here:
``connected_account_id`` points at the M8 connected account, whose credential sits in the encrypted
vault. ``RepoPullRequest`` is the Foundry pull request of a binding (one open at a time) and
``RepoItem`` is a piece of GitHub content (issue, review, failing check, alert) that became, or may
become, a task. Everything GitHub-controlled that ends up in ``body_excerpt`` is clipped, scrubbed
and flagged as untrusted.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime

REPO_STATUSES = (
    "pending",
    "creating",
    "cloning",
    "active",
    "paused",
    "auth_failed",
    "error",
    "disconnected",
)
SYNC_STATES = (
    "idle",
    "upstream_conflict",
    "upstream_rewritten",
    "push_blocked",
    "rate_limited",
    "awaiting_approval",
)
PR_STAGES = (
    "opening",
    "reviewing",
    "changes_requested",
    "approved",
    "merging",
    "awaiting_human",
    "merged",
    "closed",
)
ONBOARDING_STATUSES = ("not_needed", "pending", "running", "done", "failed")
ITEM_KINDS = ("issue", "review", "internal_review", "check_failure", "alert")
ITEM_STATUSES = ("new", "queued", "tasked", "ignored", "done")


class ProjectRepository(TimestampMixin, Base):
    """The remote repository of a project (``status`` and ``sync_state`` are driven only by
    ``services/repo_sync.py``; the owner changes ``policy``, pause/resume and disconnect)."""

    __tablename__ = "project_repositories"
    __table_args__ = (
        UniqueConstraint("company_id", "project_id", name="uq_project_repositories_project"),
        Index("ix_project_repositories_company_id", "company_id"),
        Index("ix_project_repositories_company_full_name", "company_id", "full_name"),
        Index("ix_project_repositories_due", "status", "next_sync_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(16), default="github", server_default="github", nullable=False
    )
    # "pat" in M14; "app" (a GitHub App installation) is reserved.
    auth_kind: Mapped[str] = mapped_column(
        String(16), default="pat", server_default="pat", nullable=False
    )
    connected_account_id: Mapped[str | None] = mapped_column(ForeignKey("connected_accounts.id"))
    owner: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[str] = mapped_column(String(201), nullable=False)
    html_url: Mapped[str | None] = mapped_column(String(300))
    # Never carries userinfo (validated by the service): the token reaches git only through the
    # environment of a short-lived child process.
    remote_url: Mapped[str] = mapped_column(String(300), nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(10), default="private", server_default="private", nullable=False
    )
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    work_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    # created | connected | published
    origin: Mapped[str] = mapped_column(
        String(12), default="connected", server_default="connected", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    sync_state: Mapped[str] = mapped_column(
        String(24), default="idle", server_default="idle", nullable=False
    )
    # Validated by schemas.repository.RepoPolicy.
    policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    protection: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    etags: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sync_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # the sync lease
    last_issues_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_alerts_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_cleanup_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rate_limit_reset_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sync_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    last_fetched_default_sha: Mapped[str | None] = mapped_column(String(40))
    last_pushed_sha: Mapped[str | None] = mapped_column(String(40))
    integration_head_sha: Mapped[str | None] = mapped_column(String(40))
    webhook_id: Mapped[str | None] = mapped_column(String(32))
    webhook_secret_credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id"))
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    onboarding_status: Mapped[str] = mapped_column(
        String(16), default="not_needed", server_default="not_needed", nullable=False
    )
    onboarding_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    # One company default is enforced in the service, not by a constraint.
    is_company_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    pr_opening_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    created_by_type: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[str | None] = mapped_column(String(36))


class RepoPullRequest(TimestampMixin, Base):
    """A pull request Foundry opened (or adopted) on a bound repository. ``stage`` is moved only
    by ``repo_sync._advance_pull_request``; the internal reviewer's verdict only writes the
    ``verdict*`` columns."""

    __tablename__ = "repo_pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_repo_pull_requests_repo_number"),
        Index("ix_repo_pull_requests_company_id", "company_id"),
        Index("ix_repo_pull_requests_repo_stage", "repository_id", "stage"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("project_repositories.id"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str | None] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    head_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str | None] = mapped_column(String(40))
    review_sha: Mapped[str | None] = mapped_column(String(40))
    verdict_sha: Mapped[str | None] = mapped_column(String(40))
    merged_sha: Mapped[str | None] = mapped_column(String(40))
    stage: Mapped[str] = mapped_column(
        String(24), default="opening", server_default="opening", nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    review_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    verdict: Mapped[str | None] = mapped_column(String(16))  # accepted | changes_requested
    verdict_note: Mapped[str | None] = mapped_column(Text)
    verdict_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    checks_state: Mapped[str] = mapped_column(
        String(12), default="unknown", server_default="unknown", nullable=False
    )
    checks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    checks_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ci_fix_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    mergeable_state: Mapped[str | None] = mapped_column(String(16))
    human_review_state: Mapped[str | None] = mapped_column(String(24))
    last_review_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    merged_by: Mapped[str | None] = mapped_column(String(100))
    task_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    merged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)


class RepoItem(TimestampMixin, Base):
    """A piece of GitHub content that may become a task. Bodies are excerpts (clipped, scrubbed)
    and always untrusted input for agents."""

    __tablename__ = "repo_items"
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "kind", "external_id", name="uq_repo_items_repo_kind_external"
        ),
        Index("ix_repo_items_company_id", "company_id"),
        Index("ix_repo_items_repo_status", "company_id", "repository_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("project_repositories.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(100))
    author_association: Mapped[str | None] = mapped_column(String(24))
    remote_state: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(12), default="new", server_default="new", nullable=False
    )
    ignore_reason: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    pull_request_id: Mapped[str | None] = mapped_column(ForeignKey("repo_pull_requests.id"))
    revision: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str | None] = mapped_column(String(12))
    body_excerpt: Mapped[str | None] = mapped_column(Text)
    injection_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    remote_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
