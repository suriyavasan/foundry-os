"""Which radar tools each role is offered, and that the offer matches what the server allows."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.guardrails import registry
from app.models.agent import Agent
from app.radar import access
from app.services import agent_toolkit
from tests.helpers import agent_of_family, api, new_agent
from tests.radar.wp2_helpers import radar_world, set_radar_policy, start_run

RUNTIME_SPEC = Path(__file__).resolve().parents[3] / "runtime" / "company_agent" / "spec.py"
T = access.TOOL_OF


def tools_of(app, agent_id: str) -> list[str]:
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        return [
            t for t in agent_toolkit.company_tools_for(db, agent) if t.startswith("company_radar_")
        ]


def test_each_role_gets_its_tools(app, client, company):
    cid, _, _ = radar_world(app, client, company)
    founder = agent_of_family(client, cid, "founder")
    assert tools_of(app, founder["id"]) == [
        T[access.READ], T[access.PAGE], T[access.BRIEF], T[access.PROPOSE], T[access.DECIDE],
        T[access.KEYWORDS], T[access.SOURCE],
    ]  # fmt: skip
    researcher = new_agent(
        client, cid, name="Rhea", role_title="R", role_template="market-researcher"
    )
    assert tools_of(app, researcher["id"]) == [
        T[access.READ], T[access.PAGE], T[access.BRIEF], T[access.PROPOSE], T[access.KEYWORDS],
        T[access.SOURCE], T[access.UI],
    ]  # fmt: skip
    designer = new_agent(client, cid, name="Dee", role_title="D", role_template="product-designer")
    assert tools_of(app, designer["id"]) == [
        T[access.READ], T[access.PAGE], T[access.BRIEF], T[access.PROPOSE], T[access.UI]
    ]  # fmt: skip
    qa = new_agent(client, cid, name="Quin", role_title="Q", role_template="qa-engineer")
    assert tools_of(app, qa["id"]) == [
        T[access.READ],
        T[access.PAGE],
        T[access.BRIEF],
        T[access.PROPOSE],
    ]
    lead = new_agent(client, cid, name="Lena", kind="lead", role_title="Lead")
    assert tools_of(app, lead["id"]) == [T[access.READ], T[access.PAGE], T[access.DECIDE]]
    plain = new_agent(client, cid, name="Pat", role_title="Writer", tool_permissions=["company"])
    assert tools_of(app, plain["id"]) == []  # no radar role and no web toolset
    web_worker = new_agent(
        client, cid, name="Wes", role_title="W", role_template="software-engineer"
    )
    assert tools_of(app, web_worker["id"]) == [T[access.PAGE]]  # any agent with the web toolset


def test_a_disabled_radar_offers_only_the_page_reader(app, client, company):
    cid, _, _ = radar_world(app, client, company)
    set_radar_policy(app, cid, radar_enabled=False)
    founder = agent_of_family(client, cid, "founder")
    researcher = new_agent(
        client, cid, name="Rhea", role_title="R", role_template="market-researcher"
    )
    assert tools_of(app, founder["id"]) == [T[access.PAGE]]
    assert tools_of(app, researcher["id"]) == [T[access.PAGE]]


def test_the_offer_and_the_servers_check_agree(app, client, company):
    """A tool a role is offered is never refused for its role, and one it is not offered is."""
    cid, _, items = radar_world(app, client, company)
    agents = {
        "founder": agent_of_family(client, cid, "founder"),
        "plain": new_agent(
            client, cid, name="Pat", role_title="Writer", tool_permissions=["company"]
        ),
        "qa": new_agent(client, cid, name="Quin", role_title="Q", role_template="qa-engineer"),
    }
    for who, agent in agents.items():
        sess = start_run(app, client, cid, agent["id"])
        offered = set(tools_of(app, agent["id"]))
        digest = sess.get("/radar/digest").status_code
        ui = sess.post("/radar/ui-search", json={"query": "pricing page"}).status_code
        assert (digest != 403) == (T[access.READ] in offered), who
        assert (ui != 403) == (T[access.UI] in offered), who
    assert client.get(api(cid, "/radar")).status_code == 200


def test_runtime_spec_and_tools_json_list_the_same_eight_tools():
    block = RUNTIME_SPEC.read_text(encoding="utf-8")
    block = block[block.index("COMPANY_TOOL_NAMES = (") :]
    runtime_names = set(re.findall(r'"(company_[a-z_]+)"', block[: block.index("\n)\n")]))
    registered = set(registry.tool_registry()["company_tools"])
    assert runtime_names == registered
    assert set(access.RADAR_TOOL_NAMES) <= runtime_names and len(access.RADAR_TOOL_NAMES) == 8
    for name in access.RADAR_TOOL_NAMES:
        assert registry.tier_of_tool(name) in ("read", "internal_write", "external_read")
    assert registry.tier_of_tool(T[access.PAGE]) == "external_read"
    assert registry.tier_of_tool(T[access.DECIDE]) == "internal_write"


@pytest.mark.parametrize("capability", access.CAPABILITIES)
def test_every_capability_has_a_registered_action(capability):
    action = access.ACTION_OF[capability]
    assert registry.get_action(action) is not None
    assert registry.company_tool_action(access.TOOL_OF[capability]) == action
