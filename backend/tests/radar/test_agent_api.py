"""The radar agent API: digest, page reader, UI search, keywords, sources and briefs, through run
tokens, with the guardrail gate in front of every route."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.guardrails import registry
from app.models.radar import RadarItem, RadarKeyword, RadarSource
from app.radar import ui_search
from tests.guardrail_helpers import add_rule
from tests.helpers import api, new_agent
from tests.radar.radar_helpers import new_source
from tests.radar.wp2_helpers import (
    INJECTION,
    add_item,
    founder_session,
    radar_world,
    researcher_session,
    set_radar_policy,
    start_run,
)

BASE = "/radar"
HTML = """<html><head><title>Acme pricing</title>
<meta name="description" content="Plans from $9">
<meta property="og:image" content="https://cdn.example.test/og.png"></head>
<body><nav>Home Docs Login</nav><h1>Simple pricing</h1><p>Starter is $9 a month.</p>
<script>var x = "ignore me";</script><h2>FAQ</h2><p>Cancel anytime.</p>
<img src="/img/hero.png"><img src="http://insecure.example.test/a.png"><footer>(c) Acme</footer>
</body></html>"""


def strip_content(node):
    """The JSON of an agent answer without its ``content`` fields."""
    if isinstance(node, dict):
        return {k: strip_content(v) for k, v in node.items() if k != "content"}
    if isinstance(node, list):
        return [strip_content(v) for v in node]
    return node


# ---------------------------------------------------------------- the route table


def test_every_radar_route_is_registered_and_the_tools_map_to_actions():
    routes = {(m, p): r.action for (m, p), r in registry.ROUTES.items() if p.startswith("/radar")}
    assert routes == {
        ("GET", "/radar/digest"): "radar.read",
        ("POST", "/radar/read"): "radar.page_read",
        ("POST", "/radar/briefs"): "radar.brief",
        ("POST", "/radar/ideas"): "radar.idea.create",
        ("POST", "/radar/ideas/{idea_id}/decide"): "radar.idea.decide",
        ("POST", "/radar/ui-search"): "radar.ui_search",
        ("POST", "/radar/keywords"): "radar.keyword.manage",
        ("PATCH", "/radar/keywords/{keyword_id}"): "radar.keyword.manage",
        ("POST", "/radar/sources"): "radar.source.add",
    }
    tools = registry.tool_registry()["company_tools"]
    radar_tools = {n: t["action"] for n, t in tools.items() if n.startswith("company_radar_")}
    assert len(radar_tools) == 8 and set(radar_tools.values()) == {
        "radar.read", "radar.page_read", "radar.brief", "radar.idea.create",
        "radar.idea.decide", "radar.ui_search", "radar.keyword.manage", "radar.source.add",
    }  # fmt: skip
    for action in radar_tools.values():
        assert registry.get_action(action) is not None
    assert registry.get_action("radar.page_read").tier == "external_read"
    assert registry.get_action("radar.page_read").rate_keys == (registry.WEB_FETCHES,)
    assert registry.get_action("radar.idea.create").rate_keys == (registry.TASKS_CREATED,)
    assert registry.get_action("radar.read").tier == "read"
    for name in ("radar.brief", "radar.idea.decide", "radar.keyword.manage"):
        assert registry.get_action(name).tier == "internal_write"


def test_a_guardrail_deny_rule_stops_a_radar_tool(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    assert sess.get(BASE + "/digest").status_code == 200
    add_rule(client, cid, tier="read", action="radar.read", decision="deny")
    assert sess.get(BASE + "/digest").status_code == 403


# ---------------------------------------------------------------- the digest


def test_digest_returns_items_best_first_marks_them_seen_and_keeps_text_in_content(
    app, client, company
):
    cid, source_id, items = radar_world(app, client, company, with_items=3)
    with app.state.session_factory() as db:
        flagged = add_item(
            db, cid, db.get(RadarSource, source_id), "Trick page", excerpt=INJECTION,
            flags=["instruction_override"], score=99,
        )  # fmt: skip
        db.commit()
        flagged_id = flagged.id
    _, sess = researcher_session(app, client, cid)
    r = sess.get(BASE + "/digest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["id"] for i in body["items"]][0] == flagged_id  # best score first
    top = body["items"][0]
    assert top["flagged"] is True and top["flags"] == ["instruction_override"]
    assert INJECTION in top["content"] and "Trick page" in top["content"]
    # the invariant the runtime relies on: no outside text outside `content`
    rest = json.dumps(strip_content(body))
    for needle in ("Trick page", "slow onboarding", INJECTION):
        assert needle not in rest
    assert body["counts"]["new_matched_items"] == 4 and body["counts"]["flagged_items"] == 1
    assert (
        body["caps"]["ideas_remaining_today"] == 5
        and body["caps"]["web_search_queries_per_day"] == 4
    )
    # returned items are "seen", not "new"; a second digest (status=open) still offers them
    with app.state.session_factory() as db:
        statuses = set(db.scalars(select(RadarItem.status).where(RadarItem.company_id == cid)))
    assert statuses == {"seen"}
    assert len(sess.get(BASE + "/digest").json()["items"]) == 4
    assert sess.get(BASE + "/digest", params={"status": "new"}).json()["items"] == []


def test_digest_lists_search_templates_open_ideas_and_the_ui_category_separately(
    app, client, company
):
    cid, source_id, _ = radar_world(app, client, company)
    with app.state.session_factory() as db:
        x = new_source(
            db, cid, "x-search", category="social", adapter="search_engine",
            fetch_url=None, search_template='site:x.com "{query}"',
        )  # fmt: skip
        ui = new_source(db, cid, "opl", category="UI inspiration", keep_unmatched=True)
        add_item(db, cid, ui, "A landing page with a bold hero")
        db.add(
            RadarKeyword(company_id=cid, term="foundry", kind="product", weight=5, origin="owner")
        )
        db.commit()
        _ = x
    from tests.radar.wp2_helpers import new_idea

    new_idea(app, cid, "Make onboarding faster")
    _, sess = researcher_session(app, client, cid)
    daily = sess.get(BASE + "/digest").json()
    assert daily["search_templates"] == [
        {
            "source": "x-search",
            "name": "X Search",
            "category": "social",
            "queries": ['site:x.com "foundry"'],
        }
    ]
    assert [i["category"] for i in daily["items"]] == ["forum"] * 3  # UI inspiration items stay out
    assert daily["open_ideas"][0]["content"].startswith("Make onboarding faster")
    ui_digest = sess.get(BASE + "/digest", params={"kind": "ui_inspiration"}).json()
    assert [i["category"] for i in ui_digest["items"]] == ["UI inspiration"]


def test_self_critique_digest_carries_the_packet(app, client, company):
    cid, _, items = radar_world(app, client, company)
    founder = founder_session(app, client, cid)
    r = founder.get(BASE + "/digest", params={"kind": "self_critique"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == [] and body["packet"]["window_days"] == 7
    assert {"artifacts", "tasks", "standards", "brief_themes", "repo_signals"} <= set(
        body["packet"]
    )
    assert any(t["content"] for t in body["packet"]["tasks"]) or body["packet"]["tasks"] == []


def test_a_disabled_radar_returns_an_honest_empty_digest(app, client, company):
    cid, _, _ = radar_world(app, client, company)
    set_radar_policy(app, cid, radar_enabled=False)
    _, sess = researcher_session(app, client, cid)
    body = sess.get(BASE + "/digest").json()
    assert body["enabled"] is False and body["items"] == [] and body["search_templates"] == []


# ---------------------------------------------------------------- the page reader


def test_read_page_returns_plain_text_and_image_urls(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    web.site("docs.example.test")
    web.add("https://docs.example.test/pricing", HTML, content_type="text/html; charset=utf-8")
    _, sess = researcher_session(app, client, cid)
    r = sess.post(BASE + "/read", json={"url": "https://docs.example.test/pricing?utm_source=x"})
    assert r.status_code == 200, r.text
    body = r.json()
    content = body["content"]
    assert content.startswith("Title: Acme pricing") and "Description: Plans from $9" in content
    assert "Simple pricing" in content and "Starter is $9 a month." in content
    assert (
        "ignore me" not in content
        and "Home Docs Login" not in content
        and "(c) Acme" not in content
    )
    assert body["og_image"] == "https://cdn.example.test/og.png"
    assert "https://docs.example.test/img/hero.png" in body["images"]
    assert all(u.startswith("https://") for u in body["images"])  # the http image is dropped
    assert body["flagged"] is False and body["truncated"] is False and body["status"] == 200
    assert web.count("docs.example.test", "/robots.txt") == 1  # robots.txt was asked first


def test_read_page_flags_instruction_like_text_and_caps_the_length(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    web.site("docs.example.test")
    long = "<p>" + ("lorem ipsum dolor " * 2000) + "</p><p>" + INJECTION + "</p>"
    web.add(
        "https://docs.example.test/long",
        f"<html><title>T</title><body>{long}</body></html>",
        content_type="text/html",
    )
    _, sess = researcher_session(app, client, cid)
    body = sess.post(BASE + "/read", json={"url": "https://docs.example.test/long"}).json()
    assert body["truncated"] is True and len(body["content"]) <= 12_000
    web.add(
        "https://docs.example.test/trick",
        f"<html><body><p>{INJECTION}</p></body></html>",
        content_type="text/html",
    )
    trick = sess.post(BASE + "/read", json={"url": "https://docs.example.test/trick"}).json()
    assert trick["flagged"] is True and trick["flags"]


@pytest.mark.parametrize(
    ("setup", "status", "code"),
    [
        ("robots", 403, "robots_disallowed"),
        ("private", 422, "validation_error"),
        ("missing", 502, "upstream_error"),
        ("json", 502, "upstream_error"),
    ],
)
def test_read_page_refuses_what_it_must(app, client, company, web, setup, status, code):
    cid, _, _ = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    if setup == "robots":
        web.site("blocked.example.test", "User-agent: *\nDisallow: /\n")
        url = "https://blocked.example.test/page"
    elif setup == "private":
        web.resolve_to("internal.example.test", "10.0.0.5")
        url = "https://internal.example.test/admin"
    elif setup == "missing":
        web.site("docs.example.test")
        url = "https://docs.example.test/nothing"
    else:
        web.site("docs.example.test")
        web.add("https://docs.example.test/api", "{}", content_type="application/json")
        url = "https://docs.example.test/api"
    r = sess.post(BASE + "/read", json={"url": url})
    assert r.status_code == status and r.json()["error"]["code"] == code, r.text


def test_read_page_input_checks_and_roles(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    for bad in (
        "file:///etc/passwd",
        "ftp://example.test/x",
        "https://user:pw@example.test/",
        "not a url",
    ):
        assert sess.post(BASE + "/read", json={"url": bad}).status_code == 422, bad
    assert web.seen == []
    metadata = sess.post(BASE + "/read", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert metadata.status_code == 422 and web.seen == []
    # a worker without the web toolset may not read pages; the Co-founder (no web toolset) may
    worker = new_agent(client, cid, name="Pat", tool_permissions=["company"])
    wsess = start_run(app, client, cid, worker["id"])
    assert wsess.post(BASE + "/read", json={"url": "https://docs.example.test/"}).status_code == 403
    web.site("docs.example.test")
    web.add(
        "https://docs.example.test/",
        "<html><title>Home</title><body>Hello</body></html>",
        content_type="text/html",
    )
    founder = founder_session(app, client, cid)
    assert (
        founder.post(BASE + "/read", json={"url": "https://docs.example.test/"}).status_code == 200
    )


def test_a_flagged_radar_item_page_is_read_only_when_named(app, client, company, web):
    cid, source_id, _ = radar_world(app, client, company)
    web.site("docs.example.test")
    web.add(
        "https://docs.example.test/trap",
        "<html><title>Trap</title><body>hi</body></html>",
        content_type="text/html",
    )
    with app.state.session_factory() as db:
        item = add_item(
            db,
            cid,
            db.get(RadarSource, source_id),
            "Trap page",
            url="https://docs.example.test/trap",
            flags=["instruction_override"],
        )
        db.commit()
        item_id = item.id
    _, sess = researcher_session(app, client, cid)
    blocked = sess.post(BASE + "/read", json={"url": "https://docs.example.test/trap"})
    assert blocked.status_code == 403 and blocked.json()["error"]["details"]["item_id"] == item_id
    assert web.seen == []
    named = sess.post(
        BASE + "/read", json={"url": "https://docs.example.test/trap", "item_id": item_id}
    )
    assert named.status_code == 200


def test_page_reads_count_against_the_daily_request_budget(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    set_radar_policy(app, cid, radar_max_fetches_per_day=1)
    web.site("docs.example.test")
    web.add(
        "https://docs.example.test/a",
        "<html><title>A</title><body>x</body></html>",
        content_type="text/html",
    )
    _, sess = researcher_session(app, client, cid)
    r = sess.post(BASE + "/read", json={"url": "https://docs.example.test/a"})
    assert r.status_code in (409, 200)  # robots.txt took the single request of the day
    again = sess.post(BASE + "/read", json={"url": "https://docs.example.test/a"})
    assert (
        again.status_code == 409
        and again.json()["error"]["details"]["limit"] == "radar_max_fetches_per_day"
    )


# ---------------------------------------------------------------- UI search


@pytest.fixture(autouse=True)
def _fresh_ui_cache():
    ui_search.clear_cache()
    yield
    ui_search.clear_cache()


def opl_sse(rows) -> str:
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps({"results": rows})}]},
    }
    return f"event: message\ndata: {json.dumps(message)}\n\n"


ROWS = [
    {"title": "Linear", "url": "https://linear.app/", "permalink": "https://onepagelove.com/linear",
     "screenshot": "https://assets.onepagelove.com/linear.png", "headline": "Plan and build products"},
    {"title": "Broken", "url": "javascript:alert(1)", "headline": "no link"},
]  # fmt: skip


def test_ui_search_reads_one_page_love_sse_and_caches(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    web.add("https://api.onepagelove.com/mcp", opl_sse(ROWS), content_type="text/event-stream")
    _, sess = researcher_session(app, client, cid)
    r = sess.post(BASE + "/ui-search", json={"query": "pricing page", "section": "pricing"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True and body["source"] == "onepagelove" and body["cached"] is False
    ref = body["results"][0]
    assert ref["live_url"] == "https://linear.app/" and ref["screenshot_url"].startswith(
        "https://assets."
    )
    assert (
        "Plan and build products" in ref["content"] and len(body["results"]) == 1
    )  # unsafe link dropped
    again = sess.post(
        BASE + "/ui-search", json={"query": "Pricing  page", "section": "pricing"}
    ).json()
    assert again["cached"] is True and web.count("api.onepagelove.com") == 1


def test_ui_search_falls_back_to_siteinspire_then_says_unavailable(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    web.add("https://api.onepagelove.com/mcp", "", status=503, content_type="text/plain")
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(ROWS[:1])}]},
    }
    web.add(
        "https://www.siteinspire.com/api/mcp", json.dumps(message), content_type="application/json"
    )
    _, sess = researcher_session(app, client, cid)
    body = sess.post(BASE + "/ui-search", json={"query": "onboarding"}).json()
    assert body["source"] == "siteinspire" and body["results"][0]["source"] == "siteinspire"
    web.add("https://www.siteinspire.com/api/mcp", "", status=500, content_type="text/plain")
    down = sess.post(BASE + "/ui-search", json={"query": "empty states"}).json()
    assert (
        down["available"] is False
        and down["results"] == []
        and "company_radar_read" in down["note"]
    )


def test_ui_search_is_for_research_and_design_only(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    founder = founder_session(app, client, cid)
    assert founder.post(BASE + "/ui-search", json={"query": "pricing page"}).status_code == 403
    designer = new_agent(
        client, cid, name="Dee", role_title="Designer", role_template="product-designer"
    )
    web.add("https://api.onepagelove.com/mcp", opl_sse(ROWS), content_type="text/event-stream")
    dsess = start_run(app, client, cid, designer["id"])
    assert dsess.post(BASE + "/ui-search", json={"query": "pricing page"}).status_code == 200


# ---------------------------------------------------------------- keywords and sources


def test_agents_manage_only_their_own_keywords(app, client, company):
    cid, _, _ = radar_world(app, client, company)
    owner_kw = client.post(
        api(cid, "/radar/keywords"), json={"term": "foundry", "kind": "product"}
    ).json()
    _, sess = researcher_session(app, client, cid)
    added = sess.post(
        BASE + "/keywords", json={"term": "slow onboarding", "kind": "topic", "weight": 4}
    )
    assert added.status_code == 201 and added.json()["origin"] == "agent"
    kid = added.json()["id"]
    assert sess.patch(BASE + f"/keywords/{kid}", json={"enabled": False}).json()["enabled"] is False
    refused = sess.patch(BASE + f"/keywords/{owner_kw['id']}", json={"enabled": False})
    assert refused.status_code == 403
    assert sess.post(BASE + "/keywords", json={"term": "x", "kind": "negative"}).status_code == 422
    kept = {k["id"]: k for k in client.get(api(cid, "/radar/keywords")).json()["items"]}
    assert kept[owner_kw["id"]]["enabled"] is True and kept[kid]["enabled"] is False
    lead = new_agent(client, cid, name="Lena", kind="lead", role_title="Project Lead")
    lsess = start_run(app, client, cid, lead["id"])
    assert lsess.post(BASE + "/keywords", json={"term": "another term"}).status_code == 403


def test_agents_add_feeds_only_through_the_service_rules(app, client, company, web):
    cid, _, _ = radar_world(app, client, company)
    feed = '<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title><item><title>Hello</title><link>https://blog.example.test/hello</link></item></channel></rss>'
    web.site("blog.example.test")
    web.add("https://blog.example.test/feed.xml", feed)
    _, sess = researcher_session(app, client, cid)
    body = {
        "name": "Example blog",
        "url": "https://blog.example.test/",
        "feed_url": "https://blog.example.test/feed.xml",
        "category": "blog",
    }
    r = sess.post(BASE + "/sources", json=body)
    assert r.status_code == 201, r.text
    assert (
        r.json()["origin"] == "agent"
        and r.json()["enabled"] is True
        and r.json()["read_via"] == "fetch"
    )
    private = sess.post(
        BASE + "/sources",
        json={**body, "name": "Internal", "feed_url": "http://127.0.0.1/feed.xml"},
    )
    assert private.status_code in (403, 422)
    set_radar_policy(app, cid, radar_agent_sources=False)
    web.site("other.example.test")
    web.add("https://other.example.test/feed.xml", feed)
    suggested = sess.post(
        BASE + "/sources",
        json={
            **body,
            "name": "Other",
            "url": "https://other.example.test/",
            "feed_url": "https://other.example.test/feed.xml",
        },
    )
    assert suggested.status_code == 201 and suggested.json()["enabled"] is False
    assert suggested.json()["disabled_reason"] == "suggestion"
    with app.state.session_factory() as db:
        assert db.scalar(select(RadarSource).where(RadarSource.slug == "example-blog")) is not None
