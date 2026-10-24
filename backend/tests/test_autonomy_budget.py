"""Budget gap fix (found live in M3b: HR gave a hire $5,000/month with no company budget set):
agent-initiated hires get a default budget and can never exceed the per-agent cap, which only a
human can raise."""

from __future__ import annotations

import pytest

from tests.helpers import api, set_policy
from tests.test_agent_api import start_run
from tests.test_hiring import get_agent, hire_body, skills_env_override, world  # noqa: F401, F811

CAP = 25_000_000
DEFAULT = 5_000_000


def test_autonomy_policy_defaults(client, company):
    p = client.get(api(company["id"], "/policy")).json()
    assert p["max_agent_monthly_budget_micros"] == CAP
    assert p["hire_default_monthly_budget_micros"] == DEFAULT
    assert (
        p["max_planning_runs_per_hour"],
        p["max_open_tasks"],
        p["circuit_breaker_failures"],
    ) == (
        6,
        200,
        8,
    )


def test_a_hire_without_a_requested_budget_gets_the_default_never_none(client, world):
    cid, founder, _, hr = world
    r = hr.post("/hires", json=hire_body(founder, hr))
    assert r.status_code == 201, r.text
    assert get_agent(client, cid, r.json()["agent"]["id"])["monthly_budget_micros"] == DEFAULT
    # even if a human nulled the policy default, the constant $5 applies
    set_policy(client, cid, hire_default_monthly_budget_micros=None)
    r = hr.post("/hires", json=hire_body(founder, hr, name="B"))
    assert get_agent(client, cid, r.json()["agent"]["id"])["monthly_budget_micros"] == DEFAULT


def test_a_hire_above_the_cap_is_refused_with_limit_exceeded(client, world):
    cid, founder, _, hr = world
    huge = hr.post(
        "/hires", json=hire_body(founder, hr, name="Greedy", monthly_budget_micros=5_000_000_000)
    )  # the $5,000 HR chose live
    assert huge.status_code == 409
    err = huge.json()["error"]
    assert (
        err["code"] == "limit_exceeded"
        and err["details"]["limit"] == "max_agent_monthly_budget_micros"
    )
    assert err["details"]["max"] == CAP and err["details"]["requested"] == 5_000_000_000
    agents = client.get(api(cid, "/agents")).json()["items"]
    assert "Greedy" not in {a["name"] for a in agents}  # nothing half-created
    just_over = hr.post(
        "/hires", json=hire_body(founder, hr, name="Over", monthly_budget_micros=CAP + 1)
    )
    assert just_over.status_code == 409
    exact = hr.post("/hires", json=hire_body(founder, hr, name="Exact", monthly_budget_micros=CAP))
    assert exact.status_code == 201


def test_a_policy_default_above_the_cap_is_clamped(client, world):
    cid, founder, _, hr = world
    set_policy(client, cid, hire_default_monthly_budget_micros=100_000_000)
    r = hr.post("/hires", json=hire_body(founder, hr))
    assert get_agent(client, cid, r.json()["agent"]["id"])["monthly_budget_micros"] == CAP


def test_only_humans_raise_the_cap(client, world):
    cid, founder, hr_agent, hr = world
    founder_run = start_run(client.app, client, cid, founder["id"], task_title="greedy")
    # an agent token on the owner API cannot touch the policy (403), whatever it asks for
    r = client.patch(
        api(cid, "/policy"),
        json={"max_agent_monthly_budget_micros": 10**12},
        headers={
            "Authorization": f"Bearer {founder_run.token}"
        },  # the run token wins over the cookie
    )
    assert r.status_code == 403
    assert client.get(api(cid, "/policy")).json()["max_agent_monthly_budget_micros"] == CAP
    # a human can: then the larger hire is allowed
    set_policy(client, cid, max_agent_monthly_budget_micros=50_000_000)
    ok = hr.post("/hires", json=hire_body(founder, hr, monthly_budget_micros=40_000_000))
    assert ok.status_code == 201
    assert get_agent(client, cid, ok.json()["agent"]["id"])["monthly_budget_micros"] == 40_000_000


def test_an_agent_proposed_colleague_also_gets_the_default_budget(app, client, world):
    cid, founder, _, _ = world
    founder_run = start_run(app, client, cid, founder["id"], task_title="propose")
    r = founder_run.client.post(
        api(cid, "/agents"),
        json={"name": "Proposed", "role_title": "Helper"},
        headers=founder_run.headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["monthly_budget_micros"] == DEFAULT and r.json()["status"] == "proposed"
    # and an agent still cannot set a budget itself
    bad = founder_run.client.post(
        api(cid, "/agents"),
        json={"name": "Rich", "role_title": "Helper", "monthly_budget_micros": 1},
        headers=founder_run.headers,
    )
    assert bad.status_code == 403


@pytest.mark.parametrize("amount", [0, 1, DEFAULT])
def test_small_requested_budgets_pass(client, world, amount):
    cid, founder, _, hr = world
    r = hr.post(
        "/hires", json=hire_body(founder, hr, name=f"N{amount}", monthly_budget_micros=amount)
    )
    assert r.status_code == 201
