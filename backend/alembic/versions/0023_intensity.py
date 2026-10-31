"""M23 (B5): company intensity (company_policies.intensity, intensity_custom)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-31 12:00:00.000000

* ``company_policies.intensity`` (1-5, default 3 = Balanced) and ``intensity_custom`` (default false).
* Existing companies stay at level 3. The policy's ``heartbeat_interval_seconds`` is first aligned
  with the interval of the company's built-in heartbeat schedule (the value that is really used:
  the model default was 900 while every company was created with a 1800 s schedule), then a company
  whose pace fields differ from the level-3 preset is marked ``intensity_custom`` (shown as "Custom").

Portable (SQLite and Postgres). The downgrade drops both columns (the heartbeat alignment stays).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The level-3 preset (kept in step with app/services/intensity.py PRESETS[3]; frozen here on purpose).
_LEVEL_3 = {
    "heartbeat_interval_seconds": 1800,
    "max_concurrent_runs": 2,
    "max_planning_runs_per_hour": 6,
    "initiative_enabled": True,
    "initiative_interval_seconds": 21_600,
    "meetings_enabled": True,
    "max_meetings_per_company_per_day": 12,
    "self_check_enabled": True,
}


def upgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("intensity", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.add_column(
            sa.Column("intensity_custom", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    bind = op.get_bind()
    schedules = sa.table(
        "schedules",
        sa.column("company_id", sa.String),
        sa.column("name", sa.String),
        sa.column("kind", sa.String),
        sa.column("interval_seconds", sa.Integer),
    )
    policies = sa.table(
        "company_policies",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("intensity_custom", sa.Boolean),
        *[sa.column(name) for name in _LEVEL_3],
    )
    beats = {
        row.company_id: row.interval_seconds
        for row in bind.execute(
            sa.select(schedules.c.company_id, schedules.c.interval_seconds).where(
                schedules.c.name == "heartbeat",
                schedules.c.kind == "heartbeat",
                schedules.c.interval_seconds.is_not(None),
            )
        )
    }
    for row in bind.execute(sa.select(policies)).mappings().all():
        values: dict[str, object] = {}
        heartbeat = beats.get(row["company_id"], row["heartbeat_interval_seconds"])
        if heartbeat != row["heartbeat_interval_seconds"]:
            values["heartbeat_interval_seconds"] = heartbeat
        current = {**{k: row[k] for k in _LEVEL_3}, "heartbeat_interval_seconds": heartbeat}
        if any(current[k] != v for k, v in _LEVEL_3.items()):
            values["intensity_custom"] = True
        if values:
            bind.execute(sa.update(policies).where(policies.c.id == row["id"]).values(**values))


def downgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("intensity_custom")
        batch_op.drop_column("intensity")
