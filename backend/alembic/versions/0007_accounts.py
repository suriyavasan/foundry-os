"""M7 accounts, login & team: credentials, sessions, API tokens, invitations, notifications, org refs

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-24 23:30:00.000000

Existing installs: the placeholder owner (``owner@localhost``, no password) is flagged
``is_local_owner`` so the first-run setup can CLAIM that user (memberships and the company are kept)
instead of creating a new one. Every existing ``company_members`` row becomes an active member;
nothing else is rewritten. Portable (SQLite batch mode / Postgres): no dialect-specific SQL.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("email", existing_type=sa.String(length=320), nullable=True)
        batch_op.add_column(sa.Column("username", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("password_changed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_local_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")
            )
        )
        batch_op.add_column(sa.Column("totp_secret_enc", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch_op.add_column(sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "recovery_code_hashes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
            )
        )
        batch_op.create_unique_constraint(op.f("uq_users_username"), ["username"])
    op.execute(
        sa.text("UPDATE users SET is_local_owner = :t WHERE email = 'owner@localhost'").bindparams(
            t=True
        )
    )

    with op.batch_alter_table("company_members", schema=None) as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("manager_ref", sa.String(length=48), nullable=True))
        batch_op.add_column(
            sa.Column("status", sa.String(length=12), nullable=False, server_default="active")
        )
        batch_op.add_column(sa.Column("removed_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manager_member_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_agents_manager_member_id_company_members"),
            "company_members",
            ["manager_member_id"],
            ["id"],
        )
        batch_op.create_index("ix_agents_manager_member_id", ["manager_member_id"], unique=False)

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("assignee_member_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("reviewer_member_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_tasks_assignee_member_id_company_members"),
            "company_members",
            ["assignee_member_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            op.f("fk_tasks_reviewer_member_id_company_members"),
            "company_members",
            ["reviewer_member_id"],
            ["id"],
        )
        batch_op.create_index("ix_tasks_assignee_member_id", ["assignee_member_id"], unique=False)

    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("approver_member_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_approvals_approver_member_id_company_members"),
            "company_members",
            ["approver_member_id"],
            ["id"],
        )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    with op.batch_alter_table("auth_sessions", schema=None) as batch_op:
        batch_op.create_index("ix_auth_sessions_user_id", ["user_id"], unique=False)

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_api_tokens_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.create_index("ix_api_tokens_user_id", ["user_id"], unique=False)

    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_failure_at", sa.DateTime(), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_login_throttles")),
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("manager_ref", sa.String(length=48), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_invitations_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_invitations_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name=op.f("fk_invitations_accepted_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.create_index("ix_invitations_company_id", ["company_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("related_type", sa.String(length=32), nullable=True),
        sa.Column("related_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_notifications_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notifications_user_created", ["user_id", "created_at"], unique=False
        )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("invitations")
    op.drop_table("login_throttles")
    op.drop_table("api_tokens")
    op.drop_table("auth_sessions")
    with op.batch_alter_table("approvals", schema=None) as batch_op:
        batch_op.drop_column("approver_member_id")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_assignee_member_id")
        batch_op.drop_column("reviewer_member_id")
        batch_op.drop_column("assignee_member_id")
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_index("ix_agents_manager_member_id")
        batch_op.drop_column("manager_member_id")
    with op.batch_alter_table("company_members", schema=None) as batch_op:
        batch_op.drop_column("removed_at")
        batch_op.drop_column("status")
        batch_op.drop_column("manager_ref")
        batch_op.drop_column("title")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("uq_users_username"), type_="unique")
        batch_op.drop_column("recovery_code_hashes")
        batch_op.drop_column("totp_last_step")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret_enc")
        batch_op.drop_column("is_local_owner")
        batch_op.drop_column("password_changed_at")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("username")
        batch_op.alter_column("email", existing_type=sa.String(length=320), nullable=False)
