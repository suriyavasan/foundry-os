"""M8 reachability & identity: connected accounts, domains, hosted UI, e-mail identity, mailboxes

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25 09:00:00.000000

Only new tables; nothing existing is rewritten. Device-level reachability settings (the LAN bind
preference, the tunnel configuration) live in the existing ``system_state`` key/value table.
Portable (no dialect-specific SQL).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "connected_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("extra_credential_ids", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_connected_accounts_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("fk_connected_accounts_credential_id_credentials"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connected_accounts")),
    )
    with op.batch_alter_table("connected_accounts", schema=None) as batch_op:
        batch_op.create_index("ix_connected_accounts_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "ix_connected_accounts_company_provider", ["company_id", "provider"], unique=False
        )

    op.create_table(
        "domain_registrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=20), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("registry_id", sa.String(length=40), nullable=True),
        sa.Column("provider_id", sa.String(length=40), nullable=True),
        sa.Column("registrar", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("pr_url", sa.String(length=500), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("years", sa.Integer(), nullable=True),
        sa.Column("price_micros", sa.BigInteger(), nullable=True),
        sa.Column("max_price_micros", sa.BigInteger(), nullable=True),
        sa.Column("renewal_price_micros", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_domain_registrations_company_id_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name=op.f("fk_domain_registrations_approval_id_approvals"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_registrations")),
    )
    with op.batch_alter_table("domain_registrations", schema=None) as batch_op:
        batch_op.create_index("ix_domain_registrations_company_id", ["company_id"], unique=False)

    op.create_table(
        "company_domains",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("path", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("dns_status", sa.String(length=12), nullable=False),
        sa.Column("tls_status", sa.String(length=12), nullable=False),
        sa.Column("dns_provider", sa.String(length=32), nullable=True),
        sa.Column("zone_id", sa.String(length=64), nullable=True),
        sa.Column("dns_records", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("registration_id", sa.String(length=36), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_company_domains_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["registration_id"],
            ["domain_registrations.id"],
            name=op.f("fk_company_domains_registration_id_domain_registrations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_domains")),
        sa.UniqueConstraint("company_id", name=op.f("uq_company_domains_company_id")),
    )

    op.create_table(
        "hosted_ui_deployments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("project_name", sa.String(length=100), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("deployment_id", sa.String(length=64), nullable=True),
        sa.Column("url", sa.String(length=300), nullable=True),
        sa.Column("proxy_target", sa.String(length=300), nullable=True),
        sa.Column("last_deployed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_hosted_ui_deployments_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hosted_ui_deployments")),
        sa.UniqueConstraint("company_id", name=op.f("uq_hosted_ui_deployments_company_id")),
    )

    op.create_table(
        "email_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=True),
        sa.Column("domain", sa.String(length=253), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_email_identities_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_identities")),
        sa.UniqueConstraint("company_id", name=op.f("uq_email_identities_company_id")),
    )

    op.create_table(
        "mailboxes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("owner_agent_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("inbound_count", sa.Integer(), nullable=False),
        sa.Column("outbound_count", sa.Integer(), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(), nullable=True),
        *_ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_mailboxes_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["owner_agent_id"], ["agents.id"], name=op.f("fk_mailboxes_owner_agent_id_agents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mailboxes")),
        sa.UniqueConstraint("address", name=op.f("uq_mailboxes_address")),
    )
    with op.batch_alter_table("mailboxes", schema=None) as batch_op:
        batch_op.create_index("ix_mailboxes_company_id", ["company_id"], unique=False)

    op.create_table(
        "mail_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("mailbox_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("sender", sa.String(length=320), nullable=True),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("untrusted", sa.Boolean(), nullable=False),
        sa.Column("injection_flags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_mail_messages_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], ["mailboxes.id"], name=op.f("fk_mail_messages_mailbox_id_mailboxes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_messages")),
        sa.UniqueConstraint("mailbox_id", "external_id", name="uq_mail_messages_mailbox_external"),
    )
    with op.batch_alter_table("mail_messages", schema=None) as batch_op:
        batch_op.create_index(
            "ix_mail_messages_company_mailbox",
            ["company_id", "mailbox_id", "created_at"],
            unique=False,
        )

    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email_kinds", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notification_preferences_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_notification_preferences")),
    )


def downgrade() -> None:
    for name in (
        "notification_preferences",
        "mail_messages",
        "mailboxes",
        "email_identities",
        "hosted_ui_deployments",
        "company_domains",
        "domain_registrations",
        "connected_accounts",
    ):
        op.drop_table(name)
