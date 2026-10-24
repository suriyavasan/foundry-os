"""Device-level reachability through the API: status, LAN preference, the public-exposure gate,
the tunnel (fake binary), the verified install, owner-only access."""

from __future__ import annotations

import hashlib

from app.integrations import tunnel as tunnel_mod
from app.models.guardrails import GuardrailEvent, SystemState
from tests import accounts_helpers as ah
from tests.m8_helpers import (
    TAILSCALE_RUNNING,
    claim_owner,
    connect,
    fake_cloudflared,
    fake_run_factory,
    wait_for,
)
from tests.test_m8_cloudflared_install import API, ASSET, BASE, release, tgz

URL = "https://quiet-river-1234.trycloudflare.com"


def put_binary(m8):
    m8.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    return fake_cloudflared(m8.settings.resolved_bin_dir / "cloudflared", url=URL)


def test_status_lists_lan_addresses_the_mdns_name_and_the_gate(client, m8):
    r = client.get("/api/reachability")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["mode"] == "local" and s["auth_mode"] == "password"
    assert s["local_urls"] == ["http://127.0.0.1:8700", "http://localhost:8700"]
    addrs = {a["address"]: a for a in s["lan"]["addresses"]}
    # loopback, link-local (fe80, 169.254), tunnel and VM-bridge interfaces are not LAN addresses
    assert set(addrs) == {"192.168.1.20", "2001:db8:abcd:12::20"}
    assert addrs["192.168.1.20"] == {
        "address": "192.168.1.20",
        "family": "ipv4",
        "interface": "en0",
        "url": "http://192.168.1.20:8700",
    }
    assert addrs["2001:db8:abcd:12::20"]["url"] == "http://[2001:db8:abcd:12::20]:8700"
    assert (
        s["lan"]["mdns_name"] == "test-mac.local"
        and s["lan"]["mdns_url"] == "http://test-mac.local:8700"
    )
    assert s["device_name"] == "Test Mac"
    assert s["lan"]["enabled"] is False and s["lan"]["restart_required"] is False
    assert s["active_url"] == "http://127.0.0.1:8700"
    # a fresh install: no owner password yet, so exposure is blocked, with a code and a reason
    assert s["public_exposure_blocked_code"] == "no_password"
    assert "password" in s["public_exposure_blocked_reason"]
    assert s["tunnel"]["state"] == "stopped" and s["tunnel"]["binary"]["found"] is False


def test_the_binary_missing_state_offers_instructions_and_a_confirmed_download(client, m8):
    t = client.get("/api/reachability").json()["tunnel"]
    b = t["binary"]
    assert b["name"] == "cloudflared" and b["found"] is False
    assert b["install_instructions"] and b["download_offered"] is True
    assert b["download_url"] is None  # nothing is looked up (or downloaded) until asked
    quick = next(p for p in t["providers"] if p["kind"] == "cloudflare_quick")
    assert quick["available"] is False and "not installed" in quick["unavailable_reason"]
    assert m8.net.requests == []  # polling the status never touches the network


def test_lan_needs_the_exposure_gate_and_reports_restart_required(client, app, m8):
    # 1. unclaimed install
    r = client.patch("/api/reachability/lan", json={"enabled": True})
    assert r.status_code == 403
    assert r.json()["error"]["details"] == {
        "reason": "public_exposure_blocked",
        "code": "no_password",
    }
    # 2. a password that was not set under the policy (and no 2FA)
    claim_owner(app, strong=False)
    r = client.patch("/api/reachability/lan", json={"enabled": True})
    assert r.status_code == 403 and r.json()["error"]["details"]["code"] == "weak_password"
    assert client.get("/api/reachability").json()["public_exposure_blocked_code"] == "weak_password"
    # 3. 2FA is an acceptable alternative
    claim_owner(app, strong=False, totp=True)
    assert client.get("/api/reachability").json()["public_exposure_blocked_code"] is None
    # 4. a strong password: allowed -> saved as a preference; the running process is still loopback
    claim_owner(app, strong=True)
    r = client.patch("/api/reachability/lan", json={"enabled": True})
    assert r.status_code == 200, r.text
    lan = r.json()["lan"]
    assert lan["requested"] is True and lan["enabled"] is False and lan["restart_required"] is True
    assert r.json()["mode"] == "local"  # nothing is claimed that is not true
    with app.state.session_factory() as db:
        assert db.get(SystemState, "reachability").value["lan_enabled"] is True
    # once the launcher rebinds (COMPANY_OS_BIND_HOST), the same status says so
    app.state.settings.bind_host = "0.0.0.0"
    s = client.get("/api/reachability").json()
    assert (
        s["mode"] == "lan" and s["lan"]["enabled"] is True and s["lan"]["restart_required"] is False
    )
    assert s["active_url"] == "http://test-mac.local:8700"
    # switching it off is always allowed, even if the gate would block
    claim_owner(app, strong=False)
    r = client.patch("/api/reachability/lan", json={"enabled": False})
    assert r.status_code == 200 and r.json()["lan"]["requested"] is False
    assert r.json()["lan"]["restart_required"] is True  # still bound until restart


def test_local_auth_mode_never_allows_exposure(make_settings, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.runtimes.registry import RuntimeRegistry

    app = create_app(make_settings(auth_mode="local"), runtime_registry=RuntimeRegistry())
    with TestClient(app, base_url="http://localhost:8700", client=("127.0.0.1", 50123)) as c:
        c.headers["Authorization"] = f"Bearer {app.state.owner_token.get()}"
        claim_owner(app)
        s = c.get("/api/reachability").json()
        assert s["public_exposure_blocked_code"] == "auth_local"
        for call in (
            lambda: c.patch("/api/reachability/lan", json={"enabled": True}),
            lambda: c.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"}),
        ):
            r = call()
            assert r.status_code == 403 and r.json()["error"]["details"]["code"] == "auth_local"


def test_quick_tunnel_runs_a_fake_binary_and_reports_its_url(client, app, m8):
    claim_owner(app)
    put_binary(m8)
    r = client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    assert r.status_code == 200, r.text
    wait_for(lambda: client.get("/api/reachability").json()["tunnel"]["state"] == "running")
    s = client.get("/api/reachability").json()
    assert s["mode"] == "public" and s["active_url"] == URL
    assert s["tunnel"]["url"] == URL and s["tunnel"]["provider"] == "cloudflare_quick"
    assert (
        s["tunnel"]["binary"]["found"] is True and s["tunnel"]["binary"]["version"] == "2026.10.3"
    )
    assert any(
        "quick Tunnel" in line
        for line in client.get("/api/reachability/tunnel/logs").json()["lines"]
    )
    # a second start is refused while it runs
    assert (
        client.post(
            "/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"}
        ).status_code
        == 409
    )
    r = client.post("/api/reachability/tunnel/stop")
    assert r.status_code == 200 and r.json()["state"] == "stopped" and r.json()["url"] is None
    assert client.get("/api/reachability").json()["mode"] == "local"
    with app.state.session_factory() as db:
        assert db.get(SystemState, "reachability").value["tunnel"]["desired"] is False


def test_the_tunnel_does_not_start_without_the_binary_or_under_weak_auth(client, app, m8):
    r = client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    assert r.status_code == 403  # unclaimed: the gate comes first
    claim_owner(app)
    r = client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "binary_missing"
    r = client.post(
        "/api/reachability/tunnel/start",
        json={"provider": "cloudflare_named", "hostname": "a.example.com"},
    )
    assert r.status_code == 409  # still no binary
    put_binary(m8)
    r = client.post(
        "/api/reachability/tunnel/start",
        json={"provider": "cloudflare_named", "hostname": "not a host"},
    )
    assert r.status_code == 409 or r.status_code == 422  # no Cloudflare account yet / invalid host
    assert client.get("/api/reachability").json()["tunnel"]["state"] == "stopped"


def test_funnel_needs_tailscale_running_and_a_connected_account(client, app, company, m8, tmp_path):
    claim_owner(app)
    ts = tmp_path / "tailscale"
    ts.write_text("#!/bin/sh\n")
    app.state.settings.tailscale_path = ts
    r = client.post("/api/reachability/tunnel/start", json={"provider": "tailscale_funnel"})
    assert r.status_code == 409 and "account" in r.json()["error"]["message"]
    connect(client, company["id"], "tailscale")
    opts = {p["kind"]: p for p in client.get("/api/reachability").json()["tunnel"]["providers"]}
    assert opts["tailscale_funnel"]["available"] is True
    m8.ic.run = fake_run_factory(tailscale=dict(TAILSCALE_RUNNING, BackendState="Stopped"))
    r = client.post("/api/reachability/tunnel/start", json={"provider": "tailscale_funnel"})
    assert r.status_code == 409 and "not running" in r.json()["error"]["message"]


def test_install_endpoint_refuses_without_confirmation_and_verifies_the_checksum(
    client, app, m8, monkeypatch
):
    claim_owner(app)
    monkeypatch.setattr("app.integrations.cloudflared.platform_asset", lambda *a: ASSET)
    r = client.post("/api/reachability/tunnel/install", json={})
    assert (
        r.status_code == 409 and r.json()["error"]["details"]["reason"] == "confirmation_required"
    )
    assert m8.net.requests == []
    blob = tgz()
    m8.net.add("GET", API, release(ASSET, blob))
    m8.net.add("GET", BASE + ASSET, content=blob)
    plan = client.get("/api/reachability/tunnel/install-plan").json()
    assert plan["sha256"] == hashlib.sha256(blob).hexdigest() and plan["asset"] == ASSET
    # the status now shows what the owner would be confirming
    b = client.get("/api/reachability").json()["tunnel"]["binary"]
    assert b["download_sha256"] == plan["sha256"] and b["download_url"] == BASE + ASSET
    # a checksum other than the published one is refused before any download
    r = client.post("/api/reachability/tunnel/install", json={"confirm": True, "sha256": "0" * 64})
    assert r.status_code == 409 and not m8.net.calls("GET", "releases/download")
    r = client.post(
        "/api/reachability/tunnel/install", json={"confirm": True, "sha256": plan["sha256"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["binary"]["found"] is True and r.json()["binary"]["path"].endswith(
        "bin/cloudflared"
    )


def test_only_the_installation_owner_can_see_or_change_reachability(app, client, m8):
    owner = ah.do_setup(app)
    cid = ah.create_company(owner)["id"]
    assert owner.get("/api/reachability").status_code == 200
    created = ah.invite(owner, cid, "bob@example.com", "admin")
    bob = ah.join(app, created, "bob")
    # a company admin is not the installation owner
    for call in (
        lambda: bob.get("/api/reachability"),
        lambda: bob.patch("/api/reachability/lan", json={"enabled": True}),
        lambda: bob.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"}),
        lambda: bob.post("/api/reachability/tunnel/stop"),
        lambda: bob.get("/api/reachability/tunnel/logs"),
    ):
        assert call().status_code == 403
    assert ah.browser(app).get("/api/reachability").status_code == 401
    # setup went through the real flow, so the gate is open: no password problem, 2FA only advised
    s = owner.get("/api/reachability").json()
    assert s["public_exposure_blocked_code"] is None
    assert s["public_exposure_advice"] == "two_factor_recommended"


def test_agents_and_write_scoped_tokens_cannot_touch_reachability(app, client, company, m8):
    from tests.helpers import new_agent
    from tests.test_agent_api import start_run

    agent = new_agent(client, company["id"], name="Ops")
    session = start_run(app, client, company["id"], agent["id"])
    for method, path in (
        ("get", "/api/reachability"),
        ("patch", "/api/reachability/lan"),
        ("post", "/api/reachability/tunnel/start"),
    ):
        r = getattr(client, method)(
            path,
            headers=session.headers,
            **(
                {"json": {"enabled": True, "provider": "cloudflare_quick"}}
                if method != "get"
                else {}
            ),
        )
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["code"] == "guardrail_denied"
    with app.state.session_factory() as db:
        denied = [e.action for e in db.query(GuardrailEvent).filter_by(decision="deny")]
    assert "reachability.manage" in denied


def test_a_write_scoped_token_is_not_enough(app, m8):
    owner = ah.do_setup(app)
    token = owner.post("/api/auth/tokens", json={"name": "ci", "scope": "write"}).json()["token"]
    other = ah.browser(app)
    r = other.patch(
        "/api/reachability/lan",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "token_scope"
    r = other.get("/api/reachability", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200  # reading is fine


def test_tunnel_events_reach_the_company_stream(client, app, company, m8):
    claim_owner(app)
    put_binary(m8)
    client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    wait_for(lambda: client.get("/api/reachability").json()["tunnel"]["state"] == "running")
    from app.models.run import CompanyEvent

    with app.state.session_factory() as db:
        types = {e.type for e in db.query(CompanyEvent).filter_by(company_id=company["id"])}
    assert "tunnel.updated" in types and "reachability.updated" in types


def test_resume_brings_back_a_tunnel_that_was_running(client, app, company, m8):
    from app.services import reachability

    claim_owner(app)
    put_binary(m8)
    with app.state.session_factory() as db:
        reachability.save_state(
            db, tunnel={"provider": "cloudflare_quick", "hostname": None, "desired": True}
        )
        db.commit()
    assert reachability.resume_tunnel(app.state.session_factory, m8.ic) is True
    wait_for(lambda: m8.ic.supervisor.status()["state"] == "running")
    assert m8.ic.supervisor.status()["url"] == URL
    assert tunnel_mod  # imported for clarity of what is under test
