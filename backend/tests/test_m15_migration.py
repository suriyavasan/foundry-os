"""M15 schema (migration 0014): the policy / agent / expense columns and the five new tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company

POLICY_DEFAULTS = {
    "purchasing_enabled": False,
    "purchase_auto_approve_micros": 0,
    "purchase_agent_auto_monthly_micros": 0,
    "purchase_cofounder_limit_micros": 0,
    "purchase_cofounder_monthly_micros": 0,
    "purchase_monthly_budget_micros": None,
    "purchase_recurring_requires_human": True,
    "purchase_new_vendor_requires_human": True,
    "purchase_tolerance_bps": 1000,
    "purchase_tolerance_min_micros": 2_000_000,
    "purchase_card_ttl_hours": 72,
    "purchase_checkout_member_id": None,
    "subscription_renewal_alert_days": 7,
}
NEW_TABLES = {
    "card_accounts",
    "agent_cards",
    "purchase_requests",
    "card_transactions",
    "subscriptions",
}
EXPENSE_COLUMNS = {"cost_kind", "source", "purchase_request_id", "card_transaction_id"}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def test_0014_defaults_existing_rows_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm15.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0012")
    engine = create_engine(url)
    now = _now()
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        _insert_legacy_rows(conn, ids["company"], ids["worker"])
        conn.execute(
            text(
                "INSERT INTO external_expenses (id, company_id, vendor, amount_micros, recurrence, "
                "incurred_on, created_at) VALUES (:i, :c, 'Namecheap', 12000000, 'one_time', "
                "'2026-10-01', :t)"
            ),
            {"i": str(uuid.uuid4()), "c": ids["company"], "t": now},
        )
    insp = inspect(engine)
    assert not NEW_TABLES & set(insp.get_table_names())
    assert not set(POLICY_DEFAULTS) & {c["name"] for c in insp.get_columns("company_policies")}

    command.upgrade(cfg, "0013")  # M14 lands first; 0014 is chained after it
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "0014")
    assert set(inspect(engine).get_table_names()) >= NEW_TABLES
    with engine.begin() as conn:
        row = (
            conn.execute(text(f"SELECT {', '.join(POLICY_DEFAULTS)} FROM company_policies"))
            .mappings()
            .one()
        )
        for name, expected in POLICY_DEFAULTS.items():
            got = row[name]
            assert (bool(got) if isinstance(expected, bool) else got) == expected, name
        # fail closed: purchasing off, every AI approval band 0
        assert (
            conn.execute(text("SELECT purchase_monthly_limit_micros FROM agents LIMIT 1")).scalar()
            is None
        )
        # an existing expense is a human record of a real charge
        expense = conn.execute(
            text(
                "SELECT cost_kind, source, purchase_request_id, card_transaction_id "
                "FROM external_expenses"
            )
        ).one()
        assert tuple(expense) == ("verified", "human", None, None)
        assert conn.execute(text("SELECT count(*) FROM tasks")).scalar() == 1

    # a card expense is recorded once per (company, card transaction); human rows (NULL) repeat
    with engine.begin() as conn:
        insert = text(
            "INSERT INTO external_expenses (id, company_id, vendor, amount_micros, recurrence, "
            "incurred_on, created_at, cost_kind, source, card_transaction_id) VALUES "
            "(:i, :c, 'Plausible', 9000000, 'one_time', '2026-10-02', :t, 'verified', 'card', :x)"
        )
        params = {"c": ids["company"], "t": now, "x": "txn-1"}
        conn.execute(insert, {**params, "i": str(uuid.uuid4())})
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(insert, {**params, "i": str(uuid.uuid4())})
    with engine.begin() as conn:
        for _ in range(2):
            conn.execute(
                text(
                    "INSERT INTO external_expenses (id, company_id, vendor, amount_micros, "
                    "recurrence, incurred_on, created_at) VALUES (:i, :c, 'x', 1, 'one_time', "
                    "'2026-10-03', :t)"
                ),
                {"i": str(uuid.uuid4()), "c": ids["company"], "t": now},
            )

    insp = inspect(engine)
    assert {i["name"] for i in insp.get_indexes("purchase_requests")} >= {
        "ix_purchase_requests_company_status",
        "ix_purchase_requests_company_agent_month",
        "ix_purchase_requests_company_vendor",
    }
    uniques = {u["name"] for u in insp.get_unique_constraints("purchase_requests")}
    assert "uq_purchase_requests_dedupe" in uniques
    assert "uq_card_transactions_provider_txn" in {
        u["name"] for u in insp.get_unique_constraints("card_transactions")
    }
    assert "uq_agent_cards_provider_card" in {
        u["name"] for u in insp.get_unique_constraints("agent_cards")
    }
    # no column anywhere can hold a card number
    forbidden = {"pan", "cvc", "cvv", "card_number", "number", "full_number"}
    for table in NEW_TABLES:
        assert not forbidden & {c["name"] for c in insp.get_columns(table)}, table

    command.downgrade(cfg, "0013")
    insp = inspect(engine)
    assert not NEW_TABLES & set(insp.get_table_names())
    assert not set(POLICY_DEFAULTS) & {c["name"] for c in insp.get_columns("company_policies")}
    assert "purchase_monthly_limit_micros" not in {c["name"] for c in insp.get_columns("agents")}
    assert not EXPENSE_COLUMNS & {c["name"] for c in insp.get_columns("external_expenses")}
    with engine.connect() as conn:  # the legacy rows survive the round trip
        assert conn.execute(text("SELECT count(*) FROM tasks")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM external_expenses")).scalar() == 4
    command.upgrade(cfg, "head")
    engine.dispose()


def test_0014_follows_0013_and_is_the_only_head():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert script.get_revision("0014").down_revision == "0013"
    assert len(script.get_heads()) == 1  # a single line of history; M16 chains 0015 after 0014
    assert script.get_revision("0015").down_revision == "0014"


def test_the_migrated_m15_schema_matches_the_models(tmp_path):
    """No drift between ``alembic upgrade head`` and the M15 models."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.base import Base

    url = f"sqlite:///{tmp_path / 'm15-drift.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": False})
            diff = [
                d
                for d in compare_metadata(ctx, Base.metadata)
                if not (isinstance(d, tuple) and d[0] in ("remove_index", "add_index"))
            ]
    finally:
        engine.dispose()
    watched = (*NEW_TABLES, "purchase_", "subscription_renewal", "purchasing_enabled")
    drift = [d for d in diff if any(t in str(d) for t in watched)]
    assert not drift, drift


def test_a_new_company_gets_the_fail_closed_purchasing_defaults(app, client, company):
    """Purchasing is off and every AI approval band is 0, so a request goes to the human."""
    from sqlalchemy import select

    from app.models.policy import CompanyPolicy

    with app.state.session_factory() as db:
        policy = db.scalars(
            select(CompanyPolicy).where(CompanyPolicy.company_id == company["id"])
        ).one()
        for name, expected in POLICY_DEFAULTS.items():
            assert getattr(policy, name) == expected, name
