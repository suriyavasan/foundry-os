"""M17 routing (docs/specs/m17-ai-stack.md section 4): the dispatcher routes every run through the
company's AI stack - class (heavy / everyday), usability, weighted fair share, backups, concurrency,
owner pins, waiting with a reason, the run's record, and the sign-in of the chosen connection.

API connections run on a scripted ``company_agent`` stand-in; CLI connections use the real adapters
driving the fake binaries of ``tests/m10_helpers.py`` (no real sign-in, key or model call)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.main import create_app
from app.models.agent import Agent
from app.models.ai_stack import AiConnection
from app.models.autonomy import Alert
from app.models.enums import TaskStatus
from app.models.provider import ModelProvider
from app.models.run import TaskRun
from app.models.work import Task
from app.runtimes.cli.base import CliAdapter
from app.runtimes.registry import RuntimeRegistry
from app.runtimes.scripted import ScriptedRuntime
from app.services import ai_stack, model_providers, policy, routing
from tests.helpers import agent_of_family, api, get_task, new_agent, new_task, set_policy, settle
from tests.m10_helpers import (
    CODEX_OK,
    CURSOR_OK,
    add_connection,
    install_fake_cli,
    record_of,
    register_cli_adapters,
)

KEY = "sk-test-key-0123456789abcdef"
LIMIT_TEXT = "You've hit your session limit · resets 3:45pm"
OK = [{"type": "result", "subtype": "success", "is_error": False, "result": "OK", "usage": {}}]


class FakeCompanyAgent(ScriptedRuntime):
    id = "company_agent"
    display_name = "Company agent (scripted stand-in)"


class FakeCursor(ScriptedRuntime):
    id = "cli:cursor"
    display_name = "Cursor CLI (scripted stand-in)"


@pytest.fixture
def scripted():
    return FakeCompanyAgent()


@pytest.fixture
def app(make_settings, scripted, skills_env_override):
    application = create_app(
        make_settings(),
        runtime_registry=RuntimeRegistry([scripted, FakeCursor(), ScriptedRuntime()]),
        skills_env=skills_env_override,
    )
    application.state.cli_env.use_system_path = False
    return application


@pytest.fixture
def bin_dir(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "realhome"))
    (tmp_path / "realhome").mkdir()
    d = tmp_path / "bin"
    d.mkdir()
    app.state.cli_env.extra_dirs.append(d)
    return d


# ------------------------------------------------------------------ helpers


def provider(client, cid, name, kind="ollama", **kw) -> dict:
    body = {"name": name, "kind": kind, "default_model": "qwen2.5:7b", **kw}
    r = client.post(api(cid, "/model-providers"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def api_conn(app, client, cid, name, **body) -> dict:
    p = provider(client, cid, name)
    return add_connection(
        app,
        client,
        cid,
        "ollama",
        model_provider_id=p["id"],
        model="qwen2.5:7b",
        label=name,
        **body,
    )


def decide(
    app, agent_id, kind="work", *, recent=None, in_flight=None, now=None, runtime_ok=None
) -> routing.Decision:
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        task = Task(company_id=agent.company_id, title="t", kind=kind)
        return routing.decide(
            db,
            agent,
            task,
            policy.load_policy(db, agent.company_id),
            now=now or datetime.now(UTC),
            cli_env=app.state.cli_env,
            runtime_ok=runtime_ok,
            in_flight=in_flight,
            recent=recent,
        )


def clear_legacy(app, cid):
    """Registering a provider makes it the legacy company default; routing must not need that."""
    with app.state.session_factory() as db:
        limits = policy.load_policy(db, cid)
        limits.default_model_provider_id = limits.default_model_name = None
        db.commit()


def worker_agent(client, cid, name="Wes", **kw):
    return new_agent(client, cid, name=name, runtime="company_agent", **kw)


def edit(app, conn_id, **fields):
    with app.state.session_factory() as db:
        row = db.get(AiConnection, conn_id)
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def runs_of(app, cid=None) -> list[TaskRun]:
    with app.state.session_factory() as db:
        rows = list(db.scalars(select(TaskRun).order_by(TaskRun.queued_at, TaskRun.id)))
        db.expunge_all()
        return rows


def loose_policy(client, cid, **extra):
    set_policy(
        client, cid, max_concurrent_runs=12, management_reserved_slots=0, **extra
    )  # room for many runs


# ------------------------------------------------------------------ the chooser


def test_heavy_runs_use_the_heavy_connection_and_everyday_runs_the_everyday_one(
    app, client, company
):
    cid = company["id"]
    heavy = api_conn(app, client, cid, "Big", use_for="heavy")
    everyday = api_conn(app, client, cid, "Small", use_for="everyday")
    founder = agent_of_family(client, cid, "founder")
    plain = worker_agent(client, cid)
    for agent_id, kind in (
        (founder["id"], "work"),
        (plain["id"], "planning"),
        (plain["id"], "review"),
    ):
        got = decide(app, agent_id, kind)
        assert got.route.ai_connection_id == heavy["id"], (agent_id, kind)
        assert got.route.reason == "AI stack: Big (primary, Normal) for heavy work"
    got = decide(app, plain["id"], "work")
    assert got.route.ai_connection_id == everyday["id"]
    assert got.route.reason == "AI stack: Small (secondary, Normal) for everyday work"
    assert got.runtime == "company_agent" and got.stacked


def test_use_for_filters_and_a_class_nobody_covers_waits(app, client, company):
    cid = company["id"]
    both = api_conn(app, client, cid, "Both", use_for="everything")
    heavy_only = api_conn(app, client, cid, "Heavy", use_for="heavy")
    plain = worker_agent(client, cid)
    # everyday work: only "Both" covers it
    for _ in range(3):
        assert decide(app, plain["id"], "work").route.ai_connection_id == both["id"]
    edit(app, both["id"], use_for="heavy")
    got = decide(app, plain["id"], "work")
    assert got.waits and got.wait_reason == "No AI in the stack covers everyday work"
    assert heavy_only["id"]  # heavy work still has both


def test_weighted_fair_share_follows_the_formula(app, client, company):
    cid = company["id"]
    a = api_conn(app, client, cid, "A", share=3)
    b = api_conn(app, client, cid, "B", share=1)
    plain = worker_agent(client, cid)
    recent: Counter = Counter()
    order = []
    for _ in range(8):
        got = decide(app, plain["id"], "work", recent=recent)
        recent[got.route.ai_connection_id] += 1
        order.append("A" if got.route.ai_connection_id == a["id"] else "B")
    # (n + 1) / share: A 1/3, 2/3, 3/3 (tie -> lower position), then B 1/1 wins at A 4/3 ...
    assert "".join(order) == "AAABAAAB"
    assert recent[a["id"]] == 6 and recent[b["id"]] == 2


def test_equal_shares_tie_break_by_position(app, client, company):
    cid = company["id"]
    first = api_conn(app, client, cid, "First")
    second = api_conn(app, client, cid, "Second")
    plain = worker_agent(client, cid)
    recent: Counter = Counter()
    picked = []
    for _ in range(4):
        got = decide(app, plain["id"], "work", recent=recent)
        recent[got.route.ai_connection_id] += 1
        picked.append(got.route.ai_connection_id)
    assert picked == [first["id"], second["id"], first["id"], second["id"]]
    # reordering the stack changes who is first
    client.put(api(cid, "/ai-stack/order"), json={"ids": [second["id"], first["id"]]})
    assert decide(app, plain["id"], "work").route.ai_connection_id == second["id"]


def test_the_rank_and_importance_are_in_the_reason(app, client, company):
    cid = company["id"]
    api_conn(app, client, cid, "A", share=2)
    api_conn(app, client, cid, "B", share=4)
    plain = worker_agent(client, cid)
    got = decide(app, plain["id"], "work", recent=Counter({}))
    assert got.route.reason == "AI stack: B (secondary, Most) for everyday work"


def test_backups_are_used_only_when_no_other_connection_can_take_the_run(app, client, company):
    cid = company["id"]
    main = api_conn(app, client, cid, "Main", share=2)
    backup = api_conn(app, client, cid, "Backup", share=0)
    plain = worker_agent(client, cid)
    # the backup has run nothing, the main one a lot: the backup is still not chosen
    recent = Counter({main["id"]: 50})
    assert decide(app, plain["id"], "work", recent=recent).route.ai_connection_id == main["id"]
    # paused
    edit(app, main["id"], paused_until=datetime.now(UTC) + timedelta(hours=1), pause_reason="limit")
    got = decide(app, plain["id"], "work")
    assert got.route.ai_connection_id == backup["id"]
    assert (
        got.route.reason
        == "AI stack: Backup (secondary, Backup only) as the backup for everyday work"
    )
    # unusable for another reason
    edit(app, main["id"], paused_until=None, pause_reason=None, enabled=False)
    assert decide(app, plain["id"], "work").route.ai_connection_id == backup["id"]
    # at capacity: a Cursor plan runs one at a time
    edit(app, main["id"], enabled=True)
    assert decide(app, plain["id"], "work").route.ai_connection_id == main["id"]


def test_a_backup_cli_connection_at_capacity_is_not_a_candidate(app, client, company, bin_dir):
    cid = company["id"]
    install_fake_cli(bin_dir, "agent", CURSOR_OK)
    cursor = add_connection(app, client, cid, "cursor_plan", share=2)
    backup = api_conn(app, client, cid, "Backup", share=0)
    plain = worker_agent(client, cid)
    got = decide(app, plain["id"], "work")
    assert got.route.ai_connection_id == cursor["id"] and got.runtime == "cli:cursor"
    got = decide(app, plain["id"], "work", in_flight=Counter({cursor["id"]: 1}))
    assert got.route.ai_connection_id == backup["id"]
    assert decide(app, plain["id"], "work", in_flight=Counter({backup["id"]: 5})).stacked


@pytest.mark.parametrize(
    "mode",
    [
        "disabled",
        "untested",
        "login_expired",
        "paused",
        "provider_disabled",
        "provider_not_allowed",
    ],
)
def test_unusable_connections_are_excluded_and_named_when_nothing_is_left(
    app, client, company, mode
):
    cid = company["id"]
    p = provider(client, cid, "Sole")
    sole = add_connection(
        app, client, cid, "ollama", model_provider_id=p["id"], model="qwen2.5:7b",
        passed=mode != "untested",
    )  # fmt: skip
    other = api_conn(app, client, cid, "Other")
    plain = worker_agent(client, cid)
    # starve the other connection of share priority so "Sole" would win if it were usable
    recent = Counter({other["id"]: 9})
    if mode != "untested":  # (an untested connection is unusable from the start)
        assert decide(app, plain["id"], "work", recent=recent).route.ai_connection_id == sole["id"]
    expect = {
        "disabled": "is turned off",
        "untested": "has not passed its test",
        "login_expired": "needs to be signed in again",
        "paused": "is paused until",
        "provider_disabled": "model provider of Sole is not available",
        "provider_not_allowed": "company policy does not allow",
    }[mode]
    if mode == "disabled":
        edit(app, sole["id"], enabled=False)
    elif mode == "login_expired":
        edit(app, sole["id"], pause_reason=ai_stack.LOGIN_EXPIRED)
    elif mode == "paused":
        edit(app, sole["id"], paused_until=datetime.now(UTC) + timedelta(hours=1), pause_reason="x")
    elif mode == "provider_disabled":
        with app.state.session_factory() as db:  # (the API would also reset the connection's test)
            db.get(ModelProvider, p["id"]).enabled = False
            db.commit()
    elif mode == "provider_not_allowed":
        other_provider = client.get(api(cid, "/ai-stack")).json()["items"][1]["model_provider_id"]
        set_policy(client, cid, allowed_model_providers=[other_provider])
    got = decide(app, plain["id"], "work", recent=recent)
    assert got.route.ai_connection_id == other["id"], mode  # the other connection takes over
    # and with only the sole connection left, the wait names the reason
    edit(app, other["id"], enabled=False)
    got = decide(app, plain["id"], "work")
    assert got.waits and expect in got.wait_reason and "Other is turned off" in got.wait_reason


def test_a_single_connection_wait_names_why_and_that_nobody_else_can_help(app, client, company):
    cid = company["id"]
    c = api_conn(app, client, cid, "Claude plan", use_for="heavy")
    edit(app, c["id"], paused_until=datetime.now(UTC) + timedelta(hours=1), pause_reason="limit")
    founder = agent_of_family(client, cid, "founder")
    got = decide(app, founder["id"], "planning")
    assert got.waits
    assert got.wait_reason.startswith("Claude plan is paused until ")
    assert got.wait_reason.endswith("; no other AI can take heavy work right now")


def test_an_unregistered_runtime_is_not_a_candidate(app, client, company):
    cid = company["id"]
    api_conn(app, client, cid, "Only")
    plain = worker_agent(client, cid)
    got = decide(app, plain["id"], "work", runtime_ok=lambda runtime: False)
    assert got.waits and "company_agent runtime is not available" in got.wait_reason


# ------------------------------------------------------------------ pins and the agents own runtime


def test_owner_pins_and_other_runtimes_bypass_the_stack(app, client, company, scheduler, worker):
    cid = company["id"]
    loose_policy(client, cid)
    api_conn(app, client, cid, "Stack")
    own = provider(client, cid, "Own")
    pinned = worker_agent(
        client, cid, name="Pia", model_provider=own["id"], model_name="m",
        runtime_config={"pin_model": True, "script": {"summary": "p"}},
    )  # fmt: skip
    scripted_agent = new_agent(client, cid, name="Sid")  # runtime "scripted"
    t1 = new_task(client, cid, assignee_agent_id=pinned["id"])
    t2 = new_task(client, cid, assignee_agent_id=scripted_agent["id"])
    settle(scheduler, worker)
    by_task = {r.task_id: r for r in runs_of(app)}
    assert by_task[t1["id"]].model_provider_id == own["id"]
    assert by_task[t1["id"]].ai_connection_id is None
    assert by_task[t1["id"]].routing_reason.startswith("pinned by the owner")
    assert by_task[t2["id"]].runtime == "scripted" and by_task[t2["id"]].ai_connection_id is None
    assert get_task(client, cid, t1["id"])["status"] == "done"


# ------------------------------------------------------------------ the dispatcher


def test_a_run_records_the_connection_provider_model_and_reason(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    conn = api_conn(app, client, cid, "Local brain")
    clear_legacy(app, cid)
    agent = worker_agent(client, cid, script={"summary": "done"})  # no provider of its own
    assert agent["model_provider"] is None
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    [run] = runs_of(app)
    assert run.ai_connection_id == conn["id"] and run.runtime == "company_agent"
    assert run.model_provider_id == conn["model_provider_id"] and run.model_used == "qwen2.5:7b"
    assert run.routing_reason == "AI stack: Local brain (primary, Normal) for everyday work"
    assert get_task(client, cid, task["id"])["status"] == "done"
    # the run API and the finance rows show the connection
    items = client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"]
    assert items[0]["ai_connection_id"] == conn["id"]
    rows = client.get(api(cid, "/finance/runs")).json()["items"]
    assert rows[0]["ai_connection_id"] == conn["id"] and rows[0]["ai_connection"] == "Local brain"
    # a free (local) connection reserves nothing; the adapter resolves the provider from the run alone
    with app.state.session_factory() as db:
        assert policy.load_policy(db, cid).default_model_provider_id is None
        resolved = model_providers.resolve_for_agent(
            db,
            db.get(Agent, agent["id"]),
            provider_id=run.model_provider_id,
            model_name=run.model_used,
        )
        assert resolved.provider_id == conn["model_provider_id"] and resolved.model == "qwen2.5:7b"


def test_shares_decide_the_split_across_real_dispatches(app, client, company, scheduler, worker):
    cid = company["id"]
    loose_policy(client, cid)
    a = api_conn(app, client, cid, "A", share=3)
    b = api_conn(app, client, cid, "B", share=1)
    for i in range(4):
        agent = worker_agent(client, cid, name=f"W{i}", script={"summary": "ok"})
        new_task(client, cid, title=f"job {i}", assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    counts = Counter(r.ai_connection_id for r in runs_of(app))
    assert counts == {a["id"]: 3, b["id"]: 1}


def test_a_cursor_plan_takes_one_run_at_a_time(app, client, company, scheduler, worker, bin_dir):
    cid = company["id"]
    loose_policy(client, cid)
    install_fake_cli(bin_dir, "agent", CURSOR_OK)
    cursor = add_connection(app, client, cid, "cursor_plan", use_for="everyday")
    api_conn(app, client, cid, "Heavy", use_for="heavy")
    ids = []
    for i in range(2):
        agent = worker_agent(client, cid, name=f"W{i}", script={"summary": "ok"})
        ids.append(new_task(client, cid, title=f"job {i}", assignee_agent_id=agent["id"])["id"])
    first = scheduler.tick().dispatch
    assert len(first.queued_run_ids) == 1 and first.waiting_for_runtime == 1
    [waiting] = first.waiting_reasons.values()
    assert "is busy" in waiting and cursor["label"] in waiting
    worker.drain()  # the first run finishes ...
    second = scheduler.tick().dispatch
    assert len(second.queued_run_ids) == 1  # ... and the second may start
    assert {r.ai_connection_id for r in runs_of(app)} == {cursor["id"]}


def test_a_task_waits_with_a_reason_and_resumes_when_a_connection_becomes_usable(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    conn = api_conn(app, client, cid, "Solo")
    edit(app, conn["id"], paused_until=datetime.now(UTC) + timedelta(hours=1), pause_reason="limit")
    agent = worker_agent(client, cid, script={"summary": "ok"})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    report = scheduler.tick().dispatch
    assert report.queued_run_ids == [] and report.waiting_for_runtime == 1
    reason = report.waiting_reasons[task["id"]]
    assert reason.startswith("Solo is paused until ") and "no other AI can take" in reason
    assert get_task(client, cid, task["id"])["status"] == "ready"  # waiting, not failed
    edit(app, conn["id"], paused_until=None, pause_reason=None)
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert runs_of(app)[0].ai_connection_id == conn["id"]


def test_the_provider_allow_list_applies_to_stack_connections(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    blocked = api_conn(app, client, cid, "Blocked", share=4)
    allowed = api_conn(app, client, cid, "Allowed", share=1)
    set_policy(client, cid, allowed_model_providers=[allowed["model_provider_id"]])
    agent = worker_agent(client, cid, script={"summary": "ok"})
    new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    [run] = runs_of(app)
    assert run.ai_connection_id == allowed["id"] and blocked["id"] != run.ai_connection_id


# ------------------------------------------------------------------ CLI connections


def complete_when_it_ran(monkeypatch):
    """The fake CLI cannot call the MCP tools; stand in for its ``complete_task`` call."""

    def _task_open(self, task_id):
        with self._session_factory() as db:
            db.execute(update(Task).where(Task.id == task_id).values(status=TaskStatus.DONE))
            db.commit()
        return False

    monkeypatch.setattr(CliAdapter, "_task_open", _task_open)


def test_a_cli_connection_run_uses_that_connections_key(
    app, client, company, scheduler, worker, bin_dir, monkeypatch
):
    cid = company["id"]
    loose_policy(client, cid)
    install_fake_cli(bin_dir, "codex", CODEX_OK)
    register_cli_adapters(app)
    complete_when_it_ran(monkeypatch)
    api_conn(app, client, cid, "Heavy", use_for="heavy")
    k1 = client.post(api(cid, "/credentials"), json={"name": "k1", "value": KEY}).json()
    k2 = client.post(api(cid, "/credentials"), json={"name": "k2", "value": KEY + "-two"}).json()
    c1 = add_connection(app, client, cid, "codex_api", credential_id=k1["id"], use_for="everyday")
    c2 = add_connection(app, client, cid, "codex_api", credential_id=k2["id"], use_for="everyday")
    seen = {}
    for i, conn in enumerate((c1, c2)):
        agent = worker_agent(client, cid, name=f"W{i}")
        task = new_task(client, cid, title=f"job {i}", assignee_agent_id=agent["id"])
        settle(scheduler, worker)
        assert get_task(client, cid, task["id"])["status"] == "done"
        run = next(r for r in runs_of(app) if r.task_id == task["id"])
        assert run.ai_connection_id == conn["id"] and run.runtime == "cli:codex"
        assert run.routing_reason.endswith("for everyday work")
        seen[conn["id"]] = record_of(bin_dir, "codex")["env"]["CODEX_API_KEY"]
    assert seen == {c1["id"]: KEY, c2["id"]: KEY + "-two"}


@pytest.fixture
def real_runtime(app, monkeypatch):
    from app.core.config import REPO_DIR

    python = REPO_DIR / "runtime" / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip("runtime/.venv is not installed")
    monkeypatch.setattr(app.state.settings, "runtime_dir", REPO_DIR / "runtime")
    monkeypatch.setattr(app.state.settings, "runtime_python", python)


def anthropic_connection(app, client, cid, **body) -> dict:
    cred = client.post(api(cid, "/credentials"), json={"name": "anthropic", "value": KEY}).json()
    p = provider(
        client, cid, "Anthropic", kind="anthropic", credential_id=cred["id"],
        default_model="claude-sonnet-4-5",
    )  # fmt: skip
    return add_connection(
        app,
        client,
        cid,
        "anthropic_api",
        model_provider_id=p["id"],
        model="claude-sonnet-4-5",
        **body,
    )


def test_a_claude_plan_usage_limit_moves_the_next_run_to_the_anthropic_api(
    app, client, company, scheduler, worker, bin_dir, real_runtime, monkeypatch
):
    cid = company["id"]
    loose_policy(client, cid)
    install_fake_cli(
        bin_dir, "claude",
        [{"type": "result", "subtype": "success", "is_error": True, "result": LIMIT_TEXT, "usage": {}}],
        exit=1,
    )  # fmt: skip
    register_cli_adapters(app)
    plan = add_connection(app, client, cid, "claude_plan")  # primary
    api_c = anthropic_connection(app, client, cid)  # secondary, same share
    founder = agent_of_family(client, cid, "founder")
    first = new_task(
        client, cid, title="Plan one", kind="planning", assignee_agent_id=founder["id"]
    )
    scheduler.tick()
    worker.drain()
    [run1] = runs_of(app)
    assert run1.ai_connection_id == plan["id"] and run1.runtime == "cli:claude_code"
    got = get_task(client, cid, first["id"])
    assert got["status"] == "ready" and got["attempt_count"] == 0  # transient: refunded
    with app.state.session_factory() as db:
        row = db.get(AiConnection, plan["id"])
        assert row.paused_until is not None and row.pause_reason
        assert len(list(db.scalars(select(Alert).where(Alert.kind == "claude_plan_limit")))) == 1
        db.execute(update(Task).where(Task.id == first["id"]).values(next_attempt_at=None))
        db.commit()
    # the paused plan is no longer a candidate: the retry and new work go to the Anthropic API
    second = new_task(
        client, cid, title="Plan two", kind="planning", assignee_agent_id=founder["id"]
    )
    settle(scheduler, worker)
    done = [r for r in runs_of(app) if r.ai_connection_id == api_c["id"]]
    assert {r.task_id for r in done} == {first["id"], second["id"]}
    assert {r.runtime for r in done} == {"company_agent"}
    assert all(r.model_used == "claude-sonnet-4-5" and r.model_provider_id for r in done)
    assert "Anthropic" in done[0].routing_reason
    assert get_task(client, cid, first["id"])["status"] == "done"
    # the plan comes back at its reset time: it is a candidate again, and a success clears the alert
    edit(app, plan["id"], paused_until=datetime(2020, 1, 1, tzinfo=UTC))
    install_fake_cli(bin_dir, "claude", OK)
    complete_when_it_ran(monkeypatch)
    founder_again = new_task(
        client, cid, title="Plan three", kind="planning", assignee_agent_id=founder["id"]
    )
    settle(scheduler, worker)
    last = next(r for r in runs_of(app) if r.task_id == founder_again["id"])
    assert last.ai_connection_id == plan["id"] and last.status == "succeeded"
    with app.state.session_factory() as db:
        assert db.get(AiConnection, plan["id"]).pause_reason is None
        assert all(
            a.status != "open"
            for a in db.scalars(select(Alert).where(Alert.kind == "claude_plan_limit"))
        )


@pytest.mark.ai_gate
def test_planning_and_work_complete_on_a_claude_plan_plus_anthropic_api_stack(
    app, client, company, scheduler, worker, bin_dir, real_runtime, monkeypatch
):
    """The gate is on, and no legacy setting is used: [Claude plan: heavy, Anthropic API: everyday]."""
    cid = company["id"]
    loose_policy(client, cid)
    install_fake_cli(bin_dir, "claude", OK)
    register_cli_adapters(app)
    complete_when_it_ran(monkeypatch)
    plan = add_connection(app, client, cid, "claude_plan", use_for="heavy")
    api_c = anthropic_connection(app, client, cid, use_for="everyday")
    clear_legacy(app, cid)
    stack = client.get(api(cid, "/ai-stack")).json()
    assert stack["ready"] is True
    # zero legacy policy fields
    pol = client.get(api(cid, "/policy")).json()
    assert pol["work_mode"] == "api" and pol["default_model_provider_id"] is None
    assert not pol.get("work_runtime") and not pol.get("work_model_provider_id")
    assert pol["ai_engine"] == "api"
    founder = agent_of_family(client, cid, "founder")
    specialist = worker_agent(client, cid, name="Sam", script={"summary": "work done"})
    planning = new_task(
        client, cid, title="Plan the quarter", kind="planning", assignee_agent_id=founder["id"]
    )
    work = new_task(client, cid, title="Write the copy", assignee_agent_id=specialist["id"])
    settle(scheduler, worker)
    assert get_task(client, cid, planning["id"])["status"] == "done"
    assert get_task(client, cid, work["id"])["status"] == "done"
    by_task = {r.task_id: r for r in runs_of(app)}
    p, w = by_task[planning["id"]], by_task[work["id"]]
    assert (p.runtime, p.ai_connection_id) == ("cli:claude_code", plan["id"])
    assert p.routing_reason == "AI stack: Claude Code (primary, Normal) for heavy work"
    assert (w.runtime, w.ai_connection_id) == ("company_agent", api_c["id"])
    assert w.routing_reason == "AI stack: Anthropic (secondary, Normal) for everyday work"
    assert w.model_used == "claude-sonnet-4-5"
    with app.state.session_factory() as db:
        assert db.get(ModelProvider, w.model_provider_id).kind == "anthropic"
