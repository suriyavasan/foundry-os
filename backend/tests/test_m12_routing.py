"""M12 roles and models, on the M17 AI stack: management runs are heavy work and use the heavy
connection, work runs of specialists use the everyday one, owner pins win, and a CLI runtime is not
routed to a model. (The M12 hybrid / work-model / role-family routing it used to test is gone:
docs/specs/m17-ai-stack.md section 4.)"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.agent import Agent
from app.models.work import Task
from app.services import policy, routing
from tests.helpers import agent_of_family, api, new_agent
from tests.m10_helpers import add_connection


@pytest.fixture
def providers(app, client, company):
    """Two model providers: a paid cloud one and a free local one. The stack: the cloud model for heavy
    work, the local model for everyday work."""
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
    heavy = add_connection(
        app, client, cid, "openai_api", model_provider_id=cloud["id"], model="big-model",
        use_for="heavy",
    )  # fmt: skip
    everyday = add_connection(
        app, client, cid, "ollama", model_provider_id=local["id"], model="qwen2.5:7b",
        use_for="everyday",
    )  # fmt: skip
    return cloud, local, heavy, everyday


def decide(app, agent_id: str, kind: str = "work", *, runtime: str | None = None):
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        if runtime:
            agent.runtime = runtime
        task = Task(company_id=agent.company_id, title="t", kind=kind)
        return routing.decide(
            db,
            agent,
            task,
            policy.load_policy(db, agent.company_id),
            now=datetime.now(UTC),
            cli_env=app.state.cli_env,
        )


def worker(client, cid, name="Wes", **kw):
    return new_agent(client, cid, name=name, runtime="company_agent", **kw)


def test_management_runs_use_the_heavy_connection_and_specialist_work_the_everyday_one(
    app, client, company, providers
):
    cid = company["id"]
    cloud, local, heavy, everyday = providers
    founder = agent_of_family(client, cid, "founder")
    lead = new_agent(client, cid, name="Lena", kind="lead", runtime="company_agent")
    boss = worker(client, cid, name="Boss")
    worker(client, cid, name="Report", manager_agent_id=boss["id"])
    plain = worker(client, cid, name="Plain")
    heavy_cases = {
        "founder work": (founder["id"], "work"),
        "lead work": (lead["id"], "work"),
        "manager work": (boss["id"], "work"),
        "worker planning": (plain["id"], "planning"),
        "worker review": (plain["id"], "review"),
        "worker hiring": (plain["id"], "hiring"),
        "worker routine": (plain["id"], "routine"),
    }
    for name, (agent_id, kind) in heavy_cases.items():
        got = decide(app, agent_id, kind)
        assert got.runtime == "company_agent" and got.stacked, name
        assert got.route.provider_id == cloud["id"] and got.route.model == "big-model", name
        assert got.route.ai_connection_id == heavy["id"] and got.route.free is False, name
        assert got.route.reason.endswith("for heavy work"), name
    got = decide(app, plain["id"], "work")
    assert got.route.provider_id == local["id"] and got.route.ai_connection_id == everyday["id"]
    assert got.route.free is True and got.route.reason.endswith("for everyday work")


def test_the_agents_own_provider_is_ignored_by_stack_routing(app, client, company, providers):
    cid = company["id"]
    cloud, local, _, everyday = providers
    agent = worker(client, cid, model_provider=cloud["id"], model_name="big-model")
    assert decide(app, agent["id"], "work").route.provider_id == local["id"]


def test_owner_pins_beat_the_stack(app, client, company, providers):
    cid = company["id"]
    cloud, local, _, _ = providers
    pinned = new_agent(client, cid, name="Pia", runtime="company_agent", model_provider=local["id"])
    r = client.patch(
        api(cid, f"/agents/{pinned['id']}"), json={"runtime_config": {"pin_model": True}}
    )
    assert r.status_code == 200, r.text
    for kind in ("work", "planning"):
        got = decide(app, pinned["id"], kind)
        assert got.route.provider_id == local["id"] and not got.stacked
        assert got.route.ai_connection_id is None and "pinned by the owner" in got.route.reason
    # pin_runtime keeps the agent's runtime
    other = worker(client, cid, name="Rae")
    client.patch(api(cid, f"/agents/{other['id']}"), json={"runtime_config": {"pin_runtime": True}})
    got = decide(app, other["id"], "work")
    assert got.runtime == "company_agent" and not got.stacked
    assert got.runtime_reason == "agent runtime pinned by the owner"


def test_an_agent_on_a_cli_runtime_is_an_override_and_is_not_routed_to_a_model(
    app, client, company, providers
):
    cid = company["id"]
    plain = worker(client, cid)
    got = decide(app, plain["id"], "work", runtime="cli:opencode")
    assert got.runtime == "cli:opencode" and not got.stacked and got.route.routed is False
    assert got.runtime_reason == "agent runtime override"
    with app.state.session_factory() as db:
        agent = db.get(Agent, plain["id"])
        assert routing.choose(db, agent, None, "cli:opencode").routed is False


def test_other_runtimes_and_a_company_without_a_stack_keep_the_agents_own_runtime(
    app, client, company
):
    cid = company["id"]
    scripted = new_agent(client, cid, name="Sid")  # runtime "scripted"
    got = decide(app, scripted["id"], "work")
    assert got.runtime == "scripted" and not got.stacked and got.route == routing.Route()
    own = worker(client, cid, name="Ola")  # company_agent, but the company has no connections
    got = decide(app, own["id"], "work")
    assert got.runtime == "company_agent" and not got.stacked and got.waits is False


def test_the_policy_api_still_accepts_the_family_scope(client, company):
    cid = company["id"]
    r = client.patch(api(cid, "/policy"), json={"cli_role_families": ["engineering"]})
    assert r.status_code == 200 and r.json()["cli_role_families"] == ["engineering"]
