"""M15 WP3: who gets the purchase tools (docs/specs/m15-agent-cards-and-purchasing.md section 4.4.1).

Every working role may ask the company to buy something; the AI Co-founder answers subscription
renewal reviews. Enforcement stays on the server (the agent-API routes), these tests pin the
toolkit selection, the guardrail classification and the runtime/backend name agreement."""

from __future__ import annotations

import re
from pathlib import Path

from app.guardrails import registry
from app.models.agent import Agent
from app.models.work import Task
from app.runtimes.base import TaskSpec
from app.runtimes.cli.base import CLI_MCP_TOOLS
from app.services import agent_toolkit
from tests.guardrail_helpers import make_team, set_family
from tests.helpers import api, new_agent
from tests.test_company_agent_adapter import (  # noqa: F401  (fixtures)
    _inspect,
    env,
    fake_runtime_dir,
    skills_env_override,
)

REQUEST = "company_request_purchase"
REVIEW = "company_review_subscription"
RUNTIME_SPEC = Path(__file__).resolve().parents[2] / "runtime" / "company_agent" / "spec.py"


def test_tools_are_classified_and_match_the_step_0_routes():
    reg = registry.tool_registry()["company_tools"]
    assert reg[REQUEST]["action"] == "purchase.request"
    assert reg[REVIEW]["action"] == "subscription.review"
    # neither tool can move money: they are internal writes (the money moment is the SPEND action
    # evaluated inside the purchasing service)
    assert registry.tier_of_tool(REQUEST) == "internal_write"
    assert registry.tier_of_tool(REVIEW) == "internal_write"
    routes = {spec.action: path for (_, path), spec in registry.ROUTES.items()}
    assert routes["purchase.request"] == "/purchases"
    assert routes["subscription.review"] == "/subscriptions/{subscription_id}/review"


def test_runtime_and_backend_agree_on_the_tool_names():
    spec_text = RUNTIME_SPEC.read_text(encoding="utf-8")
    block = spec_text[spec_text.index("COMPANY_TOOL_NAMES = (") :]
    runtime_names = set(re.findall(r'"(company_[a-z_]+)"', block[: block.index("\n)\n")]))
    assert {REQUEST, REVIEW} <= runtime_names
    assert runtime_names == set(registry.tool_registry()["company_tools"])
    handed_out = {
        *agent_toolkit.WORKER_TOOLS,
        *agent_toolkit.MANAGER_EXTRA,
        *agent_toolkit.LEAD_TOOLS,
        *agent_toolkit.FOUNDER_TOOLS,
        *agent_toolkit.HR_TOOLS,
    }
    assert handed_out <= runtime_names


def test_working_roles_request_and_the_cofounder_reviews():
    for working in (
        agent_toolkit.WORKER_TOOLS,
        agent_toolkit.LEAD_TOOLS,
        agent_toolkit.HR_TOOLS,
    ):
        assert REQUEST in working
        assert REVIEW not in working
    assert REQUEST in {*agent_toolkit.WORKER_TOOLS, *agent_toolkit.MANAGER_EXTRA}  # managers
    assert REVIEW in agent_toolkit.FOUNDER_TOOLS
    assert REQUEST not in agent_toolkit.FOUNDER_TOOLS  # the strategic loop does not shop


def test_role_selection_hands_out_the_tools(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    hr = new_agent(client, cid, name="Hana", role_title="HR Partner")
    set_family(app, hr["id"], "hr")
    with app.state.session_factory() as db:
        tools = {
            name: agent_toolkit.company_tools_for(db, db.get(Agent, agent["id"]))
            for name, agent in (
                ("founder", team.founder),
                ("lead", team.manager),
                ("worker", team.worker),
                ("hr", hr),
            )
        }
    assert REVIEW in tools["founder"] and REQUEST not in tools["founder"]
    for role in ("lead", "worker", "hr"):
        assert REQUEST in tools[role], role
        assert REVIEW not in tools[role], role
    assert (
        len(tools["worker"]) <= 15
    )  # small models degrade with many tools (M18 added two, M21 two, M22 one)
    assert "company_request_human_work" in tools["worker"]  # M22: every role, the one new tool
    assert "company_draft_outsource_job" in tools["hr"]  # M22: HR only
    for role in ("founder", "lead", "worker"):
        assert "company_draft_outsource_job" not in tools[role], role


def test_manager_role_gets_the_request_tool(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    # a worker with a live report is a manager
    new_agent(client, cid, name="Rae", role_title="Intern", manager_agent_id=team.worker["id"])
    with app.state.session_factory() as db:
        assert REQUEST in agent_toolkit.company_tools_for(db, db.get(Agent, team.worker["id"]))


def test_an_owner_can_remove_the_tool_but_never_add_one(app, client, company):
    team = make_team(client, company["id"])
    with app.state.session_factory() as db:
        worker = db.get(Agent, team.worker["id"])
        narrowed = agent_toolkit.company_tools_for(
            db, worker, ["company_get_context", "company_complete_task"]
        )
        assert narrowed == ["company_get_context", "company_complete_task"]
        # asking for the Co-founder's review tool does not add it to a worker
        assert REVIEW not in agent_toolkit.company_tools_for(db, worker, [REVIEW, REQUEST])


def test_effective_permissions_list_the_tools_with_their_tier(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    resp = client.get(api(cid, f"/agents/{team.worker['id']}/effective-permissions"))
    assert resp.status_code == 200, resp.text
    by_name = {t["name"]: t for t in resp.json()["tools"]}
    assert by_name[REQUEST]["tier"] == "internal_write"
    assert REVIEW not in by_name


def test_coding_clis_get_only_the_request_tool():
    assert "request_purchase" in CLI_MCP_TOOLS
    assert "review_subscription" not in CLI_MCP_TOOLS
    assert all(f"company_{t}" in registry.tool_registry()["company_tools"] for t in CLI_MCP_TOOLS)


def test_the_assignee_of_a_renewal_review_task_is_handed_the_review_tool(env):  # noqa: F811
    """A run on a ``subrev:`` task (services/subscriptions.py) lists ``company_review_subscription``
    whoever the reviewer is (a worker or a lead as well as the Co-founder); an ordinary run does
    not get it. The server still decides who may answer (``subscriptions.review``)."""
    harper = env.agent["id"]
    with env.app.state.session_factory() as db:
        review = Task(
            company_id=env.cid, title="Review subscription: Linear renews 2026-11-01", kind="routine",
            status="in_progress", created_by_type="system", assignee_agent_id=harper,
            idempotency_key="subrev:sub-1:2026-11-01",
        )  # fmt: skip
        plain = Task(
            company_id=env.cid, title="Plain routine", kind="routine", status="in_progress",
            created_by_type="system", assignee_agent_id=harper, idempotency_key="subcancel:sub-1:x",
        )  # fmt: skip
        db.add_all([review, plain])
        db.commit()
        review_id, plain_id = review.id, plain.id

    def tools(task_id: str) -> list[str]:
        spec = TaskSpec(task_id, "t scenario:inspect", "d", None, "routine", "high", [])
        return _inspect(env, task=spec)["company_tools"]

    assert REVIEW in tools(review_id)
    assert REVIEW not in tools(plain_id)
