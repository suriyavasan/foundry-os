from __future__ import annotations

import pytest
from sqlalchemy import update

from app.core.principal import Principal, PrincipalType
from app.models.agent import Agent
from tests.conftest import agents_url, new_agent

ACTIONS = ["activate", "reject", "pause", "resume", "retire"]
# (from_status, action) -> to_status ; everything else must be 409 invalid_transition.
VALID = {
    ("proposed", "activate"): "active",
    ("proposed", "reject"): "rejected",
    ("active", "pause"): "paused",
    ("paused", "resume"): "active",
    ("active", "retire"): "retired",
    ("paused", "retire"): "retired",
}
STATUSES = ["proposed", "active", "paused", "retired", "rejected", "retiring"]
AUDIT_ACTION = {
    "activate": "agent.activated",
    "reject": "agent.rejected",
    "pause": "agent.paused",
    "resume": "agent.resumed",
    "retire": "agent.retired",
}


def _agent_in_status(client, app, cid, status):
    agent = new_agent(client, cid, name=f"A-{status}")
    url = f"{agents_url(cid)}/{agent['id']}"
    path = {
        "proposed": [],
        "active": ["activate"],
        "paused": ["activate", "pause"],
        "retired": ["activate", "retire"],
        "rejected": ["reject"],
    }
    if status == "retiring":  # not reachable via API in M1: force it
        with app.state.session_factory() as db:
            db.execute(update(Agent).where(Agent.id == agent["id"]).values(status="retiring"))
            db.commit()
        return agent["id"], url
    for step in path[status]:
        assert client.post(f"{url}/{step}").status_code == 200
    return agent["id"], url


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("action", ACTIONS)
def test_lifecycle_matrix(client, app, company, status, action):
    cid = company["id"]
    agent_id, url = _agent_in_status(client, app, cid, status)
    activity = f"/api/companies/{cid}/activity"
    before = client.get(activity, params={"target_id": agent_id}).json()["items"]
    resp = client.post(f"{url}/{action}", json={"reason": "because"})
    if (status, action) in VALID:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == VALID[(status, action)]
        events = client.get(
            f"/api/companies/{cid}/activity", params={"target_id": body["id"]}
        ).json()
        top = events["items"][0]
        assert top["action"] == AUDIT_ACTION[action]
        assert top["data"]["from"] == status
        assert top["data"]["to"] == body["status"]
        assert top["data"]["reason"] == "because"
        assert top["target_type"] == "agent"
    else:
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "invalid_transition"
        # status unchanged, and the failed attempt wrote no audit event
        assert client.get(url).json()["status"] == status
        after = client.get(activity, params={"target_id": agent_id}).json()["items"]
        assert [e["id"] for e in after] == [e["id"] for e in before]


def test_lifecycle_action_without_body_and_unknown_action(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    assert client.post(f"{agents_url(cid)}/{agent['id']}/activate").status_code == 200
    assert client.post(f"{agents_url(cid)}/{agent['id']}/explode").status_code == 422
    assert client.post(f"{agents_url(cid)}/nope/pause").status_code == 404


def test_status_changed_at_moves_on_transition(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    assert agent["status"] == "proposed"
    activated = client.post(f"{agents_url(cid)}/{agent['id']}/activate").json()
    assert activated["status_changed_at"] >= agent["status_changed_at"]


def test_create_agent_defaults(client, company):
    agent = new_agent(client, company["id"])
    assert agent["status"] == "proposed"
    assert agent["kind"] == "specialist"
    assert agent["runtime"] == "unassigned"
    assert agent["tool_permissions"] == []
    assert agent["runtime_config"] == {}
    assert agent["monthly_budget_micros"] is None


def test_agent_filters(client, company):
    cid = company["id"]
    dept = client.post(f"/api/companies/{cid}/departments", json={"name": "Eng"}).json()
    a = new_agent(client, cid, name="InEng", department_id=dept["id"])
    new_agent(client, cid, name="Loose")
    client.post(f"{agents_url(cid)}/{a['id']}/activate")
    by_dept = client.get(agents_url(cid), params={"department_id": dept["id"]}).json()["items"]
    assert [x["name"] for x in by_dept] == ["InEng"]
    proposed = client.get(agents_url(cid), params={"status": "proposed"}).json()["items"]
    assert [x["name"] for x in proposed] == ["Loose"]
    assert client.get(agents_url(cid), params={"status": "bogus"}).status_code == 422
    # department agent_count reflects membership
    depts = client.get(f"/api/companies/{cid}/departments").json()["items"]
    assert depts[0]["agent_count"] == 1


# ------------------------------------------------------------------ founder rules


def test_second_founder_rejected(client, company):
    cid = company["id"]
    resp = client.post(
        agents_url(cid), json={"name": "Two", "role_title": "CEO", "kind": "founder"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    founders = [a for a in client.get(agents_url(cid)).json()["items"] if a["kind"] == "founder"]
    assert len(founders) == 1


def test_only_founder_cannot_be_retired_but_can_pause(client, company):
    cid = company["id"]
    founder = client.get(agents_url(cid)).json()["items"][0]
    url = f"{agents_url(cid)}/{founder['id']}"
    resp = client.post(f"{url}/retire")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert client.get(url).json()["status"] == "active"
    assert client.post(f"{url}/pause").status_code == 200
    # still the only founder while paused
    assert client.post(f"{url}/retire").status_code == 409


def test_founder_unique_per_company_not_global(client, company):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    assert len(client.get(agents_url(other["id"])).json()["items"]) == 1


# ------------------------------------------------------------------ editing rules


def test_patch_agent_updates_and_audits(client, company):
    cid = company["id"]
    dept = client.post(f"/api/companies/{cid}/departments", json={"name": "Eng"}).json()
    agent = new_agent(client, cid)
    resp = client.patch(
        f"{agents_url(cid)}/{agent['id']}",
        json={
            "name": "Nova II",
            "department_id": dept["id"],
            "tool_permissions": ["web.search", "web.search", "files.read"],
            "monthly_budget_micros": 5_000_000,
            "runtime": "hermes",
            "runtime_config": {"toolset": "research"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Nova II"
    assert body["tool_permissions"] == ["web.search", "files.read"]
    assert body["monthly_budget_micros"] == 5_000_000
    ev = client.get(f"/api/companies/{cid}/activity").json()["items"][0]
    assert ev["action"] == "agent.updated"
    assert set(ev["data"]["changed"]) == {
        "name",
        "department_id",
        "tool_permissions",
        "monthly_budget_micros",
        "runtime",
        "runtime_config",
    }
    # budgets can be cleared with null
    cleared = client.patch(f"{agents_url(cid)}/{agent['id']}", json={"monthly_budget_micros": None})
    assert cleared.json()["monthly_budget_micros"] is None


@pytest.mark.parametrize("field", ["status", "kind", "company_id", "id"])
def test_non_editable_fields_rejected(client, company, field):
    agent = new_agent(client, company["id"])
    resp = client.patch(f"{agents_url(company['id'])}/{agent['id']}", json={field: "x"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_negative_budget_and_null_name_rejected(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    url = f"{agents_url(cid)}/{agent['id']}"
    assert client.patch(url, json={"daily_budget_micros": -1}).status_code == 422
    assert client.patch(url, json={"name": None}).status_code == 422


def test_retired_agent_is_read_only(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    url = f"{agents_url(cid)}/{agent['id']}"
    client.post(f"{url}/activate")
    client.post(f"{url}/retire")
    resp = client.patch(url, json={"name": "Zombie"})
    assert resp.status_code == 409
    assert client.get(url).json()["name"] == "Nova"


def test_retire_clears_department_lead(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    url = f"{agents_url(cid)}/{agent['id']}"
    client.post(f"{url}/activate")
    dept = client.post(
        f"/api/companies/{cid}/departments", json={"name": "Eng", "lead_agent_id": agent["id"]}
    ).json()
    client.post(f"{url}/retire")
    got = client.get(f"/api/companies/{cid}/departments").json()["items"][0]
    assert got["id"] == dept["id"] and got["lead_agent_id"] is None
    # and retired agents can't be put in charge
    r = client.patch(
        f"/api/companies/{cid}/departments/{dept['id']}", json={"lead_agent_id": agent["id"]}
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ manager hierarchy


def test_manager_cycle_rejected(client, company):
    cid = company["id"]
    a = new_agent(client, cid, name="A")
    b = new_agent(client, cid, name="B", manager_agent_id=a["id"])
    c = new_agent(client, cid, name="C", manager_agent_id=b["id"])
    base = agents_url(cid)

    resp = client.patch(f"{base}/{a['id']}", json={"manager_agent_id": c["id"]})  # A->C->B->A
    assert resp.status_code == 409
    assert "cycle" in resp.json()["error"]["message"]
    assert client.patch(f"{base}/{a['id']}", json={"manager_agent_id": a["id"]}).status_code == 409
    assert client.patch(f"{base}/{a['id']}", json={"manager_agent_id": b["id"]}).status_code == 409
    assert client.get(f"{base}/{a['id']}").json()["manager_agent_id"] is None
    # re-parenting without a cycle is fine, and so is clearing
    assert client.patch(f"{base}/{c['id']}", json={"manager_agent_id": a["id"]}).status_code == 200
    assert client.patch(f"{base}/{c['id']}", json={"manager_agent_id": None}).status_code == 200


def test_retired_manager_rejected(client, company):
    cid = company["id"]
    m = new_agent(client, cid, name="M")
    client.post(f"{agents_url(cid)}/{m['id']}/activate")
    client.post(f"{agents_url(cid)}/{m['id']}/retire")
    r = client.post(
        agents_url(cid), json={"name": "E", "role_title": "R", "manager_agent_id": m["id"]}
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ secrets in runtime_config


@pytest.mark.parametrize(
    "config",
    [
        {"api_key": "sk-123"},
        {"API_KEY": "x"},
        {"auth_token": "x"},
        {"db_password": "x"},
        {"client_secret": "x"},
        {"credentials": {"user": "x"}},
        {"nested": {"deep": {"Access-Token": "x"}}},
        {"list": [{"secretValue": "x"}]},
    ],
)
def test_secret_looking_runtime_config_rejected(client, company, config):
    cid = company["id"]
    resp = client.post(
        agents_url(cid), json={"name": "S", "role_title": "R", "runtime_config": config}
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"][0]["loc"][:2] == ["body", "runtime_config"]
    assert err["details"][0]["msg"] != "x"

    agent = new_agent(client, cid, name="Clean")
    patched = client.patch(f"{agents_url(cid)}/{agent['id']}", json={"runtime_config": config})
    assert patched.status_code == 422
    assert client.get(f"{agents_url(cid)}/{agent['id']}").json()["runtime_config"] == {}
    # secret values never reach the audit log either
    acts = client.get(f"/api/companies/{cid}/activity").json()["items"]
    assert "sk-123" not in str(acts)


@pytest.mark.parametrize(
    "name", ["max_tokens", "keyboard_layout", "monkey", "passthrough", "tokenizer"]
)
def test_secret_heuristic_allows_benign_keys(name):
    from app.services.agents import looks_like_secret_key

    assert not looks_like_secret_key(name)


@pytest.mark.parametrize(
    "name", ["apiKey", "privateKey", "x-api-key", "auth.token", "Authorization", "DB_PASSWORD"]
)
def test_secret_heuristic_flags_secret_keys(name):
    from app.services.agents import looks_like_secret_key

    assert looks_like_secret_key(name)


def test_clean_runtime_config_accepted(client, company):
    agent = new_agent(
        client,
        company["id"],
        runtime_config={"toolset": "research", "max_turns": 20, "sandbox": True},
    )
    assert agent["runtime_config"]["max_turns"] == 20


# ------------------------------------------------------------------ agent principals


def _agent_client(client_as, company_id, agent):
    return client_as(
        Principal(
            type=PrincipalType.AGENT, id=agent["id"], label=agent["name"], company_id=company_id
        )
    )


def test_agent_cannot_modify_itself(client, client_as, company):
    cid = company["id"]
    me = new_agent(client, cid, name="Selfie")
    client.post(f"{agents_url(cid)}/{me['id']}/activate")
    ac = _agent_client(client_as, cid, me)
    url = f"{agents_url(cid)}/{me['id']}"

    for body in [
        {"tool_permissions": ["shell.exec"]},
        {"monthly_budget_micros": 999_999_999},
        {"daily_budget_micros": 1},
        {"runtime": "hermes"},
        {"runtime_config": {"toolset": "all"}},
    ]:
        resp = ac.patch(url, json=body)
        assert resp.status_code == 403, (body, resp.text)
        assert resp.json()["error"]["code"] == "forbidden"
        assert (
            "their own" in resp.json()["error"]["message"]
            or "own" in resp.json()["error"]["message"]
        )

    for action in ACTIONS:
        resp = ac.post(f"{url}/{action}")
        assert resp.status_code == 403, (action, resp.text)
        assert "own lifecycle" in resp.json()["error"]["message"]

    after = client.get(url).json()
    assert after["tool_permissions"] == [] and after["monthly_budget_micros"] is None
    assert after["status"] == "active" and after["runtime"] == "unassigned"


def test_agent_cannot_change_enforced_fields_or_lifecycle_of_others(client, client_as, company):
    cid = company["id"]
    me = new_agent(client, cid, name="Me")
    other = new_agent(client, cid, name="Other")
    ac = _agent_client(client_as, cid, me)
    url = f"{agents_url(cid)}/{other['id']}"
    assert ac.patch(url, json={"tool_permissions": ["x"]}).status_code == 403
    assert ac.patch(url, json={"monthly_budget_micros": 1}).status_code == 403
    assert ac.post(f"{url}/activate").status_code == 403
    r = ac.post(agents_url(cid), json={"name": "N", "role_title": "R", "tool_permissions": ["x"]})
    assert r.status_code == 403
    r = ac.post(agents_url(cid), json={"name": "N", "role_title": "R", "kind": "founder"})
    assert r.status_code == 403
    assert ac.post(f"/api/companies/{cid}/departments", json={"name": "D"}).status_code == 403


def test_agent_may_edit_descriptive_fields_and_propose_colleagues(client, client_as, company):
    cid = company["id"]
    me = new_agent(client, cid, name="Me")
    ac = _agent_client(client_as, cid, me)
    assert (
        ac.patch(f"{agents_url(cid)}/{me['id']}", json={"responsibilities": "Docs"}).status_code
        == 200
    )
    r = ac.post(agents_url(cid), json={"name": "Junior", "role_title": "Intern"})
    assert r.status_code == 201 and r.json()["status"] == "proposed"
    ev = ac.get(f"/api/companies/{cid}/activity").json()["items"][0]
    assert ev["actor_type"] == "agent" and ev["actor_id"] == me["id"]


def test_agent_principal_of_another_company_gets_404(client, client_as, company):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    foreign = client.get(agents_url(other["id"])).json()["items"][0]
    ac = _agent_client(client_as, other["id"], foreign)
    assert ac.get(f"/api/companies/{company['id']}").status_code == 404
    assert ac.get(agents_url(company["id"])).status_code == 404
