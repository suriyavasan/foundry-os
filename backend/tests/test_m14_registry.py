"""M14 guardrail registry: tiers and default decisions of the GitHub actions, system actions that
agents can never call, the repo.create floor, human rules over action-level defaults, rate keys,
the verdict route and the runtime tool."""

from __future__ import annotations

import pytest

from app.core.errors import ApprovalPending, GuardrailDenied
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.guardrails import engine as guard_engine
from app.guardrails import ratelimit, registry, rules
from app.models.agent import Agent
from app.services import policy
from tests.guardrail_helpers import add_rule, connect_mail, events, make_team, run_as
from tests.helpers import api


def _agent_ctx(agent: Agent) -> CompanyContext:
    principal = Principal(PrincipalType.AGENT, agent.id, agent.name, agent.company_id)
    return CompanyContext(agent.company_id, principal, Role.AGENT)


@pytest.fixture
def team(client, company):
    return make_team(client, company["id"])


# action: (tier, kind, default decision for a regular agent)
EXPECTED = {
    "github.read": ("external_read", "provider", "allow"),
    "github.issue.read": ("external_read", "provider", "allow"),
    "github.pr.read": ("external_read", "provider", "allow"),
    "github.checks.read": ("external_read", "provider", "allow"),
    "github.alerts.read": ("external_read", "provider", "allow"),
    "github.issue.comment": ("external_communication", "provider", "peer_review"),
    "github.pr.comment": ("external_communication", "provider", "peer_review"),
    "github.issue.create": ("external_communication", "provider", "peer_review"),
    "github.issue.update": ("external_communication", "provider", "peer_review"),
    "github.release.draft": ("external_communication", "provider", "peer_review"),
    "github.release.publish": ("external_commitment", "provider", "human_approval"),
    "github.repo.create": ("identity", "provider", "human_approval"),
    "github.pr.open": ("external_communication", "system", "allow"),
    "github.pr.update": ("external_communication", "system", "allow"),
    "github.branch.push": ("external_communication", "system", "allow"),
    "github.push.default": ("external_commitment", "system", "allow"),
    "github.pr.merge": ("external_commitment", "system", "allow"),
    "github.branch.delete": ("destructive", "system", "allow"),
    "repo.pr.review": ("internal_write", "api", "allow"),
}
SYSTEM = {a for a, (_, kind, _) in EXPECTED.items() if kind == "system"}
PROVIDER = {a for a, (_, kind, _) in EXPECTED.items() if kind == "provider"}


def test_every_m14_action_has_its_tier_kind_and_default_decision(app, client, team):
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        limits = policy.load_policy(db, team.cid)
        for action, (tier, kind, decision) in EXPECTED.items():
            spec = registry.get_action(action)
            assert spec is not None, action
            assert (spec.tier, spec.kind) == (tier, kind), action
            assert rules.resolve(db, agent, spec, limits, rules.Facts()).decision == decision, (
                action
            )


def test_system_actions_are_registered_but_never_provider_actions():
    assert set(registry.SYSTEM_ACTIONS) == SYSTEM
    assert SYSTEM.isdisjoint(registry.PROVIDER_ACTIONS)
    assert set(registry.PROVIDER_ACTIONS) >= PROVIDER
    # the old M8 placeholder moved: github.pr.open is no longer a provider action
    assert "github.pr.open" not in registry.PROVIDER_ACTIONS
    assert registry.ACTIONS["github.pr.open"].kind == "system"


def test_an_agent_cannot_reach_a_system_action_through_provider_actions(app, client, team):
    cid = team.cid
    mail = connect_mail(app, client, cid, actions=("github.*",), provider="github")
    worker = run_as(app, client, cid, team.worker)
    for action in sorted(SYSTEM):
        r = worker.post(
            "/provider-actions",
            json={"credential_id": mail.id, "action": action, "params": {"repo": "a/b"}},
        )
        assert r.status_code == 403, action
        assert r.json()["error"]["code"] == "guardrail_denied", action
        assert "provider action" in r.json()["error"]["message"], action
    assert mail.calls == []  # nothing was executed


def test_credentials_cannot_be_scoped_to_system_actions(client, company):
    cid = company["id"]
    ok = client.post(
        api(cid, "/credentials"),
        json={
            "name": "gh",
            "value": "v" * 20,
            "kind": "token",
            "provider": "github",
            "allowed_actions": ["github.*"],
        },
    )
    assert ok.status_code == 201, ok.text
    for action in SYSTEM:
        r = client.post(
            api(cid, "/credentials"),
            json={
                "name": f"gh-{action}",
                "value": "v" * 20,
                "kind": "token",
                "provider": "github",
                "allowed_actions": [action],
            },
        )
        assert r.status_code == 422, action
    # the wildcard expands to provider actions only
    from app.guardrails.providers import action_allowed

    assert action_allowed(ok.json()["allowed_actions"], "github.pr.comment")


def test_repo_create_always_needs_a_human_whatever_the_rules_say(app, client, team):
    cid = team.cid
    add_rule(client, cid, tier="identity", action="github.repo.create", decision="allow")
    add_rule(client, cid, tier="identity", decision="cofounder_approval")
    add_rule(
        client,
        cid,
        tier="identity",
        action="github.repo.create",
        decision="allow",
        scope_type="agent",
        scope_value=team.worker["id"],
    )
    spec = registry.PROVIDER_ACTIONS["github.repo.create"]
    assert spec.human_required and spec.default_decision == "human_approval"
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        limits = policy.load_policy(db, cid)
        assert rules.resolve(db, agent, spec, limits, rules.Facts()).decision == "human_approval"
    # ... but a human can still forbid it outright
    add_rule(
        client,
        cid,
        tier="identity",
        action="github.repo.create",
        decision="deny",
        scope_type="agent",
        scope_value=team.worker["id"],
    )
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        limits = policy.load_policy(db, cid)
        assert rules.resolve(db, agent, spec, limits, rules.Facts()).decision == "deny"


def test_a_human_rule_overrides_an_action_level_default(app, client, team):
    cid = team.cid
    spec = registry.ACTIONS["github.pr.merge"]
    assert spec.default_decision == "allow" and spec.tier == "external_commitment"
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        limits = policy.load_policy(db, cid)
        assert rules.default_decision(spec, limits, rules.Facts()) == "allow"
    rule = add_rule(
        client, cid, tier="external_commitment", action="github.pr.merge", decision="human_approval"
    )
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        res = rules.resolve(db, agent, spec, policy.load_policy(db, cid), rules.Facts())
        assert (res.decision, res.source, res.rule.id) == ("human_approval", "rule", rule["id"])


def test_the_engine_checks_system_actions_under_an_agent_context(app, client, team):
    """repo_gate's mechanism: engine.check under the lead's context. Allowed by default, parked
    behind an approval by a human rule, denied by a deny rule, counted by the push rate key."""
    cid = team.cid
    payload = {"repo": "acme/app", "number": 3, "head_sha": "a" * 40, "method": "squash"}
    with app.state.session_factory() as db:
        verdict = guard_engine.check(
            db, _agent_ctx(db.get(Agent, team.manager["id"])), "github.pr.merge", payload
        )
        assert verdict.action == "github.pr.merge" and verdict.rate_keys == (
            "github_pushes_per_hour",
        )
    add_rule(
        client, cid, tier="external_commitment", action="github.pr.merge", decision="human_approval"
    )
    with app.state.session_factory() as db:
        with pytest.raises(ApprovalPending) as exc:
            guard_engine.check(
                db, _agent_ctx(db.get(Agent, team.manager["id"])), "github.pr.merge", payload
            )
        assert exc.value.fields["approval_id"]
    add_rule(client, cid, tier="external_commitment", action="github.pr.merge", decision="deny")
    with app.state.session_factory() as db, pytest.raises(GuardrailDenied):
        guard_engine.check(
            db, _agent_ctx(db.get(Agent, team.manager["id"])), "github.pr.merge", payload
        )
    assert any(e["action"] == "github.pr.merge" for e in events(client, cid))


def test_the_github_rate_keys_exist_and_agent_writes_do_not_use_the_generic_one():
    lim = ratelimit.LIMITS
    assert (
        lim["github_comments_per_day"].per_agent,
        lim["github_comments_per_day"].per_company,
    ) == (30, 100)
    assert lim["github_comments_per_day"].window_seconds == ratelimit.DAY
    assert (lim["github_writes_per_hour"].per_agent, lim["github_writes_per_hour"].per_company) == (
        10,
        30,
    )
    assert (lim["github_pushes_per_hour"].per_agent, lim["github_pushes_per_hour"].per_company) == (
        20,
        60,
    )
    assert lim["github_pushes_per_hour"].window_seconds == ratelimit.HOUR
    for action in (
        "github.issue.comment",
        "github.pr.comment",
        "github.issue.create",
        "github.issue.update",
    ):
        assert registry.ACTIONS[action].rate_keys == ("github_comments_per_day",), action
    for action in ("github.release.draft", "github.release.publish"):
        assert registry.ACTIONS[action].rate_keys == ("github_writes_per_hour",), action
    for action in (
        "github.pr.open",
        "github.pr.update",
        "github.branch.push",
        "github.push.default",
        "github.pr.merge",
    ):
        assert registry.ACTIONS[action].rate_keys == ("github_pushes_per_hour",), action
    assert registry.EXTERNAL_COMMS not in {
        k for a in EXPECTED for k in registry.ACTIONS[a].rate_keys
    }


def test_the_review_verdict_route_and_runtime_tool_are_registered():
    route = registry.route_spec("POST", "/repo/pulls/{pull_id}/review")
    assert route is not None and route.action == "repo.pr.review" and not route.dynamic
    assert registry.company_tool_action("company_review_pull_request") == "repo.pr.review"
    assert registry.tier_of_tool("company_review_pull_request") == "internal_write"


def test_the_verdict_route_is_gated_like_every_agent_route(app, client, team):
    """No real pull request: the gate runs first (registered -> not a 403), then the service
    answers 404 for a pull request that does not exist in the company."""
    worker = run_as(app, client, team.cid, team.worker)
    r = worker.post("/repo/pulls/p1/review", json={"decision": "accept", "note": "ok"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    # an invalid decision is a validation error before the service is reached
    bad = worker.post("/repo/pulls/p1/review", json={"decision": "yes"})
    assert bad.status_code == 422
