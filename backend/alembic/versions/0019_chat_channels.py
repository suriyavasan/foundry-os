"""M19: chat channels (Telegram, Discord, WhatsApp Cloud API)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-30 12:00:00.000000

* ``chat_channels`` -- one connected bot per kind and company (secrets live in ``credentials``).
* ``chat_links`` -- a chat identity tied to a company member (unique while not revoked).
* ``chat_link_codes`` -- one-time, hashed, short-lived linking codes.
* ``chat_messages`` -- inbound dedupe / routing log and the durable outbound outbox.

Portable (SQLite and Postgres). The downgrade drops all four tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_channels",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("poll_cursor", sa.String(length=64), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_event_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "kind", name="uq_chat_channels_company_kind"),
    )
    op.create_index("ix_chat_channels_company_id", "chat_channels", ["company_id"])

    op.create_table(
        "chat_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("external_user_id", sa.String(length=64), nullable=False),
        sa.Column("external_chat_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("notify", sa.JSON(), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["chat_channels.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_links_company_id", "chat_links", ["company_id"])
    op.create_index("ix_chat_links_channel_id", "chat_links", ["channel_id"])
    op.create_index("ix_chat_links_user_id", "chat_links", ["user_id"])
    op.create_index(
        "uq_chat_links_active_identity",
        "chat_links",
        ["channel_id", "external_user_id"],
        unique=True,
        sqlite_where=sa.text("revoked_at IS NULL"),
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "chat_link_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["chat_channels.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_link_codes_company_id", "chat_link_codes", ["company_id"])
    op.create_index("ix_chat_link_codes_code_hash", "chat_link_codes", ["code_hash"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("link_id", sa.String(length=36), nullable=True),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("external_message_id", sa.String(length=200), nullable=True),
        sa.Column("external_user_id", sa.String(length=64), nullable=True),
        sa.Column("ref_type", sa.String(length=16), nullable=True),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["channel_id"], ["chat_channels.id"]),
        sa.ForeignKeyConstraint(["link_id"], ["chat_links.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id", "direction", "external_message_id", name="uq_chat_messages_external"
        ),
    )
    op.create_index("ix_chat_messages_company_id", "chat_messages", ["company_id"])
    op.create_index(
        "ix_chat_messages_channel_status", "chat_messages", ["channel_id", "direction", "status"]
    )
    op.create_index("ix_chat_messages_ref", "chat_messages", ["channel_id", "ref_type", "ref_id"])
    op.create_index("ix_chat_messages_link_created", "chat_messages", ["link_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_link_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_ref", table_name="chat_messages")
    op.drop_index("ix_chat_messages_channel_status", table_name="chat_messages")
    op.drop_index("ix_chat_messages_company_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_link_codes_code_hash", table_name="chat_link_codes")
    op.drop_index("ix_chat_link_codes_company_id", table_name="chat_link_codes")
    op.drop_table("chat_link_codes")
    op.drop_index("uq_chat_links_active_identity", table_name="chat_links")
    op.drop_index("ix_chat_links_user_id", table_name="chat_links")
    op.drop_index("ix_chat_links_channel_id", table_name="chat_links")
    op.drop_index("ix_chat_links_company_id", table_name="chat_links")
    op.drop_table("chat_links")
    op.drop_index("ix_chat_channels_company_id", table_name="chat_channels")
    op.drop_table("chat_channels")
