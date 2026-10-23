"""Deterministic skill-gap detection in the dispatcher: assign by tags, hire when nobody fits."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.work import Task
from tests.helpers import (
    agent_of_family,
    api,
    get_task,
    new_agent,
    new_task,
    seed_library,
    skills_by_slug,
)
from tests.test_agent_api import start_run


@pytest.fixture
def skills_env_override(fake_skills_env):
    return fake_skills_env


@pytest.fixture
def world(client, company):
    cid = company["id"]
    seed_library(client, cid)
    return cid


def skilled(client, cid, name, *slugs, **kw):
    agent = new_agent(client, cid, name=name, **kw)
    if slugs:
        r = client.post(api(cid, f"/agents/{agent['id']}/skills"), json={"slugs": list(slugs)})
        assert r.status_code == 200, r.text
    return agent


def hiring_tasks(client, cid):
    return client.get(api(cid, "/tasks"), params={"kind": "hiring"}).json()["items"]


def actions(app, cid):
    with app.state.session_factory() as db:
        return [
            e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid))
        ]


def test_task_is_assigned_to_an_agent_whose_skill_tags_cover_the_requirement(
    app, client, scheduler, world
):
    cid = world
    ada = skilled(client, cid, "Ada", "spike", "xlsx")
    skilled(client, cid, "Bea", "xlsx")
    task = new_task(client, cid, title="Prototype", required_skills=["Spike", "xlsx"])
    assert task["assignee_agent_id"] is None and task["status"] == "backlog"
    scheduler.tick()
    got = get_task(client, cid, task["id"])
    assert got["assignee_agent_id"] == ada["id"] and got["status"] in ("ready", "in_progress")
    assert got["blocked_reason"] is None
    assert hiring_tasks(client, cid) == []
    assert "task.assigned" in actions(app, cid)


def test_least_loaded_wins_then_the_oldest_agent(client, scheduler, world):
    cid = world
    ada = skilled(client, cid, "Ada", "spike")
    bea = skilled(client, cid, "Bea", "spike")
    cy = skilled(client, cid, "Cy", "spike")
    for _ in range(2):
        new_task(client, cid, title="Busy", assignee_agent_id=ada["id"])
    new_task(client, cid, title="Busy", assignee_agent_id=bea["id"])
    first = new_task(client, cid, title="One", required_skills=["spike"])
    scheduler.tick()
    assert get_task(client, cid, first["id"])["assignee_agent_id"] == cy["id"]  # load 0
    # Cy now has one task; Bea has one, Ada two: the older of the equally loaded wins
    second = new_task(client, cid, title="Two", required_skills=["spike"])
    scheduler.tick()
    assert get_task(client, cid, second["id"])["assignee_agent_id"] == bea["id"]


def test_same_department_as_the_project_lead_is_preferred(client, scheduler, world):
    cid = world
    dept = client.post(api(cid, "/departments"), json={"name": "Platform"}).json()
    away = skilled(client, cid, "Away", "spike")
    home = skilled(client, cid, "Home", "spike", department_id=dept["id"])
    lead = new_agent(client, cid, name="Lead", department_id=dept["id"])
    project = client.post(
        api(cid, "/projects"), json={"name": "P", "lead_agent_id": lead["id"]}
    ).json()
    new_task(client, cid, title="Busy", assignee_agent_id=home["id"])  # home is more loaded
    task = new_task(client, cid, title="Do", required_skills=["spike"], project_id=project["id"])
    scheduler.tick()
    assert get_task(client, cid, task["id"])["assignee_agent_id"] == home["id"] != away["id"]


def test_no_match_creates_the_hiring_request_once_and_parks_the_task(app, client, scheduler, world):
    cid = world
    hr = agent_of_family(client, cid, "hr")
    skilled(client, cid, "Ada", "spike")
    skilled(client, cid, "Bea", "xlsx")
    task = new_task(client, cid, title="Needs both", required_skills=["spike", "xlsx"])
    for _ in range(3):
        scheduler.tick()
    got = get_task(client, cid, task["id"])
    assert got["status"] == "backlog" and got["assignee_agent_id"] is None
    assert got["blocked_reason"] == "waiting_for_hire"
    [hiring] = hiring_tasks(client, cid)
    assert hiring["assignee_agent_id"] == hr["id"] and hiring["required_skills"] == []
    assert hiring["created_by_type"] == "system" and hiring["priority"] == task["priority"]
    assert "spike, xlsx" in hiring["description"] and task["id"] in hiring["description"]
    assert actions(app, cid).count("hire.requested") == 1


def test_a_hire_with_matching_skills_picks_up_the_waiting_task(app, client, scheduler, world):
    cid = world
    founder = agent_of_family(client, cid, "founder")
    task = new_task(client, cid, title="Model it", required_skills=["xlsx"], priority="high")
    scheduler.tick()
    assert get_task(client, cid, task["id"])["blocked_reason"] == "waiting_for_hire"
    [hiring] = hiring_tasks(client, cid)
    # HR works its hiring task: the new employee is activated within policy
    hr = start_run(app, client, cid, agent_of_family(client, cid, "hr")["id"], task_title="HR")
    hired = hr.post(
        "/hires",
        json={
            "role_template": "finance-analyst",
            "name": "Fin",
            "manager_agent_id": founder["id"],
            "reason": "spreadsheets",
            "task_id": hiring["id"],
        },
    ).json()
    assert hired["auto_activated"] is True
    got = get_task(client, cid, task["id"])
    assert got["assignee_agent_id"] == hired["agent"]["id"]
    assert got["status"] == "ready" and got["blocked_reason"] is None  # without waiting for a tick
    assert len(hiring_tasks(client, cid)) == 1


def test_a_hire_awaiting_approval_assigns_only_after_it_is_approved(app, client, scheduler, world):
    cid = world
    founder = agent_of_family(client, cid, "founder")
    client.patch(api(cid, "/policy"), json={"auto_activate_hires": False})
    task = new_task(client, cid, title="Model it", required_skills=["xlsx"])
    scheduler.tick()
    hr = start_run(app, client, cid, agent_of_family(client, cid, "hr")["id"], task_title="HR")
    hired = hr.post(
        "/hires",
        json={
            "role_template": "finance-analyst",
            "name": "Fin",
            "manager_agent_id": founder["id"],
            "reason": "spreadsheets",
            "task_id": hr.task_id,
        },
    ).json()
    assert hired["auto_activated"] is False
    scheduler.tick()
    assert (
        get_task(client, cid, task["id"])["blocked_reason"] == "waiting_for_hire"
    )  # proposed != staff
    client.post(api(cid, f"/approvals/{hired['approval_id']}/approve"))
    got = get_task(client, cid, task["id"])
    assert got["assignee_agent_id"] == hired["agent"]["id"] and got["blocked_reason"] is None


def test_giving_an_existing_agent_the_skill_resolves_the_gap(client, scheduler, world):
    cid = world
    ada = skilled(client, cid, "Ada")
    task = new_task(client, cid, title="Needs xlsx", required_skills=["xlsx"])
    scheduler.tick()
    assert get_task(client, cid, task["id"])["blocked_reason"] == "waiting_for_hire"
    client.post(api(cid, f"/agents/{ada['id']}/skills"), json={"slugs": ["xlsx"]})
    got = get_task(client, cid, task["id"])
    assert got["assignee_agent_id"] == ada["id"] and got["blocked_reason"] is None


def test_only_active_skills_count(client, scheduler, world):
    cid = world
    ada = skilled(client, cid, "Ada", "xlsx")
    client.post(api(cid, f"/skills/{skills_by_slug(client, cid)['xlsx']['id']}/archive"))
    task = new_task(client, cid, title="Needs xlsx", required_skills=["xlsx"])
    scheduler.tick()
    got = get_task(client, cid, task["id"])
    assert got["assignee_agent_id"] is None and got["blocked_reason"] == "waiting_for_hire"
    assert ada["id"]


def test_tasks_with_open_dependencies_are_left_alone(client, scheduler, world):
    cid = world
    skilled(client, cid, "Ada", "spike")
    blocker = new_task(client, cid, title="Blocker")
    task = new_task(
        client, cid, title="Later", required_skills=["nothing-matches"], depends_on=[blocker["id"]]
    )
    scheduler.tick()
    assert get_task(client, cid, task["id"])["blocked_reason"] is None
    assert hiring_tasks(client, cid) == []


def test_unassigned_tasks_without_required_skills_and_assigned_tasks_are_untouched(
    client, scheduler, world
):
    cid = world
    ada = skilled(client, cid, "Ada", "spike")
    plain = new_task(client, cid, title="No skills")
    mine = new_task(
        client, cid, title="Assigned", assignee_agent_id=ada["id"], required_skills=["nothing"]
    )
    scheduler.tick()
    assert get_task(client, cid, plain["id"])["assignee_agent_id"] is None
    assert get_task(client, cid, mine["id"])["assignee_agent_id"] == ada["id"]
    assert hiring_tasks(client, cid) == []


def test_without_an_active_hr_agent_nothing_is_requested_until_it_returns(client, scheduler, world):
    cid = world
    hr = agent_of_family(client, cid, "hr")
    client.post(api(cid, f"/agents/{hr['id']}/pause"))
    task = new_task(client, cid, title="Needs xlsx", required_skills=["xlsx"])
    scheduler.tick()
    got = get_task(client, cid, task["id"])
    assert got["blocked_reason"] is None and hiring_tasks(client, cid) == []
    client.post(api(cid, f"/agents/{hr['id']}/resume"))
    scheduler.tick()
    assert get_task(client, cid, task["id"])["blocked_reason"] == "waiting_for_hire"
    assert len(hiring_tasks(client, cid)) == 1


def test_a_paused_company_does_nothing_and_a_pass_is_idempotent(app, client, scheduler, world):
    cid = world
    skilled(client, cid, "Ada", "spike")
    task = new_task(client, cid, title="Do", required_skills=["spike"])
    client.post(api(cid, "/pause"))
    scheduler.tick()
    assert get_task(client, cid, task["id"])["assignee_agent_id"] is None
    client.post(api(cid, "/resume"))
    scheduler.tick()
    before = len(actions(app, cid))
    scheduler.tick()
    scheduler.tick()
    assert len(actions(app, cid)) == before  # nothing new to decide: no events, no audit noise
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Task.id)).where(Task.kind == "hiring")) == 0


def test_hiring_task_itself_never_triggers_gap_detection(client, scheduler, world):
    cid = world
    task = new_task(client, cid, title="X", required_skills=["nothing-matches"])
    for _ in range(3):
        scheduler.tick()
    [hiring] = hiring_tasks(client, cid)
    assert hiring["required_skills"] == [] and task["id"] != hiring["id"]
