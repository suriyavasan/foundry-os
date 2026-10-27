"""M15 agent cards and purchasing: card accounts, agent cards, purchase requests, card
transactions, subscriptions, and the policy / agent / expense columns that go with them

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-27 15:00:00.000000

* ``company_policies`` (human-only): ``purchasing_enabled`` (off) and every AI approval band at 0, so
  an existing company keeps today's behaviour until the owner opts in. Every non-null column has a
  server default; nothing is backfilled.
* ``agents.purchase_monthly_limit_micros``: an optional per-agent purchase cap (NULL = inherit).
* ``external_expenses``: ``cost_kind`` (existing rows are human records of real charges, hence
  ``verified``), ``source``, ``purchase_request_id`` and ``card_transaction_id`` (unique per
  company, so a replayed sync records a charge once).
* New tables ``card_accounts``, ``agent_cards``, ``purchase_requests``, ``card_transactions``,
  ``subscriptions``. No column can hold a card number.

Chained after 0013 (GitHub-backed projects). Portable (SQLite and Postgres): string enums, JSON, BigInteger, no partial indexes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_COLUMNS = (
    "purchasing_enabled",
    "purchase_auto_approve_micros",
    "purchase_agent_auto_monthly_micros",
    "purchase_cofounder_limit_micros",
    "purchase_cofounder_monthly_micros",
    "purchase_monthly_budget_micros",
    "purchase_recurring_requires_human",
    "purchase_new_vendor_requires_human",
    "purchase_tolerance_bps",
    "purchase_tolerance_min_micros",
    "purchase_card_ttl_hours",
    "purchase_checkout_member_id",
    "subscription_renewal_alert_days",
)


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    ref_table = target.split(".")[0]
    return sa.ForeignKeyConstraint(
        [column], [target], name=op.f(f"fk_{table}_{column}_{ref_table}")
    )


def upgrade() -> None:
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("purchasing_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        for name in (
            "purchase_auto_approve_micros",
            "purchase_agent_auto_monthly_micros",
            "purchase_cofounder_limit_micros",
            "purchase_cofounder_monthly_micros",
        ):
            batch_op.add_column(
                sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
            )
        batch_op.add_column(
            sa.Column("purchase_monthly_budget_micros", sa.BigInteger(), nullable=True)
        )
        for name in ("purchase_recurring_requires_human", "purchase_new_vendor_requires_human"):
            batch_op.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true())
            )
        batch_op.add_column(
            sa.Column("purchase_tolerance_bps", sa.Integer(), nullable=False, server_default="1000")
        )
        batch_op.add_column(
            sa.Column(
                "purchase_tolerance_min_micros",
                sa.BigInteger(),
                nullable=False,
                server_default="2000000",
            )
        )
        batch_op.add_column(
            sa.Column("purchase_card_ttl_hours", sa.Integer(), nullable=False, server_default="72")
        )
        batch_op.add_column(
            sa.Column("purchase_checkout_member_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "subscription_renewal_alert_days",
                sa.Integer(),
                nullable=False,
                server_default="7",
            )
        )

    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("purchase_monthly_limit_micros", sa.BigInteger(), nullable=True)
        )

    with op.batch_alter_table("external_expenses", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cost_kind", sa.String(length=16), nullable=False, server_default="verified")
        )
        batch_op.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="human")
        )
        batch_op.add_column(sa.Column("purchase_request_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("card_transaction_id", sa.String(length=36), nullable=True))
        batch_op.create_unique_constraint(
            "uq_external_expenses_card_transaction", ["company_id", "card_transaction_id"]
        )

    op.create_table(
        "card_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("connected_account_id", sa.String(length=36), nullable=True),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("manual_brand", sa.String(length=24), nullable=True),
        sa.Column("manual_last4", sa.String(length=4), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("last_balance_micros", sa.BigInteger(), nullable=True),
        sa.Column("balance_checked_at", sa.DateTime(), nullable=True),
        sa.Column("sync_cursor", sa.String(length=200), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_by_type", sa.String(length=16), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        *_ts(),
        _fk("card_accounts", "company_id", "companies.id"),
        _fk("card_accounts", "connected_account_id", "connected_accounts.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_accounts")),
    )
    op.create_index(
        "ix_card_accounts_company_status", "card_accounts", ["company_id", "status"], unique=False
    )

    op.create_table(
        "agent_cards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("card_account_id", sa.String(length=36), nullable=False),
        sa.Column("provider_card_id", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("purchase_request_id", sa.String(length=36), nullable=True),
        sa.Column("subscription_id", sa.String(length=36), nullable=True),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("last4", sa.String(length=4), nullable=False),
        sa.Column("brand", sa.String(length=24), nullable=True),
        sa.Column("exp_month", sa.Integer(), nullable=True),
        sa.Column("exp_year", sa.Integer(), nullable=True),
        sa.Column("limit_micros", sa.BigInteger(), nullable=False),
        sa.Column("limit_interval", sa.String(length=24), nullable=False),
        sa.Column("merchant_lock", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("frozen_reason", sa.String(length=64), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        *_ts(),
        _fk("agent_cards", "company_id", "companies.id"),
        _fk("agent_cards", "card_account_id", "card_accounts.id"),
        _fk("agent_cards", "agent_id", "agents.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_cards")),
        sa.UniqueConstraint(
            "card_account_id", "provider_card_id", name="uq_agent_cards_provider_card"
        ),
    )
    op.create_index(
        "ix_agent_cards_company_status", "agent_cards", ["company_id", "status"], unique=False
    )
    op.create_index(
        "ix_agent_cards_purchase_request_id", "agent_cards", ["purchase_request_id"], unique=False
    )
    op.create_index(
        "ix_agent_cards_subscription_id", "agent_cards", ["subscription_id"], unique=False
    )

    op.create_table(
        "purchase_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("item", sa.String(length=200), nullable=False),
        sa.Column("vendor", sa.String(length=200), nullable=False),
        sa.Column("vendor_domain", sa.String(length=253), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("original_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("original_currency", sa.String(length=3), nullable=True),
        sa.Column("recurrence", sa.String(length=16), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("alternatives", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("checkout_path", sa.String(length=24), nullable=True),
        sa.Column("approval_id", sa.String(length=36), nullable=True),
        sa.Column("card_id", sa.String(length=36), nullable=True),
        sa.Column("subscription_id", sa.String(length=36), nullable=True),
        sa.Column("checkout_task_id", sa.String(length=36), nullable=True),
        sa.Column("held_micros", sa.BigInteger(), nullable=False),
        sa.Column("settled_micros", sa.BigInteger(), nullable=True),
        sa.Column("cost_kind", sa.String(length=16), nullable=True),
        sa.Column("checkout_packet", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        *_ts(),
        _fk("purchase_requests", "company_id", "companies.id"),
        _fk("purchase_requests", "agent_id", "agents.id"),
        _fk("purchase_requests", "task_id", "tasks.id"),
        _fk("purchase_requests", "project_id", "projects.id"),
        _fk("purchase_requests", "approval_id", "approvals.id"),
        _fk("purchase_requests", "checkout_task_id", "tasks.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_requests")),
        sa.UniqueConstraint("company_id", "dedupe_key", name="uq_purchase_requests_dedupe"),
    )
    op.create_index(
        "ix_purchase_requests_company_status",
        "purchase_requests",
        ["company_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_requests_company_agent_month",
        "purchase_requests",
        ["company_id", "agent_id", "period_month"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_requests_company_vendor",
        "purchase_requests",
        ["company_id", "vendor_domain"],
        unique=False,
    )

    op.create_table(
        "card_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("card_account_id", sa.String(length=36), nullable=False),
        sa.Column("card_id", sa.String(length=36), nullable=True),
        sa.Column("purchase_request_id", sa.String(length=36), nullable=True),
        sa.Column("subscription_id", sa.String(length=36), nullable=True),
        sa.Column("provider_txn_id", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("merchant_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("merchant_currency", sa.String(length=3), nullable=True),
        sa.Column("merchant_name", sa.String(length=200), nullable=True),
        sa.Column("merchant_mcc", sa.String(length=8), nullable=True),
        sa.Column("merchant_country", sa.String(length=2), nullable=True),
        sa.Column("match_status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        _fk("card_transactions", "company_id", "companies.id"),
        _fk("card_transactions", "card_account_id", "card_accounts.id"),
        _fk("card_transactions", "card_id", "agent_cards.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_transactions")),
        sa.UniqueConstraint(
            "card_account_id", "provider_txn_id", name="uq_card_transactions_provider_txn"
        ),
    )
    op.create_index(
        "ix_card_transactions_company_occurred",
        "card_transactions",
        ["company_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_card_transactions_purchase_request_id",
        "card_transactions",
        ["purchase_request_id"],
        unique=False,
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("purchase_request_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("vendor", sa.String(length=200), nullable=False),
        sa.Column("vendor_domain", sa.String(length=253), nullable=False),
        sa.Column("item", sa.String(length=200), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("recurrence", sa.String(length=16), nullable=False),
        sa.Column("card_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("next_renewal_on", sa.Date(), nullable=True),
        sa.Column("last_charged_on", sa.Date(), nullable=True),
        sa.Column("last_charge_micros", sa.BigInteger(), nullable=True),
        sa.Column("review_task_id", sa.String(length=36), nullable=True),
        sa.Column("cancel_task_id", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        *_ts(),
        _fk("subscriptions", "company_id", "companies.id"),
        _fk("subscriptions", "agent_id", "agents.id"),
        _fk("subscriptions", "review_task_id", "tasks.id"),
        _fk("subscriptions", "cancel_task_id", "tasks.id"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    op.create_index(
        "ix_subscriptions_company_status_renewal",
        "subscriptions",
        ["company_id", "status", "next_renewal_on"],
        unique=False,
    )
    op.create_index(
        "ix_subscriptions_purchase_request_id",
        "subscriptions",
        ["purchase_request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_purchase_request_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_company_status_renewal", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_card_transactions_purchase_request_id", table_name="card_transactions")
    op.drop_index("ix_card_transactions_company_occurred", table_name="card_transactions")
    op.drop_table("card_transactions")
    op.drop_index("ix_purchase_requests_company_vendor", table_name="purchase_requests")
    op.drop_index("ix_purchase_requests_company_agent_month", table_name="purchase_requests")
    op.drop_index("ix_purchase_requests_company_status", table_name="purchase_requests")
    op.drop_table("purchase_requests")
    op.drop_index("ix_agent_cards_subscription_id", table_name="agent_cards")
    op.drop_index("ix_agent_cards_purchase_request_id", table_name="agent_cards")
    op.drop_index("ix_agent_cards_company_status", table_name="agent_cards")
    op.drop_table("agent_cards")
    op.drop_index("ix_card_accounts_company_status", table_name="card_accounts")
    op.drop_table("card_accounts")
    with op.batch_alter_table("external_expenses", schema=None) as batch_op:
        batch_op.drop_constraint("uq_external_expenses_card_transaction", type_="unique")
        batch_op.drop_column("card_transaction_id")
        batch_op.drop_column("purchase_request_id")
        batch_op.drop_column("source")
        batch_op.drop_column("cost_kind")
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("purchase_monthly_limit_micros")
    with op.batch_alter_table("company_policies", schema=None) as batch_op:
        for column in reversed(_POLICY_COLUMNS):
            batch_op.drop_column(column)
