from __future__ import annotations

from tests.conftest import agents_url, human, new_agent


def test_viewer_is_read_only_everywhere(client, client_as, make_user, add_member, company):
    cid = company["id"]
    dept = client.post(f"/api/companies/{cid}/departments", json={"name": "Eng"}).json()
    agent = new_agent(client, cid)
    viewer_user = make_user("viewer@example.com")
    add_member(cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))

    # reads work
    for url in [
        f"/api/companies/{cid}",
        f"/api/companies/{cid}/overview",
        f"/api/companies/{cid}/agents",
        f"/api/companies/{cid}/agents/{agent['id']}",
        f"/api/companies/{cid}/departments",
        f"/api/companies/{cid}/activity",
    ]:
        assert viewer.get(url).status_code == 200, url
    assert viewer.get("/api/me").json()["memberships"][0]["role"] == "viewer"

    # every mutation is forbidden
    mutations = [
        ("patch", f"/api/companies/{cid}", {"mission": "x"}),
        ("post", f"/api/companies/{cid}/departments", {"name": "Evil"}),
        ("patch", f"/api/companies/{cid}/departments/{dept['id']}", {"name": "Evil"}),
        ("post", agents_url(cid), {"name": "X", "role_title": "Y"}),
        ("patch", f"{agents_url(cid)}/{agent['id']}", {"name": "Hax"}),
    ] + [
        ("post", f"{agents_url(cid)}/{agent['id']}/{a}", None)
        for a in ("activate", "reject", "pause", "resume", "retire")
    ]
    for method, url, body in mutations:
        resp = viewer.request(method, url, json=body)
        assert resp.status_code == 403, (method, url, resp.text)
        assert resp.json()["error"]["code"] == "forbidden"

    assert client.get(f"{agents_url(cid)}/{agent['id']}").json()["status"] == "proposed"


def test_admin_can_mutate_but_company_creator_stays_owner(
    client, client_as, make_user, add_member, company
):
    cid = company["id"]
    admin_user = make_user("admin@example.com")
    add_member(cid, admin_user.id, "admin")
    admin = client_as(human(admin_user))
    assert admin.patch(f"/api/companies/{cid}", json={"vision": "v"}).status_code == 200
    assert admin.post(f"/api/companies/{cid}/departments", json={"name": "Eng"}).status_code == 201
    ev = admin.get(f"/api/companies/{cid}/activity").json()["items"][0]
    assert ev["actor_label"] == "Admin"
    assert ev["actor_type"] == "human"
