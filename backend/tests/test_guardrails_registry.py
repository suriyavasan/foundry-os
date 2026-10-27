"""The action registry (docs/specs/m6 section 1): every agent-API route and runtime tool has a risk
tier, and anything unregistered is denied (fail closed)."""

from __future__ import annotations

import json

from app.guardrails import registry
from app.models.enums import ActionTier
from app.services import agent_toolkit
from tests.guardrail_helpers import events, make_team, run_as
from tests.helpers import api

BASE = "/api/agent/v1"


def agent_routes(app) -> list[tuple[str, str]]:
    """Every (method, path) under /api/agent/v1, read from the generated OpenAPI document so it
    sees routes however the framework mounts them."""
    out = []
    for path, operations in app.openapi()["paths"].items():
        if path.startswith(BASE):
            for method in operations:
                if method.upper() not in ("HEAD", "OPTIONS"):
                    out.append((method.upper(), registry.relative_route(path)))
    return out


def test_every_agent_api_route_is_registered_with_a_tier(app):
    routes = agent_routes(app)
    assert len(routes) > 40  # the enumeration really found the agent API
    missing = [r for r in routes if registry.route_spec(*r) is None]
    assert not missing, f"agent-API routes without a risk tier: {missing}"
    for method, path in routes:
        spec = registry.route_spec(method, path)
        assert spec is not None
        if spec.dynamic:
            assert path == "/provider-actions"  # the only route whose action comes from the body
            continue
        action = registry.get_action(spec.action)
        assert action is not None, f"{method} {path} -> unknown action {spec.action}"
        assert action.tier in {t.value for t in ActionTier}


def test_no_registered_route_is_stale(app):
    live = set(agent_routes(app))
    stale = [key for key in registry.ROUTES if key not in live]
    assert not stale, f"registry lists routes that do not exist: {stale}"


def test_every_tool_has_a_tier_and_company_tools_map_to_actions():
    reg = registry.tool_registry()
    tiers = {t.value for t in ActionTier}
    for name, info in reg["tools"].items():
        assert info["tier"] in tiers, name
        assert registry.get_action(registry.tool_action(name)) is not None
    for name, info in reg["company_tools"].items():
        spec = registry.get_action(info["action"])
        assert spec is not None, f"{name} -> {info['action']}"
        assert registry.tier_of_tool(name) == spec.tier
    # every company tool the control plane can hand out is classified
    handed_out = {
        *agent_toolkit.WORKER_TOOLS,
        *agent_toolkit.MANAGER_EXTRA,
        *agent_toolkit.LEAD_TOOLS,
        *agent_toolkit.FOUNDER_TOOLS,
        *agent_toolkit.HR_TOOLS,
    }
    assert handed_out <= set(reg["company_tools"]), handed_out - set(reg["company_tools"])
    # toolsets only name registered tools
    for tools in reg["toolsets"].values():
        assert set(tools) <= set(reg["tools"])
    assert set(reg["always"]) <= set(reg["tools"])


def test_provider_actions_are_classified_in_the_spec_tiers():
    pa = registry.PROVIDER_ACTIONS
    assert pa["email.send"].tier == "external_communication"
    assert pa["terms.accept"].tier == "external_commitment"
    assert pa["purchase.make"].tier == "spend"
    assert pa["dns.change"].tier == "identity" and pa["dns.change"].human_default
    assert pa["resource.delete"].tier == "destructive"
    assert pa["github.read"].tier == "external_read"


def test_an_unregistered_route_is_denied_before_its_handler_runs(app, client, company, monkeypatch):
    cid = company["id"]
    team = make_team(client, cid)
    sess = run_as(app, client, cid, team.worker)
    assert sess.get("/context").status_code == 200
    monkeypatch.delitem(registry.ROUTES, ("GET", "/context"))
    r = sess.get("/context")
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "guardrail_denied" and "not registered" in err["message"]
    denied = [e for e in events(client, cid, decision="deny") if e["agent_id"] == team.worker["id"]]
    assert denied and denied[0]["tier"] == "destructive" and "fail closed" in denied[0]["reason"]


def test_an_unregistered_action_or_tool_is_denied(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    sess = run_as(app, client, cid, team.worker)
    r = sess.post("/guardrails/check-tool", json={"tool": "definitely_not_a_tool"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "guardrail_denied"
    # unknown provider action in the body: denied before anything else happens
    r = sess.post(
        "/provider-actions",
        json={"credential_id": "x", "action": "nuke.everything", "params": {}},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "guardrail_denied"
    # a registered read-only tool passes
    ok = sess.post("/guardrails/check-tool", json={"tool": "read_file"})
    assert ok.status_code == 200 and ok.json() == {
        "allowed": True,
        "tool": "read_file",
        "tier": "read",
    }


def test_run_spec_guardrails_block_lists_only_registered_tools():
    spec = registry.runtime_spec(["file", "web"], ["company_get_context", "company_create_task"])
    assert spec["tool_tiers"]["web_search"] == "external_read"
    assert spec["tool_tiers"]["company_create_task"] == "internal_write"
    assert "write_file" in spec["tool_tiers"] and "terminal" not in spec["tool_tiers"]
    assert spec["untrusted_tools"]["web_search"] == "web"
    assert (
        "external_read" in spec["server_checked_tiers"]
        and "read" not in spec["server_checked_tiers"]
    )
    json.dumps(spec)  # it travels as JSON


def test_effective_permissions_show_tiers_tools_and_rate_limits(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    r = client.get(api(cid, f"/agents/{team.worker['id']}/effective-permissions"))
    assert r.status_code == 200, r.text
    body = r.json()
    tiers = {t["tier"]: t for t in body["tiers"]}
    assert len(tiers) == 9
    assert tiers["read"]["decision"] == "allow" and tiers["read"]["source"] == "default"
    assert tiers["external_communication"]["decision"] == "peer_review"
    assert tiers["external_commitment"]["decision"] == "human_approval"
    assert tiers["destructive"]["decision"] == "human_approval"
    assert tiers["identity"]["decision"] == "cofounder_approval"
    names = {t["name"]: t for t in body["tools"]}
    assert names["company_create_task"]["tier"] == "internal_write"
    assert names["web_search"]["tier"] == "external_read"
    assert all(
        t["decision"] in {"allow", "peer_review", "cofounder_approval", "human_approval", "deny"}
        for t in body["tools"]
    )
    assert (
        client.get(api(cid, f"/agents/{team.worker['id']}/effective-permissions")).status_code
        == 200
    )
    assert client.get(api(cid, "/agents/nope/effective-permissions")).status_code == 404


def test_company_plan_project_has_the_tier_of_company_create_task_and_goes_to_planners_only(
    app, client, company
):
    reg = registry.tool_registry()["company_tools"]
    assert reg["company_plan_project"]["action"] == reg["company_create_task"]["action"]
    assert registry.tier_of_tool("company_plan_project") == registry.tier_of_tool(
        "company_create_task"
    )
    assert "company_plan_project" in agent_toolkit.LEAD_TOOLS
    assert "company_plan_project" in agent_toolkit.MANAGER_EXTRA
    assert "company_plan_project" not in agent_toolkit.WORKER_TOOLS
    assert (
        "company_plan_project" not in agent_toolkit.FOUNDER_TOOLS
    )  # the lead plans, never the Co-founder
    assert "company_plan_project" not in agent_toolkit.HR_TOOLS
    # and the role selection really hands it out
    from app.models.agent import Agent

    cid = company["id"]
    team = make_team(client, cid)
    with app.state.session_factory() as db:
        lead = db.get(Agent, team.manager["id"])
        worker = db.get(Agent, team.worker["id"])
        assert "company_plan_project" in agent_toolkit.company_tools_for(db, lead)
        assert "company_plan_project" not in agent_toolkit.company_tools_for(db, worker)
