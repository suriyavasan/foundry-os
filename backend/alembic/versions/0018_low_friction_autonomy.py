"""M18 Part A: low-friction autonomy (approval deadlines, founder questions, retry memory)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-30 12:00:00.000000

* ``approvals.expires_at`` -- every pending approval has a deadline (NULL = none).
* ``company_policies.approval_timeout_seconds`` / ``agent_approval_timeout_seconds`` -- how long a
  human / Co-founder approval waits (defaults 24 h / 30 min; bounded by the policy schema).
* ``founder_questions`` -- questions agents ask the Founder (each with a default and a deadline).
* ``tasks.last_attempt`` / ``tasks.replans`` -- retry memory and the one allowed re-plan.

Pending approvals that already exist get no deadline (they are decided by the people who see them
today); only approvals created from now on carry one.

Portable (SQLite and Postgres). The downgrade drops all of it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "approval_timeout_seconds",
                sa.Integer(),
                nullable=False,
                server_default="86400",
            )
        )
        batch_op.add_column(
            sa.Column(
                "agent_approval_timeout_seconds",
                sa.Integer(),
                nullable=False,
                server_default="1800",
            )
        )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_attempt", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("replans", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "founder_questions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.String(length=1000), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("default_answer", sa.String(length=500), nullable=False),
        sa.Column("blocking", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("answer", sa.String(length=500), nullable=True),
        sa.Column("answered_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("answered_via", sa.String(length=16), nullable=True),
        sa.Column("deadline", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["answered_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_founder_questions_company_id", "founder_questions", ["company_id"])
    op.create_index(
        "ix_founder_questions_company_status", "founder_questions", ["company_id", "status"]
    )
    op.create_index(
        "ix_founder_questions_status_deadline", "founder_questions", ["status", "deadline"]
    )
    op.create_index("ix_founder_questions_task_id", "founder_questions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_founder_questions_task_id", table_name="founder_questions")
    op.drop_index("ix_founder_questions_status_deadline", table_name="founder_questions")
    op.drop_index("ix_founder_questions_company_status", table_name="founder_questions")
    op.drop_index("ix_founder_questions_company_id", table_name="founder_questions")
    op.drop_table("founder_questions")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("replans")
        batch_op.drop_column("last_attempt")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("agent_approval_timeout_seconds")
        batch_op.drop_column("approval_timeout_seconds")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_column("expires_at")
