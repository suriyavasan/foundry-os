"""B9: ``company_policies.ai_engine`` (api | claude_plan)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28 12:00:00.000000

* ``company_policies.ai_engine`` -- ``String(16)``, not null, server default ``'api'``. ``claude_plan``
  means every agent runs Claude Code signed in with the founder's own Claude account. Existing
  companies keep ``api``. Human-only: it is set through ``PUT /ai-setup``, never ``PATCH /policy``.

Portable (SQLite and Postgres). The downgrade drops the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ai_engine", sa.String(length=16), nullable=False, server_default="api")
        )


def downgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("ai_engine")
