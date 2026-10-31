"""M23 company intensity (docs/specs/m23-intensity.md): presets, the custom flag, budgets untouched,
who may change it, the plan-only rule, and that the engine really runs on the written values."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text

from alembic import command
from app.core.principal import Principal, PrincipalType
from app.models.ai_stack import AiConnection
from app.models.audit import AuditEvent
from app.models.schedule import Schedule
from app.services import autonomy_watchdog, intensity
from tests.autonomy_helpers import scripted_world
from tests.conftest import alembic_config, human
from tests.helpers import api, get_task, new_agent, new_task, set_policy

LEVELS = (1, 2, 3, 4, 5)
# Mapping of the spec table onto policy fields; the radar row has no policy field and is skipped.
PRESET_TABLE = {
    1: (7200, 1, 2, False, False, False, False),
    2: (3600, 2, 4, True, 43_200, True, 4),
    3: (1800, 2, 6, True, 21_600, True, 12),
    4: (900, 3, 10, True, 10_800, True, 20),
    5: (300, 4, 15, True, 3_600, True, 30),
}


def put(client, cid, level, **extra):
    return client.put(api(cid, "/intensity"), json={"level": level, **extra})


def policy_of(client, cid) -> dict:
    return client.get(api(cid, "/policy")).json()


def add_plan_row(app, cid, *, cli_id="claude", enabled=True) -> str:
    with app.state.session_factory() as db:
        row = AiConnection(
            company_id=cid,
            position=0,
            label=f"{cli_id} plan",
            engine="cli",
            cli_id=cli_id,
            auth_mode="plan",
            enabled=enabled,
        )
        db.add(row)
        db.commit()
        return row.id


def audits(app, cid, action):
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.company_id == cid, AuditEvent.action == action)
                .order_by(AuditEvent.created_at)
            )
        )


# ------------------------------------------------------------------ presets


def test_a_new_company_is_balanced_and_matches_its_preset(client, company):
    cid = company["id"]
    body = client.get(api(cid, "/intensity")).json()
    assert body["level"] == 3 and body["name"] == "Balanced" and body["custom"] is False
    assert body["preset"] == body["effective"]
    assert body["plan_only"] is False and body["warning"] is None
    p = policy_of(client, cid)
    assert p["intensity"] == 3 and p["intensity_custom"] is False


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_writes_its_preset_exactly(client, company, level):
    cid = company["id"]
    r = put(client, cid, level)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == level and body["custom"] is False
    assert body["preset"] == body["effective"] == intensity.preset(level)
    p = policy_of(client, cid)
    heartbeat, conc, planning, init_on, init_every, meet_on, meet_max = (
        *PRESET_TABLE[level][:4],
        *(PRESET_TABLE[level][4:] + (None,) * 3)[:3],
    )[:7]
    assert p["heartbeat_interval_seconds"] == heartbeat
    assert p["max_concurrent_runs"] == conc
    assert p["max_planning_runs_per_hour"] == planning
    assert p["initiative_enabled"] is init_on
    assert p["meetings_enabled"] is (level != 1)
    assert p["self_check_enabled"] is (level != 1)
    if level > 1:
        assert p["initiative_interval_seconds"] == init_every
        assert p["max_meetings_per_company_per_day"] == meet_max
    assert p["intensity"] == level and p["intensity_custom"] is False
    # reserved management slots: at least one and below the cap whenever the cap allows it
    if conc > 1:
        assert 1 <= p["management_reserved_slots"] < conc


def test_the_spec_table_is_what_the_service_holds():
    expected = {
        1: (7200, 1, 2, False, False),
        2: (3600, 2, 4, True, 4),
        3: (1800, 2, 6, True, 12),
        4: (900, 3, 10, True, 20),
        5: (300, 4, 15, True, 30),
    }
    for level, (beat, conc, planning, on, meetings) in expected.items():
        preset = intensity.preset(level)
        assert preset["heartbeat_interval_seconds"] == beat
        assert preset["max_concurrent_runs"] == conc
        assert preset["max_planning_runs_per_hour"] == planning
        assert preset["initiative_enabled"] is on
        assert preset["meetings_enabled"] is on
        assert preset["self_check_enabled"] is on
        if on:
            assert preset["max_meetings_per_company_per_day"] == meetings
    assert [intensity.preset(n)["initiative_interval_seconds"] for n in (2, 3, 4, 5)] == [
        43_200,
        21_600,
        10_800,
        3_600,
    ]


def test_invalid_levels_and_unknown_fields_are_rejected(client, company):
    cid = company["id"]
    for bad in ({"level": 0}, {"level": 6}, {"level": "x"}, {}, {"level": 3, "budget": 1}):
        assert client.put(api(cid, "/intensity"), json=bad).status_code == 422, bad


def test_reserved_slots_are_clamped_when_a_level_lowers_the_cap(client, company):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=6, management_reserved_slots=5)
    put(client, cid, 3)  # cap 2 -> reserved slots must stay below it
    assert policy_of(client, cid)["management_reserved_slots"] == 1
    set_policy(client, cid, management_reserved_slots=0)
    put(client, cid, 4)
    assert policy_of(client, cid)["management_reserved_slots"] == 1  # never 0 when the cap is > 1


# ------------------------------------------------------------------ the custom flag


def test_a_hand_edit_of_a_pace_field_marks_the_company_custom_and_a_level_clears_it(
    client, company
):
    cid = company["id"]
    # setting a field to the value the level already has is not a customisation
    assert set_policy(client, cid, max_concurrent_runs=2)["intensity_custom"] is False
    # editing a field the intensity does not control is not either
    assert set_policy(client, cid, max_headcount=20)["intensity_custom"] is False
    p = set_policy(client, cid, max_concurrent_runs=5)
    assert p["intensity_custom"] is True and p["intensity"] == 3
    assert client.get(api(cid, "/intensity")).json()["custom"] is True
    assert put(client, cid, 3).json()["custom"] is False  # same level again re-applies the preset
    p = policy_of(client, cid)
    assert p["max_concurrent_runs"] == 2 and p["intensity_custom"] is False
    audit = audits(app=client.app, cid=cid, action="policy.updated")[-1]
    assert audit.data["changed"] == ["max_concurrent_runs"] or "intensity_custom" in audit.data


def test_every_pace_field_marks_custom_when_it_leaves_its_preset(client, company):
    cid = company["id"]
    edits = {
        "heartbeat_interval_seconds": 7200,
        "max_planning_runs_per_hour": 99,
        "initiative_enabled": False,
        "initiative_interval_seconds": 700,
        "meetings_enabled": False,
        "max_meetings_per_company_per_day": 99,
        "self_check_enabled": False,
    }
    for field, value in edits.items():
        put(client, cid, 3)
        assert policy_of(client, cid)["intensity_custom"] is False
        assert set_policy(client, cid, **{field: value})["intensity_custom"] is True, field


# ------------------------------------------------------------------ budgets untouched


def test_no_level_touches_a_budget_or_any_other_non_pace_field(client, company):
    cid = company["id"]
    set_policy(
        client,
        cid,
        daily_budget_micros=7_000_000,
        monthly_budget_micros=70_000_000,
        auto_spend_limit_micros=1_234,
        default_run_reservation_micros=77_000,
        max_agent_monthly_budget_micros=12_000_000,
        hire_default_monthly_budget_micros=3_000_000,
        cli_run_reservation_micros=2_000_000,
        max_headcount=9,
        approval_timeout_seconds=7200,
        purchase_monthly_budget_micros=50_000_000,
        human_approval_required_for=["external_purchase", "hire"],
        max_tokens_per_run=2_000_000,
    )
    before = policy_of(client, cid)
    pace = set(intensity.FIELDS) | {
        "intensity",
        "intensity_custom",
        "management_reserved_slots",
        "updated_at",
    }
    for level in (1, 5, 2, 4, 3):
        assert put(client, cid, level).status_code == 200
        after = policy_of(client, cid)
        for key, value in before.items():
            if key not in pace:
                assert after[key] == value, (level, key)
    assert before["daily_budget_micros"] == 7_000_000
    assert policy_of(client, cid)["monthly_budget_micros"] == 70_000_000


# ------------------------------------------------------------------ who may change it


def test_only_owners_and_admins_change_it_and_everyone_in_the_company_reads_it(
    client, client_as, make_user, add_member, company
):
    cid = company["id"]
    users = {}
    for role in ("viewer", "member", "admin"):
        users[role] = make_user(f"{role}@x.test")
        add_member(cid, users[role].id, role)
    for role in ("viewer", "member"):
        c = client_as(human(users[role]))
        assert c.get(api(cid, "/intensity")).status_code == 200
        assert put(c, cid, 5).status_code == 403
    admin = client_as(human(users["admin"]))
    assert put(admin, cid, 2).status_code == 200
    assert client.get(api(cid, "/intensity")).json()["level"] == 2
    outsider = client_as(human(make_user("out@x.test")))
    assert outsider.get(api(cid, "/intensity")).status_code == 404
    assert put(outsider, cid, 5).status_code == 404
    assert put(client, cid, 3).status_code == 200  # the owner


def test_no_agent_route_or_tool_can_change_the_intensity(app, client, client_as, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    ac = client_as(Principal(PrincipalType.AGENT, agent["id"], "Nova", cid))
    assert ac.get(api(cid, "/intensity")).status_code == 200  # reading it is fine
    assert put(ac, cid, 5).status_code == 403
    assert client.get(api(cid, "/intensity")).json()["level"] == 3
    for body in ({"intensity": 5}, {"intensity_custom": True}, {"max_concurrent_runs": 50}):
        assert ac.patch(api(cid, "/policy"), json=body).status_code in (403, 422), body
    p = policy_of(client, cid)
    assert p["intensity"] == 3 and p["max_concurrent_runs"] == 2
    # the policy PATCH body has no intensity field for anyone: a human cannot set it that way either
    assert client.patch(api(cid, "/policy"), json={"intensity": 5}).status_code == 422
    # and the agent API / toolkit exposes nothing that touches policy or intensity
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/agent/"):
            assert "intensity" not in path and "policy" not in path, path
    import json

    from app.core.config import BACKEND_DIR

    tools = json.loads((BACKEND_DIR / "app" / "guardrails" / "tools.json").read_text())
    assert "intensity" not in json.dumps(tools).lower()


# ------------------------------------------------------------------ audit


def test_a_change_is_audited_with_before_and_after_values(app, client, company):
    cid = company["id"]
    put(client, cid, 5)
    [event] = audits(app, cid, "policy.intensity_changed")
    assert event.actor_type == "human" and event.target_type == "policy"
    assert event.data["level_before"] == 3 and event.data["level_after"] == 5
    assert event.data["before"]["heartbeat_interval_seconds"] == 1800
    assert event.data["after"]["heartbeat_interval_seconds"] == 300
    assert event.data["before"]["max_concurrent_runs"] == 2
    assert event.data["after"]["max_concurrent_runs"] == 4
    assert "daily_budget_micros" not in event.data["after"]
    # choosing the same level again is still an audited choice, with nothing to change
    put(client, cid, 5)
    *_, again = audits(app, cid, "policy.intensity_changed")
    assert again.data["before"] == {} and again.data["after"] == {}
    assert len(audits(app, cid, "policy.intensity_changed")) == 2


# ------------------------------------------------------------------ plan-only stacks


def test_levels_four_and_five_need_a_confirmation_on_a_plan_only_stack(app, client, company):
    cid = company["id"]
    add_plan_row(app, cid)
    view = client.get(api(cid, "/intensity")).json()
    assert view["plan_only"] is True and "usage limits" in view["warning"]
    for level in (4, 5):
        r = put(client, cid, level)
        assert r.status_code == 409 and r.json()["error"]["code"] == "intensity_confirm_required"
        assert put(client, cid, level, confirm=False).status_code == 409
    assert client.get(api(cid, "/intensity")).json()["level"] == 3  # nothing was written
    assert put(client, cid, 2).status_code == 200  # lower levels never need it
    assert put(client, cid, 1).status_code == 200
    r = put(client, cid, 5, confirm=True)
    assert r.status_code == 200 and r.json()["level"] == 5 and r.json()["plan_only"] is True
    [*_, last] = audits(app, cid, "policy.intensity_changed")
    assert last.data["confirmed"] is True


def test_a_stack_with_an_api_connection_or_a_disabled_plan_needs_no_confirmation(
    app, client, company
):
    cid = company["id"]
    add_plan_row(app, cid)
    with app.state.session_factory() as db:
        db.add(
            AiConnection(
                company_id=cid, position=1, label="Key", engine="cli", cli_id="codex",
                auth_mode="api_key", enabled=True,
            )
        )  # fmt: skip
        db.commit()
    assert client.get(api(cid, "/intensity")).json()["plan_only"] is False
    assert put(client, cid, 5).status_code == 200
    # a plan that is switched off does not count either way
    other = client.post("/api/companies", json={"name": "Other", "mission": "x"}).json()["id"]
    add_plan_row(app, other, enabled=False)
    assert client.get(api(other, "/intensity")).json()["plan_only"] is False


def test_a_new_company_whose_stack_is_plan_only_moves_to_level_two(app, client, company):
    cid = company["id"]
    assert client.get(api(cid, "/intensity")).json()["level"] == 3
    r = client.post(api(cid, "/ai-stack"), json={"option": "claude_plan"})
    assert r.status_code == 201, r.text
    view = client.get(api(cid, "/intensity")).json()
    assert view["level"] == 2 and view["custom"] is False and view["plan_only"] is True
    assert view["effective"] == intensity.preset(2)
    [event] = audits(app, cid, "policy.intensity_changed")
    assert event.data["level_before"] == 3 and event.data["level_after"] == 2
    assert "personal plan" in event.data["reason"]
    # the default is applied once: the owner can go back up (with confirmation above 3)
    assert put(client, cid, 3).status_code == 200
    r2 = client.post(api(cid, "/ai-stack"), json={"option": "cursor_plan"})
    assert r2.status_code == 201, r2.text
    assert client.get(api(cid, "/intensity")).json()["level"] == 3


def test_the_plan_only_default_never_overrides_a_choice_or_a_custom_company(
    app, client, make_user, company
):
    chosen = company["id"]
    put(client, chosen, 3)  # an explicit choice (even of the default level) is respected
    assert client.post(api(chosen, "/ai-stack"), json={"option": "claude_plan"}).status_code == 201
    assert client.get(api(chosen, "/intensity")).json()["level"] == 3
    custom = client.post("/api/companies", json={"name": "Custom", "mission": "x"}).json()["id"]
    set_policy(client, custom, max_concurrent_runs=5)
    assert client.post(api(custom, "/ai-stack"), json={"option": "claude_plan"}).status_code == 201
    view = client.get(api(custom, "/intensity")).json()
    assert view["level"] == 3 and view["custom"] is True


def test_the_plan_only_default_is_not_applied_when_an_api_connection_is_in_the_stack(
    app, client, company
):
    from tests.m10_helpers import make_ready

    cid = company["id"]
    make_ready(app, client, cid)  # an Ollama API connection
    assert client.post(api(cid, "/ai-stack"), json={"option": "claude_plan"}).status_code == 201
    view = client.get(api(cid, "/intensity")).json()
    assert view["level"] == 3 and view["plan_only"] is False


# ------------------------------------------------------------------ the engine runs on it


def heartbeat(app, cid) -> Schedule:
    with app.state.session_factory() as db:
        return db.scalars(
            select(Schedule).where(Schedule.company_id == cid, Schedule.name == "heartbeat")
        ).one()


def test_a_new_companys_heartbeat_runs_at_the_policy_interval(app, client, company):
    cid = company["id"]
    assert policy_of(client, cid)["heartbeat_interval_seconds"] == 1800
    assert heartbeat(app, cid).interval_seconds == 1800


@pytest.mark.parametrize("level", LEVELS)
def test_the_heartbeat_schedule_follows_the_level(app, client, company, level):
    cid = company["id"]
    put(client, cid, level)
    beat = heartbeat(app, cid)
    assert beat.interval_seconds == PRESET_TABLE[level][0] and beat.cron is None
    assert beat.next_run_at is not None


@pytest.mark.parametrize("level", (1, 5))
def test_the_scheduler_reschedules_the_heartbeat_at_the_level_cadence(
    app, client, company, scheduler, clock, level
):
    cid = company["id"]
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            if s.name != "heartbeat":
                s.enabled = False
        db.commit()
    put(client, cid, level)
    with app.state.session_factory() as db:  # due now
        db.scalars(
            select(Schedule).where(Schedule.company_id == cid, Schedule.name == "heartbeat")
        ).one().next_run_at = clock() - timedelta(seconds=1)
        db.commit()
    scheduler.tick()
    nxt = heartbeat(app, cid).next_run_at
    assert abs((nxt - clock()).total_seconds() - PRESET_TABLE[level][0]) < 2


def test_the_dispatcher_uses_the_written_concurrency(client, company, scheduler, worker):
    cid = company["id"]
    agents = [new_agent(client, cid, name=f"W{i}") for i in range(5)]
    put(client, cid, 1)
    tasks = [
        new_task(client, cid, title=f"w{i}", assignee_agent_id=a["id"])
        for i, a in enumerate(agents)
    ]
    assert len(scheduler.tick().dispatch.queued_run_ids) == 1  # Conserve: one run at a time
    put(client, cid, 5)  # 4 slots, 1 reserved for management: work may use 3, one is running
    report = scheduler.tick()
    running = sum(1 for t in tasks if get_task(client, cid, t["id"])["status"] == "in_progress")
    assert running <= 4 and len(report.dispatch.queued_run_ids) >= 1


def test_the_initiative_watchdog_follows_the_level(app, client, scripted, anon_client, clock):
    w = scripted_world(client, scripted, anon_client)
    cid = w.cid

    def run(minutes: float) -> int:
        clock.advance(minutes * 60)
        return autonomy_watchdog.run_company(app.state.session_factory, cid, clock())["initiative"]

    put(client, cid, 1)  # initiative off
    assert run(24 * 60) == 0  # idle for a day and still nothing
    put(client, cid, 5)  # on, every hour: the company has been idle longer than that
    assert run(1) == 1


# ------------------------------------------------------------------ migration


def test_0023_adds_the_columns_aligns_the_heartbeat_and_marks_custom_companies(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0022")
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    engine = create_engine(url)
    ids = {name: str(uuid.uuid4()) for name in ("plain", "edited", "no_schedule")}
    with engine.begin() as conn:
        for name, cid in ids.items():
            heartbeat_policy = 900 if name != "edited" else 3600
            conc = 5 if name == "no_schedule" else 2
            conn.execute(
                text(
                    "INSERT INTO company_policies (id, company_id, autonomy_mode, "
                    "human_approval_required_for, max_concurrent_runs, max_delegation_depth, "
                    "max_tasks_per_planning_cycle, max_attempts_per_task, max_revisions, "
                    "max_run_seconds, max_headcount, heartbeat_interval_seconds, "
                    "default_run_reservation_micros, allowed_model_providers, "
                    "budget_alert_thresholds, budget_lock_version, created_at, updated_at) VALUES "
                    "(:i, :c, 'autonomous', '[]', :conc, 3, 10, 3, 2, 900, 12, :hb, 50000, '[]', "
                    "'[50,80,100]', 0, :t, :t)"
                ),
                {"i": str(uuid.uuid4()), "c": cid, "conc": conc, "hb": heartbeat_policy, "t": now},
            )
        for name in ("plain", "edited"):
            conn.execute(
                text(
                    "INSERT INTO schedules (id, company_id, name, kind, agent_id, interval_seconds, "
                    "timezone, task_template, enabled, created_at, updated_at) VALUES "
                    "(:i, :c, 'heartbeat', 'heartbeat', 'a', :s, 'UTC', '{}', 1, :t, :t)"
                ),
                {
                    "i": str(uuid.uuid4()),
                    "c": ids[name],
                    "s": 1800 if name == "plain" else 3600,
                    "t": now,
                },
            )
    command.upgrade(cfg, "0023")
    with engine.connect() as conn:
        rows = {
            r.company_id: r
            for r in conn.execute(
                text(
                    "SELECT company_id, intensity, intensity_custom, heartbeat_interval_seconds "
                    "FROM company_policies"
                )
            )
        }
    plain, edited, other = (rows[ids[n]] for n in ("plain", "edited", "no_schedule"))
    # the unedited company: policy 900 was never used; the schedule's 1800 is the real value = level 3
    assert (plain.intensity, bool(plain.intensity_custom), plain.heartbeat_interval_seconds) == (
        3,
        False,
        1800,
    )
    # an edited heartbeat is kept (3600 differs from the Balanced preset: Custom)
    assert (edited.heartbeat_interval_seconds, bool(edited.intensity_custom)) == (3600, True)
    # no schedule: the policy value stays; concurrency 5 and heartbeat 900 differ from Balanced
    assert (other.heartbeat_interval_seconds, bool(other.intensity_custom)) == (900, True)
    command.downgrade(cfg, "0022")
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(company_policies)"))}
    assert "intensity" not in cols and "intensity_custom" not in cols
    command.upgrade(cfg, "head")
