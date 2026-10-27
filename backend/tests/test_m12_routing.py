"""M12 (WP3): roles and models. Management runs stay on the management model, work runs use the optional
work model, and the CLI role-family scope keeps web-needing families on the company agent."""

from __future__ import annotations

import pytest

from app.models.agent import Agent
from app.models.enums import TaskKind
from app.models.work import Task
from app.services import policy, routing
from tests.guardrail_helpers import set_family
from tests.helpers import agent_of_family, api, new_agent, set_policy


@pytest.fixture
def providers(app, client, company):
    """A paid cloud provider (created first, so the company's management model) and a free local one."""
    cid = company["id"]
    cred = client.post(
        api(cid, "/credentials"), json={"name": "cloud", "value": "sk-cloud-0123456789"}
    )
    cloud = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "Cloud",
            "kind": "openai",
            "credential_id": cred.json()["id"],
            "default_model": "big-model",
            "input_price_per_mtok_micros": 1_000_000,
            "output_price_per_mtok_micros": 5_000_000,
        },
    ).json()
    local = client.post(
        api(cid, "/model-providers"),
        json={"name": "Local", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    set_policy(client, cid, default_model_provider_id=cloud["id"], default_model_name="big-model")
    return cloud, local


def choose(app, agent_id: str, kind: str | None = "work", *, runtime: str = "company_agent"):
    """routing.choose for an agent and a task of ``kind`` (None: the pre-M12 call without a task)."""
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        limits = policy.load_policy(db, agent.company_id)
        task = Task(company_id=agent.company_id, title="t", kind=kind) if kind else None
        return routing.choose(db, agent, limits, runtime, task=task)


def worker(client, cid, provider, name="Wes", **kw):
    return new_agent(
        client,
        cid,
        name=name,
        runtime="company_agent",
        model_provider=provider["id"],
        model_name=provider["default_model"],
        **kw,
    )


def test_a_work_run_without_a_task_keeps_todays_free_first_behaviour(
    app, client, company, providers
):
    cid = company["id"]
    cloud, local = providers
    agent = worker(client, cid, cloud)
    for route in (choose(app, agent["id"], None), choose(app, agent["id"], "work")):
        assert route.provider_id == local["id"] and route.free is True
        assert route.reason.startswith("free_first")


def test_management_runs_stay_on_the_management_model_even_when_a_free_local_one_exists(
    app, client, company, providers
):
    cid = company["id"]
    cloud, local = providers
    founder = agent_of_family(client, cid, "founder")
    lead = new_agent(
        client, cid, name="Lena", kind="lead", runtime="company_agent", model_provider=local["id"]
    )
    boss = worker(client, cid, cloud, name="Boss")
    worker(client, cid, cloud, name="Report", manager_agent_id=boss["id"])
    plain = worker(client, cid, cloud, name="Plain")
    cases = {
        "founder work": (founder["id"], "work"),
        "lead work": (lead["id"], "work"),
        "manager work": (boss["id"], "work"),
        "worker planning": (plain["id"], "planning"),
        "worker review": (plain["id"], "review"),
        "worker hiring": (plain["id"], "hiring"),
        "worker routine": (plain["id"], "routine"),
    }
    for name, (agent_id, kind) in cases.items():
        route = choose(app, agent_id, kind)
        assert route.provider_id == cloud["id"], name
        assert route.model == "big-model" and route.free is False, name
        assert route.reason.startswith("management"), name
        assert route.needs_paid_approval is False, name
    # an ordinary worker's work run is still a free_first candidate
    assert choose(app, plain["id"], "work").provider_id == local["id"]


def test_the_management_model_is_not_a_paid_fallback_needing_approval(
    app, client, company, providers
):
    cid = company["id"]
    cloud, local = providers
    client.patch(api(cid, f"/model-providers/{local['id']}"), json={"enabled": False})
    set_policy(client, cid, allow_paid_fallback=False)
    plain = worker(client, cid, cloud)
    # the owner chose this model in AI setup: no approval step for its management runs ...
    assert choose(app, plain["id"], "planning").needs_paid_approval is False
    # ... while a work run still follows the paid-fallback rule
    assert choose(app, plain["id"], "work").needs_paid_approval is True


def test_the_management_model_yields_to_an_owner_pin_or_a_disabled_provider(
    app, client, company, providers
):
    cid = company["id"]
    cloud, local = providers
    founder = agent_of_family(client, cid, "founder")
    pinned = new_agent(
        client, cid, name="Pia", kind="lead", runtime="company_agent", model_provider=local["id"]
    )
    r = client.patch(
        api(cid, f"/agents/{pinned['id']}"), json={"runtime_config": {"pin_model": True}}
    )
    assert r.status_code == 200, r.text
    route = choose(app, pinned["id"], "planning")
    assert route.provider_id == local["id"] and "pinned by the owner" in route.reason
    assert choose(app, founder["id"], "planning").provider_id == cloud["id"]
    # the management provider is disabled: the routing mode decides again (free_first -> the free model)
    local_worker = worker(client, cid, local, name="Lou")
    assert choose(app, local_worker["id"], "planning").provider_id == cloud["id"]
    client.patch(api(cid, f"/model-providers/{cloud['id']}"), json={"enabled": False})
    route = choose(app, local_worker["id"], "planning")
    assert route.provider_id == local["id"] and route.reason.startswith("free_first")


def test_work_runs_use_the_work_model_and_management_runs_do_not(app, client, company, providers):
    cid = company["id"]
    cloud, local = providers
    # work model = the paid cloud model, while free_first would otherwise prefer the local one
    set_policy(client, cid, work_model_provider_id=cloud["id"], work_model_name="work-model-1")
    plain = worker(client, cid, cloud)
    route = choose(app, plain["id"], "work")
    assert (route.provider_id, route.model) == (cloud["id"], "work-model-1")
    assert route.reason.startswith("work model") and route.free is False
    # no task passed: the old behaviour (the dispatcher of older code paths) is unchanged
    assert choose(app, plain["id"], None).provider_id == local["id"]
    # management runs keep the management model name, not the work model
    founder = agent_of_family(client, cid, "founder")
    assert choose(app, founder["id"], "work").model == "big-model"
    assert choose(app, plain["id"], "planning").model == "big-model"
    # a free work model carries no reservation
    set_policy(client, cid, work_model_provider_id=local["id"], work_model_name="qwen2.5:7b")
    route = choose(app, plain["id"], "work")
    assert route.provider_id == local["id"] and route.free is True
    # clearing it restores the routing mode
    set_policy(client, cid, work_model_provider_id=None, work_model_name=None)
    assert choose(app, plain["id"], "work").reason.startswith("free_first")


def test_an_owner_pin_beats_the_work_model(app, client, company, providers):
    cid = company["id"]
    cloud, local = providers
    set_policy(client, cid, work_model_provider_id=cloud["id"], work_model_name="work-model-1")
    pinned = worker(client, cid, cloud)
    client.patch(
        api(cid, f"/agents/{pinned['id']}"),
        json={"runtime_config": {"pin_model": True}, "model_provider": local["id"]},
    )
    route = choose(app, pinned["id"], "work")
    assert route.provider_id == local["id"] and "pinned by the owner" in route.reason


def test_a_disabled_work_model_falls_back_to_the_routing_mode(app, client, company, providers):
    cid = company["id"]
    cloud, local = providers
    other = client.post(
        api(cid, "/model-providers"),
        json={"name": "Local 2", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    set_policy(client, cid, work_model_provider_id=other["id"], work_model_name="qwen2.5:7b")
    plain = worker(client, cid, local, name="Lou")
    assert choose(app, plain["id"], "work").provider_id == other["id"]
    client.patch(api(cid, f"/model-providers/{other['id']}"), json={"enabled": False})
    route = choose(app, plain["id"], "work")
    assert route.provider_id == local["id"] and route.reason.startswith("free_first")


def test_cli_runtimes_are_not_routed(app, client, company, providers):
    cid = company["id"]
    cloud, _ = providers
    plain = worker(client, cid, cloud)
    assert choose(app, plain["id"], "work", runtime="cli:opencode").routed is False


# ------------------------------------------------------------------ the CLI role-family scope


def runtime_for(app, agent_id: str, kind: str = "work"):
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        limits = policy.load_policy(db, agent.company_id)
        task = Task(company_id=agent.company_id, title="t", kind=kind)
        return routing.choose_runtime(db, agent, task, limits)


def family_agent(app, client, cid, name, family):
    agent = new_agent(client, cid, name=name, runtime="company_agent")
    set_family(app, agent["id"], family)
    return agent


def hybrid_policy(app, cid, families):
    with app.state.session_factory() as db:
        limits = policy.load_policy(db, cid)
        limits.work_mode = "hybrid"
        limits.work_runtime = "cli:opencode"
        limits.cli_role_families = families
        db.commit()


def test_cli_role_families_keep_research_on_the_company_agent(app, client, company):
    cid = company["id"]
    engineer = family_agent(app, client, cid, "Eli", "engineering")
    researcher = family_agent(app, client, cid, "Ray", "research")
    designer = family_agent(app, client, cid, "Dee", "design")
    # null = every family goes to the CLI (today's behaviour)
    hybrid_policy(app, cid, None)
    for a in (engineer, researcher, designer):
        assert runtime_for(app, a["id"])[0] == "cli:opencode"
    # only the listed families go to the CLI; the others stay and say why
    hybrid_policy(app, cid, ["engineering", "design"])
    assert runtime_for(app, engineer["id"])[0] == "cli:opencode"
    assert runtime_for(app, designer["id"])[0] == "cli:opencode"
    runtime, why = runtime_for(app, researcher["id"])
    assert runtime == "company_agent" and "needs web" in why
    # an empty list sends nothing to the CLI
    hybrid_policy(app, cid, [])
    assert runtime_for(app, engineer["id"])[0] == "company_agent"


def test_the_family_scope_does_not_touch_management_or_non_work_tasks(app, client, company):
    cid = company["id"]
    engineer = family_agent(app, client, cid, "Eli", "engineering")
    hybrid_policy(app, cid, ["research"])
    runtime, why = runtime_for(app, engineer["id"], "planning")
    assert runtime == "company_agent" and "stay on the company agent" in why
    runtime, why = runtime_for(app, engineer["id"], TaskKind.WORK)
    assert runtime == "company_agent" and "needs web" in why


def test_the_policy_api_accepts_the_family_scope(client, company):
    cid = company["id"]
    r = client.patch(api(cid, "/policy"), json={"cli_role_families": ["engineering"]})
    assert r.status_code == 200 and r.json()["cli_role_families"] == ["engineering"]
