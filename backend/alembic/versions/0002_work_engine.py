"""work engine: policies, goals, projects, tasks, runs, artifacts, ledger, approvals,
schedules, jobs, company events

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 23:24:30.991350
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_company_events_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_events")),
    )
    with op.batch_alter_table("company_events", schema=None) as batch_op:
        batch_op.create_index("ix_company_events_company_id_id", ["company_id", "id"], unique=False)

    op.create_table(
        "company_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("autonomy_mode", sa.String(length=16), nullable=False),
        sa.Column("human_approval_required_for", sa.JSON(), nullable=False),
        sa.Column("daily_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("monthly_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False),
        sa.Column("max_delegation_depth", sa.Integer(), nullable=False),
        sa.Column("max_tasks_per_planning_cycle", sa.Integer(), nullable=False),
        sa.Column("max_attempts_per_task", sa.Integer(), nullable=False),
        sa.Column("max_revisions", sa.Integer(), nullable=False),
        sa.Column("max_run_seconds", sa.Integer(), nullable=False),
        sa.Column("max_headcount", sa.Integer(), nullable=False),
        sa.Column("heartbeat_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("default_run_reservation_micros", sa.BigInteger(), nullable=False),
        sa.Column("allowed_model_providers", sa.JSON(), nullable=False),
        sa.Column("budget_lock_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_company_policies_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_policies")),
        sa.UniqueConstraint("company_id", name=op.f("uq_company_policies_company_id")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("run_after", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("leased_by", sa.String(length=80), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_jobs_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_jobs_status_run_after", ["status", "run_after"], unique=False)

    op.create_table(
        "goals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("owner_agent_id", sa.String(length=36), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_goals_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["owner_agent_id"], ["agents.id"], name=op.f("fk_goals_owner_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goals")),
    )
    with op.batch_alter_table("goals", schema=None) as batch_op:
        batch_op.create_index("ix_goals_company_id", ["company_id"], unique=False)

    op.create_table(
        "schedules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("cron", sa.String(length=120), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("task_template", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_schedules_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_schedules_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
    )
    with op.batch_alter_table("schedules", schema=None) as batch_op:
        batch_op.create_index("ix_schedules_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_schedules_enabled_next_run_at", ["enabled", "next_run_at"], unique=False
        )

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lead_agent_id", sa.String(length=36), nullable=True),
        sa.Column("budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_projects_company_id_companies")
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name=op.f("fk_projects_goal_id_goals")),
        sa.ForeignKeyConstraint(
            ["lead_agent_id"], ["agents.id"], name=op.f("fk_projects_lead_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.create_index("ix_projects_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_projects_goal_id", ["goal_id"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("assignee_agent_id", sa.String(length=36), nullable=True),
        sa.Column("reviewer_agent_id", sa.String(length=36), nullable=True),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False),
        sa.Column("delegation_depth", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("input_refs", sa.JSON(), nullable=False),
        sa.Column("output_refs", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_run_id", sa.String(length=36), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignee_agent_id"], ["agents.id"], name=op.f("fk_tasks_assignee_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_tasks_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["parent_task_id"], ["tasks.id"], name=op.f("fk_tasks_parent_task_id_tasks")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_tasks_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_agent_id"], ["agents.id"], name=op.f("fk_tasks_reviewer_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint(
            "company_id", "idempotency_key", name="uq_tasks_company_idempotency_key"
        ),
    )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.create_index("ix_tasks_assignee_agent_id", ["assignee_agent_id"], unique=False)
        batch_op.create_index("ix_tasks_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_tasks_company_status", ["company_id", "status"], unique=False)
        batch_op.create_index("ix_tasks_parent_task_id", ["parent_task_id"], unique=False)
        batch_op.create_index("ix_tasks_project_id", ["project_id"], unique=False)

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("approver", sa.String(length=16), nullable=False),
        sa.Column("requested_by_agent_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("amount_micros", sa.BigInteger(), nullable=True),
        sa.Column("vendor", sa.String(length=200), nullable=True),
        sa.Column("recurrence", sa.String(length=16), nullable=True),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("decided_by_type", sa.String(length=16), nullable=True),
        sa.Column("decided_by_id", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_approvals_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_agent_id"],
            ["agents.id"],
            name=op.f("fk_approvals_requested_by_agent_id_agents"),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_approvals_task_id_tasks")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.create_index("ix_approvals_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_approvals_company_status", ["company_id", "status"], unique=False)

    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("settled_micros", sa.BigInteger(), nullable=True),
        sa.Column("period_day", sa.Date(), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_budget_reservations_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_budget_reservations_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_budget_reservations_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_budget_reservations_task_id_tasks")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_reservations")),
    )
    with op.batch_alter_table("budget_reservations", schema=None) as batch_op:
        batch_op.create_index("ix_budget_reservations_agent_id", ["agent_id"], unique=False)
        batch_op.create_index(
            "ix_budget_reservations_company_day", ["company_id", "period_day"], unique=False
        )
        batch_op.create_index(
            "ix_budget_reservations_company_month", ["company_id", "period_month"], unique=False
        )
        batch_op.create_index("ix_budget_reservations_project_id", ["project_id"], unique=False)
        batch_op.create_index("ix_budget_reservations_task_id", ["task_id"], unique=False)

    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("made_by_type", sa.String(length=16), nullable=False),
        sa.Column("made_by_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("related_task_id", sa.String(length=36), nullable=True),
        sa.Column("related_project_id", sa.String(length=36), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_decisions_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["related_project_id"],
            ["projects.id"],
            name=op.f("fk_decisions_related_project_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["related_task_id"], ["tasks.id"], name=op.f("fk_decisions_related_task_id_tasks")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decisions")),
    )
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.create_index("ix_decisions_company_id", ["company_id"], unique=False)

    op.create_table(
        "schedule_fires",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_schedule_fires_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["schedules.id"], name=op.f("fk_schedule_fires_schedule_id_schedules")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_schedule_fires_task_id_tasks")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_fires")),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_fires_schedule_id"),
    )
    with op.batch_alter_table("schedule_fires", schema=None) as batch_op:
        batch_op.create_index("ix_schedule_fires_company_id", ["company_id"], unique=False)

    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("depends_on_task_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_task_dependencies_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"],
            ["tasks.id"],
            name=op.f("fk_task_dependencies_depends_on_task_id_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_task_dependencies_task_id_tasks")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_dependencies")),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_task_id"),
    )
    with op.batch_alter_table("task_dependencies", schema=None) as batch_op:
        batch_op.create_index("ix_task_dependencies_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_task_dependencies_depends_on_task_id", ["depends_on_task_id"], unique=False
        )

    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("runtime", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("cost_kind", sa.String(length=16), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_usage_records_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_usage_records_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_usage_records_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_usage_records_task_id_tasks")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
        sa.UniqueConstraint("company_id", "dedupe_key", name="uq_usage_records_company_id"),
    )
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.create_index("ix_usage_records_agent_id", ["agent_id"], unique=False)
        batch_op.create_index(
            "ix_usage_records_company_occurred", ["company_id", "occurred_at"], unique=False
        )

    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("runtime_execution_id", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=80), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("cancel_reason", sa.String(length=40), nullable=True),
        sa.Column("activity_summary", sa.String(length=300), nullable=True),
        sa.Column("activity_at", sa.DateTime(), nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("cost_kind", sa.String(length=16), nullable=False),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_task_runs_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_task_runs_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["budget_reservations.id"],
            name=op.f("fk_task_runs_reservation_id_budget_reservations"),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_task_runs_task_id_tasks")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_runs")),
        sa.UniqueConstraint("idempotency_key", name="uq_task_runs_idempotency_key"),
    )
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.create_index("ix_task_runs_agent_id", ["agent_id"], unique=False)
        batch_op.create_index("ix_task_runs_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_task_runs_company_status", ["company_id", "status"], unique=False)
        batch_op.create_index("ix_task_runs_task_id", ["task_id"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_artifacts_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_artifacts_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["task_runs.id"], name=op.f("fk_artifacts_run_id_task_runs")
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_artifacts_task_id_tasks")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index("ix_artifacts_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_artifacts_project_id", ["project_id"], unique=False)
        batch_op.create_index("ix_artifacts_task_id", ["task_id"], unique=False)

    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_run_events_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["task_runs.id"], name=op.f("fk_run_events_run_id_task_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_events_run_id"),
    )
    with op.batch_alter_table("run_events", schema=None) as batch_op:
        batch_op.create_index("ix_run_events_company_id", ["company_id"], unique=False)

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_artifact_versions_artifact_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_artifact_versions_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact_versions")),
        sa.UniqueConstraint("artifact_id", "version", name="uq_artifact_versions_artifact_id"),
    )
    with op.batch_alter_table("artifact_versions", schema=None) as batch_op:
        batch_op.create_index("ix_artifact_versions_company_id", ["company_id"], unique=False)

    _backfill_company_policies()


def _backfill_company_policies() -> None:
    """Every existing company gets the default CompanyPolicy (new companies get theirs at
    creation time)."""
    bind = op.get_bind()
    companies = sa.table("companies", sa.column("id", sa.String))
    policies = sa.table(
        "company_policies",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("autonomy_mode", sa.String),
        sa.column("human_approval_required_for", sa.JSON),
        sa.column("max_concurrent_runs", sa.Integer),
        sa.column("max_delegation_depth", sa.Integer),
        sa.column("max_tasks_per_planning_cycle", sa.Integer),
        sa.column("max_attempts_per_task", sa.Integer),
        sa.column("max_revisions", sa.Integer),
        sa.column("max_run_seconds", sa.Integer),
        sa.column("max_headcount", sa.Integer),
        sa.column("heartbeat_interval_seconds", sa.Integer),
        sa.column("default_run_reservation_micros", sa.BigInteger),
        sa.column("allowed_model_providers", sa.JSON),
        sa.column("budget_lock_version", sa.BigInteger),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    for (company_id,) in bind.execute(sa.select(companies.c.id)).fetchall():
        bind.execute(
            policies.insert().values(
                id=str(uuid.uuid4()),
                company_id=company_id,
                autonomy_mode="autonomous",
                human_approval_required_for=["external_purchase", "budget_increase"],
                max_concurrent_runs=2,
                max_delegation_depth=3,
                max_tasks_per_planning_cycle=10,
                max_attempts_per_task=3,
                max_revisions=2,
                max_run_seconds=900,
                max_headcount=12,
                heartbeat_interval_seconds=900,
                default_run_reservation_micros=50_000,
                allowed_model_providers=[],
                budget_lock_version=0,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("artifact_versions", schema=None) as batch_op:
        batch_op.drop_index("ix_artifact_versions_company_id")

    op.drop_table("artifact_versions")
    with op.batch_alter_table("run_events", schema=None) as batch_op:
        batch_op.drop_index("ix_run_events_company_id")

    op.drop_table("run_events")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index("ix_artifacts_task_id")
        batch_op.drop_index("ix_artifacts_project_id")
        batch_op.drop_index("ix_artifacts_company_id")

    op.drop_table("artifacts")
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_task_runs_task_id")
        batch_op.drop_index("ix_task_runs_company_status")
        batch_op.drop_index("ix_task_runs_company_id")
        batch_op.drop_index("ix_task_runs_agent_id")

    op.drop_table("task_runs")
    with op.batch_alter_table("usage_records", schema=None) as batch_op:
        batch_op.drop_index("ix_usage_records_company_occurred")
        batch_op.drop_index("ix_usage_records_agent_id")

    op.drop_table("usage_records")
    with op.batch_alter_table("task_dependencies", schema=None) as batch_op:
        batch_op.drop_index("ix_task_dependencies_depends_on_task_id")
        batch_op.drop_index("ix_task_dependencies_company_id")

    op.drop_table("task_dependencies")
    with op.batch_alter_table("schedule_fires", schema=None) as batch_op:
        batch_op.drop_index("ix_schedule_fires_company_id")

    op.drop_table("schedule_fires")
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.drop_index("ix_decisions_company_id")

    op.drop_table("decisions")
    with op.batch_alter_table("budget_reservations", schema=None) as batch_op:
        batch_op.drop_index("ix_budget_reservations_task_id")
        batch_op.drop_index("ix_budget_reservations_project_id")
        batch_op.drop_index("ix_budget_reservations_company_month")
        batch_op.drop_index("ix_budget_reservations_company_day")
        batch_op.drop_index("ix_budget_reservations_agent_id")

    op.drop_table("budget_reservations")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_index("ix_approvals_company_status")
        batch_op.drop_index("ix_approvals_company_id")

    op.drop_table("approvals")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_project_id")
        batch_op.drop_index("ix_tasks_parent_task_id")
        batch_op.drop_index("ix_tasks_company_status")
        batch_op.drop_index("ix_tasks_company_id")
        batch_op.drop_index("ix_tasks_assignee_agent_id")

    op.drop_table("tasks")
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index("ix_projects_goal_id")
        batch_op.drop_index("ix_projects_company_id")

    op.drop_table("projects")
    with op.batch_alter_table("schedules", schema=None) as batch_op:
        batch_op.drop_index("ix_schedules_enabled_next_run_at")
        batch_op.drop_index("ix_schedules_company_id")

    op.drop_table("schedules")
    with op.batch_alter_table("goals", schema=None) as batch_op:
        batch_op.drop_index("ix_goals_company_id")

    op.drop_table("goals")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_status_run_after")
        batch_op.drop_index("ix_jobs_company_id")

    op.drop_table("jobs")
    op.drop_table("company_policies")
    with op.batch_alter_table("company_events", schema=None) as batch_op:
        batch_op.drop_index("ix_company_events_company_id_id")

    op.drop_table("company_events")
