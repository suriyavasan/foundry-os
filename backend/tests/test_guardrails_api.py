"""Guardrails API (docs/specs/m6 sections 1, 2, 7): rules are human-only and tenant-scoped, the
event log and rate-limit status, the rate-limit windows, and the M6 policy fields."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.guardrails import ratelimit, registry
from app.models.audit import AuditEvent
from app.models.autonomy import Alert
from app.models.run import CompanyEvent
from tests.conftest import human
from tests.guardrail_helpers import add_rule, connect_mail, events, make_team, run_as, send
from tests.helpers import api, set_policy

RULES = "/guardrails/rules"


# ------------------------------------------------------------------ rules: permissions & validation


def test_rules_crud_for_humans_with_audit_and_live_events(app, client, company):
    cid = company["id"]
    rule = add_rule(
        client,
        cid,
        tier="external_communication",
        decision="human_approval",
        conditions={"max_amount_micros": 5, "allowed_domains": ["@Example.COM", "example.com"]},
    )
    assert rule["action"] is None and rule["scope_type"] == "all" and rule["scope_value"] is None
    assert rule["conditions"] == {"max_amount_micros": 5, "allowed_domains": ["example.com"]}
    assert rule["created_by_type"] == "human"
    got = client.get(api(cid, f"{RULES}/{rule['id']}"))
    assert got.status_code == 200 and got.json()["id"] == rule["id"]
    listed = client.get(api(cid, RULES)).json()
    assert [r["id"] for r in listed["items"]] == [rule["id"]] and listed["next_cursor"] is None
    patched = client.patch(
        api(cid, f"{RULES}/{rule['id']}"),
        json={"decision": "deny", "action": "email.send", "conditions": {}},
    )
    assert patched.status_code == 200
    assert patched.json()["decision"] == "deny" and patched.json()["action"] == "email.send"
    assert patched.json()["updated_at"] >= rule["updated_at"]
    assert client.delete(api(cid, f"{RULES}/{rule['id']}")).status_code == 204
    assert client.get(api(cid, f"{RULES}/{rule['id']}")).status_code == 404
    with app.state.session_factory() as db:
        actions = [
            a.action
            for a in db.scalars(select(AuditEvent))
            if a.action.startswith("guardrail_rule")
        ]
        types = [
            e.type for e in db.scalars(select(CompanyEvent)) if e.type == "guardrail.rule_changed"
        ]
    assert actions == ["guardrail_rule.created", "guardrail_rule.updated", "guardrail_rule.deleted"]
    assert len(types) == 3


def test_rule_validation(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    base = {"scope_type": "all", "decision": "deny"}
    bad = [
        {**base, "tier": "nope"},
        {**base, "tier": "external_communication", "action": "email.nuke"},  # not registered
        {**base, "tier": "read", "action": "email.send"},  # action of another tier
        {**base, "tier": "spend", "decision": "maybe"},
        {**base, "tier": "spend", "scope_type": "family"},  # needs a scope_value
        {**base, "tier": "spend", "scope_type": "family", "scope_value": "plumbers"},
        {**base, "tier": "spend", "scope_value": team.worker["id"]},  # `all` takes none
        {**base, "tier": "spend", "conditions": {"max_amount_micros": -1}},
        {**base, "tier": "spend", "conditions": {"shoe_size": 9}},
        {**base, "tier": "spend", "conditions": {"allowed_domains": ["not a domain!"]}},
    ]
    for body in bad:
        assert client.post(api(cid, RULES), json=body).status_code == 422, body
    ghost = {**base, "tier": "spend", "scope_type": "agent", "scope_value": "no-such-agent"}
    assert client.post(api(cid, RULES), json=ghost).status_code == 404
    ok = add_rule(
        client,
        cid,
        tier="spend",
        decision="deny",
        scope_type="agent",
        scope_value=team.worker["id"],
    )
    # an update that makes it inconsistent is refused too
    assert (
        client.patch(api(cid, f"{RULES}/{ok['id']}"), json={"action": "email.send"}).status_code
        == 422
    )
    assert (
        client.patch(api(cid, f"{RULES}/{ok['id']}"), json={"scope_type": "all"}).json()[
            "scope_value"
        ]
        is None
    )


def test_agents_can_never_edit_or_read_guardrail_rules_even_with_a_run_token(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    rule = add_rule(client, cid, tier="spend", decision="deny")
    for who in (team.worker, team.manager, team.founder):
        auth = run_as(app, client, cid, who).headers
        assert (
            client.post(
                api(cid, RULES),
                json={"tier": "external_communication", "scope_type": "all", "decision": "allow"},
                headers=auth,
            ).status_code
            == 403
        )
        assert (
            client.patch(
                api(cid, f"{RULES}/{rule['id']}"), json={"decision": "allow"}, headers=auth
            ).status_code
            == 403
        )
        assert client.delete(api(cid, f"{RULES}/{rule['id']}"), headers=auth).status_code == 403
        assert client.get(api(cid, RULES), headers=auth).status_code == 403
        assert client.get(api(cid, f"{RULES}/{rule['id']}"), headers=auth).status_code == 403
        assert client.get(api(cid, "/guardrails/events"), headers=auth).status_code == 403
        assert client.get(api(cid, "/guardrails/rate-limits"), headers=auth).status_code == 403
        # nor the policy that holds the money and routing limits
        assert (
            client.patch(
                api(cid, "/policy"),
                json={
                    "stall_seconds": 3600,
                    "model_routing": "pinned",
                    "allow_paid_fallback": False,
                    "auto_spend_limit_micros": 10**9,
                },
                headers=auth,
            ).status_code
            == 403
        )
    still = client.get(api(cid, f"{RULES}/{rule['id']}")).json()
    assert still["decision"] == "deny"
    assert client.get(api(cid, "/policy")).json()["auto_spend_limit_micros"] == 0


def test_viewers_read_rules_but_cannot_change_them(
    app, client, company, make_user, add_member, client_as
):
    cid = company["id"]
    rule = add_rule(client, cid, tier="spend", decision="deny")
    viewer = make_user("viewer@example.com")
    add_member(cid, viewer.id, "viewer")
    as_viewer = client_as(human(viewer))
    assert as_viewer.get(api(cid, RULES)).status_code == 200
    assert as_viewer.get(api(cid, "/guardrails/events")).status_code == 200
    assert (
        as_viewer.post(
            api(cid, RULES), json={"tier": "spend", "scope_type": "all", "decision": "allow"}
        ).status_code
        == 403
    )
    assert (
        as_viewer.patch(api(cid, f"{RULES}/{rule['id']}"), json={"decision": "allow"}).status_code
        == 403
    )
    assert as_viewer.delete(api(cid, f"{RULES}/{rule['id']}")).status_code == 403


def test_rules_are_tenant_scoped(app, client, company, make_user, client_as):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    rule = add_rule(client, cid, tier="spend", decision="deny")
    assert client.get(api(other, f"{RULES}/{rule['id']}")).status_code == 404
    assert (
        client.patch(api(other, f"{RULES}/{rule['id']}"), json={"decision": "allow"}).status_code
        == 404
    )
    assert client.delete(api(other, f"{RULES}/{rule['id']}")).status_code == 404
    assert client.get(api(other, RULES)).json()["items"] == []
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(cid, RULES)).status_code == 404
    assert (
        stranger.post(
            api(cid, RULES), json={"tier": "spend", "scope_type": "all", "decision": "allow"}
        ).status_code
        == 404
    )
    # a rule of another company never changes this company's decisions
    team = make_team(client, cid)
    perms = client.get(api(cid, f"/agents/{team.worker['id']}/effective-permissions")).json()
    assert {t["tier"]: t for t in perms["tiers"]}["spend"]["source"] == "rule"
    perms_other = client.get(api(other, "/agents")).json()
    assert perms_other["items"]  # the other company has its own agents; its tiers stay default
    founder = next(a for a in perms_other["items"] if a["kind"] == "founder")
    own = client.get(api(other, f"/agents/{founder['id']}/effective-permissions")).json()
    assert {t["tier"]: t for t in own["tiers"]}["spend"]["source"] == "default"


def test_effective_permissions_follow_rules(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    rule = add_rule(client, cid, tier="external_communication", decision="deny")
    perms = client.get(api(cid, f"/agents/{team.worker['id']}/effective-permissions")).json()
    tier = {t["tier"]: t for t in perms["tiers"]}["external_communication"]
    assert (tier["decision"], tier["source"], tier["rule_id"]) == ("deny", "rule", rule["id"])
    action_rule = add_rule(
        client, cid, tier="external_read", action="tool.web_search", decision="deny"
    )
    perms = client.get(api(cid, f"/agents/{team.worker['id']}/effective-permissions")).json()
    tools = {t["name"]: t for t in perms["tools"]}
    assert (
        tools["web_search"]["decision"] == "deny"
        and tools["web_search"]["rule_id"] == action_rule["id"]
    )
    assert tools["read_file"]["decision"] == "allow"
    # the runtime hook refuses the denied tool through the control plane
    r = run_as(app, client, cid, team.worker).post(
        "/guardrails/check-tool", json={"tool": "web_search"}
    )
    assert r.status_code == 403 and r.json()["error"]["details"]["rule_id"] == action_rule["id"]


# ------------------------------------------------------------------ events


def test_event_log_filters_pagination_and_privacy(app, client, company):
    cid = company["id"]
    team = make_team(client, cid)
    mail = connect_mail(app, client, cid)
    worker = run_as(app, client, cid, team.worker)
    other = run_as(app, client, cid, team.manager)
    add_rule(client, cid, tier="external_communication", action="social.post", decision="deny")
    send(worker, mail)  # peer_review
    send(worker, mail, action="social.post")  # deny
    other.post("/guardrails/check-tool", json={"tool": "read_file"})  # a read: not logged
    send(other, mail, subject="x")  # peer_review by the manager
    everything = events(client, cid)
    assert {e["decision"] for e in everything} == {"peer_review", "deny"}
    assert all(e["payload_digest"] and len(e["payload_digest"]) == 64 for e in everything)
    assert all(e["task_id"] and e["run_id"] for e in everything)
    assert [e["decision"] for e in events(client, cid, decision="deny")] == ["deny"]
    assert {e["agent_id"] for e in events(client, cid, agent_id=team.worker["id"])} == {
        team.worker["id"]
    }
    assert all(
        e["tier"] == "external_communication"
        for e in events(client, cid, tier="external_communication")
    )
    assert events(client, cid, tier="spend") == []
    page = client.get(api(cid, "/guardrails/events"), params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = client.get(
        api(cid, "/guardrails/events"), params={"limit": 2, "cursor": page["next_cursor"]}
    ).json()
    assert len(rest["items"]) == len(everything) - 2
    # the log carries a digest, never the content that was sent
    assert "Hi Jane" not in str(everything)
    assert (
        client.get(api(cid, "/guardrails/events"), params={"decision": "bogus"}).status_code == 422
    )
    with app.state.session_factory() as db:
        assert (
            db.scalar(select(CompanyEvent).where(CompanyEvent.type == "guardrail.event"))
            is not None
        )


# ------------------------------------------------------------------ rate limits


def test_sliding_window_edges(app, company, monkeypatch):
    cid = company["id"]
    key = registry.HIRES
    monkeypatch.setitem(
        ratelimit.LIMITS, key, ratelimit.Limit(key, 3600, per_agent=2, per_company=3)
    )
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with app.state.session_factory() as db:
        ratelimit.record(db, cid, "agent-1", (key,), t0)
        ratelimit.record(db, cid, "agent-1", (key,), t0 + timedelta(seconds=10))
        db.commit()
        assert (
            ratelimit.exceeded(db, cid, "agent-1", (key,), t0 + timedelta(seconds=11)) is not None
        )
        # one second before the first hit leaves the window it still counts ...
        assert (
            ratelimit.exceeded(db, cid, "agent-1", (key,), t0 + timedelta(seconds=3599)) is not None
        )
        # ... a hit exactly one window old is outside it
        at_edge = t0 + timedelta(seconds=3600)
        assert ratelimit.exceeded(db, cid, "agent-1", (key,), at_edge) is None
        w = ratelimit.window_for(db, cid, key, at_edge, "agent-1")
        assert (w.used, w.limit) == (1, 2) and w.resets_at == t0 + timedelta(seconds=3610)
        assert ratelimit.exceeded(db, cid, "agent-1", (key,), t0 + timedelta(seconds=3610)) is None
        assert ratelimit.window_for(db, cid, key, t0 + timedelta(seconds=3610), "agent-1").used == 0
        # the per-agent window is the agent's own, the company window counts everybody
        ratelimit.record(db, cid, "agent-2", (key,), t0 + timedelta(seconds=20))
        db.commit()
        full = ratelimit.exceeded(db, cid, "agent-2", (key,), t0 + timedelta(seconds=30))
        assert full is not None and full.agent_id is None and full.used == 3  # 3 of the company's 3
        assert (
            ratelimit.exceeded(db, cid, "agent-3", (key,), t0 + timedelta(seconds=30)) is not None
        )
        assert ratelimit.window_for(db, cid, key, t0 + timedelta(seconds=30), "agent-2").used == 1
        later = t0 + timedelta(seconds=3700)
        assert ratelimit.exceeded(db, cid, "agent-2", (key,), later) is None
        assert ratelimit.prune(db, t0 + timedelta(days=3)) == 3


def test_api_call_limit_per_agent_and_company_answers_429(app, client, company, monkeypatch):
    cid = company["id"]
    team = make_team(client, cid)
    key = registry.API_CALLS
    monkeypatch.setitem(ratelimit.LIMITS, key, ratelimit.Limit(key, 60, per_agent=3, per_company=5))
    a, b = run_as(app, client, cid, team.worker), run_as(app, client, cid, team.manager)
    assert [a.get("/context").status_code for _ in range(3)] == [200, 200, 200]
    r = a.get("/context")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    d = r.json()["error"]["details"]
    assert (d["key"], d["scope"], d["limit"], d["window_seconds"]) == (key, "agent", 3, 60)
    assert d["retry_after_seconds"] >= 1
    assert (
        b.get("/context").status_code == 200 and b.get("/context").status_code == 200
    )  # 5 in total
    r = b.get("/context")
    assert r.status_code == 429 and r.json()["error"]["details"]["scope"] == "company"
    limited = events(client, cid, decision="rate_limited")
    assert limited and limited[0]["tier"] == "read"
    status = client.get(api(cid, "/guardrails/rate-limits")).json()["items"]
    company_row = next(i for i in status if i["key"] == key and i["agent_id"] is None)
    assert company_row["used"] >= 5 and company_row["limit"] == 5 and company_row["resets_at"]
    agent_row = next(i for i in status if i["key"] == key and i["agent_id"] == team.worker["id"])
    assert agent_row["limit"] == 3 and agent_row["window_seconds"] == 60
    only_worker = client.get(
        api(cid, "/guardrails/rate-limits"), params={"agent_id": team.worker["id"]}
    ).json()["items"]
    assert {i["agent_id"] for i in only_worker} == {None, team.worker["id"]}
    assert len([i for i in only_worker if i["agent_id"]]) == len(ratelimit.LIMITS)
    assert (
        client.get(api(cid, "/guardrails/rate-limits"), params={"agent_id": "nope"}).status_code
        == 404
    )


def test_action_limits_count_only_actions_that_happened_and_repeated_hits_raise_an_alert(
    app, client, company, monkeypatch
):
    cid = company["id"]
    team = make_team(client, cid)
    mail = connect_mail(app, client, cid)
    add_rule(client, cid, tier="external_communication", decision="allow")
    key = registry.EXTERNAL_COMMS
    monkeypatch.setitem(
        ratelimit.LIMITS, key, ratelimit.Limit(key, 86_400, per_agent=2, per_company=3)
    )
    worker = run_as(app, client, cid, team.worker)
    # a request that fails validation costs no quota
    bad = worker.post(
        "/provider-actions",
        json={"credential_id": "missing", "action": "email.send", "params": {"body": "x"}},
    )
    assert bad.status_code == 404
    assert send(worker, mail, subject="1").status_code == 200
    assert send(worker, mail, subject="2").status_code == 200
    third = send(worker, mail, subject="3")
    assert third.status_code == 429 and third.json()["error"]["details"]["key"] == key
    assert len(mail.calls) == 2
    # the company window (3) is shared: the manager gets one more, then none
    manager = run_as(app, client, cid, team.manager)
    assert send(manager, mail, subject="m1").status_code == 200
    again = send(manager, mail, subject="m2")
    assert again.status_code == 429 and again.json()["error"]["details"]["scope"] == "company"
    # a third refusal of the same agent within ten minutes raises one alert for the Founder
    with app.state.session_factory() as db:
        assert not [a for a in db.scalars(select(Alert)) if a.kind == "rate_limit"]
    send(worker, mail, subject="4")
    send(worker, mail, subject="5")
    with app.state.session_factory() as db:
        alerts = [a for a in db.scalars(select(Alert)) if a.kind == "rate_limit"]
    assert len(alerts) == 1 and alerts[0].related_id == team.worker["id"]
    assert alerts[0].related_type == "agent"


# ------------------------------------------------------------------ policy fields


def test_policy_m6_fields_defaults_validation_and_audit(app, client, company):
    cid = company["id"]
    p = client.get(api(cid, "/policy")).json()
    assert p["budget_alert_thresholds"] == [50, 80, 100] and p["model_routing"] == "free_first"
    assert p["allow_paid_fallback"] is True and p["auto_spend_limit_micros"] == 0
    assert p["stall_seconds"] == 180 and p["max_agent_monthly_budget_micros"] == 25_000_000
    assert p["hire_default_monthly_budget_micros"] == 5_000_000
    assert (p["max_tool_calls_per_run"], p["max_artifacts_per_run"], p["max_tokens_per_run"]) == (
        300,
        50,
        3_000_000,
    )
    assert p["ai_disclosure_enabled"] is True and p["ai_disclosure_text"] is None
    updated = set_policy(
        client,
        cid,
        model_routing="quality_first",
        allow_paid_fallback=False,
        auto_spend_limit_micros=2_000_000,
        stall_seconds=45,
        budget_alert_thresholds=[100, 25],
    )
    assert updated["budget_alert_thresholds"] == [25, 100] and updated["stall_seconds"] == 45
    for bad in (
        {"model_routing": "cheapest"},
        {"stall_seconds": 5},
        {"stall_seconds": 100_000},
        {"auto_spend_limit_micros": -1},
        {"budget_alert_thresholds": [0]},
        {"budget_alert_thresholds": list(range(1, 20))},
        {"model_routing": None},
        {"stall_seconds": None},
        {"max_tool_calls_per_run": 0},
    ):
        assert client.patch(api(cid, "/policy"), json=bad).status_code == 422, bad
    with app.state.session_factory() as db:
        changed = [a.data for a in db.scalars(select(AuditEvent)) if a.action == "policy.updated"]
    assert any("model_routing" in c.get("changed", []) for c in changed)
