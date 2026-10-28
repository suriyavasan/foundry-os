"""M12 WP2: dispatch rules - retry backoff, reserved management slots, per-agent parallelism for
CLI project work, CLI run reservations, and budget blocks that wake someone."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.engine import dispatcher
from app.main import create_app
from app.models.autonomy import WakeRequest
from app.models.ledger import BudgetReservation
from app.runtimes.registry import RuntimeRegistry
from app.runtimes.scripted import ScriptedRuntime
from tests.autonomy_helpers import scripted_world
from tests.helpers import api, get_task, new_agent, new_task, set_policy, settle
from tests.m10_helpers import (
    CLAUDE_OK,
    add_connection,
    install_fake_cli,
    make_ready,
    register_cli_adapters,
)


class FakeCompanyAgent(ScriptedRuntime):
    id = "company_agent"
    display_name = "Company agent (scripted stand-in)"


class FakeCli(ScriptedRuntime):
    id = "cli:opencode"
    display_name = "OpenCode (scripted stand-in)"


@pytest.fixture
def scripted():
    return FakeCompanyAgent()


@pytest.fixture
def app(make_settings, scripted, skills_env_override):
    application = create_app(
        make_settings(),
        runtime_registry=RuntimeRegistry([scripted, FakeCli(), ScriptedRuntime()]),
        skills_env=skills_env_override,
    )
    application.state.cli_env.use_system_path = False
    return application


def go_hybrid(app, client, cid, tmp_path, binary="opencode", plan=False, ready=None):
    """A stack with the API model for management (heavy) and a coding CLI for work (everyday): OpenCode
    with an API key, or the Claude plan (``plan=True``, a subscription: nothing to reserve)."""
    ready or make_ready(app, client, cid)
    api_conn = client.get(api(cid, "/ai-stack")).json()["items"][0]
    r = client.patch(api(cid, f"/ai-stack/{api_conn['id']}"), json={"use_for": "heavy"})
    assert r.status_code == 200, r.text
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    app.state.cli_env.extra_dirs.append(d)
    install_fake_cli(d, binary, CLAUDE_OK if binary == "claude" else [])
    if plan:
        add_connection(app, client, cid, "claude_plan", use_for="everyday")
        return d
    cred = client.post(
        api(cid, "/credentials"), json={"name": "k", "value": "sk-test-0123456789abcdef"}
    )
    assert cred.status_code == 201, cred.text
    add_connection(
        app, client, cid, "opencode", credential_id=cred.json()["id"], model="anthropic/claude-x",
        use_for="everyday",
    )  # fmt: skip
    return d


def worker_agent(client, cid, **kw):
    return new_agent(client, cid, runtime="company_agent", script={"summary": "done"}, **kw)


def project(client, cid, name="Site", **kw):
    r = client.post(api(cid, "/projects"), json={"name": name, "status": "active", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def queued(report) -> int:
    return len(report.dispatch.queued_run_ids)


# ------------------------------------------------------------------ reserved management slots


def test_effective_reserved_slots_is_clamped_and_never_negative():
    def limits(reserved, cap):
        return SimpleNamespace(management_reserved_slots=reserved, max_concurrent_runs=cap)

    assert dispatcher.effective_reserved_slots(limits(1, 2)) == 1
    assert dispatcher.effective_reserved_slots(limits(5, 2)) == 1  # never all of the pool
    assert dispatcher.effective_reserved_slots(limits(1, 1)) == 0  # a pool of one stays usable
    assert dispatcher.effective_reserved_slots(limits(0, 4)) == 0
    assert dispatcher.effective_reserved_slots(limits(-3, 4)) == 0


def test_a_reserved_slot_lets_a_planning_task_run_while_work_saturates_the_pool(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=2, management_reserved_slots=1)
    workers = [new_agent(client, cid, name=f"W{i}") for i in range(2)]
    planner = new_agent(client, cid, name="Planner")
    w1 = new_task(client, cid, title="work one", assignee_agent_id=workers[0]["id"])
    w2 = new_task(client, cid, title="work two", assignee_agent_id=workers[1]["id"])
    plan = new_task(client, cid, title="planning", assignee_agent_id=planner["id"], kind="planning")
    report = scheduler.tick()
    assert queued(report) == 2
    assert get_task(client, cid, w1["id"])["status"] == "in_progress"  # work took the open slot
    assert get_task(client, cid, w2["id"])["status"] == "ready"  # the second was held back ...
    assert get_task(client, cid, plan["id"])["status"] == "in_progress"  # ... for management
    assert scheduler.tick().dispatch.queued_run_ids == []
    settle(scheduler, worker)
    assert get_task(client, cid, w2["id"])["status"] == "done"


def test_work_never_uses_a_reserved_slot_even_when_no_management_task_waits(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=3, management_reserved_slots=2)
    for i in range(3):
        a = new_agent(client, cid, name=f"W{i}")
        new_task(client, cid, title=f"w{i}", assignee_agent_id=a["id"])
    assert queued(scheduler.tick()) == 1  # 3 slots - 2 reserved


def test_a_management_roles_work_task_may_use_the_reserved_slot(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=2, management_reserved_slots=1)
    plain = new_agent(client, cid, name="Plain")
    lead = new_agent(client, cid, name="Lena", kind="lead")
    new_task(client, cid, title="plain work", assignee_agent_id=plain["id"], priority="high")
    lead_work = new_task(client, cid, title="lead work", assignee_agent_id=lead["id"])
    extra = new_agent(client, cid, name="Extra")
    held = new_task(client, cid, title="more work", assignee_agent_id=extra["id"])
    assert queued(scheduler.tick()) == 2
    assert get_task(client, cid, lead_work["id"])["status"] == "in_progress"
    assert get_task(client, cid, held["id"])["status"] == "ready"


def test_a_pool_of_one_still_runs_work(client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, max_concurrent_runs=1)  # management_reserved_slots stays 1 -> clamped
    a = new_agent(client, cid)
    t = new_task(client, cid, assignee_agent_id=a["id"])
    settle(scheduler, worker)
    assert get_task(client, cid, t["id"])["status"] == "done"


# ------------------------------------------------------------------ per-agent parallelism


def test_cli_project_work_may_run_in_parallel_per_agent(
    app, client, company, scheduler, worker, tmp_path
):
    cid = company["id"]
    go_hybrid(app, client, cid, tmp_path)
    set_policy(
        client,
        cid,
        max_concurrent_runs=6,
        management_reserved_slots=0,
        max_parallel_runs_per_agent=2,
    )
    agent = worker_agent(client, cid)
    proj = project(client, cid)
    for i in range(3):
        new_task(
            client,
            cid,
            title=f"project job {i}",
            assignee_agent_id=agent["id"],
            project_id=proj["id"],
        )
    assert queued(scheduler.tick()) == 2  # capped at max_parallel_runs_per_agent
    assert queued(scheduler.tick()) == 0
    settle(scheduler, worker)
    runtimes = {r.runtime for r in _runs(app)}
    assert runtimes == {"cli:opencode"}


def _runs(app):
    from app.models.run import TaskRun

    with app.state.session_factory() as db:
        return list(db.scalars(select(TaskRun)))


def test_work_outside_a_project_or_on_the_api_runtime_stays_one_run_per_agent(
    app, client, company, scheduler, worker, tmp_path
):
    cid = company["id"]
    set_policy(
        client,
        cid,
        max_concurrent_runs=6,
        management_reserved_slots=0,
        max_parallel_runs_per_agent=3,
    )
    # API mode: project tasks still run one at a time per agent
    ready = make_ready(app, client, cid)
    api_agent = worker_agent(client, cid, name="ApiWorker")
    proj = project(client, cid)
    for i in range(2):
        new_task(
            client,
            cid,
            title=f"api job {i}",
            assignee_agent_id=api_agent["id"],
            project_id=proj["id"],
        )
    assert queued(scheduler.tick()) == 1
    settle(scheduler, worker)
    # hybrid: CLI work without a project is single-flight too
    go_hybrid(app, client, cid, tmp_path, ready=ready)
    cli_agent = worker_agent(client, cid, name="CliWorker")
    for i in range(3):
        new_task(client, cid, title=f"loose job {i}", assignee_agent_id=cli_agent["id"])
    assert queued(scheduler.tick()) == 1


# ------------------------------------------------------------------ CLI reservation


def test_a_cli_run_reserves_the_cli_amount(app, client, company, scheduler, worker, tmp_path):
    cid = company["id"]
    go_hybrid(app, client, cid, tmp_path)
    agent = worker_agent(client, cid)
    task = new_task(client, cid, title="Write the brief", assignee_agent_id=agent["id"])
    assert queued(scheduler.tick()) == 1
    with app.state.session_factory() as db:
        [reservation] = db.scalars(select(BudgetReservation)).all()
        assert reservation.amount_micros == 1_000_000  # policy.cli_run_reservation_micros
    worker.drain()
    assert get_task(client, cid, task["id"])["attempt_count"] >= 1


def test_a_plan_connection_is_a_subscription_and_reserves_nothing(
    app, client, company, scheduler, worker, tmp_path
):
    register_cli_adapters(app)
    cid = company["id"]
    go_hybrid(app, client, cid, tmp_path, binary="claude", plan=True)
    set_policy(client, cid, max_concurrent_runs=4, management_reserved_slots=0)
    agent = worker_agent(client, cid)
    new_task(client, cid, title="Write the brief", assignee_agent_id=agent["id"])
    new_task(
        client, cid, title="Own budget", assignee_agent_id=worker_agent(client, cid, name="B")["id"],
        budget_micros=300_000,
    )  # fmt: skip
    assert queued(scheduler.tick()) == 2
    with app.state.session_factory() as db:
        assert [r.amount_micros for r in db.scalars(select(BudgetReservation))] == [0, 0]
        runs = _runs(app)
        assert {r.runtime for r in runs} == {"cli:claude_code"}


def test_the_cli_reservation_follows_the_policy_and_an_explicit_task_budget_wins(
    app, client, company, scheduler, worker, tmp_path
):
    cid = company["id"]
    go_hybrid(app, client, cid, tmp_path)
    set_policy(client, cid, cli_run_reservation_micros=2_500_000, max_concurrent_runs=4)
    a, b = worker_agent(client, cid, name="A"), worker_agent(client, cid, name="B")
    new_task(client, cid, title="default cap", assignee_agent_id=a["id"])
    new_task(client, cid, title="own budget", assignee_agent_id=b["id"], budget_micros=300_000)
    scheduler.tick()
    with app.state.session_factory() as db:
        amounts = sorted(r.amount_micros for r in db.scalars(select(BudgetReservation)))
    assert amounts == [300_000, 2_500_000]
    worker.drain()


def test_an_api_run_keeps_the_small_default_reservation(client, company, scheduler, app):
    cid = company["id"]
    a = new_agent(client, cid)
    new_task(client, cid, assignee_agent_id=a["id"])
    scheduler.tick()
    with app.state.session_factory() as db:
        [reservation] = db.scalars(select(BudgetReservation)).all()
    assert reservation.amount_micros == 50_000


# ------------------------------------------------------------------ budget blocks wake someone


def wake_reasons(app, agent_id):
    with app.state.session_factory() as db:
        reqs = list(db.scalars(select(WakeRequest).where(WakeRequest.agent_id == agent_id)))
    return [r for req in reqs for r in req.reasons or [] if r.get("trigger") == "budget"]


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def starve(client, cid, agent_id):
    r = client.patch(api(cid, f"/agents/{agent_id}"), json={"daily_budget_micros": 1_000})
    assert r.status_code == 200, r.text


def test_an_agent_budget_block_wakes_the_project_lead(app, client, w, scheduler):
    cid = w.cid
    proj = project(client, cid, lead_agent_id=w.lead["id"])
    starve(client, cid, w.specialist["id"])
    task = new_task(
        client,
        cid,
        title="Blocked job",
        assignee_agent_id=w.specialist["id"],
        project_id=proj["id"],
    )
    report = scheduler.tick()
    assert report.dispatch.budget_blocked_task_ids == [task["id"]]
    [reason] = wake_reasons(app, w.lead["id"])
    assert reason["trigger"] == "budget" and reason["ref_id"] == task["id"]
    assert "agent_daily" in reason["text"] and "cannot raise budgets" in reason["text"]
    assert wake_reasons(app, w.founder["id"]) == []
    assert wake_reasons(app, w.specialist["id"]) == []  # not the starved agent itself


def test_without_a_project_lead_the_agents_manager_is_woken(app, client, w, scheduler):
    cid = w.cid
    starve(client, cid, w.specialist["id"])
    task = new_task(client, cid, title="Loose job", assignee_agent_id=w.specialist["id"])
    scheduler.tick()
    [reason] = wake_reasons(app, w.lead["id"])  # Sam's manager is Lena
    assert reason["trigger"] == "budget" and reason["ref_id"] == task["id"]


def test_without_lead_or_manager_the_cofounder_is_woken(app, client, w, scheduler):
    cid = w.cid
    loner = new_agent(client, cid, name="Loner", runtime="scripted", daily_budget_micros=1_000)
    client.patch(api(cid, f"/agents/{loner['id']}"), json={"manager_agent_id": None})
    task = new_task(client, cid, title="Lonely job", assignee_agent_id=loner["id"])
    scheduler.tick()
    [reason] = wake_reasons(app, w.founder["id"])
    assert reason["trigger"] == "budget" and reason["ref_id"] == task["id"]


def test_a_company_wide_budget_block_wakes_nobody(app, client, w, scheduler):
    cid = w.cid
    set_policy(client, cid, daily_budget_micros=10)
    new_task(client, cid, title="Any job", assignee_agent_id=w.specialist["id"])
    scheduler.tick()
    for agent in (w.lead, w.founder, w.specialist):
        assert wake_reasons(app, agent["id"]) == []


# ------------------------------------------------------------------ retry backoff


def test_a_task_waiting_for_its_backoff_is_skipped_but_others_run(
    app, client, company, scheduler, worker, clock
):
    from datetime import timedelta

    from app.models.work import Task

    cid = company["id"]
    a, b = new_agent(client, cid, name="A"), new_agent(client, cid, name="B")
    waiting = new_task(client, cid, title="waiting", assignee_agent_id=a["id"])
    other = new_task(client, cid, title="other", assignee_agent_id=b["id"])
    with app.state.session_factory() as db:
        db.get(Task, waiting["id"]).next_attempt_at = clock() + timedelta(seconds=90)
        db.commit()
    report = scheduler.tick()
    assert queued(report) == 1
    worker.drain()
    assert get_task(client, cid, waiting["id"])["status"] == "ready"
    assert get_task(client, cid, other["id"])["status"] == "done"
    clock.advance(90)
    settle(scheduler, worker)
    assert get_task(client, cid, waiting["id"])["status"] == "done"
