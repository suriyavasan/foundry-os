"""Tenancy of the radar agent API: an agent of company B sees nothing of company A, whatever ids it
sends; ids of another company are 404 (or a validation error naming nothing about A)."""

from __future__ import annotations

from tests.helpers import api, new_agent
from tests.radar.wp2_helpers import (
    founder_session,
    new_idea,
    radar_world,
    researcher_session,
    start_run,
)

BASE = "/radar"


def two_companies(app, client):
    a = client.post("/api/companies", json={"name": "Company A", "mission": "a"}).json()
    b = client.post("/api/companies", json={"name": "Company B", "mission": "b"}).json()
    return a, b


def test_the_digest_and_open_ideas_are_per_company(app, client):
    a, b = two_companies(app, client)
    cid_a, _, items_a = radar_world(app, client, a)
    cid_b, _, items_b = radar_world(app, client, b, with_items=1)
    new_idea(app, cid_a, "Idea of company A")
    _, sess_b = researcher_session(app, client, cid_b)
    body = sess_b.get(BASE + "/digest").json()
    assert {i["id"] for i in body["items"]} == set(items_b)
    assert body["open_ideas"] == [] and body["counts"]["open_ideas"] == 0
    assert "Idea of company A" not in str(body) and not set(items_a) & {
        i["id"] for i in body["items"]
    }


def test_foreign_ids_never_work(app, client):
    a, b = two_companies(app, client)
    cid_a, source_a, items_a = radar_world(app, client, a)
    cid_b, _, _ = radar_world(app, client, b)
    idea_a = new_idea(app, cid_a)
    _, sess_b = researcher_session(app, client, cid_b)
    # evidence and brief citations
    r = sess_b.post(
        BASE + "/ideas",
        json={"title": "Steal it", "problem": "p", "proposal": "q", "item_ids": items_a[:1]},
    )
    assert r.status_code == 422
    assert (
        sess_b.post(
            BASE + "/briefs",
            json={
                "summary": "s",
                "themes": [{"title": "t", "kind": "risk", "item_ids": items_a[:1]}],
            },
        ).status_code
        == 422
    )
    # reading a page "for" a foreign item
    assert (
        sess_b.post(
            BASE + "/read", json={"url": "https://x.example.test/", "item_id": items_a[0]}
        ).status_code
        == 422
    )
    # deciding a foreign idea (the Co-founder of B)
    founder_b = founder_session(app, client, cid_b)
    assert (
        founder_b.post(BASE + f"/ideas/{idea_a}/decide", json={"action": "reject"}).status_code
        == 404
    )
    # a foreign keyword
    kw = client.post(api(cid_a, "/radar/keywords"), json={"term": "secret"}).json()
    assert sess_b.patch(BASE + f"/keywords/{kw['id']}", json={"enabled": False}).status_code == 404
    # a project of another company is not a place to accept into
    other_project = client.post(api(cid_a, "/projects"), json={"name": "A's project"}).json()
    idea_b = new_idea(app, cid_b)
    refused = founder_b.post(
        BASE + f"/ideas/{idea_b}/decide",
        json={"action": "accept", "project_id": other_project["id"]},
    )
    assert refused.status_code == 404


def test_merging_into_another_companys_idea_is_a_404(app, client):
    a, b = two_companies(app, client)
    cid_a, _, _ = radar_world(app, client, a)
    cid_b, _, _ = radar_world(app, client, b)
    idea_a, idea_b = new_idea(app, cid_a), new_idea(app, cid_b)
    r = client.patch(
        api(cid_b, f"/radar/ideas/{idea_b}"), json={"action": "merge", "merge_into_id": idea_a}
    )
    assert r.status_code == 404


def test_run_tokens_are_bound_to_their_company(app, client):
    a, b = two_companies(app, client)
    cid_a, _, _ = radar_world(app, client, a)
    cid_b, _, _ = radar_world(app, client, b)
    plain = new_agent(client, cid_b, name="Pat")
    sess = start_run(app, client, cid_b, plain["id"])
    assert client.get(api(cid_a, "/radar/items"), headers=sess.headers).status_code == 404
    assert client.get(api(cid_a, "/radar/ideas"), headers=sess.headers).status_code == 404
