from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.runtimes.registry import RuntimeRegistry
from tests.conftest import agents_url, new_agent


def test_health_and_me(client):
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json() == {"status": "ok", "database": "ok", "version": "0.1.0", "auth_mode": "local"}
    me = client.get("/api/me").json()
    assert me["user"]["email"] == "owner@localhost" and me["memberships"] == []


def test_local_owner_is_created_once(app):
    with TestClient(app) as c1:
        first = c1.get("/api/me").json()["user"]["id"]
    with TestClient(app) as c2:
        assert c2.get("/api/me").json()["user"]["id"] == first


def test_validation_error_envelope(client):
    resp = client.post("/api/companies", json={"mission": 5})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert isinstance(err["message"], str)
    assert isinstance(err["details"], list) and err["details"]
    for d in err["details"]:
        assert set(d) == {"loc", "msg"}
        assert isinstance(d["loc"], list) and isinstance(d["msg"], str)
    assert ["body", "name"] in [d["loc"] for d in err["details"]]
    # no echo of submitted input
    assert "input" not in str(err)


def test_malformed_json_and_bad_query_use_envelope(client, company):
    r = client.post(
        "/api/companies", content="{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = client.get(f"/api/companies/{company['id']}/activity", params={"limit": 0})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = client.get(f"/api/companies/{company['id']}/activity", params={"limit": 201})
    assert r.status_code == 422


def test_unknown_route_and_method_use_envelope(client):
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not found", "details": None}}
    r = client.delete("/api/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_internal_error_does_not_leak(make_settings):
    app = create_app(make_settings(), runtime_registry=RuntimeRegistry())

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("secret-internal-detail /etc/passwd")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/boom")
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "details": None}
    }
    assert "secret-internal-detail" not in r.text


def test_openapi_and_docs_served(client):
    spec = client.get("/api/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    for p in [
        "/api/health",
        "/api/me",
        "/api/companies",
        "/api/companies/{company_id}",
        "/api/companies/{company_id}/overview",
        "/api/companies/{company_id}/departments",
        "/api/companies/{company_id}/departments/{department_id}",
        "/api/companies/{company_id}/agents",
        "/api/companies/{company_id}/agents/{agent_id}",
        "/api/companies/{company_id}/agents/{agent_id}/{action}",
        "/api/companies/{company_id}/activity",
        "/api/runtimes",
    ]:
        assert p in paths, p
    assert client.get("/api/docs").status_code == 200


def test_cors_allows_vite_origin_only(client):
    ok = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(
        "/api/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers


# ------------------------------------------------------------------ activity pagination


def test_activity_pagination_newest_first_no_dupes(client, company):
    cid = company["id"]
    for i in range(6):
        client.post(f"/api/companies/{cid}/departments", json={"name": f"D{i}"})
    url = f"/api/companies/{cid}/activity"
    full = client.get(url, params={"limit": 200}).json()
    assert full["next_cursor"] is None and len(full["items"]) == 7
    stamps = [e["created_at"] for e in full["items"]]
    assert [e["action"] for e in full["items"]][-1] == "company.created"
    assert [e["summary"] for e in full["items"]][0] == "Created department D5"

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        page = client.get(url, params=params).json()
        assert len(page["items"]) <= 3
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_cursor"]
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert seen == [e["id"] for e in full["items"]]
    assert len(set(seen)) == 7
    assert stamps == sorted(stamps, reverse=True)


def test_activity_filters_and_bad_cursor(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    client.post(f"{agents_url(cid)}/{agent['id']}/activate")
    url = f"/api/companies/{cid}/activity"
    by_target = client.get(url, params={"target_type": "agent", "target_id": agent["id"]}).json()
    assert [e["action"] for e in by_target["items"]] == ["agent.activated", "agent.created"]
    assert (
        client.get(url, params={"target_type": "company"}).json()["items"][0]["action"]
        == "company.created"
    )
    bad = client.get(url, params={"cursor": "garbage!!"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"


# ------------------------------------------------------------------ overview


UNAVAILABLE = {
    "tasks": "M3",
    "spending": "M6",
    "approvals": "M6",
    "artifacts": "M7",
    "routines": "M5",
}


def test_overview_reports_unbuilt_subsystems_honestly(client, company):
    cid = company["id"]
    ov = client.get(f"/api/companies/{cid}/overview").json()
    for key, milestone in UNAVAILABLE.items():
        assert ov[key] == {"available": False, "milestone": milestone}
    assert ov["company"]["id"] == cid
    assert ov["founder"]["kind"] == "founder" and ov["founder"]["status"] == "active"
    assert ov["agents"] == {
        "total": 1,
        "by_status": {
            "proposed": 0,
            "active": 1,
            "paused": 0,
            "retiring": 0,
            "retired": 0,
            "rejected": 0,
        },
    }
    assert ov["departments"] == {"total": 0}
    assert [e["action"] for e in ov["recent_activity"]] == ["company.created"]


def test_overview_counts_and_recent_limit(client, company):
    cid = company["id"]
    a = new_agent(client, cid, name="A")
    new_agent(client, cid, name="B")
    client.post(f"{agents_url(cid)}/{a['id']}/activate")
    client.post(f"{agents_url(cid)}/{a['id']}/pause")
    for i in range(12):
        client.post(f"/api/companies/{cid}/departments", json={"name": f"D{i}"})
    ov = client.get(f"/api/companies/{cid}/overview").json()
    assert ov["agents"]["total"] == 3
    assert ov["agents"]["by_status"]["paused"] == 1
    assert ov["agents"]["by_status"]["proposed"] == 1
    assert ov["agents"]["by_status"]["active"] == 1
    assert ov["departments"]["total"] == 12
    assert len(ov["recent_activity"]) == 10


@pytest.mark.parametrize("path", ["", "/overview"])
def test_overview_company_404_for_missing(client, path):
    assert client.get(f"/api/companies/nope{path}").status_code == 404
