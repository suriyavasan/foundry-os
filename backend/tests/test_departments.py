from __future__ import annotations

from tests.conftest import new_agent


def test_department_crud(client, company):
    cid = company["id"]
    base = f"/api/companies/{cid}/departments"
    lead = new_agent(client, cid, name="Lead")
    created = client.post(
        base, json={"name": "Engineering", "description": "Build", "lead_agent_id": lead["id"]}
    )
    assert created.status_code == 201
    dept = created.json()
    assert (
        dept["company_id"] == cid
        and dept["agent_count"] == 0
        and dept["lead_agent_id"] == lead["id"]
    )

    patched = client.patch(f"{base}/{dept['id']}", json={"description": None, "name": "Eng"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Eng" and patched.json()["description"] is None
    assert (
        client.patch(f"{base}/{dept['id']}", json={"lead_agent_id": None}).json()["lead_agent_id"]
        is None
    )
    assert client.patch(f"{base}/missing", json={"name": "x"}).status_code == 404

    actions = [e["action"] for e in client.get(f"/api/companies/{cid}/activity").json()["items"]]
    assert actions[:3] == ["department.updated", "department.updated", "department.created"]


def test_department_name_unique_per_company(client, company):
    cid = company["id"]
    base = f"/api/companies/{cid}/departments"
    assert client.post(base, json={"name": "Eng"}).status_code == 201
    dup = client.post(base, json={"name": "Eng"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "conflict"
    other = client.post(base, json={"name": "Ops"}).json()
    assert client.patch(f"{base}/{other['id']}", json={"name": "Eng"}).status_code == 409
    # same name in a different company is fine
    c2 = client.post("/api/companies", json={"name": "Two"}).json()["id"]
    assert client.post(f"/api/companies/{c2}/departments", json={"name": "Eng"}).status_code == 201


def test_department_validation(client, company):
    base = f"/api/companies/{company['id']}/departments"
    assert client.post(base, json={"name": ""}).status_code == 422
    assert client.post(base, json={"name": "x" * 81}).status_code == 422
    assert client.post(base, json={}).status_code == 422
