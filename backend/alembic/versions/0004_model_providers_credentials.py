"""M3b: credentials, model providers, policy defaults (default model, sandbox)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_credentials_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credentials")),
    )
    with op.batch_alter_table("credentials", schema=None) as batch_op:
        batch_op.create_index("ix_credentials_company_id", ["company_id"], unique=False)
        batch_op.create_index(
            "uq_credentials_live_name",
            ["company_id", "name"],
            unique=True,
            sqlite_where=sa.text("revoked_at IS NULL"),
            postgresql_where=sa.text("revoked_at IS NULL"),
        )

    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("default_model", sa.String(length=160), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=False),
        sa.Column("is_local", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("input_price_per_mtok_micros", sa.BigInteger(), nullable=True),
        sa.Column("output_price_per_mtok_micros", sa.BigInteger(), nullable=True),
        sa.Column("last_test", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_model_providers_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("fk_model_providers_credential_id_credentials"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_providers")),
        sa.UniqueConstraint("company_id", "name", name="uq_model_providers_company_id"),
    )
    with op.batch_alter_table("model_providers", schema=None) as batch_op:
        batch_op.create_index("ix_model_providers_company_id", ["company_id"], unique=False)

    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("default_model_provider_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("default_model_name", sa.String(length=160), nullable=True))
        batch_op.add_column(
            sa.Column(
                "sandbox_backend",
                sa.String(length=16),
                server_default="auto",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.drop_column("sandbox_backend")
        batch_op.drop_column("default_model_name")
        batch_op.drop_column("default_model_provider_id")

    with op.batch_alter_table("model_providers", schema=None) as batch_op:
        batch_op.drop_index("ix_model_providers_company_id")
    op.drop_table("model_providers")

    with op.batch_alter_table("credentials", schema=None) as batch_op:
        batch_op.drop_index("uq_credentials_live_name")
        batch_op.drop_index("ix_credentials_company_id")
    op.drop_table("credentials")
