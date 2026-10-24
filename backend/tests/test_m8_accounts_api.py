"""Connected accounts: the vault, scoping, tenancy, and that no secret ever leaves the control plane."""

from __future__ import annotations

import json
import logging

import pytest

from app.models.audit import AuditEvent
from app.models.provider import Credential
from tests import accounts_helpers as ah
from tests.helpers import api, new_agent
from tests.m8_helpers import (
    SECRET,
    SECRET2,
    cloudflare_routes,
    connect,
    store_secret,
)
from tests.test_agent_api import start_run


def test_connecting_cloudflare_tests_it_scopes_the_credential_and_returns_no_secret(
    client, app, company, m8
):
    cloudflare_routes(m8.net)
    cid = company["id"]
    acc = connect(client, cid, "cloudflare", label="Acme CF")
    assert acc["status"] == "connected" and acc["provider"] == "cloudflare"
    assert (
        acc["metadata"]["account_id"] == "acc1"
        and acc["metadata"]["account_name"] == "Acme Cloudflare"
    )
    assert set(acc["scopes"]) >= {"token:active", "zone:read"}
    assert acc["last_checked_at"] and acc["credential_id"]
    assert SECRET not in json.dumps(acc)
    # the credential is now bound to the provider and to the agent actions it enables
    cred = client.get(api(cid, f"/credentials/{acc['credential_id']}")).json()
    assert cred["provider"] == "cloudflare"
    assert set(cred["allowed_actions"]) == {
        "dns.read",
        "dns.change",
        "tunnel.status",
        "tunnel.start",
        "tunnel.stop",
    }
    listed = client.get(api(cid, "/connected-accounts")).json()
    assert [a["id"] for a in listed["items"]] == [acc["id"]] and listed["next_cursor"] is None
    # the token went to Cloudflare as a bearer header and nowhere else
    assert SECRET not in "".join(str(r.url) for r in m8.net.requests)


def test_an_invalid_token_is_stored_as_invalid_and_can_be_retested(client, company, m8):
    cid = company["id"]
    m8.net.add(
        "GET",
        r"/user/tokens/verify",
        status=401,
        json_body={"success": False, "errors": [{"message": "Invalid API Token"}]},
    )
    acc = connect(client, cid, "cloudflare")
    assert acc["status"] == "invalid" and "Invalid API Token" in acc["last_error"]
    m8.net.routes.clear()
    cloudflare_routes(m8.net)
    check = client.post(api(cid, f"/connected-accounts/{acc['id']}/test")).json()
    assert (
        check["ok"] is True
        and check["account_label"] == "Acme Cloudflare"
        and "zone:read" in check["scopes"]
    )
    assert client.get(api(cid, "/connected-accounts")).json()["items"][0]["status"] == "connected"


def test_validation_unknown_providers_oauth_and_missing_fields(client, company, m8):
    cid = company["id"]
    cred = store_secret(client, cid, "x")
    post = lambda body: client.post(api(cid, "/connected-accounts"), json=body)  # noqa: E731
    assert post({"provider": "nope", "label": "x", "credential_id": cred}).status_code == 422
    assert post({"provider": "cloudflare", "label": "x"}).status_code == 422  # a secret is needed
    assert (
        post({"provider": "cloudflare", "label": "x", "credential_id": "missing-id"}).status_code
        == 404
    )
    assert (
        post({"provider": "porkbun", "label": "x", "credential_id": cred}).status_code == 422
    )  # needs the secret key too
    assert (
        post({"provider": "namecheap", "label": "x", "credential_id": cred}).status_code == 422
    )  # needs api_user, ...
    assert (
        post(
            {
                "provider": "cloudflare",
                "label": "x",
                "credential_id": cred,
                "extra_credential_ids": {"bogus": cred},
            }
        ).status_code
        == 422
    )
    assert (
        post(
            {
                "provider": "cloudflare",
                "label": "x",
                "credential_id": cred,
                "metadata": {"api_token": "abc"},
            }
        ).status_code
        == 422
    )
    assert (
        post(
            {
                "provider": "cloudflare",
                "label": "x",
                "credential_id": cred,
                "metadata": {"account_id": "ghp_" + "a" * 30},
            }
        ).status_code
        == 422
    )
    for provider in (
        "gmail",
        "outlook",
    ):  # connect-only OAuth, not configured: said plainly, never faked
        r = post({"provider": provider, "label": "me"})
        assert (
            r.status_code == 501
            and r.json()["error"]["details"]["reason"] == "oauth_not_configured"
        )
        r = client.post(api(cid, "/connected-accounts/oauth/start"), json={"provider": provider})
        assert r.status_code == 501 and r.json()["error"]["details"]["available"] is False
    assert (
        client.post(
            api(cid, "/connected-accounts/oauth/start"), json={"provider": "cloudflare"}
        ).status_code
        == 422
    )


def test_duplicate_labels_and_foreign_credentials(client, company, m8):
    cloudflare_routes(m8.net)
    cid = company["id"]
    connect(client, cid, "cloudflare", label="main")
    connect(client, cid, "cloudflare", label="main", expect=409)
    other = client.post("/api/companies", json={"name": "Other Co"}).json()
    foreign = store_secret(client, other["id"], "foreign")
    r = client.post(
        api(cid, "/connected-accounts"),
        json={"provider": "cloudflare", "label": "x", "credential_id": foreign},
    )
    assert r.status_code == 404  # another company's credential does not exist here


def test_a_credential_cannot_serve_two_providers(client, company, m8):
    cloudflare_routes(m8.net)
    cid = company["id"]
    acc = connect(client, cid, "cloudflare")
    r = client.post(
        api(cid, "/connected-accounts"),
        json={"provider": "vercel", "label": "v", "credential_id": acc["credential_id"]},
    )
    assert r.status_code == 409


def test_revoking_removes_agent_access_and_wipes_the_secret(client, app, company, m8):
    cloudflare_routes(m8.net)
    cid = company["id"]
    acc = connect(client, cid, "cloudflare")
    r = client.delete(api(cid, f"/connected-accounts/{acc['id']}"))
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    with app.state.session_factory() as db:
        cred = db.get(Credential, acc["credential_id"])
        assert cred.revoked_at is not None and cred.encrypted_value == ""
    assert client.post(api(cid, f"/connected-accounts/{acc['id']}/test")).status_code == 409
    # an agent can no longer use it
    agent = new_agent(client, cid, name="Ops")
    session = start_run(app, client, cid, agent["id"])
    r = session.post(
        "/provider-actions",
        json={
            "credential_id": acc["credential_id"],
            "action": "dns.read",
            "params": {"domain": "example.com"},
        },
    )
    assert r.status_code == 409


def test_tenancy_and_roles(app, client, company, m8):
    cloudflare_routes(m8.net)
    owner = ah.do_setup(app)
    co = ah.create_company(owner)
    cid = co["id"]
    acc = connect(owner, cid, "cloudflare")
    # a second company's owner sees nothing of it
    other_owner = ah.join(app, ah.invite(owner, cid, "eve@example.com", "admin"), "eve")
    other_co = other_owner.post("/api/companies", json={"name": "Eve Inc"}).json()
    assert other_owner.get(api(other_co["id"], "/connected-accounts")).json()["items"] == []
    assert (
        other_owner.get(api(cid, "/connected-accounts")).status_code == 200
    )  # eve is an admin of cid
    stranger = ah.join(
        app, ah.invite(other_owner, other_co["id"], "sam@example.com", "viewer"), "sam"
    )
    # sam is a viewer of Eve's company only: company cid does not exist for them
    assert stranger.get(api(cid, "/connected-accounts")).status_code == 404
    assert stranger.post(api(cid, f"/connected-accounts/{acc['id']}/test")).status_code == 404
    # inside the company, viewers and members cannot see or use accounts
    viewer = ah.join(app, ah.invite(owner, cid, "vic@example.com", "viewer"), "vic")
    member = ah.join(app, ah.invite(owner, cid, "mia@example.com", "member"), "mia")
    for who in (viewer, member):
        assert who.get(api(cid, "/connected-accounts")).status_code == 403
        assert (
            who.post(
                api(cid, "/connected-accounts"), json={"provider": "cloudflare", "label": "z"}
            ).status_code
            == 403
        )
        assert who.delete(api(cid, f"/connected-accounts/{acc['id']}")).status_code == 403
    assert viewer.get(api(cid, "/domain")).status_code == 200  # non-secret status is readable
    assert viewer.get(api(cid, "/mailboxes")).status_code == 200


def test_agents_cannot_manage_accounts_even_with_a_run_token(app, client, company, m8):
    cloudflare_routes(m8.net)
    cid = company["id"]
    acc = connect(client, cid, "cloudflare")
    agent = new_agent(client, cid, name="Ops")
    session = start_run(app, client, cid, agent["id"])
    h = session.headers
    for method, path, body in (
        ("get", "/connected-accounts", None),
        ("post", "/connected-accounts", {"provider": "cloudflare", "label": "evil"}),
        ("post", f"/connected-accounts/{acc['id']}/test", {}),
        ("delete", f"/connected-accounts/{acc['id']}", None),
        ("get", "/connected-accounts/providers", None),
    ):
        kwargs = {"headers": h, **({"json": body} if body is not None else {})}
        r = getattr(client, method)(api(cid, path), **kwargs)
        assert r.status_code == 403 and r.json()["error"]["code"] == "guardrail_denied", (
            method,
            path,
        )


def test_the_provider_catalog_describes_every_connectable_account(client, company):
    items = {
        p["id"]: p for p in client.get(api(company["id"], "/connected-accounts/providers")).json()
    }
    assert {
        "cloudflare",
        "vercel",
        "github",
        "agentmail",
        "resend",
        "tailscale",
        "porkbun",
        "namecheap",
        "gmail",
        "outlook",
    } <= set(items)
    assert items["porkbun"]["secret_fields"] == ["api_key", "secret_api_key"]
    assert items["namecheap"]["required_metadata"] == ["api_user", "username", "client_ip"]
    assert items["tailscale"]["needs_secret"] is False and items["gmail"]["oauth"] is True


def test_secrets_never_appear_in_responses_logs_audit_events_or_urls(
    client, app, company, m8, caplog
):
    caplog.set_level(logging.DEBUG)
    cloudflare_routes(m8.net)
    cid = company["id"]
    acc = connect(client, cid, "cloudflare", secret=SECRET, extras={"webhook_secret": SECRET2})
    client.post(api(cid, f"/connected-accounts/{acc['id']}/test"))
    bodies = [
        client.get(api(cid, "/connected-accounts")).text,
        client.get(api(cid, "/activity")).text,
        client.get(api(cid, "/credentials")).text,
        client.get(api(cid, "/guardrails/events")).text
        if client.get(api(cid, "/guardrails/events")).status_code == 200
        else "",
        json.dumps(acc),
    ]
    with app.state.session_factory() as db:
        bodies.append(json.dumps([e.data for e in db.query(AuditEvent)], default=str))
        bodies.append(json.dumps([e.summary for e in db.query(AuditEvent)]))
    everything = "\n".join(bodies) + caplog.text + "".join(str(r.url) for r in m8.net.requests)
    assert SECRET not in everything and SECRET2 not in everything
    # extra secrets are control-plane only: not offered to agents
    extra = client.get(api(cid, "/credentials")).json()["items"]
    webhook = next(c for c in extra if "webhook_secret" in c["name"])
    assert webhook["provider"] == "cloudflare" and webhook["allowed_actions"] == []


def test_a_failing_provider_error_never_echoes_the_secret(client, company, m8):
    cid = company["id"]
    m8.net.add(
        "GET",
        r"/user/tokens/verify",
        status=403,
        json_body={"success": False, "errors": [{"message": f"bad token {SECRET}"}]},
    )
    acc = connect(client, cid, "cloudflare")
    assert acc["status"] == "invalid" and SECRET not in json.dumps(acc)


@pytest.mark.parametrize("provider", ["vercel", "github", "agentmail", "resend"])
def test_each_provider_connects_with_a_single_token(client, company, m8, provider):
    n = m8.net
    n.add("GET", r"/v2/user", {"user": {"username": "founder"}})
    n.add(
        "GET",
        r"api\.github\.com/user$",
        {"login": "founder"},
        headers={"x-oauth-scopes": "public_repo"},
    )
    n.add("GET", r"/v0/inboxes", {"inboxes": []})
    n.add("GET", r"api\.resend\.com/domains", {"data": []})
    acc = connect(client, company["id"], provider)
    assert acc["status"] == "connected"
    if provider == "github":
        assert acc["metadata"]["login"] == "founder" and acc["scopes"] == ["public_repo"]


def test_porkbun_needs_both_keys_and_namecheap_its_account_fields(client, company, m8):
    n = m8.net
    n.add("POST", r"api\.porkbun\.com/api/json/v3/ping", {"status": "SUCCESS", "yourIp": "1.2.3.4"})
    acc = connect(client, company["id"], "porkbun", extras={"secret_api_key": SECRET2})
    assert acc["status"] == "connected"
    cred = client.get(api(company["id"], f"/credentials/{acc['credential_id']}")).json()
    assert set(cred["allowed_actions"]) == {"domain.search", "domain.purchase"}
    r = connect(
        client,
        company["id"],
        "namecheap",
        secret="nc-key-123456789",
        metadata={"api_user": "u"},
        expect=422,
    )
    assert "username" in r["error"]["message"]
