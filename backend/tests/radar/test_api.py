"""The owner API of the radar (``/api/companies/{cid}/radar``): shapes the web builds on, filters,
pagination, who may write, and that agents and strangers are kept out."""

from __future__ import annotations

from sqlalchemy import select

from app.models.company import CompanyMember
from app.models.radar import RadarItem, RadarSource
from tests.conftest import human
from tests.helpers import api
from tests.radar.radar_helpers import clear_radar
from tests.radar.wp2_helpers import (
    INJECTION,
    add_item,
    new_idea,
    radar_world,
    researcher_session,
)


def viewer_client(app, make_user, client_as, cid: str, role: str = "viewer"):
    user = make_user(f"{role}-{cid[:6]}@example.test")
    with app.state.session_factory() as db:
        db.add(CompanyMember(company_id=cid, user_id=user.id, role=role))
        db.commit()
    return client_as(human(user))


def test_status_counts_everything_from_the_database(app, client, company):
    cid = company["id"]  # a fresh company: the 100 seed sources, derived keywords, no items yet
    r = client.get(api(cid, "/radar"))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["available"] is True and s["enabled"] is True and s["self_critique_enabled"] is True
    assert (
        s["sources_total"] == 100
        and s["sources_enabled"] == s["sources_fetched"] + s["sources_search_only"]
    )
    assert s["sources_search_only"] > 0 and s["items"] == {
        "new": 0,
        "seen": 0,
        "used": 0,
        "dismissed": 0,
        "flagged": 0,
    }
    assert {c["category"] for c in s["categories"]} >= {"forum", "blog", "UI inspiration"}
    assert s["keywords_enabled"] > 0 and s["open_ideas"] == 0 and s["last_brief_at"] is None
    assert (
        s["runner_role"] == "cofounder"
        and s["runner_agent_name"]
        and s["search_queries_per_day"] == 4
    )
    assert s["failing_sources"] == []


def test_status_follows_items_ideas_failures_and_the_researcher(app, client, company):
    cid, source_id, items = radar_world(app, client, company)
    with app.state.session_factory() as db:
        add_item(
            db,
            cid,
            db.get(RadarSource, source_id),
            "Trick",
            flags=["instruction override"],
            status="dismissed",
        )
        src = db.get(RadarSource, source_id)
        src.consecutive_failures, src.last_status, src.last_error = 3, "http_5xx", "HTTP 503"
        db.commit()
    new_idea(app, cid)
    researcher_session(app, client, cid)
    s = client.get(api(cid, "/radar")).json()
    assert s["items"] == {"new": 3, "seen": 0, "used": 0, "dismissed": 1, "flagged": 1}
    assert (
        s["open_ideas"] == 1
        and s["runner_role"] == "market_researcher"
        and s["runner_agent_name"] == "Rhea"
    )
    [failing] = s["failing_sources"]
    assert (
        failing["id"] == source_id
        and failing["consecutive_failures"] == 3
        and failing["last_error"] == "HTTP 503"
    )
    assert s["categories"] == [
        {"category": "forum", "sources": 1, "enabled": 1, "failing": 1, "items": 4}
    ]


def test_sources_have_the_shape_the_ui_needs(app, client, company):
    cid = company["id"]
    page = client.get(api(cid, "/radar/sources"), params={"limit": 5}).json()
    assert len(page["items"]) == 5 and page["next_cursor"]
    src = page["items"][0]
    assert src["origin"] == "seed" and src["read_via"] in ("fetch", "web_search")
    assert {
        "slug",
        "name",
        "category",
        "access_method",
        "adapter",
        "interval_seconds",
        "last_status",
        "robots_override",
        "disabled_reason",
        "template_values",
    } <= set(src)
    nxt = client.get(
        api(cid, "/radar/sources"), params={"limit": 5, "cursor": page["next_cursor"]}
    ).json()
    assert not {s["id"] for s in nxt["items"]} & {s["id"] for s in page["items"]}
    searched = client.get(
        api(cid, "/radar/sources"), params={"category": "social", "limit": 100}
    ).json()["items"]
    assert any(s["read_via"] == "web_search" for s in searched)
    one = client.get(api(cid, f"/radar/sources/{src['id']}"))
    assert one.status_code == 200 and one.json()["id"] == src["id"]


def test_the_owner_adds_edits_tests_and_deletes_a_source(app, client, company, web):
    cid = company["id"]
    feed = '<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title><item><title>Hello</title><link>https://blog.example.test/hello</link></item></channel></rss>'
    web.site("blog.example.test")
    web.add("https://blog.example.test/feed.xml", feed)
    created = client.post(
        api(cid, "/radar/sources"),
        json={
            "name": "My blog",
            "url": "https://blog.example.test/",
            "fetch_url": "https://blog.example.test/feed.xml",
            "category": "blog",
        },
    )
    assert created.status_code == 201, created.text
    src = created.json()
    assert src["origin"] == "owner" and src["read_via"] == "fetch"
    patched = client.patch(
        api(cid, f"/radar/sources/{src['id']}"), json={"enabled": False, "interval_seconds": 7200}
    )
    assert (
        patched.status_code == 200
        and patched.json()["enabled"] is False
        and patched.json()["interval_seconds"] == 7200
    )
    assert (
        client.patch(api(cid, f"/radar/sources/{src['id']}"), json={"name": None}).status_code
        == 422
    )
    assert (
        client.patch(api(cid, f"/radar/sources/{src['id']}"), json={"unknown": 1}).status_code
        == 422
    )
    probe = client.post(api(cid, f"/radar/sources/{src['id']}/test")).json()
    assert (
        probe["status"] == "ok"
        and probe["items"][0]["title"] == "Hello"
        and probe["robots"] in ("allowed", "missing")
    )
    with app.state.session_factory() as db:  # a test fetch stores nothing
        assert db.scalar(select(RadarItem.id).where(RadarItem.company_id == cid)) is None
    assert client.delete(api(cid, f"/radar/sources/{src['id']}")).status_code == 204
    assert client.get(api(cid, f"/radar/sources/{src['id']}")).status_code == 404
    seed = client.get(api(cid, "/radar/sources"), params={"origin": "seed", "limit": 1}).json()[
        "items"
    ][0]
    assert (
        client.delete(api(cid, f"/radar/sources/{seed['id']}")).status_code == 409
    )  # disable a seed instead
    assert client.post(api(cid, "/radar/sources"), json={"name": "x"}).status_code == 422


def test_reseed_and_fetch_now(app, client, company):
    cid = company["id"]
    assert client.post(api(cid, "/radar/sources/reseed")).json()["counts"]["inserted"] == 0
    r = client.post(api(cid, "/radar/fetch-now"))
    assert r.status_code == 202 and r.json()["enqueued"] is True and r.json()["job_id"]


def test_keywords_crud_and_derive(app, client, company):
    cid = company["id"]
    first = client.get(api(cid, "/radar/keywords")).json()
    assert first["next_cursor"] is None and all(k["origin"] == "derived" for k in first["items"])
    made = client.post(
        api(cid, "/radar/keywords"), json={"term": "billing", "kind": "product", "weight": 5}
    )
    assert made.status_code == 201 and made.json()["origin"] == "owner"
    assert client.post(api(cid, "/radar/keywords"), json={"term": "Billing"}).status_code == 409
    assert (
        client.post(api(cid, "/radar/keywords"), json={"term": "x", "weight": 9}).status_code == 422
    )
    kid = made.json()["id"]
    assert (
        client.patch(api(cid, f"/radar/keywords/{kid}"), json={"enabled": False}).json()["enabled"]
        is False
    )
    filtered = client.get(api(cid, "/radar/keywords"), params={"origin": "owner"}).json()["items"]
    assert [k["term"] for k in filtered] == ["billing"]
    assert client.delete(api(cid, f"/radar/keywords/{kid}")).status_code == 204
    derived = client.post(api(cid, "/radar/keywords/derive"))
    assert derived.status_code == 200 and derived.json()["next_cursor"] is None


def test_items_filter_paginate_dismiss_and_flag(app, client, company):
    cid, source_id, items = radar_world(app, client, company, with_items=3)
    with app.state.session_factory() as db:
        from tests.radar.radar_helpers import new_source

        news = new_source(db, cid, "news", category="news")
        bad = add_item(
            db, cid, news, "Trick page", excerpt=INJECTION, flags=["instruction override"]
        )
        db.commit()
        bad_id = bad.id
    everything = client.get(api(cid, "/radar/items")).json()["items"]
    assert len(everything) == 4
    row = next(i for i in everything if i["id"] == bad_id)
    assert row["flagged"] is True and row["injection_flags"] == ["instruction override"]
    assert (
        row["source_slug"] == "news" and row["category"] == "news" and row["source_name"] == "News"
    )
    only_flagged = client.get(api(cid, "/radar/items"), params={"flagged": "true"}).json()["items"]
    assert [i["id"] for i in only_flagged] == [bad_id]
    assert (
        len(client.get(api(cid, "/radar/items"), params={"flagged": "false"}).json()["items"]) == 3
    )
    assert (
        len(client.get(api(cid, "/radar/items"), params={"category": "forum"}).json()["items"]) == 3
    )
    assert len(client.get(api(cid, "/radar/items"), params={"source": "hn"}).json()["items"]) == 3
    assert (
        len(client.get(api(cid, "/radar/items"), params={"q": "ONBOARDING"}).json()["items"]) == 3
    )
    p1 = client.get(api(cid, "/radar/items"), params={"limit": 3}).json()
    p2 = client.get(
        api(cid, "/radar/items"), params={"limit": 3, "cursor": p1["next_cursor"]}
    ).json()
    assert len(p1["items"]) == 3 and len(p2["items"]) == 1 and p2["next_cursor"] is None
    assert not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}
    dismissed = client.patch(api(cid, f"/radar/items/{bad_id}"), json={"status": "dismissed"})
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    assert (
        len(client.get(api(cid, "/radar/items"), params={"status": "dismissed"}).json()["items"])
        == 1
    )
    assert (
        client.patch(api(cid, f"/radar/items/{items[0]}"), json={"status": "new"}).status_code
        == 400
    )
    assert (
        client.patch(api(cid, f"/radar/items/{bad_id}"), json={"status": "new"}).json()["status"]
        == "new"
    )
    assert (
        client.patch(api(cid, f"/radar/items/{bad_id}"), json={"status": "used"}).status_code == 422
    )


def test_ideas_filters_and_owner_created_ideas(app, client, company):
    cid, _, items = radar_world(app, client, company)
    new_idea(app, cid, "Make onboarding faster")
    new_idea(app, cid, "Add dark mode to the dashboard", status="rejected")
    assert len(client.get(api(cid, "/radar/ideas")).json()["items"]) == 2
    assert [
        i["status"]
        for i in client.get(api(cid, "/radar/ideas"), params={"status": "rejected"}).json()["items"]
    ] == ["rejected"]
    mine = client.post(
        api(cid, "/radar/ideas"),
        json={"title": "Offer an annual plan", "impact": 5, "effort": 1, "confidence": 4},
    )
    assert (
        mine.status_code == 201
        and mine.json()["origin"] == "owner"
        and mine.json()["priority_score"] == 80
    )
    row = client.get(api(cid, "/radar/ideas"), params={"origin": "owner"}).json()["items"][0]
    assert row["age_days"] == 0 and row["task_id"] is None and row["decided_at"] is None


def test_brief_detail_404_for_an_unknown_id(app, client, company):
    cid = company["id"]
    assert client.get(api(cid, "/radar/briefs/nope")).status_code == 404
    assert client.get(api(cid, "/radar/briefs")).json() == {"items": [], "next_cursor": None}


def test_viewers_read_but_never_write(app, client, company, make_user, client_as):
    cid, source_id, items = radar_world(app, client, company)
    idea = new_idea(app, cid)
    v = viewer_client(app, make_user, client_as, cid)
    for path in ("", "/sources", "/keywords", "/items", "/briefs", "/ideas"):
        assert v.get(api(cid, f"/radar{path}")).status_code == 200, path
    writes = [
        (
            "post",
            "/radar/sources",
            {"name": "x", "url": "https://a.example", "fetch_url": "https://a.example/f"},
        ),
        ("patch", f"/radar/sources/{source_id}", {"enabled": False}),
        ("delete", f"/radar/sources/{source_id}", None),
        ("post", f"/radar/sources/{source_id}/test", None),
        ("post", "/radar/sources/reseed", None),
        ("post", "/radar/fetch-now", None),
        ("post", "/radar/keywords", {"term": "billing"}),
        ("post", "/radar/keywords/derive", None),
        ("patch", f"/radar/items/{items[0]}", {"status": "dismissed"}),
        ("post", "/radar/ideas", {"title": "x"}),
        ("patch", f"/radar/ideas/{idea}", {"action": "reject"}),
    ]
    for method, path, body in writes:
        kwargs = {"json": body} if body is not None else {}
        r = getattr(v, method)(api(cid, path), **kwargs)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    m = viewer_client(app, make_user, client_as, cid, "member")
    assert m.patch(api(cid, f"/radar/ideas/{idea}"), json={"action": "reject"}).status_code == 403


def test_agents_get_403_on_the_owner_routes_and_cannot_touch_radar_policy(app, client, company):
    cid, source_id, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    token = {"Authorization": f"Bearer {sess.token}"}
    for path in ("", "/sources", "/items", "/ideas", "/briefs", "/keywords"):
        r = client.get(api(cid, f"/radar{path}"), headers=token)
        assert r.status_code == 403, path
    assert (
        client.patch(
            api(cid, f"/radar/sources/{source_id}"), json={"enabled": False}, headers=token
        ).status_code
        == 403
    )
    for field, value in (
        ("radar_enabled", False),
        ("radar_max_ideas_per_day", 99),
        ("radar_auto_accept", False),
        ("radar_agent_sources", False),
    ):
        r = client.patch(api(cid, "/policy"), json={field: value}, headers=token)
        assert r.status_code == 403, field
    policy = client.get(api(cid, "/policy")).json()
    assert policy["radar_enabled"] is True and policy["radar_max_ideas_per_day"] == 5


def test_strangers_and_other_companies_get_404_and_anonymous_gets_401(
    app, client, company, anon_client, make_user, client_as
):
    cid, source_id, items = radar_world(app, client, company)
    idea = new_idea(app, cid)
    assert anon_client.get(api(cid, "/radar")).status_code == 401
    stranger = client_as(human(make_user("nosy@example.test")))
    for path in (
        "",
        "/sources",
        "/items",
        "/ideas",
        "/briefs",
        "/keywords",
        f"/sources/{source_id}",
        f"/ideas/{idea}",
    ):
        assert stranger.get(api(cid, f"/radar{path}")).status_code == 404, path
    assert stranger.post(api(cid, "/radar/fetch-now")).status_code == 404
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "m"}).json()
    ocid = other["id"]
    with app.state.session_factory() as db:
        clear_radar(db, ocid)
    assert client.get(api(ocid, f"/radar/sources/{source_id}")).status_code == 404
    assert client.get(api(ocid, "/radar/items")).json()["items"] == []
    assert (
        client.patch(
            api(ocid, f"/radar/items/{items[0]}"), json={"status": "dismissed"}
        ).status_code
        == 404
    )
    assert (
        client.patch(api(ocid, f"/radar/keywords/{items[0]}"), json={"enabled": False}).status_code
        == 404
    )
    assert client.get(api(ocid, "/radar/briefs/whatever")).status_code == 404


def test_a_radar_run_token_of_another_company_is_a_404(app, client, company):
    cid, _, _ = radar_world(app, client, company)
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "m"}).json()
    _, sess = researcher_session(app, client, other["id"])
    r = client.get(api(cid, "/radar"), headers={"Authorization": f"Bearer {sess.token}"})
    assert r.status_code == 404
