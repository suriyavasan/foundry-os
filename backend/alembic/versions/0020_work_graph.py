"""M18 Part B: durable work graph -- LangGraph checkpoints (``graph_checkpoints``, ``graph_writes``)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-30 15:00:00.000000

* ``graph_checkpoints`` -- state snapshots per thread and namespace.
* ``graph_writes`` -- pending and intermediate writes for checkpoints.
* ``task_runs.graph_thread_id`` / ``task_runs.proposed_result`` -- a work-graph run and the result its
  agent proposed (the graph's finish node completes the task).
* ``company_policies.self_check_enabled`` / ``work_graph_enabled`` -- both on for every company.

Portable (SQLite and Postgres).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("graph_thread_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("proposed_result", sa.JSON(), nullable=True))
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("self_check_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("work_graph_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    op.create_table(
        "graph_checkpoints",
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=True),
        sa.Column("checkpoint", sa.LargeBinary(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_index(
        "ix_graph_checkpoints_company_id_thread_id",
        "graph_checkpoints",
        ["company_id", "thread_id"],
    )
    op.create_table(
        "graph_writes",
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_ns", sa.String(length=255), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=True),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.String(length=255), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
    )


def downgrade() -> None:
    op.drop_table("graph_writes")
    op.drop_index("ix_graph_checkpoints_company_id_thread_id", table_name="graph_checkpoints")
    op.drop_table("graph_checkpoints")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("work_graph_enabled")
        batch_op.drop_column("self_check_enabled")
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.drop_column("proposed_result")
        batch_op.drop_column("graph_thread_id")
