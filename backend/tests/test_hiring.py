"""HR hiring: auto-activation rules (each condition), approvals, authorization, hiring requests."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.governance import Approval
from tests.helpers import (
    agent_of_family,
    api,
    new_agent,
    new_task,
    seed_library,
    set_policy,
)
from tests.test_agent_api import start_run

ENGINEER_DEFAULTS = {
    "github",
    "systematic-debugging",
    "test-driven-development",
    "requesting-code-review",
    "spike",
    "code-wiki",
    "rest-graphql-debug",
}


@pytest.fixture
def skills_env_override(fake_skills_env):
    return fake_skills_env


@pytest.fixture
def world(app, client, company):
    cid = company["id"]
    seed_library(client, cid)
    founder = agent_of_family(client, cid, "founder")
    hr_agent = agent_of_family(client, cid, "hr")
    hr = start_run(app, client, cid, hr_agent["id"], task_title="Hire an engineer", kind="hiring")
    return cid, founder, hr_agent, hr


def hire_body(founder, hr, **extra):
    return {
        "role_template": "software-engineer",
        "name": "Ada",
        "manager_agent_id": founder["id"],
        "reason": "We need someone to write code.",
        "task_id": hr.task_id,
        **extra,
    }


def get_agent(client, cid, agent_id):
    return client.get(api(cid, f"/agents/{agent_id}")).json()


def audit_actions(app, cid):
    with app.state.session_factory() as db:
        return [
            e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid))
        ]


# ------------------------------------------------------------------ the happy path


def test_hire_within_policy_is_activated_automatically(app, client, world):
    cid, founder, hr_agent, hr = world
    r = hr.post(
        "/hires", json=hire_body(founder, hr, skill_slugs=["xlsx"], monthly_budget_micros=0)
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["auto_activated"] is True and body["approval_id"] is None
    assert body["reasons_not_auto_activated"] == []
    assert set(body["skills"]) == ENGINEER_DEFAULTS | {"xlsx"}
    agent = get_agent(client, cid, body["agent"]["id"])
    assert agent["status"] == "active"
    assert (agent["role_template"], agent["role_family"], agent["kind"]) == (
        "software-engineer",
        "engineering",
        "specialist",
    )
    assert agent["role_title"] == "Software Engineer" and agent["name"] == "Ada"
    assert agent["manager_agent_id"] == founder["id"] and agent["reports_to_human"] is False
    assert "terminal" in agent["tool_permissions"] and "company" in agent["tool_permissions"]
    assert "Software Engineer" in agent["instructions"] or "tested changes" in agent["instructions"]
    assert agent["runtime"] == founder["runtime"]
    depts = client.get(api(cid, "/departments")).json()["items"]
    eng = next(d for d in depts if d["name"] == "Engineering")
    assert agent["department_id"] == eng["id"]
    actions = audit_actions(app, cid)
    assert "hire.created" in actions and "agent.activated" in actions
    skills = client.get(api(cid, f"/agents/{agent['id']}/skills")).json()["items"]
    assert {i["skill"]["slug"] for i in skills} == ENGINEER_DEFAULTS | {"xlsx"}
    assert {i["assigned_by_type"] for i in skills} == {"agent"}


def test_hire_inherits_the_founders_model_and_runtime_or_the_company_default(client, world):
    cid, founder, _, hr = world
    # no provider registered yet: nothing pretends to be runnable
    r = hr.post("/hires", json=hire_body(founder, hr, name="Early"))
    early = get_agent(client, cid, r.json()["agent"]["id"])
    assert (early["runtime"], early["model_provider"]) == ("unassigned", None)
    # registering a provider equips the founder; later hires inherit its runtime and model
    p = client.post(
        api(cid, "/model-providers"),
        json={"name": "Local", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    r = hr.post("/hires", json=hire_body(founder, hr, name="Late"))
    late = get_agent(client, cid, r.json()["agent"]["id"])
    assert (late["runtime"], late["model_provider"], late["model_name"]) == (
        "company_agent",
        p["id"],
        "qwen2.5:7b",
    )


def test_title_department_and_model_can_be_chosen(client, world):
    cid, founder, _, hr = world
    provider = client.post(
        api(cid, "/model-providers"),
        json={"name": "Local", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    body = hire_body(
        founder,
        hr,
        role_title="Backend Engineer",
        department="Platform",
        model_provider="ollama",  # a name is not a provider id: rejected, never silently stored
        model_name="qwen2.5:7b",
    )
    assert hr.post("/hires", json=body).status_code == 404
    body["model_provider"] = provider["id"]
    r = hr.post("/hires", json=body)
    agent = get_agent(client, cid, r.json()["agent"]["id"])
    assert (agent["role_title"], agent["model_provider"], agent["model_name"]) == (
        "Backend Engineer",
        provider["id"],
        "qwen2.5:7b",
    )
    depts = {d["id"]: d["name"] for d in client.get(api(cid, "/departments")).json()["items"]}
    assert depts[agent["department_id"]] == "Platform"


# ------------------------------------------------------------------ auto-activation conditions


def assert_pending_hire(client, cid, hr, founder, reason: str, approver: str):
    r = hr.post("/hires", json=hire_body(founder, hr, name=f"Hire-{reason}"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["auto_activated"] is False, body
    assert reason in body["reasons_not_auto_activated"]
    assert body["approver"] == approver and body["approval_id"]
    assert get_agent(client, cid, body["agent"]["id"])["status"] == "proposed"
    approval = client.get(api(cid, f"/approvals/{body['approval_id']}")).json()
    assert approval["kind"] == "hire" and approval["status"] == "pending"
    assert (
        approval["subject_type"] == "agent_hire" and approval["subject_id"] == body["agent"]["id"]
    )
    assert approval["requested_by_agent_id"] == hr.agent_id
    assert approval["task_id"] == hr.task_id
    assert approval["scope"] == {"action": "activate_agent", "agent_id": body["agent"]["id"]}
    return body


def test_supervised_mode_requires_a_human_decision(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, autonomy_mode="supervised")
    assert_pending_hire(client, cid, hr, founder, "autonomy_mode_not_autonomous", "human")


def test_auto_activation_can_be_switched_off(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, auto_activate_hires=False)
    # decided by the founder: `hire` is not reserved for humans
    assert_pending_hire(client, cid, hr, founder, "auto_activate_hires_disabled", "founder")


def test_hire_reserved_for_humans_by_policy(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, human_approval_required_for=["hire", "budget_increase"])
    assert_pending_hire(client, cid, hr, founder, "hire_requires_human_approval", "human")


def test_headcount_limit_is_a_hard_limit(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, max_headcount=3)  # founder + HR + one more
    ok = hr.post("/hires", json=hire_body(founder, hr, name="Last seat"))
    assert ok.status_code == 201 and ok.json()["auto_activated"] is True
    full = hr.post("/hires", json=hire_body(founder, hr, name="Too many"))
    assert full.status_code == 409 and full.json()["error"]["code"] == "limit_exceeded"
    assert full.json()["error"]["details"]["limit"] == "max_headcount"
    names = [a["name"] for a in client.get(api(cid, "/agents")).json()["items"]]
    assert "Too many" not in names
    # proposed hires occupy a seat too
    set_policy(client, cid, max_headcount=5, auto_activate_hires=False)
    for i in range(2):
        assert hr.post("/hires", json=hire_body(founder, hr, name=f"P{i}")).status_code == 201
    assert hr.post("/hires", json=hire_body(founder, hr, name="P2")).status_code == 409


def test_requested_budget_must_fit_the_company_budget(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, monthly_budget_micros=1_000_000)
    first = hr.post("/hires", json=hire_body(founder, hr, name="A", monthly_budget_micros=600_000))
    assert first.json()["auto_activated"] is True
    assert get_agent(client, cid, first.json()["agent"]["id"])["monthly_budget_micros"] == 600_000
    too_much = hr.post(
        "/hires", json=hire_body(founder, hr, name="B", monthly_budget_micros=500_000)
    )
    assert too_much.json()["auto_activated"] is False
    assert too_much.json()["reasons_not_auto_activated"] == ["budget_does_not_fit"]
    exact = hr.post("/hires", json=hire_body(founder, hr, name="C", monthly_budget_micros=400_000))
    assert exact.json()["auto_activated"] is True  # exactly what is left fits
    # without a budget request the default ($5) applies and is checked like any other; without a
    # company budget any request up to the per-agent cap fits (the cap itself: see test_budget_cap)
    set_policy(client, cid, monthly_budget_micros=None)
    big = hr.post("/hires", json=hire_body(founder, hr, name="E", monthly_budget_micros=20_000_000))
    assert big.json()["auto_activated"] is True


def test_default_hire_budget_applies_and_counts(client, world):
    cid, founder, _, hr = world
    set_policy(
        client, cid, hire_default_monthly_budget_micros=300_000, monthly_budget_micros=500_000
    )
    a = hr.post("/hires", json=hire_body(founder, hr, name="A")).json()
    assert get_agent(client, cid, a["agent"]["id"])["monthly_budget_micros"] == 300_000
    assert a["auto_activated"] is True
    b = hr.post("/hires", json=hire_body(founder, hr, name="B")).json()
    assert b["auto_activated"] is False and b["reasons_not_auto_activated"] == [
        "budget_does_not_fit"
    ]


def test_several_blockers_are_all_reported(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, auto_activate_hires=False, human_approval_required_for=["hire"])
    body = hr.post("/hires", json=hire_body(founder, hr)).json()
    assert set(body["reasons_not_auto_activated"]) == {
        "auto_activate_hires_disabled",
        "hire_requires_human_approval",
    }


# ------------------------------------------------------------------ approval path


def make_pending(client, cid, hr, founder, name="Pending"):
    set_policy(client, cid, auto_activate_hires=False)
    return hr.post("/hires", json=hire_body(founder, hr, name=name)).json()


def test_founder_approval_activates_the_hire_once(app, client, world):
    cid, founder, _, hr = world
    pending = make_pending(client, cid, hr, founder)
    founder_run = start_run(app, client, cid, founder["id"], task_title="Review hires")
    decided = founder_run.post(
        f"/approvals/{pending['approval_id']}/decide", json={"decision": "approve"}
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved" and decided.json()["consumed_at"]
    assert get_agent(client, cid, pending["agent"]["id"])["status"] == "active"
    again = founder_run.post(
        f"/approvals/{pending['approval_id']}/decide", json={"decision": "approve"}
    )
    assert again.status_code == 409
    assert "agent.activated" in audit_actions(app, cid)


def test_human_approval_activates_and_rejection_rejects(client, world):
    cid, founder, _, hr = world
    approved = make_pending(client, cid, hr, founder, "Yes")
    assert client.post(api(cid, f"/approvals/{approved['approval_id']}/approve")).status_code == 200
    assert get_agent(client, cid, approved["agent"]["id"])["status"] == "active"
    rejected = hr.post("/hires", json=hire_body(founder, hr, name="No")).json()
    client.post(api(cid, f"/approvals/{rejected['approval_id']}/reject"), json={"note": "no"})
    assert get_agent(client, cid, rejected["agent"]["id"])["status"] == "rejected"
    changes = hr.post("/hires", json=hire_body(founder, hr, name="Maybe")).json()
    client.post(api(cid, f"/approvals/{changes['approval_id']}/request_changes"))
    assert get_agent(client, cid, changes["agent"]["id"])["status"] == "proposed"


def test_the_requester_cannot_approve_its_own_hire(app, client, world):
    cid, founder, _, hr = world
    pending = make_pending(client, cid, hr, founder)
    r = hr.post(f"/approvals/{pending['approval_id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403
    assert get_agent(client, cid, pending["agent"]["id"])["status"] == "proposed"


def test_a_forged_hire_approval_activates_nothing(app, client, world):
    """Only approvals created by the hiring service carry a subject; a generic `hire` approval
    requested by any agent decides nothing about any agent."""
    cid, founder, _, hr = world
    stranger = new_agent(client, cid, name="Stranger", activate=False)  # proposed by a human
    worker = start_run(app, client, cid, new_agent(client, cid, name="Worker")["id"])
    created = worker.post(
        "/approvals",
        json={"kind": "hire", "title": "activate Stranger", "scope": {"agent_id": stranger["id"]}},
    )
    assert created.status_code == 201
    founder_run = start_run(app, client, cid, founder["id"], task_title="Decide")
    assert (
        founder_run.post(
            f"/approvals/{created.json()['id']}/decide", json={"decision": "approve"}
        ).status_code
        == 200
    )
    assert get_agent(client, cid, stranger["id"])["status"] == "proposed"
    with app.state.session_factory() as db:
        assert db.get(Approval, created.json()["id"]).subject_type is None


# ------------------------------------------------------------------ authorization and validation


def test_only_the_hr_agent_can_hire(app, client, world):
    cid, founder, _, hr = world
    founder_run = start_run(app, client, cid, founder["id"], task_title="Founder work")
    worker = start_run(app, client, cid, new_agent(client, cid, name="Worker")["id"])
    for sess in (founder_run, worker):
        r = sess.post("/hires", json=hire_body(founder, hr))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.post("/api/agent/v1/hires", json=hire_body(founder, hr)).status_code == 401
    assert "Ada" not in [a["name"] for a in client.get(api(cid, "/agents")).json()["items"]]


def test_a_paused_hr_agent_cannot_hire(client, world):
    cid, founder, hr_agent, hr = world
    client.post(api(cid, f"/agents/{hr_agent['id']}/pause"))
    assert hr.post("/hires", json=hire_body(founder, hr)).status_code == 403


@pytest.mark.parametrize("template", ["founder-ceo", "hr-partner"])
def test_hr_cannot_hire_into_founder_or_hr_roles(world, template):
    _, founder, _, hr = world
    assert hr.post("/hires", json=hire_body(founder, hr, role_template=template)).status_code == 403


def test_hire_validation(app, client, world):
    cid, founder, hr_agent, hr = world
    other = client.post("/api/companies", json={"name": "Other"}).json()
    other_founder = agent_of_family(client, other["id"], "founder")
    other_task = new_task(client, other["id"], title="elsewhere")
    retired = new_agent(client, cid, name="Gone")
    client.post(api(cid, f"/agents/{retired['id']}/retire"))
    cases = [
        ({"role_template": "no-such-template"}, 404),
        ({"manager_agent_id": other_founder["id"]}, 404),
        ({"manager_agent_id": retired["id"]}, 409),
        ({"task_id": other_task["id"]}, 404),
        ({"skill_slugs": ["no-such-skill"]}, 404),
        ({"name": ""}, 422),
        ({"reason": ""}, 422),
        ({"monthly_budget_micros": -1}, 422),
        ({"tool_permissions": ["terminal"]}, 422),  # permissions come from the template only
        ({"kind": "founder"}, 422),
    ]
    for patch, status in cases:
        r = hr.post("/hires", json=hire_body(founder, hr, **patch))
        assert r.status_code == status, (patch, r.status_code, r.text)
    assert len(client.get(api(cid, "/agents")).json()["items"]) == 3  # nobody was created


def test_quarantined_skills_cannot_be_given_to_a_hire(client, world, fake_scanner):
    cid, founder, _, hr = world
    fake_scanner.verdicts["shady"] = "dangerous"
    hr.post(
        "/skills/author",
        json={"slug": "shady", "name": "Shady", "description": "x", "body_markdown": "# x\n"},
    )
    r = hr.post("/hires", json=hire_body(founder, hr, skill_slugs=["shady"]))
    assert r.status_code == 409 and "quarantined" in r.json()["error"]["message"]


def test_hiring_without_a_seeded_library_reports_skipped_defaults(app, client, company):
    cid = company["id"]  # not seeded
    founder = agent_of_family(client, cid, "founder")
    hr = start_run(app, client, cid, agent_of_family(client, cid, "hr")["id"], kind="hiring")
    body = hr.post("/hires", json=hire_body(founder, hr)).json()
    assert body["skills"] == [] and set(body["skipped_default_skills"]) == ENGINEER_DEFAULTS
    assert body["auto_activated"] is True


def test_hire_events_and_audit_are_written(app, client, world):
    cid, founder, _, hr = world
    hr.post("/hires", json=hire_body(founder, hr))
    with app.state.session_factory() as db:
        from app.models.run import CompanyEvent

        types = [
            e.type for e in db.scalars(select(CompanyEvent).where(CompanyEvent.company_id == cid))
        ]
        [created] = [e for e in db.scalars(select(AuditEvent)) if e.action == "hire.created"]
    assert "hire.created" in types and "agent.skills_changed" in types
    assert created.actor_type == "agent" and created.data["task_id"] == hr.task_id
    assert created.data["skills"] and "terminal" not in str(created.data)


# ------------------------------------------------------------------ hiring requests


def test_hiring_request_creates_one_task_for_hr(app, client, world):
    cid, founder, hr_agent, _ = world
    founder_run = start_run(app, client, cid, founder["id"], task_title="Plan the launch")
    body = {
        "required_skills": ["Design", "ui"],
        "role_hint": "product designer",
        "reason": "No one can design the landing page.",
        "task_id": founder_run.task_id,
    }
    first = founder_run.post("/hiring-requests", json=body)
    assert first.status_code == 201, first.text
    task = client.get(api(cid, f"/tasks/{first.json()['hiring_task_id']}")).json()
    assert task["kind"] == "hiring" and task["assignee_agent_id"] == hr_agent["id"]
    assert task["required_skills"] == [] and task["created_by_id"] == founder["id"]
    assert "design, ui" in task["description"] and founder_run.task_id in task["description"]
    again = founder_run.post(
        "/hiring-requests", json={**body, "required_skills": ["ui", "design"], "reason": "again"}
    )
    assert again.status_code == 200
    assert again.json() == {**first.json(), "created": False}
    other = founder_run.post("/hiring-requests", json={**body, "required_skills": ["sql"]})
    assert (
        other.status_code == 201
        and other.json()["hiring_task_id"] != first.json()["hiring_task_id"]
    )


def test_hiring_request_authorization_and_validation(app, client, world):
    cid, founder, hr_agent, hr = world
    founder_run = start_run(app, client, cid, founder["id"], task_title="Plan")
    body = {"required_skills": ["sql"], "reason": "r", "task_id": founder_run.task_id}
    worker = new_agent(client, cid, name="Worker")
    worker_run = start_run(app, client, cid, worker["id"])
    assert (
        worker_run.post(
            "/hiring-requests", json={**body, "task_id": worker_run.task_id}
        ).status_code
        == 403
    )
    # a manager (anyone with reports) may ask
    new_agent(client, cid, name="Junior", manager_agent_id=worker["id"])
    assert (
        worker_run.post(
            "/hiring-requests", json={**body, "task_id": worker_run.task_id}
        ).status_code
        == 201
    )
    assert founder_run.post("/hiring-requests", json={**body, "task_id": "nope"}).status_code == 404
    assert (
        founder_run.post("/hiring-requests", json={**body, "required_skills": []}).status_code
        == 422
    )
    assert client.post("/api/agent/v1/hiring-requests", json=body).status_code == 401


def test_hiring_request_needs_an_active_hr_agent(app, client, world):
    cid, founder, hr_agent, _ = world
    client.post(api(cid, f"/agents/{hr_agent['id']}/pause"))
    founder_run = start_run(app, client, cid, founder["id"], task_title="Plan")
    r = founder_run.post(
        "/hiring-requests",
        json={"required_skills": ["sql"], "reason": "r", "task_id": founder_run.task_id},
    )
    assert r.status_code == 409


def test_open_hiring_requests_are_capped_per_agent(app, client, world):
    cid, founder, _, _ = world
    founder_run = start_run(app, client, cid, founder["id"], task_title="Plan")
    statuses = [
        founder_run.post(
            "/hiring-requests",
            json={"required_skills": [f"skill-{i}"], "reason": "r", "task_id": founder_run.task_id},
        ).status_code
        for i in range(6)
    ]
    assert statuses == [201] * 5 + [409]


# ------------------------------------------------------------------ colleague search


def test_colleague_search(app, client, world):
    cid, founder, hr_agent, hr = world
    hr.post("/hires", json=hire_body(founder, hr, name="Ada"))
    designer_body = hire_body(founder, hr, name="Bea", role_template="product-designer")
    hr.post("/hires", json=designer_body)
    ada = next(a for a in client.get(api(cid, "/agents")).json()["items"] if a["name"] == "Ada")
    new_task(client, cid, title="Ada's work", assignee_agent_id=ada["id"])
    worker = start_run(app, client, cid, new_agent(client, cid, name="Worker")["id"])

    everyone = worker.get("/agents").json()["items"]
    assert {c["name"] for c in everyone} >= {"Ada", "Bea", "Harper", "Atlas", "Worker"}
    eng = worker.get("/agents", params={"skill_tags": "systematic-debugging,github"}).json()[
        "items"
    ]
    assert [c["name"] for c in eng] == ["Ada"]
    assert "github" in eng[0]["skills"] and "general" in eng[0]["skill_tags"]
    assert eng[0]["open_tasks"] == 1 and eng[0]["role_family"] == "engineering"
    assert [
        c["name"] for c in worker.get("/agents", params={"family": "design"}).json()["items"]
    ] == ["Bea"]
    assert worker.get("/agents", params={"skill_tags": "no-such-tag"}).json()["items"] == []
    proposed = worker.get("/agents", params={"status": "proposed"}).json()["items"]
    assert all(c["status"] == "proposed" for c in proposed)
    loads = [c["open_tasks"] for c in everyone]
    assert loads == sorted(loads)  # least loaded first


def test_agent_context_carries_skill_tags(app, client, world):
    cid, founder, hr_agent, hr = world
    ctx = hr.get("/context").json()
    assert ctx["skills_available"] is True
    assert "hiring-playbook" in ctx["agent"]["skills"] and "hiring" in ctx["agent"]["skill_tags"]
    assert ctx["agent"]["role_family"] == "hr"
    assert (
        "decision-questionnaire"
        in next(c for c in ctx["colleagues"] if c["id"] == founder["id"])["skills"]
    )


# ------------------------------------------------------------------ owner API: templates on agents


def test_human_creates_an_agent_from_a_role_template(client, world):
    cid, founder, _, _ = world
    r = client.post(
        api(cid, "/agents"),
        json={"name": "Dana", "role_title": "Dev", "role_template": "software-engineer"},
    )
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["status"], a["role_template"], a["role_family"]) == (
        "proposed",
        "software-engineer",
        "engineering",
    )
    assert "tested changes" in a["instructions"] and a["responsibilities"]
    assert "terminal" in a["tool_permissions"]
    eng = next(
        d
        for d in client.get(api(cid, "/departments")).json()["items"]
        if d["name"] == "Engineering"
    )
    assert a["department_id"] == eng["id"]
    skills = client.get(api(cid, f"/agents/{a['id']}/skills")).json()["items"]
    assert {i["skill"]["slug"] for i in skills} == ENGINEER_DEFAULTS
    # explicit fields win over the template, and skill_slugs add to the defaults
    custom = client.post(
        api(cid, "/agents"),
        json={
            "name": "Eli",
            "role_title": "Dev",
            "role_template": "qa-engineer",
            "instructions": "Mine.",
            "tool_permissions": ["company"],
            "kind": "lead",
            "skill_slugs": ["xlsx"],
        },
    ).json()
    assert (custom["instructions"], custom["tool_permissions"], custom["kind"]) == (
        "Mine.",
        ["company"],
        "lead",
    )
    got = {
        i["skill"]["slug"]
        for i in client.get(api(cid, f"/agents/{custom['id']}/skills")).json()["items"]
    }
    assert "xlsx" in got and "rest-graphql-debug" in got
    assert (
        client.post(
            api(cid, "/agents"), json={"name": "X", "role_title": "Y", "role_template": "nope"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            api(cid, "/agents"),
            json={"name": "X", "role_title": "Y", "role_template": "founder-ceo"},
        ).status_code
        == 409
    )  # the company already has a founder


def test_agents_cannot_apply_templates_or_skills_when_proposing(app, client, world):
    cid, founder, _, _ = world
    founder_run = start_run(app, client, cid, founder["id"], task_title="Plan")
    for extra in ({"role_template": "software-engineer"}, {"skill_slugs": ["spike"]}):
        r = client.post(
            api(cid, "/agents"),
            json={"name": "Sneak", "role_title": "Dev", **extra},
            headers=founder_run.headers,
        )
        assert r.status_code == 403
    plain = client.post(
        api(cid, "/agents"),
        json={"name": "Plain", "role_title": "Dev"},
        headers=founder_run.headers,
    )
    assert plain.status_code == 201 and plain.json()["role_family"] == "generic"


def test_role_family_defaults_and_agent_responses(client, world):
    cid, founder, hr_agent, _ = world
    assert founder["role_family"] == "founder" and hr_agent["role_family"] == "hr"
    plain = new_agent(client, cid, name="Plain")
    assert plain["role_family"] == "generic" and plain["role_template"] is None
    assert "role_family" not in client.patch(
        api(cid, f"/agents/{plain['id']}"), json={"role_family": "hr"}
    ).json().get("name", "")  # unknown fields are rejected
    assert (
        client.patch(api(cid, f"/agents/{plain['id']}"), json={"role_family": "hr"}).status_code
        == 422
    )


# ------------------------------------------------------------------ custom templates


def test_agent_authored_templates_cannot_escalate(app, client, world):
    cid, founder, _, hr = world
    ok = hr.post(
        "/role-templates",
        json={
            "slug": "data-curator",
            "family": "research",
            "title": "Data Curator",
            "instructions": "Curate data.",
            "default_toolsets": ["company", "file", "web"],
            "default_skill_slugs": ["arxiv"],
        },
    )
    assert ok.status_code == 201, ok.text
    for patch in (
        {"slug": "root-a", "default_toolsets": ["company", "terminal"]},
        {"slug": "root-b", "default_toolsets": ["code_execution"]},
        {"slug": "root-c", "family": "hr"},
        {"slug": "root-d", "family": "founder"},
        {"slug": "root-e", "default_kind": "founder"},
    ):
        body = {"title": "T", "family": "generic", "default_toolsets": ["company"], **patch}
        assert hr.post("/role-templates", json=body).status_code == 403, patch
    hired = hr.post("/hires", json=hire_body(founder, hr, role_template="data-curator", name="Cy"))
    assert hired.status_code == 201 and hired.json()["skills"] == ["arxiv"]
    # a human may grant terminal access in a custom template
    human = client.post(
        api(cid, "/role-templates"),
        json={"slug": "sysadmin", "title": "Sysadmin", "default_toolsets": ["company", "terminal"]},
    )
    assert human.status_code == 201
    assert (
        hr.post(
            "/role-templates", json={"slug": "bogus", "title": "T", "default_toolsets": ["browser"]}
        ).status_code
        == 422
    )


def test_hires_never_get_more_toolsets_than_their_template(client, world):
    cid, founder, _, hr = world
    body = hr.post(
        "/hires", json=hire_body(founder, hr, role_template="growth-marketer", name="Mo")
    ).json()
    perms = get_agent(client, cid, body["agent"]["id"])["tool_permissions"]
    assert set(perms) == {"company", "file", "web", "search", "skills", "todo"}
    with_agents = client.get(api(cid, "/agents")).json()["items"]
    hired = [a for a in with_agents if a["id"] == body["agent"]["id"]]
    assert hired and "terminal" not in hired[0]["tool_permissions"]


def test_headcount_in_db_matches(app, client, world):
    cid, founder, _, hr = world
    hr.post("/hires", json=hire_body(founder, hr))
    with app.state.session_factory() as db:
        agents = db.scalars(select(Agent).where(Agent.company_id == cid)).all()
    assert len(agents) == 3
