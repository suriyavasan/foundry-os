"""skills & HR: skill library, role templates, hiring policy, HR agent backfill

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24 10:00:00.000000
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HR_NAME = "Harper"
HR_TEMPLATE = "hr-partner"
FOUNDER_TEMPLATE = "founder-ceo"
PEOPLE_DEPARTMENT = "People"
LIVE_STATUSES = ("proposed", "active", "paused", "retiring")


def upgrade() -> None:
    op.create_table(
        "skill_blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_skill_blobs")),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=True),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("license", sa.String(length=80), nullable=True),
        sa.Column("source_kind", sa.String(length=24), nullable=False),
        sa.Column("source_ref", sa.String(length=400), nullable=False),
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("trust", sa.String(length=16), nullable=False),
        sa.Column("scan_verdict", sa.String(length=16), nullable=False),
        sa.Column("scan_report", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requires_toolsets", sa.JSON(), nullable=False),
        sa.Column("required_env", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["blob_sha256"], ["skill_blobs.sha256"], name=op.f("fk_skills_blob_sha256_skill_blobs")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_skills_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("company_id", "slug", name="uq_skills_company_id"),
    )
    with op.batch_alter_table("skills", schema=None) as batch_op:
        batch_op.create_index("ix_skills_blob_sha256", ["blob_sha256"], unique=False)
        batch_op.create_index("ix_skills_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_skills_company_status", ["company_id", "status"], unique=False)

    op.create_table(
        "agent_skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("assigned_by_type", sa.String(length=16), nullable=False),
        sa.Column("assigned_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_skills_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_agent_skills_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], name=op.f("fk_agent_skills_skill_id_skills")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_skills")),
        sa.UniqueConstraint("agent_id", "skill_id", name="uq_agent_skills_agent_id"),
    )
    with op.batch_alter_table("agent_skills", schema=None) as batch_op:
        batch_op.create_index("ix_agent_skills_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_agent_skills_skill_id", ["skill_id"], unique=False)

    op.create_table(
        "role_templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("family", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("default_kind", sa.String(length=16), nullable=False),
        sa.Column("default_department", sa.String(length=80), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("default_toolsets", sa.JSON(), nullable=False),
        sa.Column("default_skill_slugs", sa.JSON(), nullable=False),
        sa.Column("required_skill_tags", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_role_templates_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_templates")),
        sa.UniqueConstraint("company_id", "slug", name="uq_role_templates_company_id"),
    )
    with op.batch_alter_table("role_templates", schema=None) as batch_op:
        batch_op.create_index("ix_role_templates_company_id", ["company_id"], unique=False)

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("role_template", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "role_family",
                sa.String(length=24),
                server_default="generic",
                nullable=False,
            )
        )
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("auto_activate_hires", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch_op.add_column(
            sa.Column("hire_default_monthly_budget_micros", sa.BigInteger(), nullable=True)
        )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("blocked_reason", sa.String(length=80), nullable=True))
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("subject_type", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("subject_id", sa.String(length=36), nullable=True))

    _backfill_roles_and_hr()


def _hr_template_text() -> tuple[str, str, list[str]]:
    """(responsibilities, instructions, toolsets) of the built-in HR template. The migration
    stays usable if the template module is ever refactored: it falls back to a short text."""
    try:
        from app.roles.loader import get_builtin_template

        template = get_builtin_template(HR_TEMPLATE)
    except Exception:  # pragma: no cover - defensive
        template = None
    if template is None:  # pragma: no cover - defensive
        return (
            "Hires employees and equips them with skills.",
            "You are the HR partner. Fill capability gaps by hiring and by installing or "
            "authoring skills. Never exceed headcount or budget limits.",
            ["company", "file", "web", "skills"],
        )
    return template.description, template.instructions, list(template.default_toolsets)


def _backfill_roles_and_hr() -> None:
    """Existing agents: role_family inferred (founder -> founder, else generic).
    Existing companies: an HR agent "Harper" in a "People" department, plus a background job
    that seeds the skill library (the worker runs it)."""
    bind = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None)

    agents = sa.table(
        "agents",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("name", sa.String),
        sa.column("role_title", sa.String),
        sa.column("kind", sa.String),
        sa.column("status", sa.String),
        sa.column("department_id", sa.String),
        sa.column("manager_agent_id", sa.String),
        sa.column("reports_to_human", sa.Boolean),
        sa.column("responsibilities", sa.Text),
        sa.column("instructions", sa.Text),
        sa.column("model_provider", sa.String),
        sa.column("model_name", sa.String),
        sa.column("runtime", sa.String),
        sa.column("runtime_config", sa.JSON),
        sa.column("tool_permissions", sa.JSON),
        sa.column("monthly_budget_micros", sa.BigInteger),
        sa.column("daily_budget_micros", sa.BigInteger),
        sa.column("status_changed_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
        sa.column("role_template", sa.String),
        sa.column("role_family", sa.String),
    )
    departments = sa.table(
        "departments",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("lead_agent_id", sa.String),
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
        sa.column("summary", sa.Text),
        sa.column("data", sa.JSON),
        sa.column("created_at", sa.DateTime),
    )
    jobs = sa.table(
        "jobs",
        sa.column("id", sa.String),
        sa.column("company_id", sa.String),
        sa.column("kind", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("status", sa.String),
        sa.column("run_after", sa.DateTime),
        sa.column("attempts", sa.Integer),
        sa.column("max_attempts", sa.Integer),
        sa.column("dedupe_key", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    companies = sa.table("companies", sa.column("id", sa.String))

    # role_family / role_template for existing founders (everyone else keeps "generic").
    bind.execute(
        agents.update()
        .where(agents.c.kind == "founder")
        .values(role_family="founder", role_template=FOUNDER_TEMPLATE)
    )

    responsibilities, instructions, toolsets = _hr_template_text()
    for (company_id,) in bind.execute(sa.select(companies.c.id)).fetchall():
        founder = bind.execute(
            sa.select(
                agents.c.id,
                agents.c.runtime,
                agents.c.model_provider,
                agents.c.model_name,
            ).where(
                agents.c.company_id == company_id,
                agents.c.kind == "founder",
                agents.c.status.in_(LIVE_STATUSES),
            )
        ).first()
        # Idempotent across downgrade/upgrade: the role columns are dropped on downgrade, so a
        # previously backfilled Harper is recognized by name and re-labelled.
        already = bind.execute(
            sa.select(agents.c.id).where(
                agents.c.company_id == company_id,
                sa.or_(
                    agents.c.role_template == HR_TEMPLATE,
                    sa.and_(
                        agents.c.name == HR_NAME,
                        agents.c.department_id.in_(
                            sa.select(departments.c.id).where(
                                departments.c.company_id == company_id,
                                departments.c.name == PEOPLE_DEPARTMENT,
                            )
                        ),
                    ),
                ),
            )
        ).first()
        if already is not None:
            bind.execute(
                agents.update()
                .where(agents.c.id == already.id)
                .values(role_template=HR_TEMPLATE, role_family="hr")
            )
        else:
            department_id = bind.execute(
                sa.select(departments.c.id).where(
                    departments.c.company_id == company_id, departments.c.name == PEOPLE_DEPARTMENT
                )
            ).scalar()
            if department_id is None:
                department_id = str(uuid.uuid4())
                bind.execute(
                    departments.insert().values(
                        id=department_id,
                        company_id=company_id,
                        name=PEOPLE_DEPARTMENT,
                        description="Hiring, skills and people operations.",
                        lead_agent_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                _audit(
                    bind,
                    audit,
                    company_id,
                    now,
                    "department.created",
                    "department",
                    department_id,
                    f"Created department {PEOPLE_DEPARTMENT}",
                )
            hr_id = str(uuid.uuid4())
            bind.execute(
                agents.insert().values(
                    id=hr_id,
                    company_id=company_id,
                    name=HR_NAME,
                    role_title="HR Partner",
                    kind="specialist",
                    status="active",
                    department_id=department_id,
                    manager_agent_id=founder.id if founder else None,
                    reports_to_human=founder is None,
                    responsibilities=responsibilities,
                    instructions=instructions,
                    model_provider=founder.model_provider if founder else None,
                    model_name=founder.model_name if founder else None,
                    runtime=founder.runtime if founder else "unassigned",
                    runtime_config={},
                    tool_permissions=toolsets,
                    monthly_budget_micros=None,
                    daily_budget_micros=None,
                    status_changed_at=now,
                    created_at=now,
                    updated_at=now,
                    role_template=HR_TEMPLATE,
                    role_family="hr",
                )
            )
            bind.execute(
                departments.update()
                .where(departments.c.id == department_id, departments.c.lead_agent_id.is_(None))
                .values(lead_agent_id=hr_id)
            )
            _audit(
                bind,
                audit,
                company_id,
                now,
                "agent.created",
                "agent",
                hr_id,
                f"Added HR partner {HR_NAME} (backfill for Milestone 4)",
                {"kind": "specialist", "status": "active", "role_template": HR_TEMPLATE},
            )
        bind.execute(
            jobs.insert().values(
                id=str(uuid.uuid4()),
                company_id=company_id,
                kind="seed_skill_library",
                payload={"company_id": company_id},
                status="queued",
                run_after=now,
                attempts=0,
                max_attempts=5,
                dedupe_key=f"seed_skill_library:{company_id}",
                created_at=now,
                updated_at=now,
            )
        )


def _audit(bind, audit, company_id, now, action, target_type, target_id, summary, data=None):
    bind.execute(
        audit.insert().values(
            id=str(uuid.uuid4()),
            company_id=company_id,
            actor_type="system",
            actor_id=None,
            actor_label="System",
            action=action,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
            data=data or {},
            created_at=now,
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM jobs WHERE kind = 'seed_skill_library'"))
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_column("subject_id")
        batch_op.drop_column("subject_type")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("blocked_reason")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("hire_default_monthly_budget_micros")
        batch_op.drop_column("auto_activate_hires")
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("role_family")
        batch_op.drop_column("role_template")

    with op.batch_alter_table("role_templates", schema=None) as batch_op:
        batch_op.drop_index("ix_role_templates_company_id")
    op.drop_table("role_templates")
    with op.batch_alter_table("agent_skills", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_skills_skill_id")
        batch_op.drop_index("ix_agent_skills_company_id")
    op.drop_table("agent_skills")
    with op.batch_alter_table("skills", schema=None) as batch_op:
        batch_op.drop_index("ix_skills_company_status")
        batch_op.drop_index("ix_skills_company_id")
        batch_op.drop_index("ix_skills_blob_sha256")
    op.drop_table("skills")
    op.drop_table("skill_blobs")
