"""M10 AI setup, coding-CLI workforce, reflections, standards, Command Center

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26 09:00:00.000000

* ``company_policies``: ``work_mode`` (api | hybrid) and ``work_runtime``.
* ``reflections``, ``reflection_responses``, ``company_standards``: the AI Co-founder's reflections,
  the Founder's reactions and the standards a human adopts from them.
* ``command_exchanges``: one row per Command Center input. Every existing Founder's note gets a row
  (backfill) so the feed is a single table; triaged and archived notes are ``answered``, new ones ``pending``.
* Backfill (portable SQL, idempotent): every existing AI Co-founder gets the ``daily-reflection``
  routine.

The readiness gate itself needs no schema: a company is "AI ready" when its default model provider's
latest test passed every check, and providers tested before this migration carry no ``checks`` - so an
existing company without a re-tested provider becomes not-ready until the owner completes AI setup.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from croniter import croniter

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATE_HINT = "Call company_get_state first. "
# Frozen copy of the built-in (app/services/builtin_schedules.py) as of this migration.
DAILY_REFLECTION = (
    "daily-reflection",
    "routine",
    None,
    "0 20 * * *",
    {
        "title": "Daily reflection",
        "description": STATE_HINT
        + "Look back over the day: finished and failed work, revisions, blockers, spend. If you "
        "noticed something the Founder should know, post ONE reflection with company_post_reflection "
        "(kind observation, retro, risk or praise; kind standard_proposal with proposed_standard when "
        "a rule should bind the whole company; evidence = real task/run/project ids from the state). "
        "If nothing is noteworthy, post nothing. Answer Founder reactions listed in the state with "
        "company_reply_reflection or company_revise_reflection. Then complete this task.",
        "skip_when_idle": True,
    },
)


def _next_run(cron: str, tz: str, now: datetime) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    return croniter(cron, local).get_next(datetime).astimezone(UTC)


def _dt(value):
    """Raw ``text()`` queries return DateTime columns as strings on SQLite and as datetimes elsewhere."""
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace(" ", "T"))
    return value


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("work_mode", sa.String(length=16), nullable=False, server_default="api")
        )
        batch_op.add_column(sa.Column("work_runtime", sa.String(length=32), nullable=True))

    op.create_table(
        "reflections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("author_agent_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("proposed_standard", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("standard_id", sa.String(length=36), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_reflections_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["author_agent_id"], ["agents.id"], name=op.f("fk_reflections_author_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reflections")),
    )
    op.create_index("ix_reflections_company_id", "reflections", ["company_id"])
    op.create_index("ix_reflections_company_status", "reflections", ["company_id", "status"])

    op.create_table(
        "reflection_responses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("reflection_id", sa.String(length=36), nullable=False),
        sa.Column("author_type", sa.String(length=16), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("reaction", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_reflection_responses_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["reflection_id"],
            ["reflections.id"],
            name=op.f("fk_reflection_responses_reflection_id_reflections"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reflection_responses")),
    )
    op.create_index("ix_reflection_responses_company_id", "reflection_responses", ["company_id"])
    op.create_index(
        "ix_reflection_responses_reflection_id", "reflection_responses", ["reflection_id"]
    )

    op.create_table(
        "company_standards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.String(length=500), nullable=False),
        sa.Column("source_reflection_id", sa.String(length=36), nullable=True),
        sa.Column("adopted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("adopted_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("applies_to", sa.JSON(), nullable=False),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_company_standards_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_standards")),
    )
    op.create_index("ix_company_standards_company_id", "company_standards", ["company_id"])
    op.create_index(
        "ix_company_standards_company_status", "company_standards", ["company_id", "status"]
    )

    op.create_table(
        "command_exchanges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("input_kind", sa.String(length=16), nullable=False),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("note_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=False),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_command_exchanges_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["founder_notes.id"],
            name=op.f("fk_command_exchanges_note_id_founder_notes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command_exchanges")),
    )
    op.create_index(
        "ix_command_exchanges_company_created", "command_exchanges", ["company_id", "created_at"]
    )
    op.create_index("ix_command_exchanges_note_id", "command_exchanges", ["note_id"])

    _backfill_exchanges()
    _backfill_daily_reflection()


def _backfill_exchanges() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).replace(microsecond=0, tzinfo=None)
    exchanges = sa.table(
        "command_exchanges",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("input_kind", sa.String),
        sa.column("input", sa.Text),
        sa.column("status", sa.String),
        sa.column("output", sa.JSON),
        sa.column("note_id", sa.String),
        sa.column("run_id", sa.String),
        sa.column("actions", sa.JSON),
        sa.column("attempts", sa.Integer),
        sa.column("last_attempt_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    notes = bind.execute(
        sa.text(
            "SELECT id, company_id, author_user_id, status, triage, triaged_by_run_id, created_at, "
            "updated_at FROM founder_notes"
        )
    ).all()
    for note_id, company_id, user_id, status, triage, run_id, created, updated in notes:
        if isinstance(triage, str):
            import json

            triage = json.loads(triage) if triage else None
        bind.execute(
            exchanges.insert().values(
                id=str(uuid.uuid4()),
                company_id=company_id,
                user_id=user_id,
                input_kind="note",
                input=None,
                status="pending" if status == "new" else "answered",
                output=None,
                note_id=note_id,
                run_id=run_id,
                actions=list((triage or {}).get("actions") or []),
                attempts=1,
                last_attempt_at=now if status == "new" else _dt(updated),
                created_at=_dt(created),
                updated_at=_dt(updated),
            )
        )


def _backfill_daily_reflection() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).replace(microsecond=0)
    naive = now.replace(tzinfo=None)
    name, kind, interval, cron, template = (
        DAILY_REFLECTION[0],
        DAILY_REFLECTION[1],
        DAILY_REFLECTION[2],
        DAILY_REFLECTION[3],
        DAILY_REFLECTION[4],
    )
    schedules = sa.table(
        "schedules",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("name", sa.String),
        sa.column("kind", sa.String),
        sa.column("agent_id", sa.String),
        sa.column("interval_seconds", sa.Integer),
        sa.column("cron", sa.String),
        sa.column("timezone", sa.String),
        sa.column("task_template", sa.JSON),
        sa.column("enabled", sa.Boolean),
        sa.column("next_run_at", sa.DateTime),
        sa.column("last_fired_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    audit = sa.table(
        "audit_events",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("actor_type", sa.String),
        sa.column("actor_id", sa.String),
        sa.column("actor_label", sa.String),
        sa.column("action", sa.String),
        sa.column("target_type", sa.String),
        sa.column("target_id", sa.String),
        sa.column("summary", sa.String),
        sa.column("data", sa.JSON),
        sa.column("created_at", sa.DateTime),
    )
    for company_id, tz in bind.execute(sa.text("SELECT id, timezone FROM companies")).all():
        tz = tz or "UTC"
        founder = bind.execute(
            sa.text(
                "SELECT id, name FROM agents WHERE company_id = :c AND kind = 'founder' "
                "AND status IN ('active', 'paused') ORDER BY created_at LIMIT 1"
            ),
            {"c": company_id},
        ).first()
        if founder is None:
            continue
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM schedules WHERE company_id = :c AND agent_id = :a AND name = :n"
            ),
            {"c": company_id, "a": founder[0], "n": name},
        ).first()
        if exists:
            continue
        schedule_id = str(uuid.uuid4())
        bind.execute(
            schedules.insert().values(
                id=schedule_id,
                company_id=company_id,
                name=name,
                kind=kind,
                agent_id=founder[0],
                interval_seconds=interval,
                cron=cron,
                timezone=tz,
                task_template=template,
                enabled=True,
                next_run_at=_next_run(cron, tz, now + timedelta(0)).replace(tzinfo=None),
                last_fired_at=None,
                created_at=naive,
                updated_at=naive,
            )
        )
        bind.execute(
            audit.insert().values(
                id=str(uuid.uuid4()),
                company_id=company_id,
                actor_type="system",
                actor_id=None,
                actor_label="System",
                action="schedule.created",
                target_type="schedule",
                target_id=schedule_id,
                summary=f"Created built-in schedule '{name}' for {founder[1]} (Milestone 10 backfill)",
                data={"agent_id": founder[0], "built_in": True, "enabled": True},
                created_at=naive,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM schedule_fires WHERE schedule_id IN "
            "(SELECT id FROM schedules WHERE name = 'daily-reflection')"
        )
    )
    bind.execute(sa.text("DELETE FROM schedules WHERE name = 'daily-reflection'"))
    op.drop_table("command_exchanges")
    op.drop_table("company_standards")
    op.drop_table("reflection_responses")
    op.drop_table("reflections")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("work_runtime")
        batch_op.drop_column("work_mode")
