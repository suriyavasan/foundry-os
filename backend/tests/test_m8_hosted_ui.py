"""The hosted control UI on Vercel (owner path)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from tests import accounts_helpers as ah
from tests.helpers import api, new_agent
from tests.m8_helpers import claim_owner, connect, fake_cloudflared, wait_for
from tests.test_agent_api import start_run
from tests.test_m8_provider_actions import dist, vercel_routes  # noqa: F401  (fixture)

QUICK = "https://quiet-river-1234.trycloudflare.com"


@pytest.fixture
def tunnel(app, client, m8):
    """A running quick tunnel (fake binary), so the device has a public URL."""
    claim_owner(app)
    m8.settings.resolved_bin_dir.mkdir(parents=True, exist_ok=True)
    fake_cloudflared(m8.settings.resolved_bin_dir / "cloudflared", url=QUICK)
    client.post("/api/reachability/tunnel/start", json={"provider": "cloudflare_quick"})
    wait_for(lambda: m8.ic.supervisor.status()["state"] == "running")


def test_nothing_is_reported_before_a_deploy(client, company):
    r = client.get(api(company["id"], "/hosted-ui"))
    assert r.status_code == 200 and r.json() is None


def test_deploy_needs_vercel_a_public_url_and_a_build(client, app, company, m8, dist):
    cid = company["id"]
    vercel_routes(m8.net)
    r = client.post(api(cid, "/hosted-ui/deploy"))
    assert r.status_code == 403  # the owner has no password yet: no exposure
    claim_owner(app)
    r = client.post(api(cid, "/hosted-ui/deploy"))
    assert (
        r.status_code == 409
        and r.json()["error"]["details"]["reason"] == "no_account"
        or r.status_code == 422
    )
    connect(client, cid, "vercel")
    r = client.post(api(cid, "/hosted-ui/deploy"))
    assert r.status_code == 422 and "public https URL" in r.json()["error"]["message"]
    assert not m8.net.calls("POST", "/v13/deployments")


def test_deploy_forwards_api_to_the_tunnel_and_follows_the_deployment(
    client, app, company, m8, dist, tunnel
):
    cid = company["id"]
    state = {"ready": False}
    m8.net.add("GET", r"/v2/user", {"user": {"username": "founder"}})
    m8.net.add(
        "GET", r"/v9/projects/acme-labs-control", {"id": "prj_1", "name": "acme-labs-control"}
    )
    m8.net.add(
        "POST",
        r"/v13/deployments",
        {"id": "dpl_1", "url": "acme-abc.vercel.app", "readyState": "QUEUED"},
    )
    m8.net.add(
        "GET",
        r"/v13/deployments/dpl_1",
        handler=lambda r: httpx.Response(
            200,
            json={
                "id": "dpl_1",
                "url": "acme-abc.vercel.app",
                "readyState": "READY" if state["ready"] else "BUILDING",
                "alias": ["acme.vercel.app"],
            },
        ),
    )
    connect(client, cid, "vercel")
    r = client.post(api(cid, "/hosted-ui/deploy"))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "status": "deploying",
        "provider": "vercel",
        "project_name": "acme-labs-control",
        "url": "https://acme-abc.vercel.app",
        "proxy_target": QUICK,
        "last_deployed_at": None,
        "error": None,
        "approval_id": None,
    }
    body = json.loads(m8.net.calls("POST", "/v13/deployments")[0].content)
    cfg = json.loads(
        base64.b64decode(next(f for f in body["files"] if f["file"] == "vercel.json")["data"])
    )
    assert cfg["rewrites"][0] == {"source": "/api/:path*", "destination": f"{QUICK}/api/:path*"}
    assert not m8.net.calls("POST", "/v11/projects")  # the project already existed
    # the UI polls while deploying; the status follows Vercel
    assert client.get(api(cid, "/hosted-ui")).json()["status"] == "deploying"
    state["ready"] = True
    got = client.get(api(cid, "/hosted-ui")).json()
    assert (
        got["status"] == "ready"
        and got["url"] == "https://acme.vercel.app"
        and got["last_deployed_at"]
    )
    # redeploy is the same endpoint
    assert client.post(api(cid, "/hosted-ui/deploy")).status_code == 200
    assert len(m8.net.calls("POST", "/v13/deployments")) == 2


def test_a_failed_deployment_and_a_provider_error_are_reported(
    client, app, company, m8, dist, tunnel
):
    cid = company["id"]
    m8.net.add("GET", r"/v2/user", {"user": {"username": "founder"}})
    m8.net.add("GET", r"/v9/projects/acme-labs-control", {"id": "prj_1"})
    m8.net.add(
        "POST", r"/v13/deployments", {"id": "dpl_9", "url": "x.vercel.app", "readyState": "QUEUED"}
    )
    m8.net.add("GET", r"/v13/deployments/dpl_9", {"id": "dpl_9", "readyState": "ERROR"})
    connect(client, cid, "vercel")
    r = client.post(api(cid, "/hosted-ui/deploy")).json()
    assert r["status"] == "error" and "error" in r["error"].lower()
    m8.net.routes[:] = [x for x in m8.net.routes if "v13/deployments" not in x[1].pattern]
    m8.net.add(
        "POST",
        r"/v13/deployments",
        status=402,
        json_body={"error": {"message": "plan limit reached"}},
    )
    r = client.post(api(cid, "/hosted-ui/deploy")).json()
    assert r["status"] == "error" and "plan limit reached" in r["error"]


def test_a_missing_build_is_a_clear_error(client, app, company, m8, tunnel, tmp_path):
    cid = company["id"]
    app.state.settings.web_dist_dir = tmp_path / "nothing-here"
    m8.net.add("GET", r"/v2/user", {"user": {"username": "founder"}})
    connect(client, cid, "vercel")
    r = client.post(api(cid, "/hosted-ui/deploy"))
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "dist_missing"


def test_roles_and_agents(app, client, company, m8, dist):
    owner = ah.do_setup(app)
    cid = ah.create_company(owner)["id"]
    viewer = ah.join(app, ah.invite(owner, cid, "v@example.com", "viewer"), "vic")
    assert viewer.get(api(cid, "/hosted-ui")).status_code == 200
    assert viewer.post(api(cid, "/hosted-ui/deploy")).status_code == 403
    agent = new_agent(owner, cid, name="Ops")
    session = start_run(app, owner, cid, agent["id"])
    assert owner.post(api(cid, "/hosted-ui/deploy"), headers=session.headers).status_code == 403


def test_a_domain_that_points_at_vercel_is_attached_and_never_used_as_the_proxy_target(
    client, app, company, m8, dist, tunnel
):
    from app.models.reachability import CompanyDomain

    cid = company["id"]
    m8.net.add("GET", r"/v2/user", {"user": {"username": "founder"}})
    m8.net.add("GET", r"/v9/projects/acme-labs-control", {"id": "prj_1"})
    m8.net.add(
        "POST", r"/v13/deployments", {"id": "dpl_1", "url": "a.vercel.app", "readyState": "READY"}
    )
    m8.net.add(
        "GET",
        r"/v13/deployments/dpl_1",
        {"id": "dpl_1", "readyState": "READY", "url": "a.vercel.app"},
    )
    m8.net.add("POST", r"/v10/projects/acme-labs-control/domains", {"name": "acme.is-a.dev"})
    with app.state.session_factory() as db:
        db.add(
            CompanyDomain(
                company_id=cid,
                domain="acme.is-a.dev",
                path="github_registry",
                status="active",
                dns_records=[
                    {"type": "CNAME", "name": "acme.is-a.dev", "value": "cname.vercel-dns.com"}
                ],
            )
        )
        db.commit()
    connect(client, cid, "vercel")
    r = client.post(api(cid, "/hosted-ui/deploy"))
    assert r.status_code == 200, r.text
    # /api is forwarded to the tunnel, not to acme.is-a.dev (which IS the hosted UI)
    assert r.json()["proxy_target"] == QUICK
    assert json.loads(m8.net.calls("POST", "/domains")[0].content) == {"name": "acme.is-a.dev"}
