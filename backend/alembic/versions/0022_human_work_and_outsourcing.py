"""M22 (B3): human work (tasks.requires_human ...) and outsourcing (outsource_jobs)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-31 12:00:00.000000

* ``tasks.requires_human`` / ``human_category`` / ``human_instructions`` / ``requested_by_agent_id``
  -- something only a person can do (docs/specs/m22-human-work-and-outsourcing.md, section 1).
* ``outsource_jobs`` -- freelance jobs HR drafts, a person approves, posts and records (section 2).
* ``company_policies.outsource_max_job_micros`` -- the per-job ceiling ($200 by default).

Portable (SQLite and Postgres). The downgrade drops all of it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("requires_human", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(sa.Column("human_category", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("human_instructions", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("requested_by_agent_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_tasks_requested_by_agent_id_agents", "agents", ["requested_by_agent_id"], ["id"]
        )
        batch_op.create_index("ix_tasks_company_requires_human", ["company_id", "requires_human"])

    op.create_table(
        "outsource_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_agent_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("marketplace", sa.String(length=16), nullable=False),
        sa.Column("budget_micros", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("listing_url", sa.String(length=500), nullable=True),
        sa.Column("freelancer_name", sa.String(length=200), nullable=True),
        sa.Column("freelancer_profile_url", sa.String(length=500), nullable=True),
        sa.Column("agreed_price_micros", sa.BigInteger(), nullable=True),
        sa.Column("paid_micros", sa.BigInteger(), nullable=True),
        sa.Column("ledger_entry_id", sa.String(length=36), nullable=True),
        sa.Column("delivered_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["created_by_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.ForeignKeyConstraint(["ledger_entry_id"], ["external_expenses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outsource_jobs_company_id", "outsource_jobs", ["company_id"])
    op.create_index("ix_outsource_jobs_company_status", "outsource_jobs", ["company_id", "status"])
    op.create_index("ix_outsource_jobs_task_id", "outsource_jobs", ["task_id"])

    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "outsource_max_job_micros",
                sa.BigInteger(),
                server_default="200000000",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("outsource_max_job_micros")
    op.drop_index("ix_outsource_jobs_task_id", table_name="outsource_jobs")
    op.drop_index("ix_outsource_jobs_company_status", table_name="outsource_jobs")
    op.drop_index("ix_outsource_jobs_company_id", table_name="outsource_jobs")
    op.drop_table("outsource_jobs")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_company_requires_human")
        batch_op.drop_constraint("fk_tasks_requested_by_agent_id_agents", type_="foreignkey")
        batch_op.drop_column("requested_by_agent_id")
        batch_op.drop_column("human_instructions")
        batch_op.drop_column("human_category")
        batch_op.drop_column("requires_human")
