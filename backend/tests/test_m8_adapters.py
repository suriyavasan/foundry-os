"""Provider adapters against scripted HTTP (recorded-style fixtures). No test touches the network."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.core.errors import AppError
from app.integrations import tailscale
from app.integrations.agentmail import AgentMailClient
from app.integrations.base import ProviderAuthError, ProviderError, micros_to_cents, usd_to_micros
from app.integrations.cloudflare import CloudflareClient, apply_dns_plan, plan_dns
from app.integrations.github import GithubClient, submit_registry_pr
from app.integrations.namecheap import NamecheapClient
from app.integrations.porkbun import PorkbunClient
from app.integrations.resend import ResendClient
from app.integrations.vercel import (
    VercelClient,
    build_vercel_json,
    collect_dist,
    normalize_proxy_target,
    project_name_for,
)
from tests.m8_helpers import SECRET, TAILSCALE_RUNNING, FakeNetwork, cf, fake_run_factory


def http(net: FakeNetwork) -> httpx.Client:
    return httpx.Client(transport=net.transport())


# ------------------------------------------------------------------ cloudflare


def test_cloudflare_verifies_lists_zones_and_finds_the_zone_of_a_host():
    net = FakeNetwork()
    net.add("GET", r"/user/tokens/verify", cf({"id": "t", "status": "active"}))
    net.add(
        "GET",
        r"/zones\?",
        handler=lambda r: httpx.Response(
            200,
            json=cf(
                [{"id": "z9", "name": "example.com"}]
                if dict(r.url.params).get("name") == "example.com"
                else []
            ),
        ),
    )
    client = CloudflareClient(http(net), SECRET)
    assert client.verify_token()["status"] == "active"
    zone = client.zone_for("app.eu.example.com")
    assert zone == {"id": "z9", "name": "example.com"}
    assert client.zone_for("nothing.invalid") is None
    # the token is sent as a bearer header and nowhere else
    assert all(r.headers["authorization"] == f"Bearer {SECRET}" for r in net.requests)
    assert SECRET not in "".join(str(r.url) for r in net.requests)


def test_cloudflare_dns_plan_creates_updates_and_leaves_matching_records_alone():
    existing = [
        {"id": "r1", "type": "CNAME", "name": "app.example.com", "content": "old.example.net"},
        {"id": "r2", "type": "TXT", "name": "example.com", "content": '"v=spf1 -all"'},
        {"id": "r3", "type": "A", "name": "keep.example.com", "content": "1.2.3.4"},
    ]
    desired = [
        {"type": "CNAME", "name": "app.example.com", "value": "new.example.net", "proxied": True},
        {"type": "TXT", "name": "example.com", "value": "v=spf1 -all"},
        {"type": "TXT", "name": "_dmarc.example.com", "value": "v=DMARC1; p=none"},
    ]
    steps = plan_dns(desired, existing)
    assert [s["op"] for s in steps] == ["update", "noop", "create"]
    assert steps[0]["previous"] == "old.example.net" and steps[0]["id"] == "r1"
    # records that are not part of the plan are never in it (r3 is untouched)
    assert all(s["name"] != "keep.example.com" for s in steps)

    net = FakeNetwork()
    net.add("PUT", r"/zones/z1/dns_records/r1", cf({"id": "r1"}))
    net.add("POST", r"/zones/z1/dns_records", cf({"id": "r9"}))
    client = CloudflareClient(http(net), SECRET)
    done = apply_dns_plan(client, "z1", steps)
    assert [d["op"] for d in done] == ["update", "noop", "create"]
    assert [r.method for r in net.requests] == ["PUT", "POST"]  # the noop made no call
    put = json.loads(net.requests[0].content)
    assert put == {
        "type": "CNAME",
        "name": "app.example.com",
        "content": "new.example.net",
        "ttl": 1,
        "proxied": True,
    }


def test_cloudflare_tunnel_calls_and_the_token_is_masked_in_errors():
    net = FakeNetwork()
    net.add("POST", r"/accounts/acc1/cfd_tunnel$", cf({"id": "tun1", "name": "t"}))
    net.add("GET", r"/cfd_tunnel/tun1/token", cf("eyJhIjoiYWNjIiwidCI6InR1biJ9"))
    net.add(
        "PUT",
        r"/cfd_tunnel/tun1/configurations",
        status=400,
        json_body={
            "success": False,
            "errors": [{"message": "bad eyJhIjoiYWNjIiwidCI6InR1biJ9 ingress"}],
        },
    )
    client = CloudflareClient(http(net), SECRET)
    assert client.tunnel_create("acc1", "t")["id"] == "tun1"
    token = client.tunnel_token("acc1", "tun1")
    with pytest.raises(ProviderError) as err:
        client.tunnel_configure("acc1", "tun1", "app.example.com", "http://127.0.0.1:8700")
    assert token not in err.value.message and "[redacted]" in err.value.message
    body = json.loads(net.requests[0].content)
    assert body == {"name": "t", "config_src": "cloudflare"}


def test_cloudflare_rejected_token_is_an_auth_error_without_the_secret():
    net = FakeNetwork().add(
        "GET",
        r"/user/tokens/verify",
        status=403,
        json_body={"success": False, "errors": [{"message": f"Invalid token {SECRET}"}]},
    )
    with pytest.raises(ProviderAuthError) as err:
        CloudflareClient(http(net), SECRET).verify_token()
    assert SECRET not in err.value.message and err.value.details["status"] == 403


def test_cloudflare_email_routing_needs_a_destination():
    net = FakeNetwork().add("POST", r"/zones/z1/email/routing/rules", cf({"id": "rule1"}))
    client = CloudflareClient(http(net), SECRET)
    with pytest.raises(ProviderError):
        client.email_routing_create("z1", "a@example.com", forward_to=None, worker=None)
    client.email_routing_create("z1", "a@example.com", forward_to=None, worker="mail-hook")
    rule = json.loads(net.requests[-1].content)
    assert rule["actions"] == [{"type": "worker", "value": ["mail-hook"]}]
    assert rule["matchers"][0]["value"] == "a@example.com"


# ------------------------------------------------------------------ vercel


def test_vercel_json_rewrites_api_to_the_public_url_and_falls_back_to_the_spa():
    cfg = build_vercel_json("https://quiet-river.trycloudflare.com/")
    assert cfg["rewrites"][0] == {
        "source": "/api/:path*",
        "destination": "https://quiet-river.trycloudflare.com/api/:path*",
    }
    assert cfg["rewrites"][1]["destination"] == "/index.html"
    assert "api" in cfg["rewrites"][1]["source"]  # the SPA fallback never swallows /api
    for bad in (
        "http://example.com",
        "https://localhost:8700",
        "https://127.0.0.1",
        "https://nas.local",
        "",
    ):
        with pytest.raises(AppError):
            normalize_proxy_target(bad)
    assert normalize_proxy_target("https://app.example.com/some/path") == "https://app.example.com"


def test_collect_dist_inlines_files_and_ignores_a_shipped_vercel_json(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "vercel.json").write_text(
        '{"rewrites":[{"source":"/api/(.*)","destination":"https://evil.example"}]}'
    )
    (dist / ".secret").write_text("nope")
    files = collect_dist(dist, "https://app.example.com", max_bytes=10_000)
    names = [f["file"] for f in files]
    assert names == ["assets/app.js", "index.html", "vercel.json"]
    generated = json.loads(base64.b64decode(files[-1]["data"]))
    assert "evil.example" not in json.dumps(generated)  # the build cannot redirect /api elsewhere
    assert generated["rewrites"][0]["destination"] == "https://app.example.com/api/:path*"
    with pytest.raises(AppError):
        collect_dist(dist, "https://app.example.com", max_bytes=5)
    with pytest.raises(AppError):
        collect_dist(tmp_path / "missing", "https://app.example.com", max_bytes=10_000)
    (dist / "index.html").unlink()
    with pytest.raises(AppError):
        collect_dist(dist, "https://app.example.com", max_bytes=10_000)


def test_vercel_project_and_deployment_calls():
    net = FakeNetwork()
    net.add("GET", r"/v2/user", {"user": {"username": "founder", "email": "f@example.com"}})
    net.add(
        "GET",
        r"/v9/projects/acme-control",
        status=404,
        json_body={"error": {"message": "not found"}},
    )
    net.add("POST", r"/v11/projects", {"id": "prj_1", "name": "acme-control"})
    net.add(
        "POST",
        r"/v13/deployments",
        {"id": "dpl_1", "url": "acme-control-abc.vercel.app", "readyState": "QUEUED"},
    )
    client = VercelClient(http(net), SECRET, team_id="team_1")
    assert client.user()["username"] == "founder"
    assert client.project("acme-control") is None
    assert client.project_create("acme-control")["id"] == "prj_1"
    dep = client.deploy(
        "acme-control", [{"file": "index.html", "data": "eA==", "encoding": "base64"}]
    )
    assert dep["id"] == "dpl_1"
    call = net.calls("POST", "/v13/deployments")[0]
    assert dict(call.url.params)["teamId"] == "team_1"
    body = json.loads(call.content)
    assert body["target"] == "production" and body["name"] == "acme-control"
    assert project_name_for("Acme Labs!") == "acme-labs-control"


# ------------------------------------------------------------------ github


def test_github_registry_pr_flow_forks_branches_commits_and_opens_the_pull_request():
    state = {"forked": False}
    net = FakeNetwork()
    net.add("GET", r"/user$", {"login": "founder"}, headers={"x-oauth-scopes": "public_repo"})
    net.add(
        "GET",
        r"/repos/is-a-dev/register$",
        {"default_branch": "main", "full_name": "is-a-dev/register"},
    )

    def fork_lookup(request):
        if state["forked"]:
            return httpx.Response(200, json={"full_name": "founder/register", "fork": True})
        return httpx.Response(404, json={"message": "Not Found"})

    def do_fork(request):
        state["forked"] = True
        return httpx.Response(202, json={"full_name": "founder/register"})

    net.add("GET", r"/repos/founder/register$", handler=fork_lookup)
    net.add("POST", r"/repos/is-a-dev/register/forks", handler=do_fork)
    net.add("POST", r"/repos/founder/register/merge-upstream", {"message": "ok"})
    net.add("GET", r"/repos/is-a-dev/register/git/ref/heads/main", {"object": {"sha": "abc123"}})
    net.add("POST", r"/repos/founder/register/git/refs", {"ref": "x"}, status=201)
    net.add(
        "PUT",
        r"/repos/founder/register/contents/domains/acme.json",
        {"commit": {"sha": "c1"}},
        status=201,
    )
    pr_url = "https://github.com/is-a-dev/register/pull/42"
    net.add(
        "POST",
        r"/repos/is-a-dev/register/pulls",
        {"number": 42, "html_url": pr_url, "state": "open"},
        status=201,
    )
    client = GithubClient(http(net), SECRET, sleep=lambda s: None)
    assert client.identity()["login"] == "founder" and client.scopes == ["public_repo"]
    result = submit_registry_pr(
        client,
        upstream="is-a-dev/register",
        login="founder",
        branch="company-os/register-acme",
        file_path="domains/acme.json",
        file_content='{"owner":{"username":"founder"}}\n',
        commit_message="Register acme.is-a.dev",
        pr_title="Register acme.is-a.dev",
        pr_body="body",
    )
    assert result == {"number": 42, "url": pr_url, "state": "open"}
    pr = json.loads(net.calls("POST", "/pulls")[0].content)
    assert pr["head"] == "founder:company-os/register-acme" and pr["base"] == "main"
    put = json.loads(net.calls("PUT", "contents")[0].content)
    assert base64.b64decode(put["content"]).decode() == '{"owner":{"username":"founder"}}\n'
    assert put["branch"] == "company-os/register-acme"
    assert net.calls("POST", "/forks")  # forked because the fork did not exist yet


# ------------------------------------------------------------------ e-mail providers


def test_agentmail_creates_inboxes_and_sends():
    net = FakeNetwork()
    net.add("GET", r"/v0/inboxes\?", {"inboxes": [], "count": 0})
    net.add(
        "POST",
        r"/v0/inboxes$",
        {"inbox_id": "cofounder@agentmail.to", "email": "cofounder@agentmail.to"},
    )
    net.add(
        "POST",
        r"/v0/inboxes/cofounder@agentmail.to/messages/send",
        {"message_id": "m1", "thread_id": "t1"},
    )
    client = AgentMailClient(http(net), SECRET)
    assert client.inboxes(1) == []
    inbox = client.create_inbox(
        username="cofounder", display_name="Co-founder", client_id="c:cofounder"
    )
    assert inbox["email"] == "cofounder@agentmail.to"
    assert json.loads(net.requests[1].content) == {
        "username": "cofounder",
        "display_name": "Co-founder",
        "client_id": "c:cofounder",
    }
    sent = client.send(
        "cofounder@agentmail.to", to=["jane@customer.example"], subject="Hi", text="Hello"
    )
    assert sent["message_id"] == "m1"
    assert net.requests[0].headers["authorization"] == f"Bearer {SECRET}"


def test_resend_domains_and_sending_with_an_idempotency_key():
    net = FakeNetwork()
    net.add(
        "GET", r"/domains$", {"data": [{"id": "d1", "name": "example.com", "status": "verified"}]}
    )
    net.add("POST", r"/emails$", {"id": "em_1"})
    client = ResendClient(http(net), SECRET)
    assert client.domains()[0]["status"] == "verified"
    client.send(
        sender="Co-founder <cofounder@example.com>",
        to=["jane@customer.example"],
        subject="Hi",
        text="Hello",
        idempotency_key="k1",
    )
    sent = net.calls("POST", "/emails")[0]
    assert sent.headers["idempotency-key"] == "k1"
    assert json.loads(sent.content)["from"] == "Co-founder <cofounder@example.com>"


# ------------------------------------------------------------------ registrars


def test_porkbun_check_register_and_money_handling():
    assert usd_to_micros("9.73") == 9_730_000 and micros_to_cents(9_730_000) == 973
    assert micros_to_cents(9_730_001) == 974  # never under-pay a price
    net = FakeNetwork()
    net.add(
        "POST",
        r"/domain/checkDomain/example\.com",
        {
            "status": "SUCCESS",
            "response": {
                "avail": "yes",
                "price": "9.73",
                "regularPrice": "9.73",
                "premium": "no",
                "minDuration": 1,
                "additional": {"renewal": {"price": "10.50"}},
            },
        },
    )
    net.add(
        "POST",
        r"/domain/checkDomain/taken\.com",
        {"status": "SUCCESS", "response": {"avail": "no"}},
    )
    net.add(
        "POST",
        r"/domain/create/example\.com",
        {"status": "SUCCESS", "domain": "example.com", "cost": 973, "orderId": 77},
    )
    client = PorkbunClient(http(net), "pk1_key", "sk1_secret")
    quote = client.check("example.com")
    assert quote == {
        "domain": "example.com",
        "available": True,
        "price_micros": 9_730_000,
        "renewal_price_micros": 10_500_000,
        "premium": False,
        "min_years": 1,
    }
    assert client.check("taken.com")["available"] is False
    done = client.register("example.com", 9_730_000)
    assert done["charged_micros"] == 9_730_000 and done["order_id"] == 77
    create = net.calls("POST", "/domain/create/")[0]
    assert json.loads(create.content) == {"cost": 973, "agreeToTerms": "yes"}
    # credentials travel in headers, never in the URL or body
    assert (
        create.headers["x-api-key"] == "pk1_key"
        and create.headers["x-secret-api-key"] == "sk1_secret"
    )
    assert "sk1_secret" not in net.everything_sent().replace('"x-secret-api-key": "sk1_secret"', "")


def test_porkbun_error_status_is_a_provider_error():
    net = FakeNetwork().add(
        "POST", r"/ping", {"status": "ERROR", "message": "Invalid API key sk1_secret"}
    )
    with pytest.raises(ProviderError) as err:
        PorkbunClient(http(net), "pk1_key", "sk1_secret").ping()
    assert "sk1_secret" not in err.value.message


NC_OK = '<?xml version="1.0"?><ApiResponse Status="OK" xmlns="http://api.namecheap.com/xml.response"><CommandResponse>{body}</CommandResponse></ApiResponse>'


def test_namecheap_xml_check_pricing_and_registration_needs_contact_details():
    def handler(request):
        form = dict(p.split("=", 1) for p in request.content.decode().split("&"))
        cmd = form["Command"]
        if cmd == "namecheap.domains.check":
            body = (
                '<DomainCheckResult Domain="example.com" Available="true" IsPremiumName="false"/>'
            )
        elif cmd == "namecheap.users.getPricing":
            price = "8.88" if form["ActionName"] == "REGISTER" else "12.98"
            body = f'<UserGetPricingResult><ProductType Name="domains"><ProductCategory Name="register"><Product Name="com"><Price Duration="1" DurationType="YEAR" YourPrice="{price}"/></Product></ProductCategory></ProductType></UserGetPricingResult>'
        elif cmd == "namecheap.domains.create":
            body = '<DomainCreateResult Domain="example.com" Registered="true" ChargedAmount="8.88" OrderID="991"/>'
        else:
            body = '<UserGetBalancesResult Currency="USD"/>'
        return httpx.Response(200, text=NC_OK.format(body=body))

    net = FakeNetwork().add("POST", r"api\.namecheap\.com", handler=handler)
    client = NamecheapClient(
        http(net), "ncKEY123456", api_user="u", username="u", client_ip="1.2.3.4"
    )
    quote = client.check("example.com")
    assert (
        quote["available"]
        and quote["price_micros"] == 8_880_000
        and quote["renewal_price_micros"] == 12_980_000
    )
    with pytest.raises(ProviderError) as err:
        client.register("example.com", {})
    assert "registrant" in err.value.message
    contact = {
        k: "x"
        for k in (
            "FirstName",
            "LastName",
            "Address1",
            "City",
            "StateProvince",
            "PostalCode",
            "Country",
            "Phone",
            "EmailAddress",
        )
    }
    done = client.register("example.com", contact)
    assert done == {"domain": "example.com", "charged_micros": 8_880_000, "order_id": "991"}
    assert "ncKEY123456" not in str(net.requests[0].url)  # POST form, not the URL


def test_namecheap_refuses_hostile_xml():
    net = FakeNetwork().add(
        "POST",
        r"api\.namecheap\.com",
        text='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><ApiResponse Status="OK"/>',
    )
    with pytest.raises(ProviderError):
        NamecheapClient(http(net), "k", api_user="u", username="u", client_ip="1.1.1.1").ping()


# ------------------------------------------------------------------ tailscale


def test_tailscale_status_reads_the_local_cli():
    info = tailscale.status("/usr/bin/tailscale", fake_run_factory(tailscale=TAILSCALE_RUNNING))
    assert (
        info["running"]
        and info["dns_name"] == "test-mac.tail1234.ts.net"
        and info["funnel_capable"] is True
    )
    stopped = dict(TAILSCALE_RUNNING, BackendState="NeedsLogin")
    info = tailscale.status("/usr/bin/tailscale", fake_run_factory(tailscale=stopped))
    assert not info["running"] and "NeedsLogin" in info["error"]
    assert tailscale.status(None, fake_run_factory())["installed"] is False
