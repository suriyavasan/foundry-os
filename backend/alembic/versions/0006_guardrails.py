"""M6 guardrails & governance: rules, events, rate limits, system state, finance, policy fields

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24 22:00:00.000000

No data backfill: the default guardrail table (tier -> decision) lives in code
(``app/guardrails/rules.py``), so existing companies get the spec defaults the moment this code
runs, and a company's ``guardrail_rules`` rows are only overrides a human added. The new
``company_policies`` columns carry server defaults, so existing policy rows are valid immediately.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guardrail_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=True),
        sa.Column("scope_type", sa.String(length=12), nullable=False),
        sa.Column("scope_value", sa.String(length=36), nullable=True),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_guardrail_rules_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guardrail_rules")),
    )
    with op.batch_alter_table("guardrail_rules", schema=None) as batch_op:
        batch_op.create_index("ix_guardrail_rules_company_id", ["company_id"], unique=False)

    op.create_table(
        "guardrail_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_guardrail_events_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guardrail_events")),
    )
    with op.batch_alter_table("guardrail_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_guardrail_events_company_created", ["company_id", "created_at"], unique=False
        )
        batch_op.create_index(
            "ix_guardrail_events_company_decision", ["company_id", "decision"], unique=False
        )
        batch_op.create_index(
            "ix_guardrail_events_company_agent", ["company_id", "agent_id"], unique=False
        )

    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_rate_limit_hits_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_hits")),
    )
    with op.batch_alter_table("rate_limit_hits", schema=None) as batch_op:
        batch_op.create_index(
            "ix_rate_limit_hits_company_key", ["company_id", "key", "occurred_at"], unique=False
        )
        batch_op.create_index(
            "ix_rate_limit_hits_agent_key",
            ["company_id", "agent_id", "key", "occurred_at"],
            unique=False,
        )

    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_state")),
    )

    op.create_table(
        "external_expenses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("vendor", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("recurrence", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("incurred_on", sa.Date(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_external_expenses_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_external_expenses_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_expenses")),
    )
    with op.batch_alter_table("external_expenses", schema=None) as batch_op:
        batch_op.create_index(
            "ix_external_expenses_company_incurred", ["company_id", "incurred_on"], unique=False
        )

    op.create_table(
        "budget_alert_marks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False),
        sa.Column("period_key", sa.String(length=12), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_budget_alert_marks_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_alert_marks")),
        sa.UniqueConstraint(
            "company_id",
            "scope",
            "scope_id",
            "period_key",
            "threshold",
            name="uq_budget_alert_marks_scope",
        ),
    )

    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "budget_alert_thresholds",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[50, 80, 100]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "model_routing", sa.String(length=16), nullable=False, server_default="free_first"
            )
        )
        batch_op.add_column(
            sa.Column("allow_paid_fallback", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column(
                "auto_spend_limit_micros", sa.BigInteger(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column("stall_seconds", sa.Integer(), nullable=False, server_default="180")
        )
        batch_op.add_column(
            sa.Column("max_tool_calls_per_run", sa.Integer(), nullable=False, server_default="300")
        )
        batch_op.add_column(
            sa.Column("max_artifacts_per_run", sa.Integer(), nullable=False, server_default="50")
        )
        batch_op.add_column(
            sa.Column(
                "max_tokens_per_run", sa.BigInteger(), nullable=False, server_default="3000000"
            )
        )
        batch_op.add_column(
            sa.Column(
                "ai_disclosure_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )
        batch_op.add_column(sa.Column("ai_disclosure_text", sa.String(length=500), nullable=True))

    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tier", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("action", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("reviewer_agent_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("outbound", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("dlp_findings", sa.JSON(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_approvals_reviewer_agent_id_agents"),
            "agents",
            ["reviewer_agent_id"],
            ["id"],
        )

    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("routing_reason", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("model_provider_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("model_used", sa.String(length=160), nullable=True))

    with op.batch_alter_table("credentials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("allowed_actions", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(
            sa.Column("allowed_families", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("credentials", schema=None) as batch_op:
        batch_op.drop_column("allowed_families")
        batch_op.drop_column("allowed_actions")
        batch_op.drop_column("provider")
    with op.batch_alter_table("task_runs", schema=None) as batch_op:
        batch_op.drop_column("model_used")
        batch_op.drop_column("model_provider_id")
        batch_op.drop_column("routing_reason")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("fk_approvals_reviewer_agent_id_agents"), type_="foreignkey")
        batch_op.drop_column("dlp_findings")
        batch_op.drop_column("outbound")
        batch_op.drop_column("reviewer_agent_id")
        batch_op.drop_column("action")
        batch_op.drop_column("tier")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("ai_disclosure_text")
        batch_op.drop_column("ai_disclosure_enabled")
        batch_op.drop_column("max_tokens_per_run")
        batch_op.drop_column("max_artifacts_per_run")
        batch_op.drop_column("max_tool_calls_per_run")
        batch_op.drop_column("stall_seconds")
        batch_op.drop_column("auto_spend_limit_micros")
        batch_op.drop_column("allow_paid_fallback")
        batch_op.drop_column("model_routing")
        batch_op.drop_column("budget_alert_thresholds")
    op.drop_table("budget_alert_marks")
    with op.batch_alter_table("external_expenses", schema=None) as batch_op:
        batch_op.drop_index("ix_external_expenses_company_incurred")
    op.drop_table("external_expenses")
    op.drop_table("system_state")
    with op.batch_alter_table("rate_limit_hits", schema=None) as batch_op:
        batch_op.drop_index("ix_rate_limit_hits_agent_key")
        batch_op.drop_index("ix_rate_limit_hits_company_key")
    op.drop_table("rate_limit_hits")
    with op.batch_alter_table("guardrail_events", schema=None) as batch_op:
        batch_op.drop_index("ix_guardrail_events_company_agent")
        batch_op.drop_index("ix_guardrail_events_company_decision")
        batch_op.drop_index("ix_guardrail_events_company_created")
    op.drop_table("guardrail_events")
    with op.batch_alter_table("guardrail_rules", schema=None) as batch_op:
        batch_op.drop_index("ix_guardrail_rules_company_id")
    op.drop_table("guardrail_rules")
