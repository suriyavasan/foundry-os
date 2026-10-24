"""0008 adds only new tables: an install at 0007 upgrades without touching its data, and downgrades."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config

NEW_TABLES = {
    "connected_accounts",
    "domain_registrations",
    "company_domains",
    "hosted_ui_deployments",
    "email_identities",
    "mailboxes",
    "mail_messages",
    "notification_preferences",
}


def test_0008_upgrades_an_existing_install_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm7.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0007")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, is_local_owner, totp_enabled, recovery_code_hashes, "
                "created_at, updated_at) VALUES ('u1', 'owner@localhost', 'Owner', 1, 0, '[]', "
                "'2026-10-24 00:00:00', '2026-10-24 00:00:00')"
            )
        )
    assert not NEW_TABLES & set(inspect(engine).get_table_names())

    command.upgrade(cfg, "0008")
    names = set(inspect(engine).get_table_names())
    assert names >= NEW_TABLES
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1  # data untouched
    cols = {c["name"] for c in inspect(engine).get_columns("connected_accounts")}
    assert {
        "provider",
        "credential_id",
        "extra_credential_ids",
        "scopes",
        "status",
        "metadata",
    } <= cols
    assert {u["name"] for u in inspect(engine).get_unique_constraints("mailboxes")} == {
        "uq_mailboxes_address"
    }

    command.downgrade(cfg, "0007")
    assert not NEW_TABLES & set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= NEW_TABLES
    engine.dispose()
