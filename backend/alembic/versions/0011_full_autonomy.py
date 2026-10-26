"""M12 full autonomy: per-role models, failure backoff, shared project workspaces, watchdog

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-26 12:00:00.000000

* ``company_policies`` (human-only): an optional work model, the CLI role-family scope, the CLI run
  reservation, reserved management slots, per-agent parallelism, decomposition caps, the breaker
  cooldown, and the initiative / auto-complete switches.
* ``tasks``: ``next_attempt_at`` (retry backoff; indexed with company and status),
  ``transient_failures`` and ``agent_retry_count``.
* ``task_runs``: ``workspace_ref`` (where the run's files live and what was merged).
* ``autonomy_interventions``: bounds every automatic nudge of the autonomy watchdog.

Portable (SQLite and Postgres). Every non-null column has a server default so existing rows are
valid immediately; nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("work_model_provider_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("work_model_name", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("cli_role_families", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "cli_run_reservation_micros",
                sa.BigInteger(),
                nullable=False,
                server_default="1000000",
            )
        )
        batch_op.add_column(
            sa.Column("management_reserved_slots", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column(
                "max_parallel_runs_per_agent", sa.Integer(), nullable=False, server_default="1"
            )
        )
        batch_op.add_column(
            sa.Column(
                "max_work_tasks_per_project", sa.Integer(), nullable=False, server_default="15"
            )
        )
        batch_op.add_column(
            sa.Column("max_subtasks_per_task", sa.Integer(), nullable=False, server_default="5")
        )
        batch_op.add_column(
            sa.Column(
                "circuit_breaker_cooldown_seconds",
                sa.Integer(),
                nullable=False,
                server_default="1800",
            )
        )
        batch_op.add_column(
            sa.Column("initiative_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column(
                "initiative_interval_seconds",
                sa.Integer(),
                nullable=False,
                server_default="21600",
            )
        )
        batch_op.add_column(
            sa.Column(
                "project_auto_complete", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("transient_failures", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("agent_retry_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_index(
            "ix_tasks_company_status_next_attempt",
            ["company_id", "status", "next_attempt_at"],
            unique=False,
        )

    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workspace_ref", sa.JSON(), nullable=True))

    op.create_table(
        "autonomy_interventions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("subject_type", sa.String(length=24), nullable=False),
        sa.Column("subject_id", sa.String(length=80), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_at", sa.DateTime(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_autonomy_interventions_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_autonomy_interventions")),
        sa.UniqueConstraint(
            "company_id", "kind", "subject_id", name="uq_autonomy_interventions_subject"
        ),
    )
    op.create_index(
        "ix_autonomy_interventions_company_id", "autonomy_interventions", ["company_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_autonomy_interventions_company_id", table_name="autonomy_interventions")
    op.drop_table("autonomy_interventions")
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.drop_column("workspace_ref")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_company_status_next_attempt")
        batch_op.drop_column("agent_retry_count")
        batch_op.drop_column("transient_failures")
        batch_op.drop_column("next_attempt_at")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        for column in (
            "project_auto_complete",
            "initiative_interval_seconds",
            "initiative_enabled",
            "circuit_breaker_cooldown_seconds",
            "max_subtasks_per_task",
            "max_work_tasks_per_project",
            "max_parallel_runs_per_agent",
            "management_reserved_slots",
            "cli_run_reservation_micros",
            "cli_role_families",
            "work_model_name",
            "work_model_provider_id",
        ):
            batch_op.drop_column(column)
