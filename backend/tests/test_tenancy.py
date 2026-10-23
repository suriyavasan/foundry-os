from __future__ import annotations

from tests.conftest import agents_url, human, new_agent


def test_other_user_cannot_access_company(client, client_as, make_user, company):
    cid = company["id"]
    dept = client.post(f"/api/companies/{cid}/departments", json={"name": "Eng"}).json()
    agent = new_agent(client, cid)

    bob = client_as(human(make_user("bob@example.com")))
    for method, url, body in [
        ("get", f"/api/companies/{cid}", None),
        ("patch", f"/api/companies/{cid}", {"mission": "stolen"}),
        ("get", f"/api/companies/{cid}/overview", None),
        ("get", f"/api/companies/{cid}/agents", None),
        ("post", f"/api/companies/{cid}/agents", {"name": "X", "role_title": "Y"}),
        ("get", f"/api/companies/{cid}/agents/{agent['id']}", None),
        ("patch", f"/api/companies/{cid}/agents/{agent['id']}", {"name": "Hax"}),
        ("post", f"/api/companies/{cid}/agents/{agent['id']}/activate", None),
        ("get", f"/api/companies/{cid}/departments", None),
        ("post", f"/api/companies/{cid}/departments", {"name": "Evil"}),
        ("patch", f"/api/companies/{cid}/departments/{dept['id']}", {"name": "Evil"}),
        ("get", f"/api/companies/{cid}/activity", None),
    ]:
        resp = bob.request(method, url, json=body)
        assert resp.status_code == 404, (method, url, resp.text)
        assert resp.json()["error"]["code"] == "not_found"

    # Same response as for a company that does not exist: no existence leak.
    missing = bob.get("/api/companies/does-not-exist")
    assert missing.status_code == 404
    assert missing.json() == bob.get(f"/api/companies/{cid}").json()

    # Nothing was changed.
    assert client.get(f"/api/companies/{cid}").json()["mission"] == "Ship"
    assert client.get(f"/api/companies/{cid}/agents/{agent['id']}").json()["name"] == "Nova"


def test_company_list_only_returns_own(client, client_as, make_user, company):
    bob_user = make_user("bob@example.com")
    bob = client_as(human(bob_user))
    bobs = bob.post("/api/companies", json={"name": "Bob Inc"}).json()
    assert [c["id"] for c in bob.get("/api/companies").json()["items"]] == [bobs["id"]]
    assert [c["id"] for c in client.get("/api/companies").json()["items"]] == [company["id"]]
    assert bob.get("/api/me").json()["memberships"][0]["company_id"] == bobs["id"]


def test_cross_company_id_does_not_leak_between_own_companies(client, company):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    agent = new_agent(client, company["id"])
    # Own company B, but agent id belongs to company A.
    resp = client.get(f"{agents_url(other['id'])}/{agent['id']}")
    assert resp.status_code == 404
    resp = client.patch(f"{agents_url(other['id'])}/{agent['id']}", json={"name": "x"})
    assert resp.status_code == 404
    resp = client.post(f"{agents_url(other['id'])}/{agent['id']}/activate")
    assert resp.status_code == 404


def test_cross_company_references_rejected(client, company):
    a, b = company["id"], client.post("/api/companies", json={"name": "B Co"}).json()["id"]
    dept_b = client.post(f"/api/companies/{b}/departments", json={"name": "Eng"}).json()
    agent_b = new_agent(client, b, name="Bee")
    agent_a = new_agent(client, a, name="Aye")

    # create
    r = client.post(
        agents_url(a), json={"name": "N", "role_title": "R", "department_id": dept_b["id"]}
    )
    assert r.status_code == 404
    r = client.post(
        agents_url(a), json={"name": "N", "role_title": "R", "manager_agent_id": agent_b["id"]}
    )
    assert r.status_code == 404
    # patch
    r = client.patch(f"{agents_url(a)}/{agent_a['id']}", json={"department_id": dept_b["id"]})
    assert r.status_code == 404
    r = client.patch(f"{agents_url(a)}/{agent_a['id']}", json={"manager_agent_id": agent_b["id"]})
    assert r.status_code == 404
    # department lead from another company
    r = client.post(
        f"/api/companies/{a}/departments", json={"name": "Ops", "lead_agent_id": agent_b["id"]}
    )
    assert r.status_code == 404
    dept_a = client.post(f"/api/companies/{a}/departments", json={"name": "Ops"}).json()
    r = client.patch(
        f"/api/companies/{a}/departments/{dept_a['id']}", json={"lead_agent_id": agent_b["id"]}
    )
    assert r.status_code == 404
    assert client.get(f"{agents_url(a)}/{agent_a['id']}").json()["department_id"] is None


def test_activity_is_scoped_to_company(client, company):
    other = client.post("/api/companies", json={"name": "Other"}).json()
    ids = {
        e["company_id"]
        for e in client.get(f"/api/companies/{company['id']}/activity").json()["items"]
    }
    assert ids == {company["id"]}
    assert other["id"] not in ids
