"""M10 section 2: API-only vs hybrid work mode. The dispatcher picks the runtime per run."""

from __future__ import annotations

import pytest

from app.main import create_app
from app.runtimes.registry import RuntimeRegistry
from app.runtimes.scripted import ScriptedRuntime
from tests.helpers import api, get_task, new_agent, new_task, settle
from tests.m10_helpers import install_fake_cli, make_ready


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


def hybrid(app, client, cid, tmp_path):
    p = make_ready(app, client, cid)
    d = tmp_path / "bin"
    app.state.cli_env.extra_dirs.append(d)
    install_fake_cli(d, "opencode", [])
    client.put(
        "/api/cli-runtimes/opencode/auth", json={"mode": "own_login", "model": "anthropic/claude-x"}
    )
    r = client.put(
        api(cid, "/ai-setup"),
        json={
            "management_provider_id": p["id"],
            "management_model": p["default_model"],
            "work_mode": "hybrid",
            "work_runtime": "cli:opencode",
        },
    )
    assert r.status_code == 200, r.text


def run_of(client, cid, task_id) -> dict:
    return client.get(api(cid, f"/tasks/{task_id}/runs")).json()["items"][0]


def worker_agent(client, cid, **kw):
    return new_agent(client, cid, runtime="company_agent", script={"summary": "done"}, **kw)


def test_api_mode_runs_everything_on_the_agents_runtime(app, client, company, scheduler, worker):
    cid = company["id"]
    make_ready(app, client, cid)
    agent = worker_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    run = run_of(client, cid, task["id"])
    assert run["runtime"] == "company_agent" and run["status"] == "succeeded"


def test_hybrid_sends_work_tasks_of_workers_to_the_cli(
    app, client, company, scheduler, worker, tmp_path
):
    cid = company["id"]
    hybrid(app, client, cid, tmp_path)
    agent = worker_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    run = run_of(client, cid, task["id"])
    assert run["runtime"] == "cli:opencode"
    assert run["routing_reason"] and "hybrid: work task on cli:opencode" in run["routing_reason"]
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_management_roles_and_non_work_tasks_stay_on_the_company_agent(
    app, client, company, scheduler, worker, tmp_path
):
    cid = company["id"]
    hybrid(app, client, cid, tmp_path)
    founder = client.get(api(cid, "/overview")).json()["founder"]
    lead = new_agent(
        client, cid, name="Lena", runtime="company_agent", kind="lead", script={"summary": "led"}
    )
    boss = worker_agent(client, cid, name="Boss")
    report = worker_agent(client, cid, name="Report", manager_agent_id=boss["id"])
    cases = {
        "lead work": new_task(client, cid, assignee_agent_id=lead["id"]),
        "manager work": new_task(client, cid, assignee_agent_id=boss["id"]),
        "worker planning": new_task(client, cid, assignee_agent_id=report["id"], kind="planning"),
        "worker review": new_task(client, cid, assignee_agent_id=report["id"], kind="review"),
        "worker routine": new_task(client, cid, assignee_agent_id=report["id"], kind="routine"),
        "worker work": new_task(client, cid, assignee_agent_id=report["id"], kind="work"),
    }
    settle(scheduler, worker, rounds=40)
    runtimes = {name: run_of(client, cid, t["id"])["runtime"] for name, t in cases.items()}
    assert runtimes["worker work"] == "cli:opencode"
    assert {v for k, v in runtimes.items() if k != "worker work"} == {"company_agent"}
    assert founder["kind"] == "founder"


def test_a_per_agent_override_wins(app, client, company, scheduler, worker, tmp_path):
    cid = company["id"]
    hybrid(app, client, cid, tmp_path)
    pinned = worker_agent(client, cid, name="Pinned")
    r = client.patch(
        api(cid, f"/agents/{pinned['id']}"),
        json={"runtime_config": {"script": {"summary": "p"}, "pin_runtime": True}},
    )
    assert r.status_code == 200, r.text
    explicit = new_agent(
        client, cid, name="Explicit", runtime="cli:opencode", script={"summary": "e"}
    )
    t1 = new_task(client, cid, assignee_agent_id=pinned["id"])
    t2 = new_task(client, cid, assignee_agent_id=explicit["id"])
    settle(scheduler, worker, rounds=30)
    assert run_of(client, cid, t1["id"])["runtime"] == "company_agent"
    assert "pinned" in run_of(client, cid, t1["id"])["routing_reason"]
    assert run_of(client, cid, t2["id"])["runtime"] == "cli:opencode"


def test_only_an_owner_can_set_the_override_not_the_agent(app, client, company):
    from tests.test_agent_api import start_run

    cid = company["id"]
    agent = worker_agent(client, cid)
    session = start_run(app, client, cid, agent["id"])
    r = client.patch(
        api(cid, f"/agents/{agent['id']}"),
        json={"runtime": "cli:opencode"},
        headers=session.headers,
    )
    assert r.status_code == 403


def test_when_the_cli_is_unusable_work_waits_instead_of_falling_back(
    app, client, company, scheduler, worker, tmp_path
):
    cid = company["id"]
    hybrid(app, client, cid, tmp_path)
    app.state.runtimes._adapters.pop("cli:opencode")  # the work runtime is gone
    agent = worker_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    report = scheduler.tick()
    assert report.dispatch.waiting_for_runtime >= 1 and report.dispatch.queued_run_ids == []
    assert get_task(client, cid, task["id"])["status"] == "ready"


def test_a_subscription_run_records_unknown_cost_and_charges_nothing(
    app, client, company, scheduler, worker
):
    """own_login CLI runs: the price is unknown (never a made-up number) and no budget is consumed."""
    from sqlalchemy import select

    from app.models.ledger import BudgetReservation, UsageRecord
    from app.models.run import TaskRun

    cid = company["id"]
    make_ready(app, client, cid)
    agent = new_agent(
        client,
        cid,
        runtime="company_agent",
        script={
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_micros": 0,
                "cost_kind": "unknown",
                "billing_note": "subscription",
            }
        },
    )
    new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        run = db.scalars(select(TaskRun)).one()
        assert run.cost_kind == "unknown" and run.cost_micros == 0
        assert db.scalars(select(UsageRecord)).one().cost_micros == 0
        assert db.scalars(select(BudgetReservation)).one().settled_micros == 0


def test_a_cli_on_a_local_provider_reserves_nothing(
    app, client, company, scheduler, worker, tmp_path
):
    from sqlalchemy import select

    from app.models.ledger import BudgetReservation

    cid = company["id"]
    p = make_ready(app, client, cid)
    d = tmp_path / "bin"
    app.state.cli_env.extra_dirs.append(d)
    install_fake_cli(d, "opencode", [])
    client.put(
        "/api/cli-runtimes/opencode/auth",
        json={"mode": "api_key", "model_provider_id": p["id"], "model": "qwen2.5:7b"},
    )
    client.put(
        api(cid, "/ai-setup"),
        json={
            "management_provider_id": p["id"],
            "management_model": "qwen2.5:7b",
            "work_mode": "hybrid",
            "work_runtime": "cli:opencode",
        },
    )
    agent = worker_agent(client, cid)
    new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    with app.state.session_factory() as db:
        assert (
            db.scalars(select(BudgetReservation.amount_micros)).one() == 0
        )  # a local model: nothing to hold


def test_cli_role_families_keep_research_on_the_company_agent_end_to_end(
    app, client, company, scheduler, worker, tmp_path
):
    """M12: with ``cli_role_families`` set, only the listed families go to the CLI; research (it needs the
    web, which a coding CLI does not have) stays on the company agent and the run says why."""
    from tests.guardrail_helpers import set_family

    cid = company["id"]
    hybrid(app, client, cid, tmp_path)
    r = client.patch(api(cid, "/policy"), json={"cli_role_families": ["engineering"]})
    assert r.status_code == 200, r.text
    engineer = worker_agent(client, cid, name="Eli")
    researcher = worker_agent(client, cid, name="Ray")
    set_family(app, engineer["id"], "engineering")
    set_family(app, researcher["id"], "research")
    t_eng = new_task(client, cid, assignee_agent_id=engineer["id"])
    t_res = new_task(client, cid, assignee_agent_id=researcher["id"])
    settle(scheduler, worker, rounds=30)
    eng, res = run_of(client, cid, t_eng["id"]), run_of(client, cid, t_res["id"])
    assert eng["runtime"] == "cli:opencode"
    assert res["runtime"] == "company_agent" and "needs web" in res["routing_reason"]
    assert get_task(client, cid, t_res["id"])["status"] == "done"
