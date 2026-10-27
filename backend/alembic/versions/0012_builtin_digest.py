"""M12: existing companies' standup and weekly review get the company digest

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-27 12:00:00.000000

Built-in schedules are created with the company, so companies created before M12 keep templates
without ``"digest": true``. This sets the flag on the Co-founder's ``daily-standup`` and
``weekly-strategy-review`` schedules whose template does not mention ``digest`` at all; a template
the owner already edited to ``"digest": false`` is left alone. Data only, no schema change.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAMES = ("daily-standup", "weekly-strategy-review")

schedules = sa.table(
    "schedules",
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("agent_id", sa.String),
    sa.column("task_template", sa.JSON),
)
agents = sa.table("agents", sa.column("id", sa.String), sa.column("kind", sa.String))


def _founder_schedules(conn: sa.Connection):
    return conn.execute(
        sa.select(schedules.c.id, schedules.c.task_template)
        .join(agents, agents.c.id == schedules.c.agent_id)
        .where(schedules.c.name.in_(_NAMES), agents.c.kind == "founder")
    ).all()


def upgrade() -> None:
    conn = op.get_bind()
    for sid, template in _founder_schedules(conn):
        template = dict(template or {})
        if "digest" in template:
            continue
        template["digest"] = True
        conn.execute(
            sa.update(schedules).where(schedules.c.id == sid).values(task_template=template)
        )


def downgrade() -> None:
    conn = op.get_bind()
    for sid, template in _founder_schedules(conn):
        template = dict(template or {})
        if template.get("digest") is not True:
            continue
        del template["digest"]
        conn.execute(
            sa.update(schedules).where(schedules.c.id == sid).values(task_template=template)
        )
