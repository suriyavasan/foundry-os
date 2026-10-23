"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-23 21:42:51.568102
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("mission", sa.Text(), nullable=True),
        sa.Column("vision", sa.Text(), nullable=True),
        sa.Column("strategic_goal", sa.Text(), nullable=True),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("slug", name=op.f("uq_companies_slug")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("actor_label", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_audit_events_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_audit_events_company_created", ["company_id", "created_at", "id"], unique=False
        )
        batch_op.create_index(
            "ix_audit_events_company_target",
            ["company_id", "target_type", "target_id"],
            unique=False,
        )

    op.create_table(
        "company_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_company_members_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_company_members_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_members")),
        sa.UniqueConstraint("company_id", "user_id", name="uq_company_members_company_user"),
    )
    with op.batch_alter_table("company_members", schema=None) as batch_op:
        batch_op.create_index("ix_company_members_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_company_members_user_id", ["user_id"], unique=False)

    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("lead_agent_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_departments_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("company_id", "name", name="uq_departments_company_name"),
    )
    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.create_index("ix_departments_company_id", ["company_id"], unique=False)

    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role_title", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=True),
        sa.Column("manager_agent_id", sa.String(length=36), nullable=True),
        sa.Column("reports_to_human", sa.Boolean(), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("model_provider", sa.String(length=80), nullable=True),
        sa.Column("model_name", sa.String(length=160), nullable=True),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("runtime_config", sa.JSON(), nullable=False),
        sa.Column("tool_permissions", sa.JSON(), nullable=False),
        sa.Column("monthly_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("daily_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_agents_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name=op.f("fk_agents_department_id_departments")
        ),
        sa.ForeignKeyConstraint(
            ["manager_agent_id"], ["agents.id"], name=op.f("fk_agents_manager_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
    )
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.create_index("ix_agents_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_agents_company_status", ["company_id", "status"], unique=False)
        batch_op.create_index("ix_agents_department_id", ["department_id"], unique=False)
        batch_op.create_index("ix_agents_manager_agent_id", ["manager_agent_id"], unique=False)
        batch_op.create_index(
            "uq_agents_one_live_founder",
            ["company_id"],
            unique=True,
            sqlite_where=sa.text(
                "kind = 'founder' AND status IN ('proposed','active','paused','retiring')"
            ),
            postgresql_where=sa.text(
                "kind = 'founder' AND status IN ('proposed','active','paused','retiring')"
            ),
        )

    # departments.lead_agent_id <-> agents.department_id is a cycle: add the FK last.
    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_departments_lead_agent_id_agents", "agents", ["lead_agent_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.drop_constraint("fk_departments_lead_agent_id_agents", type_="foreignkey")

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_agents_one_live_founder",
            sqlite_where=sa.text(
                "kind = 'founder' AND status IN ('proposed','active','paused','retiring')"
            ),
            postgresql_where=sa.text(
                "kind = 'founder' AND status IN ('proposed','active','paused','retiring')"
            ),
        )
        batch_op.drop_index("ix_agents_manager_agent_id")
        batch_op.drop_index("ix_agents_department_id")
        batch_op.drop_index("ix_agents_company_status")
        batch_op.drop_index("ix_agents_company_id")

    op.drop_table("agents")
    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.drop_index("ix_departments_company_id")

    op.drop_table("departments")
    with op.batch_alter_table("company_members", schema=None) as batch_op:
        batch_op.drop_index("ix_company_members_user_id")
        batch_op.drop_index("ix_company_members_company_id")

    op.drop_table("company_members")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_events_company_target")
        batch_op.drop_index("ix_audit_events_company_created")

    op.drop_table("audit_events")
    op.drop_table("users")
    op.drop_table("companies")
