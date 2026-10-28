"""M16 schema (migration 0015): the radar tables, the policy columns, the opt-in default for
existing companies and the seed sources."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from tests.conftest import alembic_config
from tests.test_m12_migration import _insert_legacy_rows
from tests.test_migrations import _legacy_company

M16_TABLES = {
    "radar_sources",
    "radar_keywords",
    "radar_items",
    "radar_briefs",
    "radar_ideas",
    "radar_host_state",
}
POLICY_COLUMNS = {
    "radar_enabled",
    "radar_max_fetches_per_day",
    "radar_max_items_per_day",
    "radar_max_ideas_per_day",
    "radar_max_open_ideas",
    "radar_auto_accept",
    "radar_max_goals_per_week",
    "radar_agent_sources",
    "self_critique_enabled",
    "radar_contact_email",
}


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _insert_bare_policy(conn, company_id: str) -> None:
    """A policy row written without any radar column (what a client of an older schema, or a raw
    insert, does): the server defaults decide."""
    conn.execute(
        text(
            "INSERT INTO company_policies (id, company_id, autonomy_mode, human_approval_required_for, "
            "max_concurrent_runs, max_delegation_depth, max_tasks_per_planning_cycle, "
            "max_attempts_per_task, max_revisions, max_run_seconds, max_headcount, "
            "heartbeat_interval_seconds, default_run_reservation_micros, allowed_model_providers, "
            "auto_activate_hires, max_agent_monthly_budget_micros, max_planning_runs_per_hour, "
            "max_open_tasks, circuit_breaker_failures, sandbox_backend, budget_alert_thresholds, "
            "model_routing, allow_paid_fallback, auto_spend_limit_micros, stall_seconds, "
            "max_tool_calls_per_run, max_artifacts_per_run, max_tokens_per_run, "
            "ai_disclosure_enabled, work_mode, budget_lock_version, created_at, updated_at) VALUES "
            "(:i, :c, 'autonomous', '[]', 2, 3, 10, 3, 2, 900, 12, 900, 50000, '[]', 1, 25000000, "
            "6, 200, 8, 'auto', '[50,80,100]', 'free_first', 1, 0, 180, 300, 50, 3000000, 1, "
            "'api', 0, :t, :t)"
        ),
        {"i": str(uuid.uuid4()), "c": company_id, "t": _now()},
    )


def test_0015_adds_the_tables_seeds_existing_companies_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm16.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0014")
    engine = create_engine(url)
    with engine.begin() as conn:
        ids = _legacy_company(conn, "Legacy")
        _insert_legacy_rows(conn, ids["company"], ids["worker"])  # an existing policy row
        other = _legacy_company(conn, "Second")
    insp = inspect(engine)
    assert not M16_TABLES & set(insp.get_table_names())

    command.upgrade(cfg, "0015")
    insp = inspect(engine)
    assert set(insp.get_table_names()) >= M16_TABLES
    assert {c["name"] for c in insp.get_columns("company_policies")} >= POLICY_COLUMNS

    with engine.begin() as conn:
        # the existing company opts in: radar off, the documented limits everywhere else
        row = conn.execute(
            text(
                "SELECT radar_enabled, radar_max_fetches_per_day, radar_max_items_per_day, "
                "radar_max_ideas_per_day, radar_max_open_ideas, radar_auto_accept, "
                "radar_max_goals_per_week, radar_agent_sources, self_critique_enabled, "
                "radar_contact_email FROM company_policies WHERE company_id = :c"
            ),
            {"c": ids["company"]},
        ).one()
        assert tuple(row) == (0, 2000, 400, 5, 25, 1, 1, 1, 1, None)

        # a company with no policy row yet, and a policy inserted after the migration, start on
        _insert_bare_policy(conn, other["company"])
        assert (
            conn.execute(
                text("SELECT radar_enabled FROM company_policies WHERE company_id = :c"),
                {"c": other["company"]},
            ).scalar()
            == 1
        )

        # every existing company got the 100 seed sources: 57 fetchable and on, 33 search-only
        # (on, never fetched), 10 off with a reason
        for company in (ids["company"], other["company"]):
            rows = conn.execute(
                text(
                    "SELECT slug, adapter, enabled, origin, seed_version, edited_by_owner, "
                    "disabled_reason, next_fetch_at, query_mode FROM radar_sources "
                    "WHERE company_id = :c"
                ),
                {"c": company},
            ).all()
            assert len(rows) == 100
            assert len({r.slug for r in rows}) == 100
            assert {r.origin for r in rows} == {"seed"}
            assert {r.seed_version for r in rows} == {1}
            assert not any(r.edited_by_owner for r in rows)
            assert all(r.next_fetch_at is None for r in rows)  # due as soon as the radar is on
            fetchable = [r for r in rows if r.enabled and r.adapter != "search_engine"]
            search = [r for r in rows if r.adapter == "search_engine"]
            off = [r for r in rows if not r.enabled]
            assert (len(fetchable), len(search), len(off)) == (57, 33, 10)
            assert all(r.enabled and r.disabled_reason is None for r in search)
            assert all(r.disabled_reason for r in off)
            assert {r.disabled_reason for r in off} <= {
                "needs_token",
                "needs_values",
                "terms",
                "unreachable",
            }

        # uniqueness: one source per slug per company, one item per url hash, one brief per day
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(
                text(
                    "INSERT INTO radar_sources (id, company_id, slug, name, url, category, "
                    "access_method, adapter, keep_unmatched, enabled, origin, edited_by_owner, "
                    "robots_override, template_values, created_at, updated_at) VALUES "
                    "(:i, :c, 'techmeme', 'Dup', 'https://x.test', 'news', 'rss', 'feed', 0, 1, "
                    "'owner', 0, 0, '{}', :t, :t)"
                ),
                {"i": str(uuid.uuid4()), "c": ids["company"], "t": _now()},
            )
    with engine.begin() as conn:
        item = text(
            "INSERT INTO radar_items (id, company_id, url, url_hash, title_hash, title, excerpt, "
            "fetched_at, kind, engagement, also_seen_on, matched_keywords, injection_flags, "
            "status) VALUES (:i, :c, 'https://a.test/x', :h, 'th', 'T', '', :t, 'post', '{}', "
            "'[]', '[]', '[]', 'new')"
        )
        conn.execute(
            item, {"i": str(uuid.uuid4()), "c": ids["company"], "h": "a" * 64, "t": _now()}
        )
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(
                item, {"i": str(uuid.uuid4()), "c": ids["company"], "h": "a" * 64, "t": _now()}
            )
        # the same URL in another company is a different item
        conn.execute(
            item, {"i": str(uuid.uuid4()), "c": other["company"], "h": "a" * 64, "t": _now()}
        )
        assert conn.execute(text("SELECT match_score FROM radar_items LIMIT 1")).scalar() == 0
    with engine.begin() as conn:
        brief = text(
            "INSERT INTO radar_briefs (id, company_id, kind, period_start, period_end, period_day, "
            "summary, themes, created_at) VALUES (:i, :c, 'daily', :t, :t, '2026-10-27', 's', "
            "'[]', :t)"
        )
        conn.execute(brief, {"i": str(uuid.uuid4()), "c": ids["company"], "t": _now()})
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(brief, {"i": str(uuid.uuid4()), "c": ids["company"], "t": _now()})

    indexes = {i["name"] for t in M16_TABLES for i in inspect(engine).get_indexes(t)}
    assert {
        "ix_radar_sources_company_due",
        "ix_radar_items_company_fetched",
        "ix_radar_items_company_status_score",
        "ix_radar_ideas_company_status",
        "ix_radar_briefs_company_created",
    } <= indexes

    command.downgrade(cfg, "0014")
    insp = inspect(engine)
    assert not M16_TABLES & set(insp.get_table_names())
    assert not POLICY_COLUMNS & {c["name"] for c in insp.get_columns("company_policies")}
    command.upgrade(cfg, "0015")  # and up again, with the data of the first run gone
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM radar_sources")).scalar() == 200
    engine.dispose()


def test_0015_on_an_empty_database_creates_no_sources(tmp_path):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM radar_sources")).scalar() == 0
    engine.dispose()


def test_the_seed_file_is_valid_and_matches_the_research_counts():
    from app.radar.seed import (
        ACCESS_METHODS,
        ADAPTER_NAMES,
        CATEGORIES,
        SEED_FILE,
        custom_placeholders,
        load_seed_sources,
    )

    raw = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    assert len(raw) == 100
    sources = load_seed_sources()
    assert len(sources) == 100
    assert len({s.slug for s in sources}) == 100
    assert {s.category for s in sources} <= set(CATEGORIES)
    assert {s.access_method for s in sources} <= set(ACCESS_METHODS)
    assert {s.adapter for s in sources} <= set(ADAPTER_NAMES)
    for s in sources:
        if s.adapter == "search_engine":
            assert s.fetch_url is None and s.search_template and "{query}" in s.search_template
        if s.enabled and s.adapter != "search_engine":
            # a seed we fetch by default is https, needs no token and no owner-chosen value
            assert s.fetch_url and s.fetch_url.startswith("https://")
            assert not custom_placeholders(s.fetch_url)
            assert s.access_method != "json_api_free_token"
        assert 3600 <= s.interval_seconds <= 86_400
    # the owner decisions: no paid or restricted source is on
    by_slug = {s.slug: s for s in sources}
    for slug in ("behance", "product-hunt-api-v2", "semantic-scholar-graph-api"):
        assert not by_slug[slug].enabled
    reddit = [s for s in sources if s.slug.startswith("reddit")]
    assert reddit and all(s.adapter == "search_engine" for s in reddit)
    assert by_slug["one-page-love"].enabled and by_slug["one-page-love"].keep_unmatched
    assert by_slug["x-twitter"].adapter == "search_engine"
