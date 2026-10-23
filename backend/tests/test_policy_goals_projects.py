from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.principal import Principal, PrincipalType
from app.models.policy import CompanyPolicy
from tests.conftest import human
from tests.helpers import api, new_agent


def test_company_creation_also_creates_default_policy(app, client, company):
    p = client.get(api(company["id"], "/policy")).json()
    assert p["autonomy_mode"] == "autonomous"
    assert p["human_approval_required_for"] == ["external_purchase", "budget_increase"]
    assert p["max_concurrent_runs"] == 2 and p["max_delegation_depth"] == 3
    assert p["max_tasks_per_planning_cycle"] == 10 and p["max_attempts_per_task"] == 3
    assert p["max_revisions"] == 2 and p["max_run_seconds"] == 900 and p["max_headcount"] == 12
    assert p["heartbeat_interval_seconds"] == 900
    assert p["default_run_reservation_micros"] == 50_000
    assert p["daily_budget_micros"] is None and p["monthly_budget_micros"] is None
    assert p["allowed_model_providers"] == []
    assert "budget_lock_version" not in p  # internal
    with app.state.session_factory() as db:
        rows = db.scalars(select(CompanyPolicy).where(CompanyPolicy.company_id == company["id"]))
        assert len(list(rows)) == 1


def test_policy_update_is_audited_and_validated(client, company):
    cid = company["id"]
    r = client.patch(
        api(cid, "/policy"),
        json={
            "max_concurrent_runs": 4,
            "daily_budget_micros": 2_000_000,
            "autonomy_mode": "supervised",
        },
    )
    assert r.status_code == 200
    assert r.json()["max_concurrent_runs"] == 4 and r.json()["autonomy_mode"] == "supervised"
    audit = client.get(api(cid, "/activity"), params={"target_type": "policy"}).json()["items"]
    assert audit[0]["action"] == "policy.updated"
    assert sorted(audit[0]["data"]["changed"]) == [
        "autonomy_mode",
        "daily_budget_micros",
        "max_concurrent_runs",
    ]
    # a no-op patch writes no audit event
    client.patch(api(cid, "/policy"), json={"max_concurrent_runs": 4})
    assert (
        len(client.get(api(cid, "/activity"), params={"target_type": "policy"}).json()["items"])
        == 1
    )
    for bad in (
        {"max_concurrent_runs": 0},
        {"max_run_seconds": 0},
        {"heartbeat_interval_seconds": 10},
        {"human_approval_required_for": ["bogus"]},
        {"autonomy_mode": "yolo"},
        {"max_concurrent_runs": None},
        {"unknown_field": 1},
        {"budget_lock_version": 5},
    ):
        assert client.patch(api(cid, "/policy"), json=bad).status_code == 422, bad
    # null clears a budget cap (no limit)
    r = client.patch(api(cid, "/policy"), json={"daily_budget_micros": None})
    assert r.json()["daily_budget_micros"] is None


def test_policy_can_only_be_changed_by_human_admins(
    client, client_as, make_user, add_member, company
):
    cid = company["id"]
    viewer = make_user("viewer@x.test")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    assert vc.get(api(cid, "/policy")).status_code == 200
    assert vc.patch(api(cid, "/policy"), json={"max_headcount": 3}).status_code == 403

    agent = new_agent(client, cid)
    ac = client_as(Principal(PrincipalType.AGENT, agent["id"], "Nova", cid))
    assert ac.get(api(cid, "/policy")).status_code == 200
    r = ac.patch(api(cid, "/policy"), json={"daily_budget_micros": 999_999_999})
    assert r.status_code == 403
    assert client.get(api(cid, "/policy")).json()["daily_budget_micros"] is None


def test_policy_is_tenant_scoped(client, client_as, make_user, company):
    outsider = make_user("out@x.test")
    oc = client_as(human(outsider))
    assert oc.get(api(company["id"], "/policy")).status_code == 404
    assert oc.patch(api(company["id"], "/policy"), json={"max_headcount": 3}).status_code == 404


def test_goals_crud_filters_and_audit(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    g = client.post(
        api(cid, "/goals"),
        json={
            "title": "Reach $1M ARR",
            "priority": "high",
            "owner_agent_id": agent["id"],
            "target_date": "2027-06-30",
        },
    )
    assert g.status_code == 201, g.text
    goal = g.json()
    assert goal["status"] == "proposed" and goal["created_by_type"] == "human"
    assert goal["target_date"] == "2027-06-30"
    other = client.post(api(cid, "/goals"), json={"title": "Second", "status": "active"}).json()

    listed = client.get(api(cid, "/goals")).json()
    assert [x["id"] for x in listed["items"]] == [goal["id"], other["id"]]
    assert [
        x["id"] for x in client.get(api(cid, "/goals"), params={"status": "active"}).json()["items"]
    ] == [other["id"]]
    assert (
        len(client.get(api(cid, "/goals"), params={"owner_agent_id": agent["id"]}).json()["items"])
        == 1
    )

    r = client.patch(
        api(cid, f"/goals/{goal['id']}"), json={"status": "active", "title": "Renamed"}
    )
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.patch(api(cid, f"/goals/{goal['id']}"), json={"title": None}).status_code == 422
    assert client.get(api(cid, "/goals/nope")).status_code == 404
    actions = [
        e["action"]
        for e in client.get(api(cid, "/activity"), params={"target_type": "goal"}).json()["items"]
    ]
    assert actions.count("goal.created") == 2 and "goal.updated" in actions


def test_goal_and_project_cross_company_refs_are_404(client, company):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    foreign_agent = new_agent(client, other["id"], name="Foreign")
    foreign_goal = client.post(api(other["id"], "/goals"), json={"title": "Theirs"}).json()
    cid = company["id"]
    assert (
        client.post(
            api(cid, "/goals"), json={"title": "x", "owner_agent_id": foreign_agent["id"]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            api(cid, "/projects"), json={"name": "x", "goal_id": foreign_goal["id"]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            api(cid, "/projects"), json={"name": "x", "lead_agent_id": foreign_agent["id"]}
        ).status_code
        == 404
    )
    assert client.get(api(cid, f"/goals/{foreign_goal['id']}")).status_code == 404


def test_projects_crud(client, company):
    cid = company["id"]
    goal = client.post(api(cid, "/goals"), json={"title": "G"}).json()
    lead = new_agent(client, cid)
    r = client.post(
        api(cid, "/projects"),
        json={
            "name": "Launch",
            "goal_id": goal["id"],
            "lead_agent_id": lead["id"],
            "budget_micros": 5_000_000,
        },
    )
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["status"] == "planned" and project["budget_micros"] == 5_000_000
    assert (
        client.get(api(cid, "/projects"), params={"goal_id": goal["id"]}).json()["items"][0]["id"]
        == project["id"]
    )
    assert client.get(api(cid, "/projects"), params={"status": "active"}).json()["items"] == []
    r = client.patch(
        api(cid, f"/projects/{project['id']}"), json={"status": "active", "budget_micros": None}
    )
    assert r.json()["status"] == "active" and r.json()["budget_micros"] is None
    assert client.post(api(cid, "/projects"), json={"name": ""}).status_code == 422
    assert (
        client.post(api(cid, "/projects"), json={"name": "x", "status": "weird"}).status_code == 422
    )


@pytest.mark.parametrize("path", ["/goals", "/projects"])
def test_viewers_cannot_create_goals_or_projects(
    client, client_as, make_user, add_member, company, path
):
    cid = company["id"]
    viewer = make_user("v2@x.test")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    assert vc.get(api(cid, path)).status_code == 200
    body = {"title": "x"} if path == "/goals" else {"name": "x"}
    assert vc.post(api(cid, path), json=body).status_code == 403
