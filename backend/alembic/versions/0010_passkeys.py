"""Passkeys (WebAuthn): credentials and single-use challenges

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27 09:00:00.000000

* ``passkeys``: one row per registered authenticator credential (public data only: the credential id,
  the COSE public key, a signature counter; the private key never leaves the authenticator).
* ``passkey_challenges``: server-issued, single-use, 5-minute challenges, bound to a purpose and (for
  a registration) to the signed-in user.

Portable (SQLite and Postgres); additive only, so existing installs are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passkeys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("credential_id", sa.String(length=1400), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("aaguid", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("backed_up", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_passkeys_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkeys")),
        sa.UniqueConstraint("credential_id", name="uq_passkeys_credential_id"),
    )
    with op.batch_alter_table("passkeys", schema=None) as batch_op:
        batch_op.create_index("ix_passkeys_user_id", ["user_id"], unique=False)

    op.create_table(
        "passkey_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("challenge", sa.String(length=128), nullable=False),
        sa.Column("rp_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_passkey_challenges_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkey_challenges")),
    )
    with op.batch_alter_table("passkey_challenges", schema=None) as batch_op:
        batch_op.create_index("ix_passkey_challenges_expires_at", ["expires_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("passkey_challenges", schema=None) as batch_op:
        batch_op.drop_index("ix_passkey_challenges_expires_at")
    op.drop_table("passkey_challenges")
    with op.batch_alter_table("passkeys", schema=None) as batch_op:
        batch_op.drop_index("ix_passkeys_user_id")
    op.drop_table("passkeys")
