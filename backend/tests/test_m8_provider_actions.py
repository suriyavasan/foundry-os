"""Provider actions of M8 through the guardrail framework: tiers per action, human-only floors,
approvals bound to the exact call, single use, parameters validated before anyone is asked."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from sqlalchemy import select

from app.guardrails import registry, rules
from app.models.agent import Agent
from app.models.enums import GuardrailScope
from app.models.governance import Approval
from app.models.guardrails import ExternalExpense
from app.models.reachability import CompanyDomain, DomainRegistration
from app.services import policy
from tests.guardrail_helpers import add_rule, events, make_team, run_as
from tests.helpers import api
from tests.m8_helpers import (
    SECRET,
    SECRET2,
    cf,
    claim_owner,
    cloudflare_routes,
    connect,
    fake_cloudflared,
    store_secret,
    wait_for,
)


@pytest.fixture
def team(client, company):
    return make_team(client, company["id"])


def act(session, credential_id: str, action: str, **params):
    return session.post(
        "/provider-actions",
        json={"credential_id": credential_id, "action": action, "params": params},
    )


def decide(client, cid: str, approval_id: str, decision: str = "approve"):
    r = client.post(api(cid, f"/approvals/{approval_id}/{decision}"), json={})
    assert r.status_code == 200, r.text
    return r.json()


def approval(client, cid, approval_id) -> dict:
    return client.get(api(cid, f"/approvals/{approval_id}")).json()


# ------------------------------------------------------------------ the tier of every action

EXPECTED = {
    # action: (tier, default decision for a regular agent)
    "dns.read": ("external_read", "allow"),
    "domain.search": ("external_read", "allow"),
    "domain.preview": ("external_read", "allow"),
    "hosted_ui.status": ("external_read", "allow"),
    "tunnel.status": ("external_read", "allow"),
    "email.read": ("external_read", "allow"),
    "github.read": ("external_read", "allow"),
    "dns.change": ("identity", "human_approval"),
    "domain.register": ("external_commitment", "human_approval"),
    "domain.register_pr": ("external_commitment", "human_approval"),
    "domain.purchase": ("spend", "human_approval"),
    "hosted_ui.deploy": ("identity", "cofounder_approval"),
    "tunnel.start": ("identity", "cofounder_approval"),
    "tunnel.stop": ("identity", "cofounder_approval"),
    "mailbox.create": ("identity", "cofounder_approval"),
    "email.send": ("external_communication", "peer_review"),
}


def test_every_m8_provider_action_has_its_tier_and_default_decision(app, client, team):
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        limits = policy.load_policy(db, team.cid)
        for action, (tier, decision) in EXPECTED.items():
            spec = registry.PROVIDER_ACTIONS[action]
            assert spec.tier == tier, action
            facts = rules.Facts(amount_micros=5_000_000) if tier == "spend" else rules.Facts()
            assert rules.resolve(db, agent, spec, limits, facts).decision == decision, action
    # the old placeholder name is gone: one action, one name
    assert (
        "ui.deploy" not in registry.PROVIDER_ACTIONS
        and "hosted_ui.deploy" in registry.PROVIDER_ACTIONS
    )


def test_money_and_domain_ownership_can_not_be_lowered_by_a_rule(app, client, team):
    """A human can write an allow rule, but domain registration and purchase still need a human."""
    cid = team.cid
    for action, tier in (
        ("domain.purchase", "spend"),
        ("domain.register_pr", "external_commitment"),
        ("domain.register", "external_commitment"),
    ):
        add_rule(client, cid, tier=tier, action=action, decision="allow")
        add_rule(client, cid, tier=tier, decision="cofounder_approval")
    with app.state.session_factory() as db:
        agent = db.get(Agent, team.worker["id"])
        limits = policy.load_policy(db, cid)
        for action in ("domain.purchase", "domain.register_pr", "domain.register"):
            spec = registry.PROVIDER_ACTIONS[action]
            res = rules.resolve(db, agent, spec, limits, rules.Facts(amount_micros=1))
            assert res.decision == "human_approval", action
            assert spec.human_required
        # ... but a human can still forbid them outright
    add_rule(client, cid, tier="spend", action="domain.purchase", decision="deny")
    with app.state.session_factory() as db:
        res = rules.resolve(
            db,
            db.get(Agent, team.worker["id"]),
            registry.PROVIDER_ACTIONS["domain.purchase"],
            policy.load_policy(db, cid),
            rules.Facts(),
        )
        assert res.decision == "deny"


# ------------------------------------------------------------------ DNS (human-gated)


def cloudflare_account(client, cid, m8, zone="example.com", records=None):
    cloudflare_routes(m8.net, zone=zone, records=records)
    return connect(client, cid, "cloudflare", metadata={"account_id": "acc1"})


CHANGES = [
    {"type": "CNAME", "name": "app.example.com", "value": "tun1.cfargotunnel.com", "proxied": True}
]


def test_dns_change_needs_a_human_and_runs_once_for_exactly_the_approved_changes(
    app, client, team, m8
):
    cid = team.cid
    acc = cloudflare_account(client, cid, m8)
    m8.net.add("POST", r"/zones/z1/dns_records", cf({"id": "rec1"}))
    worker = run_as(app, client, cid, team.worker)
    before = len(m8.net.requests)

    first = act(worker, acc["credential_id"], "dns.change", domain="example.com", changes=CHANGES)
    assert first.status_code == 202 and first.json()["approver"] == "human"
    aid = first.json()["approval_id"]
    assert len(m8.net.requests) == before  # nothing reached Cloudflare while it waits
    ap = approval(client, cid, aid)
    assert (ap["tier"], ap["action"], ap["approver"]) == ("identity", "dns.change", "human")
    # the approver reads the exact changes (the web renders these keys)
    assert ap["scope"]["domain"] == "example.com" and ap["scope"]["changes"] == [
        {
            "op": None,
            "type": "CNAME",
            "name": "app.example.com",
            "value": "tun1.cfargotunnel.com",
            "ttl": None,
        }
    ]

    # still pending: repeating does not run it
    assert (
        act(
            worker, acc["credential_id"], "dns.change", domain="example.com", changes=CHANGES
        ).status_code
        == 202
    )
    # the agent cannot approve its own request
    assert worker.post(f"/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 403

    # a different change is a different request with its own approval
    other = [{**CHANGES[0], "value": "evil.example.net"}]
    second = act(worker, acc["credential_id"], "dns.change", domain="example.com", changes=other)
    assert second.status_code == 202 and second.json()["approval_id"] != aid

    decide(client, cid, aid)
    done = act(worker, acc["credential_id"], "dns.change", domain="example.com", changes=CHANGES)
    assert done.status_code == 200, done.text
    result = done.json()["result"]
    assert result["zone"] == "example.com" and result["applied"] is True
    assert [s["op"] for s in result["steps"]] == ["create"]
    posted = json.loads(m8.net.calls("POST", "/zones/z1/dns_records")[0].content)
    assert posted == {
        "type": "CNAME",
        "name": "app.example.com",
        "content": "tun1.cfargotunnel.com",
        "ttl": 1,
        "proxied": True,
    }
    # single use: asking again needs a new approval
    again = act(worker, acc["credential_id"], "dns.change", domain="example.com", changes=CHANGES)
    assert again.status_code == 202 and again.json()["approval_id"] not in (
        aid,
        second.json()["approval_id"],
    )
    assert len(m8.net.calls("POST", "/zones/z1/dns_records")) == 1
    assert SECRET not in json.dumps(done.json())


def test_malformed_dns_changes_are_refused_before_anyone_is_asked(app, client, team, m8):
    cid = team.cid
    acc = cloudflare_account(client, cid, m8)
    worker = run_as(app, client, cid, team.worker)
    bad = [
        [{"type": "NS", "name": "example.com", "value": "ns.evil.example"}],
        [{"type": "SOA", "name": "example.com", "value": "x"}],
        [{"type": "A", "name": "example.com", "value": ""}],
        [],
        "not a list",
    ]
    for changes in bad:
        r = act(worker, acc["credential_id"], "dns.change", domain="example.com", changes=changes)
        assert r.status_code == 422, (changes, r.text)
    r = act(worker, acc["credential_id"], "dns.change", domain="not a host", changes=CHANGES)
    assert r.status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(Approval).where(Approval.action == "dns.change")) is None
    # a record outside the zone is refused when the zone is known (still before changing anything)
    decide_first = act(
        worker,
        acc["credential_id"],
        "dns.change",
        domain="example.com",
        changes=[{"type": "A", "name": "x.other.org", "value": "1.2.3.4"}],
    )
    assert decide_first.status_code == 202
    decide(client, cid, decide_first.json()["approval_id"])
    r = act(
        worker,
        acc["credential_id"],
        "dns.change",
        domain="example.com",
        changes=[{"type": "A", "name": "x.other.org", "value": "1.2.3.4"}],
    )
    assert r.status_code == 422 and not m8.net.calls("POST", "dns_records")


def test_dns_read_is_allowed_and_returns_records(app, client, team, m8):
    cid = team.cid
    acc = cloudflare_account(
        client,
        cid,
        m8,
        records=[
            {
                "id": "r1",
                "type": "A",
                "name": "example.com",
                "content": "1.2.3.4",
                "ttl": 300,
                "proxied": False,
            }
        ],
    )
    worker = run_as(app, client, cid, team.worker)
    r = act(worker, acc["credential_id"], "dns.read", domain="www.example.com")
    assert r.status_code == 200
    assert r.json()["result"]["records"] == [
        {"type": "A", "name": "example.com", "content": "1.2.3.4", "ttl": 300, "proxied": False}
    ]


# ------------------------------------------------------------------ credentials & accounts


def test_extra_secrets_and_unconnected_credentials_are_unusable_by_agents(app, client, team, m8):
    cid = team.cid
    acc = cloudflare_account(client, cid, m8)
    worker = run_as(app, client, cid, team.worker)
    # a plain credential that merely claims the provider is not a connected account
    raw = client.post(
        api(cid, "/credentials"),
        json={
            "name": "raw",
            "value": SECRET2,
            "kind": "token",
            "provider": "cloudflare",
            "allowed_actions": ["dns.*"],
        },
    )
    assert raw.status_code == 201
    r = act(worker, raw.json()["id"], "dns.read", domain="example.com")
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "no_account"
    # the second secret of an account (a webhook secret) has no agent actions at all
    acc2 = connect(client, cid, "cloudflare", label="second", extras={"webhook_secret": SECRET2})
    creds = client.get(api(cid, "/credentials")).json()["items"]
    extra = next(c for c in creds if "webhook_secret" in c["name"])
    r = act(worker, extra["id"], "dns.read", domain="example.com")
    assert r.status_code == 403
    assert acc2["status"] == "connected"
    # an action the provider does not enable is refused, e.g. buying a domain with a DNS token
    r = act(
        worker, acc["credential_id"], "domain.purchase", domain="example.com", max_price_micros=1
    )
    assert r.status_code in (403, 501)


# ------------------------------------------------------------------ domain purchase


PORKBUN_QUOTE = {
    "status": "SUCCESS",
    "response": {
        "avail": "yes",
        "price": "9.73",
        "regularPrice": "9.73",
        "premium": "no",
        "minDuration": 1,
        "additional": {"renewal": {"price": "10.50"}},
    },
}


def porkbun(client, cid, m8, *, quote=None):
    m8.net.add("POST", r"api\.porkbun\.com/api/json/v3/ping", {"status": "SUCCESS"})
    m8.net.add("POST", r"/domain/checkDomain/example\.com", quote or PORKBUN_QUOTE)
    m8.net.add(
        "POST",
        r"/domain/create/example\.com",
        {"status": "SUCCESS", "domain": "example.com", "cost": 973, "orderId": 77},
    )
    return connect(client, cid, "porkbun", extras={"secret_api_key": SECRET2})


def test_domain_search_is_allowed_and_prices_are_estimates(app, client, team, m8):
    cid = team.cid
    acc = porkbun(client, cid, m8)
    worker = run_as(app, client, cid, team.worker)
    r = act(worker, acc["credential_id"], "domain.search", query="example.com")
    assert r.status_code == 200, r.text
    row = r.json()["result"]["results"][0]
    assert row == {
        "domain": "example.com",
        "available": True,
        "registrar": "porkbun",
        "price_micros": 9_730_000,
        "renewal_price_micros": 10_500_000,
        "cost_kind": "estimated",
    }


def test_a_purchase_needs_a_human_bound_to_the_price_ceiling_and_is_recorded_as_spend(
    app, client, team, m8
):
    cid = team.cid
    acc = porkbun(client, cid, m8)
    worker = run_as(app, client, cid, team.worker)
    # even with an explicit allow rule: a human decides (the floor), and the ceiling is in the call
    add_rule(client, cid, tier="spend", action="domain.purchase", decision="allow")
    call = dict(domain="example.com", registrar="porkbun", years=1, max_price_micros=12_000_000)
    first = act(worker, acc["credential_id"], "domain.purchase", **call)
    assert first.status_code == 202 and first.json()["approver"] == "human"
    aid = first.json()["approval_id"]
    ap = approval(client, cid, aid)
    assert ap["amount_micros"] == 12_000_000 and ap["tier"] == "spend"
    assert ap["scope"]["domain"] == "example.com" and ap["scope"]["max_price_micros"] == 12_000_000
    assert not m8.net.calls("POST", "/domain/create/")
    # a different ceiling is a different request
    other = act(
        worker, acc["credential_id"], "domain.purchase", **{**call, "max_price_micros": 20_000_000}
    )
    assert other.json()["approval_id"] != aid
    decide(client, cid, aid)
    done = act(worker, acc["credential_id"], "domain.purchase", **call)
    assert done.status_code == 200, done.text
    assert done.json()["result"] == {
        "domain": "example.com",
        "status": "purchased",
        "charged_micros": 9_730_000,
        "cost_kind": "verified",
    }
    create = m8.net.calls("POST", "/domain/create/")
    assert len(create) == 1 and json.loads(create[0].content) == {
        "cost": 973,
        "agreeToTerms": "yes",
    }
    with app.state.session_factory() as db:
        expense = db.scalar(select(ExternalExpense).where(ExternalExpense.company_id == cid))
        assert (
            expense.amount_micros == 9_730_000
            and expense.vendor == "Porkbun"
            and expense.recurrence == "one_time"
        )
        reg = db.scalar(select(DomainRegistration).where(DomainRegistration.company_id == cid))
        assert (
            reg.status == "purchased"
            and reg.price_micros == 9_730_000
            and reg.max_price_micros == 12_000_000
        )
        assert (
            db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == cid)).status
            == "pending_dns"
        )
    # it cannot be replayed: the company has its domain now (and an approval is single-use anyway)
    again = act(worker, acc["credential_id"], "domain.purchase", **call)
    assert (
        again.status_code == 409 and again.json()["error"]["details"]["reason"] == "domain_exists"
    )
    assert len(m8.net.calls("POST", "/domain/create/")) == 1


def test_a_price_above_the_approved_ceiling_buys_nothing(app, client, team, m8):
    cid = team.cid
    pricey = {**PORKBUN_QUOTE, "response": {**PORKBUN_QUOTE["response"], "price": "15.00"}}
    acc = porkbun(client, cid, m8, quote=pricey)
    worker = run_as(app, client, cid, team.worker)
    call = dict(domain="example.com", years=1, max_price_micros=12_000_000)
    aid = act(worker, acc["credential_id"], "domain.purchase", **call).json()["approval_id"]
    decide(client, cid, aid)
    r = act(worker, acc["credential_id"], "domain.purchase", **call)
    assert r.status_code == 502 and "above the approved ceiling" in r.json()["error"]["message"]
    assert not m8.net.calls("POST", "/domain/create/")
    with app.state.session_factory() as db:
        assert db.scalar(select(ExternalExpense)) is None


def test_purchase_arguments_are_validated_before_a_human_is_asked(app, client, team, m8):
    cid = team.cid
    acc = porkbun(client, cid, m8)
    worker = run_as(app, client, cid, team.worker)
    for call in (
        dict(domain="example.com"),  # no ceiling
        dict(domain="example.com", max_price_micros=0),
        dict(domain="example.com", max_price_micros=True),
        dict(domain="example.com", max_price_micros=5_000_000, years=3),
        dict(domain="a.b.example.com", max_price_micros=5_000_000),
        dict(domain="example.com", max_price_micros=5_000_000, registrar="namecheap"),
    ):
        assert act(worker, acc["credential_id"], "domain.purchase", **call).status_code == 422, call
    with app.state.session_factory() as db:
        assert db.scalar(select(Approval).where(Approval.action == "domain.purchase")) is None


# ------------------------------------------------------------------ registry pull request


def github(client, cid, m8):
    m8.net.add(
        "GET",
        r"api\.github\.com/user$",
        {"login": "founder"},
        headers={"x-oauth-scopes": "public_repo"},
    )
    return connect(client, cid, "github")


def github_pr_routes(net):
    state = {"forked": False}
    net.add(
        "GET",
        r"/repos/is-a-dev/register$",
        {"default_branch": "main", "full_name": "is-a-dev/register"},
    )
    net.add(
        "GET",
        r"/repos/founder/register$",
        handler=lambda r: (
            httpx.Response(200, json={"full_name": "founder/register", "fork": True})
            if state["forked"]
            else httpx.Response(404, json={"message": "Not Found"})
        ),
    )
    net.add(
        "POST",
        r"/repos/is-a-dev/register/forks",
        handler=lambda r: (state.update(forked=True), httpx.Response(202, json={}))[1],
    )
    net.add("POST", r"/merge-upstream", {"message": "ok"})
    net.add("GET", r"/git/ref/heads/main", {"object": {"sha": "abc"}})
    net.add("POST", r"/git/refs", {}, status=201)
    net.add("PUT", r"/contents/domains/acme\.json", {"commit": {"sha": "c"}}, status=201)
    net.add(
        "POST",
        r"/repos/is-a-dev/register/pulls",
        {"number": 7, "html_url": "https://github.com/is-a-dev/register/pull/7", "state": "open"},
        status=201,
    )


def test_registry_pull_request_is_bound_to_the_exact_generated_content(app, client, team, m8):
    cid = team.cid
    acc = github(client, cid, m8)
    github_pr_routes(m8.net)
    worker = run_as(app, client, cid, team.worker)
    p = dict(registry_id="is-a.dev", name="acme", target="cname.vercel-dns.com")
    pv = act(worker, acc["credential_id"], "domain.preview", **p)
    assert pv.status_code == 200, pv.text
    content = pv.json()["result"]
    assert content["eligible"] is True and content["publishes_as"] == "founder"
    assert json.loads(content["file_content"]) == {
        "owner": {"username": "founder"},
        "records": {"CNAME": "cname.vercel-dns.com"},
    }
    assert content["file_path"] == "domains/acme.json" and content["fqdn"] == "acme.is-a.dev"
    params = {
        k: content[k]
        for k in (
            "registry_id",
            "name",
            "target",
            "fqdn",
            "repo",
            "branch",
            "file_path",
            "file_content",
            "pr_title",
            "pr_body",
            "publishes_as",
        )
    }

    # content that is not what the registry definition generates is refused outright
    tampered = {
        **params,
        "file_content": params["file_content"].replace("cname.vercel-dns.com", "evil.example.net"),
    }
    assert act(worker, acc["credential_id"], "domain.register_pr", **tampered).status_code == 422
    assert (
        act(
            worker,
            acc["credential_id"],
            "domain.register_pr",
            **{**params, "publishes_as": "someone-else"},
        ).status_code
        == 422
    )
    assert not m8.net.calls("POST", "/forks")

    first = act(worker, acc["credential_id"], "domain.register_pr", **params)
    assert first.status_code == 202 and first.json()["approver"] == "human"
    aid = first.json()["approval_id"]
    ap = approval(client, cid, aid)
    assert (
        ap["tier"] == "external_commitment"
        and ap["scope"]["file_content"] == params["file_content"]
    )
    assert (
        ap["scope"]["pr_title"] == "Register acme.is-a.dev"
        and ap["scope"]["repo"] == "is-a-dev/register"
    )
    assert not m8.net.calls("POST", "/forks")  # nothing is published while it waits
    decide(client, cid, aid)

    done = act(worker, acc["credential_id"], "domain.register_pr", **params)
    assert done.status_code == 200, done.text
    assert done.json()["result"] == {
        "status": "pr_open",
        "pr_url": "https://github.com/is-a-dev/register/pull/7",
        "domain": "acme.is-a.dev",
    }
    pr = json.loads(m8.net.calls("POST", "/pulls")[0].content)
    assert (
        pr["title"] == "Register acme.is-a.dev" and pr["head"] == "founder:company-os/register-acme"
    )
    with app.state.session_factory() as db:
        row = db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == cid))
        assert row.status == "pr_open" and row.domain == "acme.is-a.dev"
    # one domain at a time: a second request is refused before a human is asked again
    again = act(worker, acc["credential_id"], "domain.register_pr", **params)
    assert again.status_code == 409


# ------------------------------------------------------------------ hosted UI and the tunnel


def vercel_routes(net):
    net.add("GET", r"/v2/user", {"user": {"username": "founder"}})
    net.add(
        "GET", r"/v9/projects/acme-labs-control", status=404, json_body={"error": {"message": "nf"}}
    )
    net.add("POST", r"/v11/projects", {"id": "prj_1"})
    net.add(
        "POST",
        r"/v13/deployments",
        {"id": "dpl_1", "url": "acme-abc.vercel.app", "readyState": "QUEUED"},
    )
    net.add(
        "GET",
        r"/v13/deployments/dpl_1",
        {
            "id": "dpl_1",
            "url": "acme-abc.vercel.app",
            "readyState": "READY",
            "alias": ["acme-labs-control.vercel.app"],
        },
    )


@pytest.fixture
def dist(app, tmp_path):
    d = tmp_path / "web-dist"
    d.mkdir()
    (d / "index.html").write_text("<html>ui</html>")
    app.state.settings.web_dist_dir = d
    return d


def test_hosted_ui_deploy_needs_the_cofounder_and_the_exact_proxy_target(
    app, client, team, m8, dist
):
    cid = team.cid
    claim_owner(app)
    vercel_routes(m8.net)
    acc = connect(client, cid, "vercel")
    worker = run_as(app, client, cid, team.worker)
    founder = run_as(app, client, cid, team.founder)
    target = "https://app.example.com"
    # parameters are checked first: no target, an http or local target never reach an approval
    for bad in (
        {},
        {"proxy_target": "http://app.example.com"},
        {"proxy_target": "https://localhost:8700"},
    ):
        assert act(worker, acc["credential_id"], "hosted_ui.deploy", **bad).status_code == 422
    first = act(worker, acc["credential_id"], "hosted_ui.deploy", proxy_target=target)
    assert first.status_code == 202 and first.json()["approver"] == "founder"
    aid = first.json()["approval_id"]
    assert approval(client, cid, aid)["scope"]["proxy_target"] == target
    assert not m8.net.calls("POST", "/v13/deployments")
    # a different target is a different request
    assert (
        act(
            worker,
            acc["credential_id"],
            "hosted_ui.deploy",
            proxy_target="https://other.example.com",
        ).json()["approval_id"]
        != aid
    )
    # no money is involved: the AI Co-founder decides (a human could too)
    assert founder.post(f"/approvals/{aid}/decide", json={"decision": "approve"}).status_code == 200
    done = act(worker, acc["credential_id"], "hosted_ui.deploy", proxy_target=target)
    assert done.status_code == 200, done.text
    assert (
        done.json()["result"]["status"] == "ready"
        and done.json()["result"]["url"] == "https://acme-labs-control.vercel.app"
    )
    body = json.loads(m8.net.calls("POST", "/v13/deployments")[0].content)
    files = {f["file"] for f in body["files"]}
    assert files == {"index.html", "vercel.json"}
    cfg = json.loads(
        base64.b64decode(next(f for f in body["files"] if f["file"] == "vercel.json")["data"])
    )
    assert cfg["rewrites"][0]["destination"] == "https://app.example.com/api/:path*"
    assert SECRET not in json.dumps(done.json())


def test_hosted_ui_is_refused_without_the_exposure_gate(app, client, team, m8, dist):
    cid = team.cid  # the owner never set a password
    vercel_routes(m8.net)
    acc = connect(client, cid, "vercel")
    worker = run_as(app, client, cid, team.worker)
    r = act(
        worker, acc["credential_id"], "hosted_ui.deploy", proxy_target="https://app.example.com"
    )
    assert r.status_code == 403 and r.json()["error"]["details"]["code"] == "no_password"
    with app.state.session_factory() as db:
        assert db.scalar(select(Approval).where(Approval.action == "hosted_ui.deploy")) is None


def test_an_agent_starts_a_quick_tunnel_only_after_approval_and_only_with_strong_auth(
    app, client, team, m8
):
    cid = team.cid
    cloudflare_routes(m8.net)
    acc = connect(client, cid, "cloudflare", metadata={"account_id": "acc1"})
    worker = run_as(app, client, cid, team.worker)
    # weak/unclaimed owner: nobody is even asked
    r = act(worker, acc["credential_id"], "tunnel.start", provider="cloudflare_quick")
    assert r.status_code == 403 and r.json()["error"]["details"]["code"] == "no_password"
    claim_owner(app)
    # only the account-free and Cloudflare-account kinds are offered to agents
    assert (
        act(worker, acc["credential_id"], "tunnel.start", provider="tailscale_funnel").status_code
        == 422
    )
    first = act(worker, acc["credential_id"], "tunnel.start", provider="cloudflare_quick")
    assert first.status_code == 202 and first.json()["approver"] == "founder"
    decide(client, cid, first.json()["approval_id"])
    m8.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    fake_cloudflared(m8.settings.resolved_bin_dir / "cloudflared")
    done = act(worker, acc["credential_id"], "tunnel.start", provider="cloudflare_quick")
    assert done.status_code == 200, done.text
    wait_for(lambda: m8.ic.supervisor.status()["state"] == "running")
    status = act(worker, acc["credential_id"], "tunnel.status")
    assert status.status_code == 200 and status.json()["result"]["state"] == "running"
    stop = act(worker, acc["credential_id"], "tunnel.stop")
    assert stop.status_code == 202  # stopping the public URL is an identity action too
    decide(client, cid, stop.json()["approval_id"])
    assert act(worker, acc["credential_id"], "tunnel.stop").status_code == 200
    assert m8.ic.supervisor.status()["state"] == "stopped"


def test_the_guardrail_log_records_the_decisions_without_payloads(app, client, team, m8):
    cid = team.cid
    acc = cloudflare_account(client, cid, m8)
    worker = run_as(app, client, cid, team.worker)
    act(worker, acc["credential_id"], "dns.change", domain="example.com", changes=CHANGES)
    log = [e for e in events(client, cid) if e["action"] == "dns.change"]
    assert log and log[0]["decision"] == "human_approval" and log[0]["tier"] == "identity"
    assert "evil" not in json.dumps(log) and "cfargotunnel" not in json.dumps(log)
    assert store_secret  # keep helper import used
    assert GuardrailScope.ALL
