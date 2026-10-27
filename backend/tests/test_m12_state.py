"""M12 (WP3): the agent-facing state. Open projects first and recently ended ones next (so new
projects are never hidden behind old ones), goals carry project counts, small or local models get a
compact profile, and agents see the planning caps."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.time import utcnow
from app.models.agent import Agent
from app.models.provider import ModelProvider
from app.models.run import TaskRun
from app.models.work import Project
from tests.helpers import agent_of_family, api, new_agent, new_task, set_policy
from tests.test_agent_api import start_run


def founder_state(app, client, cid, **task_fields) -> dict:
    founder = agent_of_family(client, cid, "founder")
    session = start_run(
        app, client, cid, founder["id"], task_title="plan", kind="planning", **task_fields
    )
    r = session.get("/state")
    assert r.status_code == 200, r.text
    return r.json()


def project(client, cid, name, **kw) -> dict:
    r = client.post(api(cid, "/projects"), json={"name": name, "status": "active", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def age(app, project_id: str, *, days: float, status: str | None = None) -> None:
    """Make a project look ``days`` old (created and last updated)."""
    when = utcnow() - timedelta(days=days)
    values = {"created_at": when, "updated_at": when}
    if status:
        values["status"] = status
    with app.state.session_factory() as db:
        db.execute(update(Project).where(Project.id == project_id).values(**values))
        db.commit()


# ------------------------------------------------------------------ projects


def test_a_new_project_is_listed_after_thirty_old_ones(app, client, company):
    cid = company["id"]
    old = [project(client, cid, f"Old {i}") for i in range(30)]
    for i, p in enumerate(old):
        age(app, p["id"], days=60 - i)  # all open, all older than the new one
    new = project(client, cid, "Brand new")
    names = [p["name"] for p in founder_state(app, client, cid)["projects"]]
    assert len(names) == 25
    assert names[0] == "Brand new"  # newest open project first
    assert "Old 29" in names and "Old 0" not in names  # the oldest are what falls off
    assert new["id"]


def test_open_projects_come_first_then_projects_ended_in_the_last_seven_days(app, client, company):
    cid = company["id"]
    done_recent = project(client, cid, "Done yesterday")
    done_old = project(client, cid, "Done last month")
    cancelled_recent = project(client, cid, "Cancelled today")
    open_one = project(client, cid, "Open")
    paused = project(client, cid, "Paused")
    client.patch(api(cid, f"/projects/{paused['id']}"), json={"status": "paused"})
    planned = client.post(api(cid, "/projects"), json={"name": "Planned"}).json()
    for p, status, days in (
        (done_recent, "completed", 1),
        (done_old, "completed", 30),
        (cancelled_recent, "cancelled", 0.1),
    ):
        age(app, p["id"], days=days, status=status)
    projects = founder_state(app, client, cid)["projects"]
    names = [p["name"] for p in projects]
    assert "Done last month" not in names
    assert {p["status"] for p in projects[:3]} == {"planned", "active", "paused"}
    assert {p["name"] for p in projects[:3]} == {"Open", "Paused", "Planned"}
    assert names[3:] == ["Cancelled today", "Done yesterday"]  # most recently ended first
    assert open_one["id"] and planned["id"]


def test_ended_projects_fill_only_the_room_the_open_ones_leave(app, client, company):
    cid = company["id"]
    for i in range(24):
        project(client, cid, f"Open {i}")
    for i in range(5):
        p = project(client, cid, f"Ended {i}")
        age(app, p["id"], days=1, status="completed")
    projects = founder_state(app, client, cid)["projects"]
    assert len(projects) == 25
    assert sum(1 for p in projects if p["status"] == "completed") == 1


# ------------------------------------------------------------------ goals


def test_goals_carry_open_and_ended_project_counts(app, client, company):
    cid = company["id"]
    goal = client.post(api(cid, "/goals"), json={"title": "Grow", "status": "active"}).json()
    empty = client.post(api(cid, "/goals"), json={"title": "Idea", "status": "active"}).json()
    a = project(client, cid, "A", goal_id=goal["id"])
    b = project(client, cid, "B", goal_id=goal["id"])
    c = project(client, cid, "C", goal_id=goal["id"])
    client.patch(api(cid, f"/projects/{b['id']}"), json={"status": "completed"})
    client.patch(api(cid, f"/projects/{c['id']}"), json={"status": "cancelled"})
    goals = {g["title"]: g for g in founder_state(app, client, cid)["goals"]}
    assert (goals["Grow"]["open_projects"], goals["Grow"]["ended_projects"]) == (1, 2)
    assert (goals["Idea"]["open_projects"], goals["Idea"]["ended_projects"]) == (0, 0)
    assert a["id"] and empty["id"]


# ------------------------------------------------------------------ the compact profile


@pytest.fixture
def busy_company(app, client, company):
    """20 open tasks and 14 more agents, a long project description."""
    cid = company["id"]
    set_policy(client, cid, max_headcount=40, max_open_tasks=500)
    for i in range(14):
        new_agent(client, cid, name=f"Agent {i}")
    for i in range(20):
        new_task(client, cid, title=f"Task {i}")
    project(client, cid, "Long", description="word " * 100)
    return cid


def providers(client, cid):
    cloud = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "Cloud",
            "kind": "openai",
            "credential_id": client.post(
                api(cid, "/credentials"), json={"name": "k", "value": "sk-cloud-0123456789"}
            ).json()["id"],
            "default_model": "big-model",
            "input_price_per_mtok_micros": 1_000_000,
            "output_price_per_mtok_micros": 5_000_000,
        },
    ).json()
    local = client.post(
        api(cid, "/model-providers"),
        json={"name": "Local", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    return cloud, local


def state_on(app, client, cid, provider_id: str) -> dict:
    founder = agent_of_family(client, cid, "founder")
    session = start_run(app, client, cid, founder["id"], task_title="plan", kind="planning")
    with app.state.session_factory() as db:
        db.execute(
            update(TaskRun)
            .where(TaskRun.id == session.run_id)
            .values(model_provider_id=provider_id)
        )
        db.commit()
    return session.get("/state").json()


def test_a_cloud_run_gets_the_complete_but_bounded_state(app, client, busy_company):
    cid = busy_company
    cloud, _ = providers(client, cid)
    state = state_on(app, client, cid, cloud["id"])
    assert state["profile"] == "full"
    assert len(state["tasks"]["open"]) >= 20
    assert len(state["agents"]) >= 14
    long = next(p for p in state["projects"] if p["name"] == "Long")
    assert 200 < len(long["description"]) <= 300


def test_a_local_model_gets_the_compact_profile(app, client, busy_company):
    cid = busy_company
    _, local = providers(client, cid)
    state = state_on(app, client, cid, local["id"])
    assert state["profile"] == "compact"
    assert len(state["tasks"]["open"]) == 15
    assert len(state["agents"]) == 12
    long = next(p for p in state["projects"] if p["name"] == "Long")
    assert len(long["description"]) <= 120 and long["description"].endswith("...")


def test_a_small_context_cloud_model_also_gets_the_compact_profile(app, client, busy_company):
    cid = busy_company
    cloud, _ = providers(client, cid)
    with app.state.session_factory() as db:
        db.execute(
            update(ModelProvider)
            .where(ModelProvider.id == cloud["id"])
            .values(context_length=16384)
        )
        db.commit()
    state = state_on(app, client, cid, cloud["id"])
    assert state["profile"] == "compact" and len(state["tasks"]["open"]) == 15


def test_a_run_without_a_routed_provider_uses_the_company_default_model(app, client, busy_company):
    cid = busy_company
    cloud, local = providers(client, cid)
    # the first provider created is the company default, and the Co-founder runs on it: paid cloud
    assert founder_state(app, client, cid)["profile"] == "full"
    founder = agent_of_family(client, cid, "founder")
    with app.state.session_factory() as db:
        db.execute(
            update(Agent).where(Agent.id == founder["id"]).values(model_provider=local["id"])
        )
        db.commit()
    assert founder_state(app, client, cid)["profile"] == "compact"
    # with no model of its own the company default decides
    with app.state.session_factory() as db:
        db.execute(update(Agent).where(Agent.id == founder["id"]).values(model_provider=None))
        db.commit()
    assert founder_state(app, client, cid)["profile"] == "full"
    set_policy(client, cid, default_model_provider_id=local["id"], default_model_name="qwen2.5:7b")
    assert founder_state(app, client, cid)["profile"] == "compact"


def test_a_company_without_providers_gets_the_full_profile(app, client, company):
    assert founder_state(app, client, company["id"])["profile"] == "full"


# ------------------------------------------------------------------ the planning caps


def test_agents_see_the_planning_caps_in_state_and_context(app, client, company):
    cid = company["id"]
    set_policy(client, cid, max_work_tasks_per_project=9, max_subtasks_per_task=3)
    worker = new_agent(client, cid)
    session = start_run(app, client, cid, worker["id"])
    ctx_policy = session.get("/context").json()["policy"]
    assert (ctx_policy["max_work_tasks_per_project"], ctx_policy["max_subtasks_per_task"]) == (9, 3)
    limits = founder_state(app, client, cid)["limits"]
    assert (limits["max_work_tasks_per_project"], limits["max_subtasks_per_task"]) == (9, 3)
