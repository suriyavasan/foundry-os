from __future__ import annotations

from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.company import CompanyMember


def test_create_company_side_effects(client, app, company):
    cid = company["id"]
    assert company["slug"] == "acme-labs"
    assert company["status"] == "active"
    assert company["timezone"] == "UTC"
    assert company["constraints"] == []
    assert company["created_at"].endswith("Z")

    me = client.get("/api/me").json()
    assert me["user"]["email"] == "owner@localhost"
    assert me["memberships"] == [{"company_id": cid, "company_name": "Acme Labs", "role": "owner"}]

    agents = client.get(f"/api/companies/{cid}/agents").json()["items"]
    assert len(agents) == 1
    founder = agents[0]
    assert founder["kind"] == "founder"
    assert founder["status"] == "active"
    assert founder["reports_to_human"] is True
    assert founder["role_title"] == "Founder & CEO"
    assert founder["name"] == "Atlas"
    assert founder["runtime"] == "unassigned"

    events = client.get(f"/api/companies/{cid}/activity").json()["items"]
    assert [e["action"] for e in events] == ["company.created"]
    assert events[0]["actor_type"] == "human"
    assert events[0]["actor_label"] == "Owner"
    assert "Acme Labs" in events[0]["summary"]

    with app.state.session_factory() as db:
        members = db.scalars(select(CompanyMember).where(CompanyMember.company_id == cid)).all()
        assert [m.role for m in members] == ["owner"]
        assert db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid)).all()


def test_create_company_with_founder_options(client):
    resp = client.post(
        "/api/companies",
        json={
            "name": "Beta",
            "constraints": ["No spam"],
            "timezone": "America/New_York",
            "founder": {"name": "Zed", "model_provider": "openrouter", "runtime": "hermes"},
        },
    )
    assert resp.status_code == 201
    cid = resp.json()["id"]
    assert resp.json()["constraints"] == ["No spam"]
    founder = client.get(f"/api/companies/{cid}/agents").json()["items"][0]
    assert (founder["name"], founder["model_provider"], founder["runtime"]) == (
        "Zed",
        "openrouter",
        "hermes",
    )


def test_slug_deduplication(client):
    slugs = [
        client.post("/api/companies", json={"name": "Same Name"}).json()["slug"] for _ in range(3)
    ]
    assert slugs == ["same-name", "same-name-2", "same-name-3"]
    assert client.post("/api/companies", json={"name": "!!!"}).json()["slug"] == "company"


def test_invalid_timezone_and_name_rejected(client):
    assert (
        client.post("/api/companies", json={"name": "X", "timezone": "Mars/Base"}).status_code
        == 422
    )
    assert client.post("/api/companies", json={"name": ""}).status_code == 422
    assert client.post("/api/companies", json={"name": "x" * 121}).status_code == 422


def test_list_and_get_company(client, company):
    page = client.get("/api/companies").json()
    assert [c["id"] for c in page["items"]] == [company["id"]]
    assert page["next_cursor"] is None
    assert client.get(f"/api/companies/{company['id']}").json()["name"] == "Acme Labs"


def test_company_pagination(client):
    ids = [client.post("/api/companies", json={"name": f"Co {i}"}).json()["id"] for i in range(5)]
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/companies", params=params).json()
        seen += [c["id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ids


def test_patch_company_audits_changed_field_names(client, company):
    cid = company["id"]
    resp = client.patch(
        f"/api/companies/{cid}",
        json={"mission": "New mission", "constraints": ["a", "b"], "status": "paused"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["mission"], body["constraints"], body["status"]) == (
        "New mission",
        ["a", "b"],
        "paused",
    )
    ev = client.get(f"/api/companies/{cid}/activity").json()["items"][0]
    assert ev["action"] == "company.updated"
    assert ev["data"] == {"changed": ["mission", "constraints", "status"]}
    assert "New mission" not in str(ev)  # values of text fields are not copied into the audit


def test_patch_company_noop_writes_no_audit(client, company):
    cid = company["id"]
    client.patch(f"/api/companies/{cid}", json={"mission": company["mission"]})
    actions = [e["action"] for e in client.get(f"/api/companies/{cid}/activity").json()["items"]]
    assert actions == ["company.created"]


def test_patch_company_null_name_and_unknown_field_rejected(client, company):
    cid = company["id"]
    assert client.patch(f"/api/companies/{cid}", json={"name": None}).status_code == 422
    assert client.patch(f"/api/companies/{cid}", json={"slug": "hax"}).status_code == 422
    assert client.patch(f"/api/companies/{cid}", json={"mission": None}).status_code == 200
