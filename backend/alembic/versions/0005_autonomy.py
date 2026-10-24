"""M5 autonomy: Founder's notes, memory, alerts, wake queue, autonomy limits, built-in schedules

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24 18:00:00.000000

Backfills (portable SQL, idempotent):
* ``company_policies``: the new limits get their defaults; a NULL ``hire_default_monthly_budget_micros``
  becomes $5 (found live in M3b: HR handed a hire $5,000/month because nothing capped it);
* every existing company gets the built-in schedules (AI Co-founder heartbeat + routines, HR
  skills review) and one ``project-health-check`` per open project that already has a lead.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from croniter import croniter

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEARTBEAT_SECONDS = 1800
PROJECT_CHECK_SECONDS = 6 * 3600
STATE_HINT = "Call company_get_state first. "

# Frozen copy of the built-ins (app/services/builtin_schedules.py) as of this migration.
COFOUNDER = [
    (
        "heartbeat",
        "heartbeat",
        HEARTBEAT_SECONDS,
        None,
        {
            "title": "Heartbeat: review the company",
            "description": STATE_HINT
            + "Handle what needs you: unprocessed Founder's notes, failed or blocked work, pending "
            "approvals, alerts, projects without a lead. If nothing needs action, complete this task "
            "and say so.",
            "kind": "planning",
            "skip_when_idle": True,
        },
    ),
    (
        "daily-standup",
        "routine",
        None,
        "0 9 * * *",
        {
            "title": "Daily standup",
            "description": STATE_HINT
            + "Write a short standup (progress, blockers, next steps) with company_submit_artifact "
            "(title 'Standup', kind report), then complete this task.",
            "skip_when_idle": True,
        },
    ),
    (
        "weekly-strategy-review",
        "routine",
        None,
        "30 9 * * 1",
        {
            "title": "Weekly strategy review",
            "description": STATE_HINT
            + "Compare goals with progress. Adjust goals and projects, fix ownership, plan hires, "
            "record the important decisions with company_record_decision, then complete this task.",
            "skip_when_idle": True,
        },
    ),
    (
        "daily-budget-review",
        "routine",
        None,
        "0 18 * * *",
        {
            "title": "Daily budget review",
            "description": STATE_HINT
            + "Check spend against the budgets in the state. You cannot raise budgets; if spend is a "
            "problem, record a decision and adjust the plan. Then complete this task.",
            "skip_when_idle": True,
        },
    ),
]
HR = (
    "skills-review",
    "routine",
    None,
    "0 10 1 * *",
    {
        "title": "Monthly skills review",
        "description": "Review the skill library and the team: skills nobody uses, quarantined "
        "skills, recurring gaps. Install or author what is missing, then complete this task with a "
        "short summary.",
        "skip_when_idle": True,
    },
)


def _next_run(interval: int | None, cron: str | None, tz: str, now: datetime) -> datetime:
    if interval:
        return now + timedelta(seconds=interval)
    local = now.astimezone(ZoneInfo(tz))
    return croniter(cron, local).get_next(datetime).astimezone(UTC)


def upgrade() -> None:
    op.create_table(
        "founder_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("author_user_id", sa.String(length=36), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("triage", sa.JSON(), nullable=True),
        sa.Column("triaged_by_run_id", sa.String(length=36), nullable=True),
        sa.Column("triaged_at", sa.DateTime(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["users.id"], name=op.f("fk_founder_notes_author_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_founder_notes_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_founder_notes")),
    )
    with op.batch_alter_table("founder_notes", schema=None) as batch_op:
        batch_op.create_index("ix_founder_notes_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_founder_notes_company_status", ["company_id", "status"], unique=False
        )

    op.create_table(
        "note_replies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("author_type", sa.String(length=16), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_note_replies_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["note_id"], ["founder_notes.id"], name=op.f("fk_note_replies_note_id_founder_notes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note_replies")),
    )
    with op.batch_alter_table("note_replies", schema=None) as batch_op:
        batch_op.create_index("ix_note_replies_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_note_replies_note_id", ["note_id"], unique=False)

    op.create_table(
        "memory_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("applies_to", sa.JSON(), nullable=False),
        sa.Column("proposed_by_type", sa.String(length=16), nullable=True),
        sa.Column("proposed_by_id", sa.String(length=36), nullable=True),
        sa.Column("verified_by_type", sa.String(length=16), nullable=True),
        sa.Column("verified_by_id", sa.String(length=36), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("superseded_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_memory_entries_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_entries")),
    )
    with op.batch_alter_table("memory_entries", schema=None) as batch_op:
        batch_op.create_index("ix_memory_entries_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_memory_entries_company_status", ["company_id", "status"], unique=False
        )

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("related_type", sa.String(length=24), nullable=True),
        sa.Column("related_id", sa.String(length=36), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by_type", sa.String(length=16), nullable=True),
        sa.Column("resolved_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_alerts_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index("ix_alerts_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_alerts_company_status", ["company_id", "status"], unique=False)

    op.create_table(
        "wake_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("not_before", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_wake_requests_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_wake_requests_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wake_requests")),
    )
    with op.batch_alter_table("wake_requests", schema=None) as batch_op:
        batch_op.create_index("ix_wake_requests_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_wake_requests_not_before", ["not_before"], unique=False)

    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_agent_monthly_budget_micros",
                sa.BigInteger(),
                nullable=False,
                server_default="25000000",
            )
        )
        batch_op.add_column(
            sa.Column(
                "max_planning_runs_per_hour", sa.Integer(), nullable=False, server_default="6"
            )
        )
        batch_op.add_column(
            sa.Column("max_open_tasks", sa.Integer(), nullable=False, server_default="200")
        )
        batch_op.add_column(
            sa.Column("circuit_breaker_failures", sa.Integer(), nullable=False, server_default="8")
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE company_policies SET hire_default_monthly_budget_micros = 5000000 "
            "WHERE hire_default_monthly_budget_micros IS NULL"
        )
    )
    _backfill_builtin_schedules()


def _backfill_builtin_schedules() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).replace(microsecond=0)
    naive = now.replace(tzinfo=None)

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

    def existing(company_id: str, agent_id: str, name: str) -> bool:
        return (
            bind.execute(
                sa.text(
                    "SELECT 1 FROM schedules WHERE company_id = :c AND agent_id = :a AND name = :n"
                ),
                {"c": company_id, "a": agent_id, "n": name},
            ).first()
            is not None
        )

    def add(company_id, tz, agent_id, agent_name, name, kind, interval, cron, template):
        if existing(company_id, agent_id, name):
            return
        schedule_id = str(uuid.uuid4())
        bind.execute(
            schedules.insert().values(
                id=schedule_id,
                company_id=company_id,
                name=name,
                kind=kind,
                agent_id=agent_id,
                interval_seconds=interval,
                cron=cron,
                timezone=tz,
                task_template=template,
                enabled=True,
                next_run_at=_next_run(interval, cron, tz, now).replace(tzinfo=None),
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
                summary=f"Created built-in schedule '{name}' for {agent_name} (Milestone 5 backfill)",
                data={"agent_id": agent_id, "built_in": True, "enabled": True},
                created_at=naive,
            )
        )

    companies = bind.execute(sa.text("SELECT id, timezone FROM companies")).all()
    for company_id, tz in companies:
        tz = tz or "UTC"
        founder = bind.execute(
            sa.text(
                "SELECT id, name FROM agents WHERE company_id = :c AND kind = 'founder' "
                "AND status IN ('active', 'paused') ORDER BY created_at LIMIT 1"
            ),
            {"c": company_id},
        ).first()
        hr = bind.execute(
            sa.text(
                "SELECT id, name FROM agents WHERE company_id = :c AND role_family = 'hr' "
                "AND status IN ('active', 'paused') ORDER BY created_at LIMIT 1"
            ),
            {"c": company_id},
        ).first()
        if founder is not None:
            for name, kind, interval, cron, template in COFOUNDER:
                add(company_id, tz, founder[0], founder[1], name, kind, interval, cron, template)
        if hr is not None:
            name, kind, interval, cron, template = HR
            add(company_id, tz, hr[0], hr[1], name, kind, interval, cron, template)
        projects = bind.execute(
            sa.text(
                "SELECT p.id, p.name, p.lead_agent_id, a.name FROM projects p "
                "JOIN agents a ON a.id = p.lead_agent_id "
                "WHERE p.company_id = :c AND p.status IN ('planned', 'active') "
                "AND a.status IN ('active', 'paused', 'proposed')"
            ),
            {"c": company_id},
        ).all()
        for project_id, project_name, lead_id, lead_name in projects:
            add(
                company_id,
                tz,
                lead_id,
                lead_name,
                f"project-health-check: {project_name}"[:200],
                "routine",
                PROJECT_CHECK_SECONDS,
                None,
                {
                    "title": f"Project health check: {project_name}"[:300],
                    "description": STATE_HINT
                    + f"Check project '{project_name}' [id {project_id}]: stuck or failed tasks, "
                    "missing owners, finished work to review. Fix what you can; escalate with "
                    "company_escalate what you cannot. If all its work is finished, mark the "
                    "project completed with company_manage_project. Then complete this task.",
                    "project_id": project_id,
                    "skip_when_idle": True,
                },
            )


def downgrade() -> None:
    bind = op.get_bind()
    # Built-in schedules are ordinary rows; remove the ones this migration created by name.
    bind.execute(
        sa.text(
            "DELETE FROM schedule_fires WHERE schedule_id IN (SELECT id FROM schedules WHERE "
            "name IN ('heartbeat', 'daily-standup', 'weekly-strategy-review', 'daily-budget-review', "
            "'skills-review', 'failed-run-triage') OR name LIKE 'project-health-check:%')"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM schedules WHERE name IN ('heartbeat', 'daily-standup', "
            "'weekly-strategy-review', 'daily-budget-review', 'skills-review', "
            "'failed-run-triage') OR name LIKE 'project-health-check:%'"
        )
    )
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("circuit_breaker_failures")
        batch_op.drop_column("max_open_tasks")
        batch_op.drop_column("max_planning_runs_per_hour")
        batch_op.drop_column("max_agent_monthly_budget_micros")
    op.drop_table("wake_requests")
    op.drop_table("alerts")
    op.drop_table("memory_entries")
    op.drop_table("note_replies")
    op.drop_table("founder_notes")
