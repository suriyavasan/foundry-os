"""M14 GitHub-backed projects: repository bindings, Foundry pull requests, GitHub items

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27 18:00:00.000000

* ``project_repositories``: one remote repository per project (the token stays in the credential
  vault behind the connected account; nothing secret is stored here).
* ``repo_pull_requests``: the pull requests Foundry opened or adopted, with the review/CI state.
* ``repo_items``: issues, reviews, failing checks and alerts that became (or may become) tasks.
* ``company_policies.new_project_repository`` (human-only): ``local`` (default) or ``company_repo``.

Portable (SQLite and Postgres). The new policy column has a server default, so existing rows are
valid immediately and nothing is backfilled. The downgrade drops everything it added.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{ref_table}")
    )


def upgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "new_project_repository",
                sa.String(length=16),
                nullable=False,
                server_default="local",
            )
        )

    op.create_table(
        "project_repositories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="github"),
        sa.Column("auth_kind", sa.String(length=16), nullable=False, server_default="pat"),
        sa.Column("connected_account_id", sa.String(length=36), nullable=True),
        sa.Column("owner", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("full_name", sa.String(length=201), nullable=False),
        sa.Column("html_url", sa.String(length=300), nullable=True),
        sa.Column("remote_url", sa.String(length=300), nullable=False),
        sa.Column("visibility", sa.String(length=10), nullable=False, server_default="private"),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("work_branch", sa.String(length=255), nullable=False),
        sa.Column("origin", sa.String(length=12), nullable=False, server_default="connected"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("sync_state", sa.String(length=24), nullable=False, server_default="idle"),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("protection", sa.JSON(), nullable=False),
        sa.Column("etags", sa.JSON(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(), nullable=True),
        sa.Column("sync_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_issues_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_alerts_sync_at", sa.DateTime(), nullable=True),
        sa.Column("last_cleanup_at", sa.DateTime(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("rate_limit_reset_at", sa.DateTime(), nullable=True),
        sa.Column("sync_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_limit_remaining", sa.Integer(), nullable=True),
        sa.Column("last_fetched_default_sha", sa.String(length=40), nullable=True),
        sa.Column("last_pushed_sha", sa.String(length=40), nullable=True),
        sa.Column("integration_head_sha", sa.String(length=40), nullable=True),
        sa.Column("webhook_id", sa.String(length=32), nullable=True),
        sa.Column("webhook_secret_credential_id", sa.String(length=36), nullable=True),
        sa.Column("webhook_url", sa.String(length=500), nullable=True),
        sa.Column(
            "onboarding_status", sa.String(length=16), nullable=False, server_default="not_needed"
        ),
        sa.Column("onboarding_task_id", sa.String(length=36), nullable=True),
        sa.Column("is_company_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pr_opening_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        *_ts(),
        _fk("project_repositories", "company_id", "companies.id"),
        _fk("project_repositories", "project_id", "projects.id"),
        _fk("project_repositories", "connected_account_id", "connected_accounts.id"),
        _fk("project_repositories", "webhook_secret_credential_id", "credentials.id"),
        _fk("project_repositories", "onboarding_task_id", "tasks.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_repositories")),
        sa.UniqueConstraint("company_id", "project_id", name="uq_project_repositories_project"),
    )
    with op.batch_alter_table("project_repositories", schema=None) as batch_op:
        batch_op.create_index("ix_project_repositories_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_project_repositories_company_full_name", ["company_id", "full_name"], unique=False
        )
        batch_op.create_index(
            "ix_project_repositories_due", ["status", "next_sync_at"], unique=False
        )

    op.create_table(
        "repo_pull_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("head_branch", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("review_sha", sa.String(length=40), nullable=True),
        sa.Column("verdict_sha", sa.String(length=40), nullable=True),
        sa.Column("merged_sha", sa.String(length=40), nullable=True),
        sa.Column("stage", sa.String(length=24), nullable=False, server_default="opening"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_task_id", sa.String(length=36), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("verdict_note", sa.Text(), nullable=True),
        sa.Column("verdict_agent_id", sa.String(length=36), nullable=True),
        sa.Column("checks_state", sa.String(length=12), nullable=False, server_default="unknown"),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("checks_since", sa.DateTime(), nullable=True),
        sa.Column("ci_fix_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mergeable_state", sa.String(length=16), nullable=True),
        sa.Column("human_review_state", sa.String(length=24), nullable=True),
        sa.Column("last_review_seen_at", sa.DateTime(), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("merged_by", sa.String(length=100), nullable=True),
        sa.Column("task_ids", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("merged_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_ts(),
        _fk("repo_pull_requests", "company_id", "companies.id"),
        _fk("repo_pull_requests", "repository_id", "project_repositories.id"),
        _fk("repo_pull_requests", "review_task_id", "tasks.id"),
        _fk("repo_pull_requests", "verdict_agent_id", "agents.id"),
        _fk("repo_pull_requests", "approval_id", "approvals.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repo_pull_requests")),
        sa.UniqueConstraint("repository_id", "number", name="uq_repo_pull_requests_repo_number"),
    )
    with op.batch_alter_table("repo_pull_requests", schema=None) as batch_op:
        batch_op.create_index("ix_repo_pull_requests_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_repo_pull_requests_repo_stage", ["repository_id", "stage"], unique=False
        )

    op.create_table(
        "repo_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=True),
        sa.Column("author_association", sa.String(length=24), nullable=True),
        sa.Column("remote_state", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="new"),
        sa.Column("ignore_reason", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("pull_request_id", sa.String(length=36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=12), nullable=True),
        sa.Column("body_excerpt", sa.Text(), nullable=True),
        sa.Column("injection_flags", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("remote_updated_at", sa.DateTime(), nullable=True),
        *_ts(),
        _fk("repo_items", "company_id", "companies.id"),
        _fk("repo_items", "repository_id", "project_repositories.id"),
        _fk("repo_items", "task_id", "tasks.id"),
        _fk("repo_items", "pull_request_id", "repo_pull_requests.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repo_items")),
        sa.UniqueConstraint(
            "repository_id", "kind", "external_id", name="uq_repo_items_repo_kind_external"
        ),
    )
    with op.batch_alter_table("repo_items", schema=None) as batch_op:
        batch_op.create_index("ix_repo_items_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_repo_items_repo_status", ["company_id", "repository_id", "status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("repo_items", schema=None) as batch_op:
        batch_op.drop_index("ix_repo_items_repo_status")
        batch_op.drop_index("ix_repo_items_company_id")
    op.drop_table("repo_items")
    with op.batch_alter_table("repo_pull_requests", schema=None) as batch_op:
        batch_op.drop_index("ix_repo_pull_requests_repo_stage")
        batch_op.drop_index("ix_repo_pull_requests_company_id")
    op.drop_table("repo_pull_requests")
    with op.batch_alter_table("project_repositories", schema=None) as batch_op:
        batch_op.drop_index("ix_project_repositories_due")
        batch_op.drop_index("ix_project_repositories_company_full_name")
        batch_op.drop_index("ix_project_repositories_company_id")
    op.drop_table("project_repositories")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("new_project_repository")
