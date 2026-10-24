"""Domains through the owner API: registry pull request (approval-bound), assisted dashboard flow,
paid purchase (approval-bound to a price ceiling), DNS plan/verify."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from sqlalchemy import select

from app.integrations import dns as dnslib
from app.integrations.registries import IS_A_DEV, REGISTRIES
from app.models.guardrails import ExternalExpense
from app.models.reachability import CompanyDomain, DomainRegistration
from tests import accounts_helpers as ah
from tests.helpers import api
from tests.m8_helpers import (
    SECRET2,
    FakeResolver,
    cf,
    claim_owner,
    cloudflare_routes,
    connect,
)
from tests.test_m8_provider_actions import (
    PORKBUN_QUOTE,
    github,
    github_pr_routes,
    porkbun,
)


def post(client, cid, path, body=None):
    return client.post(api(cid, path), json=body if body is not None else {})


# ------------------------------------------------------------------ catalogues


def test_registry_and_free_provider_catalogues_state_their_rules_honestly(client, company):
    cid = company["id"]
    regs = client.get(api(cid, "/domain/registries")).json()
    assert regs["next_cursor"] is None
    isa = next(r for r in regs["items"] if r["id"] == "is-a.dev")
    assert (
        isa["suffix"] == "is-a.dev"
        and isa["repo"] == "is-a-dev/register"
        and isa["review"] == "human"
    )
    assert isa["rules_url"] == "https://docs.is-a.dev"
    text = " ".join(isa["eligibility"] + isa["warnings"]).lower()
    assert "non-commercial" in text and "not to use ai" in text and "human" in text
    assert re.fullmatch(isa["name_pattern"], "my-site") and not re.fullmatch(
        isa["name_pattern"], "my--site"
    )
    free = client.get(api(cid, "/domain/free-providers")).json()["items"]
    assert free[0]["id"] == "digitalplat" and "dpdns.org" in free[0]["suffixes"]
    assert free[0]["dashboard_url"].startswith("https://dash.domain.digitalplat.org")
    assert any("Foundry AI OS never signs up for you" in step for step in free[0]["steps"])


def test_the_registry_name_rules(client):
    ok = ["a", "acme", "my-site", "a1-b2"]
    bad = ["", "-a", "a-", "a--b", "A", "has space", "x" * 64, "www", "is-a.dev-site", "dot.ted"]
    problem = IS_A_DEV.label_problem
    assert all(problem(n) is None for n in ok)
    assert all(problem(n) for n in bad)
    assert REGISTRIES["is-a.dev"] is IS_A_DEV


# ------------------------------------------------------------------ preview


def test_preview_generates_the_exact_file_and_explains_what_is_missing(client, company, m8):
    cid = company["id"]
    body = {"registry_id": "is-a.dev", "name": "acme"}
    pv = post(client, cid, "/domain/registrations/preview", body).json()
    assert pv["eligible"] is False and "GitHub" in pv["ineligible_reason"]
    assert "stable address" in pv["ineligible_reason"]  # no hosted UI / named tunnel to point at
    github(client, cid, m8)
    pv = post(client, cid, "/domain/registrations/preview", body).json()
    assert pv["eligible"] is False and "stable address" in pv["ineligible_reason"]
    pv = post(
        client, cid, "/domain/registrations/preview", {**body, "target": "cname.vercel-dns.com"}
    ).json()
    assert pv["eligible"] is True and pv["ineligible_reason"] is None
    assert pv["fqdn"] == "acme.is-a.dev" and pv["repo"] == "is-a-dev/register"
    assert pv["file_path"] == "domains/acme.json" and pv["branch"] == "company-os/register-acme"
    assert (
        pv["file_content"]
        == '{\n  "owner": {\n    "username": "founder"\n  },\n  "records": {\n    "CNAME": "cname.vercel-dns.com"\n  }\n}\n'
    )
    assert (
        pv["pr_title"] == "Register acme.is-a.dev"
        and "@founder" in pv["pr_body"]
        and "cname.vercel-dns.com" in pv["pr_body"]
    )
    assert pv["publishes_as"] == "founder"
    assert any("non-commercial" in w for w in pv["warnings"])
    assert (
        post(
            client, cid, "/domain/registrations/preview", {"registry_id": "nope", "name": "x"}
        ).status_code
        == 404
    )
    pv = post(
        client,
        cid,
        "/domain/registrations/preview",
        {**body, "name": "a--b", "target": "x.example.com"},
    ).json()
    assert pv["eligible"] is False and "dashes" in pv["ineligible_reason"]
    pv = post(client, cid, "/domain/registrations/preview", {**body, "target": "not a host"}).json()
    assert pv["eligible"] is False and "hostname" in pv["ineligible_reason"]
    assert not m8.net.calls("POST") or all("/ping" not in str(r.url) for r in m8.net.calls("POST"))


# ------------------------------------------------------------------ registry pull request (UI path)


@pytest.fixture
def registry_world(client, company, m8):
    cid = company["id"]
    github(client, cid, m8)
    github_pr_routes(m8.net)
    return cid


REG = {
    "path": "github_registry",
    "registry_id": "is-a.dev",
    "name": "acme",
    "target": "cname.vercel-dns.com",
}


def test_the_registration_waits_for_a_human_who_sees_the_exact_content(
    client, app, registry_world, m8
):
    cid = registry_world
    r = post(client, cid, "/domain/registrations", REG)
    assert r.status_code == 201, r.text
    reg = r.json()
    assert (
        reg["status"] == "awaiting_approval"
        and reg["domain"] == "acme.is-a.dev"
        and reg["approval_id"]
    )
    assert not m8.net.calls("POST", "/forks") and not m8.net.calls("POST", "/pulls")
    ap = client.get(api(cid, f"/approvals/{reg['approval_id']}")).json()
    assert (ap["approver"], ap["tier"], ap["action"], ap["status"]) == (
        "human",
        "external_commitment",
        "domain.register_pr",
        "pending",
    )
    sc = ap["scope"]
    assert (
        sc["fqdn"] == "acme.is-a.dev"
        and sc["repo"] == "is-a-dev/register"
        and sc["publishes_as"] == "founder"
    )
    assert json.loads(sc["file_content"])["records"] == {"CNAME": "cname.vercel-dns.com"}
    assert sc["pr_title"] == "Register acme.is-a.dev" and "@founder" in sc["pr_body"]
    plan = client.get(api(cid, "/domain")).json()
    assert (
        plan["status"] == "awaiting_approval"
        and plan["domain"] == "acme.is-a.dev"
        and plan["path"] == "github_registry"
    )
    assert plan["dns_records"] == [
        {
            "type": "CNAME",
            "name": "acme.is-a.dev",
            "value": "cname.vercel-dns.com",
            "ttl": 1,
            "status": None,
        }
    ]
    assert plan["registration"]["id"] == reg["id"]
    # one domain at a time
    assert post(client, cid, "/domain/registrations", {**REG, "name": "other"}).status_code == 409


def test_approving_opens_the_pull_request_once_then_verify_follows_it_to_active(
    client, app, registry_world, m8
):
    cid = registry_world
    reg = post(client, cid, "/domain/registrations", REG).json()
    r = client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={})
    assert r.status_code == 200 and r.json()["consumed_at"]
    pr = json.loads(m8.net.calls("POST", "/pulls")[0].content)
    assert pr["title"] == "Register acme.is-a.dev" and pr["base"] == "main"
    put = json.loads(m8.net.calls("PUT", "contents")[0].content)
    import base64

    assert json.loads(base64.b64decode(put["content"]))["owner"] == {"username": "founder"}
    plan = client.get(api(cid, "/domain")).json()
    assert (
        plan["status"] == "pr_open"
        and plan["registration"]["pr_url"] == "https://github.com/is-a-dev/register/pull/7"
    )
    assert plan["registration"]["status"] == "pr_open"
    assert len(m8.net.calls("POST", "/pulls")) == 1
    # the approval cannot be used again
    assert (
        client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={}).status_code
        == 409
    )

    # not merged yet: nothing changes
    m8.net.add(
        "GET", r"/repos/is-a-dev/register/pulls/7", {"number": 7, "state": "open", "merged": False}
    )
    assert post(client, cid, "/domain/verify").json()["status"] == "pr_open"
    # merged, DNS not there yet
    m8.net.routes.pop()
    m8.net.add(
        "GET", r"/repos/is-a-dev/register/pulls/7", {"number": 7, "state": "closed", "merged": True}
    )
    plan = post(client, cid, "/domain/verify").json()
    assert plan["status"] == "pending_dns" and plan["registration"]["status"] == "merged"
    assert plan["dns_status"] == "pending" and plan["dns_records"][0]["status"] == "missing"
    # DNS resolves but TLS is not ready
    m8.resolver.table[("acme.is-a.dev", "CNAME")] = ["cname.vercel-dns.com"]
    plan = post(client, cid, "/domain/verify").json()
    assert plan["dns_status"] == "ok" and plan["dns_records"][0]["status"] == "ok"
    assert plan["tls_status"] == "pending" and plan["status"] == "pending_dns"
    # and finally TLS
    m8.ic.tls_check = lambda domain: ("ok", None)
    plan = post(client, cid, "/domain/verify").json()
    assert plan["status"] == "active" and plan["tls_status"] == "ok" and plan["last_checked_at"]
    assert client.get(api(cid, "/reachability/checklist")).json()["steps"][4]["status"] == "done"
    # the status the landing screen polls carries the domain summary
    claim_owner(app)
    s = client.get("/api/reachability").json()
    assert s["domain"] == {"domain": "acme.is-a.dev", "dns_status": "ok", "tls_status": "ok"}


def test_rejecting_publishes_nothing_and_frees_the_slot(client, registry_world, m8):
    cid = registry_world
    reg = post(client, cid, "/domain/registrations", REG).json()
    r = client.post(
        api(cid, f"/approvals/{reg['approval_id']}/reject"), json={"note": "wrong name"}
    )
    assert r.status_code == 200
    assert not m8.net.calls("POST", "/forks") and not m8.net.calls("POST", "/pulls")
    plan = client.get(api(cid, "/domain")).json()
    assert plan["status"] == "none" and plan["domain"] is None
    assert post(client, cid, "/domain/registrations", {**REG, "name": "second"}).status_code == 201


def test_content_changed_after_the_request_cannot_ride_on_the_approval(
    client, app, registry_world, m8
):
    cid = registry_world
    reg = post(client, cid, "/domain/registrations", REG).json()
    with app.state.session_factory() as db:
        row = db.get(DomainRegistration, reg["id"])
        payload = dict(row.payload)
        payload["file_content"] = payload["file_content"].replace(
            "cname.vercel-dns.com", "evil.example.net"
        )
        row.payload = payload
        db.commit()
    r = client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={})
    assert r.status_code == 409  # the approved scope no longer matches
    assert not m8.net.calls("POST", "/pulls")


def test_a_github_failure_after_approval_is_recorded_not_hidden(client, company, m8):
    cid = company["id"]
    github(client, cid, m8)
    m8.net.add("GET", r"/repos/is-a-dev/register$", status=500, json_body={"message": "boom"})
    reg = post(client, cid, "/domain/registrations", REG).json()
    assert (
        client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={}).status_code
        == 200
    )
    plan = client.get(api(cid, "/domain")).json()
    assert plan["status"] == "error" and plan["registration"]["status"] == "failed"
    assert "github" in plan["registration"]["error"].lower()
    assert not m8.net.calls("POST", "/pulls")


def test_only_owners_and_admins_can_register_a_domain(app, client, m8):
    owner = ah.do_setup(app)
    co = ah.create_company(owner)
    cid = co["id"]
    github(owner, cid, m8)
    for role in ("viewer", "member"):
        who = ah.join(app, ah.invite(owner, cid, f"{role}@example.com", role), role)
        assert post(who, cid, "/domain/registrations", REG).status_code == 403
        assert post(who, cid, "/domain/registrations/preview", REG).status_code in (403, 422)
        assert post(who, cid, "/domain/verify").status_code == 403


# ------------------------------------------------------------------ assisted (dashboard) flow


def test_the_assisted_flow_gives_exact_steps_and_verifies_dns_afterwards(client, company, m8):
    cid = company["id"]
    r = post(
        client,
        cid,
        "/domain/registrations",
        {"path": "free_provider", "provider_id": "digitalplat", "domain": "acme.dpdns.org"},
    )
    assert (
        r.status_code == 201
        and r.json()["status"] == "awaiting_founder"
        and r.json()["approval_id"] is None
    )
    plan = client.get(api(cid, "/domain")).json()
    assert plan["status"] == "awaiting_founder" and plan["path"] == "free_provider"
    assert plan["steps"][0].startswith("Dashboard: https://dash.domain.digitalplat.org")
    assert any("nameservers" in s for s in plan["steps"])
    # the Founder has not finished yet
    assert post(client, cid, "/domain/verify").json()["status"] == "awaiting_founder"
    # DNS shows up
    m8.resolver.table[("acme.dpdns.org", "A")] = ["104.21.0.1"]
    plan = post(client, cid, "/domain/verify").json()
    assert plan["dns_status"] == "ok" and plan["status"] == "pending_dns"
    m8.ic.tls_check = lambda domain: ("ok", None)
    assert post(client, cid, "/domain/verify").json()["status"] == "active"
    # a TLS error is reported, not hidden
    m8.ic.tls_check = lambda domain: ("error", "certificate not valid: expired")
    plan = post(client, cid, "/domain/verify").json()
    assert (
        plan["tls_status"] == "error" and "expired" in plan["error"] and plan["status"] != "active"
    )


def test_assisted_flow_validation(client, company):
    cid = company["id"]
    bad = {"path": "free_provider", "provider_id": "digitalplat", "domain": "acme.example.com"}
    assert post(client, cid, "/domain/registrations", bad).status_code == 422
    assert (
        post(
            client,
            cid,
            "/domain/registrations",
            {**bad, "provider_id": "nope", "domain": "a.dpdns.org"},
        ).status_code
        == 404
    )
    assert (
        post(client, cid, "/domain/registrations", {**bad, "domain": "not a host"}).status_code
        == 422
    )


# ------------------------------------------------------------------ paid purchase (UI path)


PAID = {
    "path": "paid_registrar",
    "registrar": "porkbun",
    "domain": "example.com",
    "years": 1,
    "max_price_micros": 12_000_000,
}


def test_search_lists_quotes_as_estimates(client, company, m8):
    cid = company["id"]
    assert (
        client.get(api(cid, "/domain/search"), params={"q": "example.com"}).status_code == 409
    )  # no registrar yet
    porkbun(client, cid, m8)
    items = client.get(api(cid, "/domain/search"), params={"q": "example.com"}).json()["items"]
    assert items == [
        {
            "domain": "example.com",
            "available": True,
            "registrar": "porkbun",
            "price_micros": 9_730_000,
            "renewal_price_micros": 10_500_000,
            "cost_kind": "estimated",
        }
    ]
    # a bare name is checked on a few TLDs, never an unbounded number
    m8.net.add("POST", r"/domain/checkDomain/", {"status": "SUCCESS", "response": {"avail": "no"}})
    items = client.get(api(cid, "/domain/search"), params={"q": "acme"}).json()["items"]
    assert [i["domain"] for i in items] == ["acme.com", "acme.net", "acme.org", "acme.io"]
    assert client.get(api(cid, "/domain/search"), params={"q": "bad name.com"}).status_code == 422


def test_a_paid_domain_waits_for_a_human_and_never_exceeds_the_approved_ceiling(
    client, app, company, m8
):
    cid = company["id"]
    porkbun(client, cid, m8)
    reg = post(client, cid, "/domain/registrations", PAID).json()
    assert (
        reg["status"] == "awaiting_approval"
        and reg["max_price_micros"] == 12_000_000
        and reg["cost_kind"] == "estimated"
    )
    ap = client.get(api(cid, f"/approvals/{reg['approval_id']}")).json()
    assert (ap["tier"], ap["action"], ap["approver"], ap["amount_micros"], ap["vendor"]) == (
        "spend",
        "domain.purchase",
        "human",
        12_000_000,
        "Porkbun",
    )
    assert (
        ap["scope"]["price_micros"] == 9_730_000 and ap["scope"]["max_price_micros"] == 12_000_000
    )
    assert ap["scope"]["renewal_price_micros"] == 10_500_000 and ap["scope"]["years"] == 1
    assert not m8.net.calls("POST", "/domain/create/")
    # the price moves above the ceiling before the human decides: nothing is bought
    m8.net.routes[:] = [r for r in m8.net.routes if "checkDomain/example" not in r[1].pattern]
    m8.net.add(
        "POST",
        r"/domain/checkDomain/example\.com",
        {"status": "SUCCESS", "response": {**PORKBUN_QUOTE["response"], "price": "99.00"}},
    )
    assert (
        client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={}).status_code
        == 200
    )
    plan = client.get(api(cid, "/domain")).json()
    assert (
        plan["registration"]["status"] == "failed"
        and "Nothing was charged" in plan["registration"]["error"]
    )
    assert not m8.net.calls("POST", "/domain/create/")
    with app.state.session_factory() as db:
        assert db.scalar(select(ExternalExpense)) is None


def test_an_approved_purchase_is_charged_once_and_recorded_as_verified_spend(
    client, app, company, m8
):
    cid = company["id"]
    porkbun(client, cid, m8)
    reg = post(client, cid, "/domain/registrations", PAID).json()
    assert (
        client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={}).status_code
        == 200
    )
    assert len(m8.net.calls("POST", "/domain/create/")) == 1
    plan = client.get(api(cid, "/domain")).json()
    assert plan["status"] == "pending_dns" and plan["registration"]["status"] == "purchased"
    assert (
        plan["registration"]["price_micros"] == 9_730_000
        and plan["registration"]["cost_kind"] == "verified"
    )
    assert any("nameservers" in s for s in plan["steps"])
    expenses = client.get(api(cid, "/finance/expenses")).json()["items"]
    assert [(e["vendor"], e["amount_micros"]) for e in expenses] == [("Porkbun", 9_730_000)]
    assert (
        client.post(api(cid, f"/approvals/{reg['approval_id']}/approve"), json={}).status_code
        == 409
    )


def test_paid_validation(client, company, m8):
    cid = company["id"]
    assert (
        post(client, cid, "/domain/registrations", PAID).status_code == 409
    )  # no registrar account
    porkbun(client, cid, m8)
    assert post(client, cid, "/domain/registrations", {**PAID, "years": 2}).status_code == 422
    assert (
        post(
            client, cid, "/domain/registrations", {**PAID, "max_price_micros": 1_000_000}
        ).status_code
        == 422
    )
    assert (
        post(client, cid, "/domain/registrations", {**PAID, "registrar": "godaddy"}).status_code
        == 422
    )
    assert (
        post(
            client, cid, "/domain/registrations", {**PAID, "domain": "a.b.example.com"}
        ).status_code
        == 422
    )
    # no ceiling given: bound to the current quote
    reg = post(client, cid, "/domain/registrations", {**PAID, "max_price_micros": None}).json()
    assert reg["max_price_micros"] == 9_730_000


# ------------------------------------------------------------------ DNS


def test_dns_verification_logic():
    resolver = FakeResolver(
        {
            ("app.example.com", "CNAME"): ["tun.cfargotunnel.com"],
            ("t.example.com", "TXT"): ["v=x"],
            ("p.example.com", "A"): ["1.1.1.1"],
            ("m.example.com", "CNAME"): ["other.example.net"],
        }
    )
    desired = [
        {"type": "CNAME", "name": "app.example.com", "value": "TUN.cfargotunnel.com."},
        {"type": "TXT", "name": "t.example.com", "value": '"v=x"'},
        {
            "type": "CNAME",
            "name": "p.example.com",
            "value": "tun.cfargotunnel.com",
            "proxied": True,
        },
        {"type": "CNAME", "name": "m.example.com", "value": "wanted.example.net"},
        {"type": "A", "name": "gone.example.com", "value": "9.9.9.9"},
    ]
    checked, overall = dnslib.verify_records(desired, resolver)
    assert [r["status"] for r in checked] == ["ok", "ok", "ok", "mismatch", "missing"]
    assert overall == "pending"
    ok, overall = dnslib.verify_records(desired[:3], resolver)
    assert overall == "ok"
    assert dnslib.verify_records([], resolver) == ([], "pending")


def test_the_founder_plans_and_applies_dns_through_cloudflare(client, app, company, m8):
    cid = company["id"]
    cloudflare_routes(
        m8.net,
        records=[
            {"id": "r1", "type": "CNAME", "name": "app.example.com", "content": "old.example.net"}
        ],
    )
    connect(client, cid, "cloudflare", metadata={"account_id": "acc1"})
    m8.net.add("PUT", r"/zones/z1/dns_records/r1", cf({"id": "r1"}))
    body = {
        "changes": [
            {
                "type": "CNAME",
                "name": "app.example.com",
                "value": "new.example.net",
                "proxied": True,
            }
        ]
    }
    assert post(client, cid, "/domain/dns/plan", body).status_code == 409  # no domain yet
    post(
        client,
        cid,
        "/domain/registrations",
        {
            "path": "free_provider",
            "provider_id": "digitalplat",
            "domain": "app.example.com.dpdns.org",
        },
    )
    with app.state.session_factory() as db:
        row = db.scalar(select(CompanyDomain).where(CompanyDomain.company_id == cid))
        row.domain = "example.com"
        db.commit()
    plan = post(client, cid, "/domain/dns/plan", body).json()
    assert plan["applied"] is False and plan["zone"] == "example.com"
    assert plan["steps"] == [
        {
            "op": "update",
            "type": "CNAME",
            "name": "app.example.com",
            "value": "new.example.net",
            "ttl": 1,
            "previous": "old.example.net",
        }
    ]
    assert not m8.net.calls("PUT")
    done = post(client, cid, "/domain/dns/apply", body).json()
    assert done["applied"] is True and len(m8.net.calls("PUT", "dns_records/r1")) == 1
    assert client.get(api(cid, "/domain")).json()["dns_records"][0]["value"] == "new.example.net"
    bad = post(
        client,
        cid,
        "/domain/dns/apply",
        {"changes": [{"type": "NS", "name": "example.com", "value": "x.example.net"}]},
    )
    assert bad.status_code == 422 and len(m8.net.calls("PUT")) == 1
    assert SECRET2 and httpx
