"""M17: one AI stack -- ``ai_connections``, ``task_runs.ai_connection_id``, ``ai_stack_bootstrapped``

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-28 18:00:00.000000

* ``ai_connections`` -- the company's ordered AI connections (model APIs and coding CLIs).
* ``task_runs.ai_connection_id`` -- which connection a run used (plain column, no FK).
* ``company_policies.ai_stack_bootstrapped`` -- legacy settings were converted once.

Portable (SQLite and Postgres). The downgrade drops all three.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("engine", sa.String(length=8), nullable=False),
        sa.Column("model_provider_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("cli_id", sa.String(length=32), nullable=True),
        sa.Column("auth_mode", sa.String(length=16), nullable=True),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("cli_provider_id", sa.String(length=36), nullable=True),
        sa.Column("use_for", sa.String(length=16), server_default="everything", nullable=False),
        sa.Column("share", sa.Integer(), server_default="2", nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("home_dir", sa.String(length=255), nullable=True),
        sa.Column("last_test", sa.JSON(), nullable=True),
        sa.Column("paused_until", sa.DateTime(), nullable=True),
        sa.Column("pause_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["model_provider_id"], ["model_providers.id"]),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"]),
        sa.ForeignKeyConstraint(["cli_provider_id"], ["model_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_connections_company_id", "ai_connections", ["company_id"])
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ai_connection_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_stack_bootstrapped",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("ai_stack_bootstrapped")
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.drop_column("ai_connection_id")
    op.drop_index("ix_ai_connections_company_id", table_name="ai_connections")
    op.drop_table("ai_connections")
