"""Owner and agent operations on sources and keywords, the radar policy fields, and tenancy."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.errors import Conflict, Forbidden, NotFound, RateLimited, ValidationFailed
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.models.audit import AuditEvent
from app.models.radar import RadarItem, RadarKeyword, RadarSource
from app.models.run import Job
from app.radar import seed as seed_mod
from app.radar import service
from app.radar.service import KeywordInput, SourceInput
from tests.radar.radar_helpers import clear_radar, new_keyword, new_source, policy_of

FIX = Path(__file__).parent.parent / "fixtures" / "radar"
RSS = (FIX / "rss2.xml").read_bytes()


@pytest.fixture
def cid(company, app):
    with app.state.session_factory() as session:
        clear_radar(session, company["id"])
    return company["id"]


@pytest.fixture
def db(app):
    with app.state.session_factory() as session:
        yield session


def ctx_for(cid: str, role: Role, kind: PrincipalType = PrincipalType.HUMAN, who: str = "u-1"):
    return CompanyContext(
        cid, Principal(kind, who, "Tester", cid if kind == "agent" else None), role
    )


@pytest.fixture
def owner(cid):
    return ctx_for(cid, Role.OWNER)


@pytest.fixture
def viewer(cid):
    return ctx_for(cid, Role.VIEWER, who="u-2")


@pytest.fixture
def member(cid):
    return ctx_for(cid, Role.MEMBER, who="u-3")


@pytest.fixture
def agent(cid):
    return ctx_for(cid, Role.AGENT, PrincipalType.AGENT, "agent-1")


@pytest.fixture(autouse=True)
def _fake_dns(web):
    """Saving a URL resolves its host (the SSRF guard): names resolve through the fake resolver."""


def audit_actions(db, cid):
    db.flush()
    return [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid))]


# ------------------------------------------------------------------ sources: reading and tenancy


def test_status_counts_sources_items_and_failures(db, cid, owner):
    new_source(db, cid, "a", category="news")
    new_source(db, cid, "b", category="news", enabled=False)
    new_source(db, cid, "c", adapter="search_engine", fetch_url=None)
    failing = new_source(db, cid, "d", category="blog")
    failing.consecutive_failures = 3
    failing.last_status = "http_5xx"
    db.commit()
    status = service.get_status(db, owner)
    assert status["enabled"] is True
    assert status["sources"]["total"] == 4 and status["sources"]["search_only"] == 1
    assert status["sources"]["fetched"] == 2
    assert status["sources"]["by_category"]["news"] == {"enabled": 1, "disabled": 1}
    assert status["failing_sources"][0]["slug"] == "d" and status["max_requests_per_day"] == 2000
    assert status["items"] == {"new": 0, "seen": 0, "used": 0, "dismissed": 0}


def test_other_companies_sources_and_keywords_are_invisible(db, cid, client, owner):
    other_id = client.post("/api/companies", json={"name": "Other", "mission": "x"}).json()["id"]
    theirs = new_source(db, other_id, "theirs")
    their_kw = new_keyword(db, other_id, "secret plan")
    mine = new_source(db, cid, "mine")
    db.commit()
    rows, cursor = service.list_sources(db, owner)
    assert [r.id for r in rows] == [mine.id] and cursor is None
    for call in (
        lambda: service.get_source(db, owner, theirs.id),
        lambda: service.update_source(db, owner, theirs.id, {"enabled": False}),
        lambda: service.delete_source(db, owner, theirs.id),
        lambda: service.probe_source(db, owner, theirs.id),
        lambda: service.update_keyword(db, owner, their_kw.id, {"weight": 1}),
        lambda: service.delete_keyword(db, owner, their_kw.id),
    ):
        with pytest.raises(NotFound):
            call()
    assert service.list_keywords(db, owner) == []


def test_sources_are_paginated_and_filtered(db, cid, owner):
    for n in range(5):
        new_source(db, cid, f"s{n}", category="news" if n % 2 else "blog", enabled=n != 4)
    db.commit()
    first, cursor = service.list_sources(db, owner, limit=2)
    second, cursor2 = service.list_sources(db, owner, limit=2, cursor=cursor)
    assert len(first) == 2 and len(second) == 2 and cursor2 is not None
    assert not {r.id for r in first} & {r.id for r in second}
    news, _ = service.list_sources(db, owner, category="news")
    assert {r.slug for r in news} == {"s1", "s3"}
    off, _ = service.list_sources(db, owner, enabled=False)
    assert [r.slug for r in off] == ["s4"]


# ------------------------------------------------------------------ sources: owner writes


def test_only_owners_and_admins_change_sources(db, cid, viewer, member, agent):
    src = new_source(db, cid, "x")
    db.commit()
    for who in (viewer, member, agent):
        with pytest.raises(Forbidden):
            service.update_source(db, who, src.id, {"enabled": False})
        with pytest.raises(Forbidden):
            service.delete_source(db, who, src.id)
        with pytest.raises(Forbidden):
            service.probe_source(db, who, src.id)
        with pytest.raises(Forbidden):
            service.reseed(db, who)
        with pytest.raises(Forbidden):
            service.enqueue_fetch_now(db, who)
    for who in (viewer, member):
        with pytest.raises(Forbidden):
            service.create_source(db, who, SourceInput("N", "https://a.test/", "https://a.test/f"))
    assert service.get_source(db, viewer, src.id).id == src.id  # reading is allowed


def test_an_owner_adds_a_source_with_unique_slugs_and_an_audit_event(db, cid, owner):
    data = SourceInput(
        "My Blog", "https://blog.test/", "https://blog.test/feed.xml", category="blog"
    )
    first = service.create_source(db, owner, data)
    second = service.create_source(db, owner, data)
    db.commit()
    assert (first.slug, second.slug) == ("my-blog", "my-blog-2")
    assert first.origin == "owner" and first.enabled and not first.robots_override
    assert first.created_by_type == "human" and first.interval_seconds == 21_600
    assert audit_actions(db, cid).count("radar.source.created") == 2


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"category": "podcasts"}, "category"),
        ({"adapter": "carrier-pigeon"}, "adapter"),
        ({"adapter": "search_engine"}, "adapter"),
        ({"interval_seconds": 60}, "interval_seconds"),
        ({"interval_seconds": 10**9}, "interval_seconds"),
        ({"fetch_url": "javascript:alert(1)"}, "fetch_url"),
        ({"fetch_url": "ftp://x.test/feed"}, "fetch_url"),
        ({"fetch_url": "http://10.0.0.5/feed"}, "fetch_url"),
        ({"fetch_url": "http://169.254.169.254/latest"}, "fetch_url"),
        ({"fetch_url": "https://user:pw@x.test/feed"}, "fetch_url"),
        ({"fetch_url": "https://{pub}.x.test/feed"}, "template_values"),
        ({"name": "   "}, "name"),
    ],
)
def test_source_input_is_validated(db, owner, override, field):
    base = dict(name="N", url="https://a.test/", fetch_url="https://a.test/f")
    with pytest.raises(ValidationFailed) as err:
        service.create_source(db, owner, SourceInput(**{**base, **override}))
    assert field in str(err.value.details)


def test_template_values_and_keyword_urls_are_handled(db, cid, owner):
    src = service.create_source(
        db,
        owner,
        SourceInput(
            "Pub",
            "https://x.test/",
            "https://{pub}.substack.test/feed",
            template_values={"pub": "acme", "junk": "x"},
        ),
    )
    assert src.template_values == {"pub": "acme"} and src.query_mode == "none"
    kw = service.create_source(
        db, owner, SourceInput("Search", "https://x.test/", "https://x.test/s?q={query}")
    )
    assert kw.query_mode == "per_keyword"


def test_robots_override_is_the_owners_alone_and_audited(db, cid, owner, agent):
    src = service.create_source(
        db, owner, SourceInput("Mine", "https://m.test/", "https://m.test/f", robots_override=True)
    )
    assert src.robots_override is True
    db.flush()
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "radar.source.created")).one()
    assert event.data["robots_override"] is True
    seed_row = new_source(db, cid, "seeded", origin="seed")
    db.commit()
    with pytest.raises(Conflict):
        service.update_source(db, owner, seed_row.id, {"robots_override": True})
    service.update_source(db, owner, src.id, {"robots_override": False})
    service.update_source(db, owner, src.id, {"robots_override": True})
    db.flush()
    assert "robots_override" in str(
        [
            e.data
            for e in db.scalars(
                select(AuditEvent).where(AuditEvent.action == "radar.source.updated")
            )
        ]
    )
    with pytest.raises(Forbidden):
        service.create_source(
            db, agent, SourceInput("A", "https://a.test/", "https://a.test/f", robots_override=True)
        )


def test_updating_a_seed_marks_it_edited_but_toggling_it_does_not(db, cid, owner):
    src = new_source(db, cid, "seeded", origin="seed", enabled=False)
    src.disabled_reason = "terms"
    src.consecutive_failures = 4
    src.last_error = "boom"
    db.commit()
    service.update_source(db, owner, src.id, {"enabled": True})
    db.refresh(src)
    assert src.enabled and src.edited_by_owner is False
    assert (src.disabled_reason, src.consecutive_failures, src.last_error, src.next_fetch_at) == (
        None,
        0,
        None,
        None,
    )
    service.update_source(db, owner, src.id, {"enabled": False})
    db.refresh(src)
    assert src.disabled_reason == "owner"
    service.update_source(db, owner, src.id, {"interval_seconds": 7200, "name": "Renamed"})
    db.refresh(src)
    assert src.edited_by_owner is True and src.interval_seconds == 7200 and src.name == "Renamed"
    assert (
        service.update_source(db, owner, src.id, {"interval_seconds": 7200}) is src
    )  # no change: no event
    for bad in (
        {"interval_seconds": 5},
        {"category": "x"},
        {"fetch_url": None},
        {"slug": "x"},
        {"etag": "x"},
    ):
        with pytest.raises(ValidationFailed):
            service.update_source(db, owner, src.id, bad)


def test_a_token_source_cannot_be_switched_on_and_a_template_needs_its_values(db, cid, owner):
    ph = new_source(db, cid, "ph", adapter="producthunt_graphql", enabled=False)
    tpl = new_source(db, cid, "tpl", fetch_url="https://{blog}.x.test/rss", enabled=False)
    db.commit()
    with pytest.raises(Conflict):
        service.update_source(db, owner, ph.id, {"enabled": True})
    with pytest.raises(ValidationFailed):
        service.update_source(db, owner, tpl.id, {"enabled": True})
    service.update_source(db, owner, tpl.id, {"template_values": {"blog": "mine"}, "enabled": True})
    db.refresh(tpl)
    assert tpl.enabled and tpl.template_values == {"blog": "mine"}


def test_delete_removes_owner_sources_only_and_keeps_cited_items(db, cid, owner):
    seed_row = new_source(db, cid, "seeded", origin="seed")
    mine = new_source(db, cid, "mine", origin="owner")
    from app.radar.normalize import title_hash, url_hash

    def item(n, status):
        db.add(
            RadarItem(
                company_id=cid,
                source_id=mine.id,
                url=f"https://x.test/{n}",
                url_hash=url_hash(str(n)),
                title_hash=title_hash(str(n)),
                title=str(n),
                status=status,
            )
        )

    item(1, "new")
    item(2, "used")
    db.commit()
    with pytest.raises(Conflict):
        service.delete_source(db, owner, seed_row.id)
    service.delete_source(db, owner, mine.id)
    db.commit()
    left = db.scalars(select(RadarItem)).all()
    assert [(i.title, i.source_id) for i in left] == [("2", None)]
    assert db.get(RadarSource, mine.id) is None and db.get(RadarSource, seed_row.id)
    assert "radar.source.deleted" in audit_actions(db, cid)


# ------------------------------------------------------------------ sources: agents


def feed_site(web, host="agent.example.test", body=RSS, **kw):
    web.site(host)
    web.add(f"https://{host}/feed.xml", body, **kw)
    return SourceInput(
        "Agent Find", f"https://{host}/", f"https://{host}/feed.xml", adapter="json_api"
    )


def test_an_agent_adds_a_feed_after_a_test_fetch_and_it_is_labelled_agent(web, db, cid, agent):
    data = feed_site(web)
    src = service.create_source(db, agent, data)
    db.commit()
    assert src.origin == "agent" and src.enabled and src.adapter == "feed"  # the adapter is forced
    assert src.created_by_type == "agent" and src.robots_override is False
    assert web.count("agent.example.test", "/feed.xml") == 1  # validated by one real fetch
    assert db.scalar(select(func.count(RadarItem.id))) == 0  # ... which stores nothing


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            dict(body=b"<html><body>hi</body></html>", content_type="text/html"),
            "not a readable feed",
        ),
        (dict(body=b"<rss><channel><title>empty</title></channel></rss>"), "no entries"),
        (dict(body=b"", status=500, content_type="text/html"), "not a readable feed"),
    ],
)
def test_an_agent_cannot_add_something_that_is_not_a_feed(web, db, agent, kwargs, message):
    data = feed_site(web, **kwargs)
    with pytest.raises(ValidationFailed) as err:
        service.create_source(db, agent, data)
    assert message in str(err.value.details)
    assert db.scalar(select(func.count(RadarSource.id))) == 0


def test_an_agent_cannot_point_a_source_at_a_private_or_disallowed_host(web, db, agent):
    web.resolve_to("internal.example.test", "10.9.8.7")
    web.site("internal.example.test")
    with pytest.raises(ValidationFailed):
        service.create_source(
            db,
            agent,
            SourceInput("Internal", "https://i.test/", "https://internal.example.test/feed.xml"),
        )
    web.robots("closed.example.test", "User-agent: *\nDisallow: /\n")
    web.add("https://closed.example.test/feed.xml", RSS)
    with pytest.raises(ValidationFailed) as err:
        service.create_source(
            db,
            agent,
            SourceInput("Closed", "https://c.test/", "https://closed.example.test/feed.xml"),
        )
    assert "robots" in str(err.value.details)
    assert web.count("closed.example.test", "/feed.xml") == 0


def test_agent_sources_are_capped_at_twenty(web, db, cid, agent):
    for n in range(20):
        new_source(db, cid, f"agent-{n}", origin="agent")
    db.commit()
    with pytest.raises(Conflict) as err:
        service.create_source(db, agent, feed_site(web))
    assert "20" in err.value.message


def test_when_agents_may_only_suggest_the_source_is_stored_off(web, db, cid, agent):
    policy_of(db, cid).radar_agent_sources = False
    db.commit()
    src = service.create_source(db, agent, feed_site(web))
    db.flush()
    assert src.enabled is False and src.disabled_reason == "suggestion"
    assert (
        "off until you enable it"
        in db.scalars(select(AuditEvent).where(AuditEvent.action == "radar.source.created"))
        .one()
        .summary
    )


def test_agents_cannot_edit_delete_or_probe_owner_sources(db, cid, agent):
    src = new_source(db, cid, "owners", origin="owner")
    db.commit()
    with pytest.raises(Forbidden):
        service.update_source(db, agent, src.id, {"enabled": False})
    with pytest.raises(Forbidden):
        service.delete_source(db, agent, src.id)
    with pytest.raises(Forbidden):
        service.update_source(db, agent, src.id, {"robots_override": True})


# ------------------------------------------------------------------ test fetch, reseed, fetch now


def test_a_test_fetch_returns_five_items_stores_nothing_and_is_rate_limited(web, db, cid, owner):
    web.site("probe.example.test")
    items = "".join(
        f"<item><title>Item {i}</title><link>https://p.test/{i}?utm_source=x</link><description>&lt;b&gt;hi&lt;/b&gt;</description></item>"
        for i in range(9)
    )
    web.add("https://probe.example.test/feed.xml", f"<rss><channel>{items}</channel></rss>")
    src = new_source(db, cid, "probe", enabled=False)  # works on a source that is off
    policy_of(db, cid).radar_enabled = False  # ... and with the radar off
    db.commit()
    result = service.probe_source(db, owner, src.id)
    assert result.status == "ok" and result.robots == "allowed" and result.http_status == 200
    assert [i["title"] for i in result.items] == [f"Item {i}" for i in range(5)]
    assert result.items[0]["url"] == "https://p.test/0" and result.items[0]["excerpt"] == "hi"
    assert result.requests == 2 and db.scalar(select(func.count(RadarItem.id))) == 0
    db.refresh(src)
    assert (
        src.last_fetched_at is None and src.last_status is None
    )  # a test leaves no trace on the source
    for _ in range(9):
        service.probe_source(db, owner, src.id)
    with pytest.raises(RateLimited):
        service.probe_source(db, owner, src.id)


def test_a_test_fetch_reports_robots_and_search_only_sources(web, db, cid, owner):
    web.robots("shut.example.test", "User-agent: *\nDisallow: /\n")
    shut = new_source(db, cid, "shut")
    search = new_source(db, cid, "x-search", adapter="search_engine", fetch_url=None)
    db.commit()
    result = service.probe_source(db, owner, shut.id)
    assert (
        result.status == "robots_disallowed"
        and result.robots == "disallowed"
        and result.items == []
    )
    info = service.probe_source(db, owner, search.id)
    assert (
        info.status == "config_error"
        and "web search" in info.error
        and web.count("x-search.example.test") == 0
    )


def test_reseed_adds_what_is_missing_and_never_overwrites_an_edited_row(db, cid, owner):
    assert service.reseed(db, owner) == {"inserted": 100, "updated": 0, "skipped": 0}
    db.commit()
    assert service.reseed(db, owner) == {"inserted": 0, "updated": 0, "skipped": 100}
    rows = {r.slug: r for r in db.scalars(select(RadarSource).where(RadarSource.company_id == cid))}
    edited, stale, gone = rows["techmeme"], rows["the-new-stack"], rows["hnrss-org-ask-hn"]
    service.update_source(
        db, owner, edited.id, {"fetch_url": "https://techmeme.example.test/mine.xml"}
    )
    stale.fetch_url = "https://old.invalid/feed"
    stale.seed_version = 0
    stale.enabled = False  # an owner's switch-off survives a refresh
    db.delete(gone)
    db.commit()
    assert service.reseed(db, owner) == {"inserted": 1, "updated": 1, "skipped": 98}
    db.commit()
    db.expire_all()
    assert db.get(RadarSource, edited.id).fetch_url == "https://techmeme.example.test/mine.xml"
    refreshed = db.get(RadarSource, stale.id)
    assert refreshed.fetch_url == "https://thenewstack.io/feed/" and refreshed.enabled is False
    assert db.scalar(select(func.count(RadarSource.id)).where(RadarSource.company_id == cid)) == 100
    assert audit_actions(db, cid).count("radar.sources.reseeded") == 3


def test_fetch_now_queues_one_job_is_limited_and_needs_the_radar_on(db, cid, owner):
    result = service.enqueue_fetch_now(db, owner)
    db.commit()
    assert result["queued"] is True
    job = db.get(Job, result["job_id"])
    assert job.kind == "radar_fetch" and job.payload == {"company_id": cid, "force": True}
    for _ in range(5):
        service.enqueue_fetch_now(db, owner)
    with pytest.raises(RateLimited):
        service.enqueue_fetch_now(db, owner)
    policy_of(db, cid).radar_enabled = False
    db.rollback()
    policy_of(db, cid).radar_enabled = False
    db.commit()
    with pytest.raises(Conflict):
        service.enqueue_fetch_now(db, owner)


# ------------------------------------------------------------------ keywords


def test_owner_keywords_are_validated_unique_and_capped(db, cid, owner, viewer, member):
    kw = service.create_keyword(db, owner, KeywordInput("Agent OS", "product", 5))
    assert kw.origin == "owner" and kw.enabled and kw.weight == 5
    with pytest.raises(Conflict):
        service.create_keyword(db, owner, KeywordInput("agent os"))  # case-insensitive
    for bad in (
        KeywordInput("x"),
        KeywordInput("ok term", weight=0),
        KeywordInput("ok term", weight=6),
        KeywordInput("ok term", kind="rant"),
    ):
        with pytest.raises(ValidationFailed):
            service.create_keyword(db, owner, bad)
    for who in (viewer, member):
        with pytest.raises(Forbidden):
            service.create_keyword(db, who, KeywordInput("blocked"))
    for n in range(39):
        service.create_keyword(db, owner, KeywordInput(f"term {n}"))
    with pytest.raises(Conflict) as err:
        service.create_keyword(db, owner, KeywordInput("one too many"))
    assert "40" in err.value.message
    service.update_keyword(db, owner, kw.id, {"enabled": False})  # frees a slot
    service.create_keyword(db, owner, KeywordInput("now fits"))
    with pytest.raises(Conflict):
        service.update_keyword(db, owner, kw.id, {"enabled": True})
    assert audit_actions(db, cid).count("radar.keyword.created") == 41


def test_an_agent_adds_topic_and_competitor_keywords_up_to_fifteen(db, cid, agent):
    kw = service.create_keyword(db, agent, KeywordInput("agent marketplaces", "topic"))
    assert kw.origin == "agent" and kw.created_by_type == "agent"
    service.create_keyword(db, agent, KeywordInput("Rivalware", "competitor"))
    for kind in ("negative", "product"):
        with pytest.raises(Forbidden):
            service.create_keyword(db, agent, KeywordInput(f"{kind} word", kind))
    for n in range(13):
        service.create_keyword(db, agent, KeywordInput(f"idea {n}"))
    with pytest.raises(Conflict) as err:
        service.create_keyword(db, agent, KeywordInput("sixteenth"))
    assert "15" in err.value.message


def test_agents_never_touch_owner_or_derived_keywords(db, cid, agent, owner):
    mine = new_keyword(db, cid, "owner term", origin="owner")
    derived = new_keyword(db, cid, "derived term", origin="derived")
    theirs = service.create_keyword(db, agent, KeywordInput("agent term"))
    db.commit()
    for row in (mine, derived):
        with pytest.raises(Forbidden):
            service.update_keyword(db, agent, row.id, {"enabled": False})
        with pytest.raises(Forbidden):
            service.delete_keyword(db, agent, row.id)
    service.update_keyword(db, agent, theirs.id, {"weight": 5, "enabled": False})
    with pytest.raises(Forbidden):
        service.update_keyword(db, agent, theirs.id, {"kind": "negative"})
    service.delete_keyword(db, agent, theirs.id)
    service.update_keyword(db, owner, derived.id, {"term": "renamed", "weight": 1})  # the owner may
    db.commit()
    assert (
        db.get(RadarKeyword, derived.id).term == "renamed"
        and db.get(RadarKeyword, theirs.id) is None
    )
    assert db.get(RadarKeyword, mine.id).enabled is True


def test_deriving_keywords_is_the_owners_and_deterministic(db, cid, owner, viewer, agent):
    for who in (viewer, agent):
        with pytest.raises(Forbidden):
            service.derive_keywords(db, who)
    first = service.derive_keywords(db, owner)
    db.commit()
    assert first and all(k.origin == "derived" for k in first)
    assert any(k.term == "Acme Labs" for k in first)
    assert service.derive_keywords(db, owner) == []
    assert "radar.keywords.derived" in audit_actions(db, cid)


def test_keyword_filters(db, cid, owner):
    new_keyword(db, cid, "alpha one", kind="product", origin="owner")
    new_keyword(db, cid, "beta two", kind="topic", origin="agent", enabled=False)
    db.commit()
    assert [k.term for k in service.list_keywords(db, owner, kind="product")] == ["alpha one"]
    assert [k.term for k in service.list_keywords(db, owner, origin="agent")] == ["beta two"]
    assert [k.term for k in service.list_keywords(db, owner, enabled=True)] == ["alpha one"]


# ------------------------------------------------------------------ the radar policy fields


def test_the_owner_sets_radar_policy_and_it_is_audited(client, db, cid):
    resp = client.patch(
        f"/api/companies/{cid}/policy",
        json={
            "radar_enabled": False,
            "radar_max_fetches_per_day": 500,
            "radar_max_ideas_per_day": 2,
            "radar_agent_sources": False,
            "radar_contact_email": " Owner@Example.test ",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["radar_enabled"] is False and body["radar_max_fetches_per_day"] == 500
    assert (
        body["radar_agent_sources"] is False and body["radar_contact_email"] == "Owner@Example.test"
    )
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "radar_policy.updated")).one()
    assert set(event.data["changed"]) >= {"radar_enabled", "radar_max_fetches_per_day"}
    cleared = client.patch(f"/api/companies/{cid}/policy", json={"radar_contact_email": None})
    assert cleared.json()["radar_contact_email"] is None
    assert (
        client.patch(f"/api/companies/{cid}/policy", json={"radar_contact_email": ""}).json()[
            "radar_contact_email"
        ]
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"radar_max_fetches_per_day": -1},
        {"radar_max_ideas_per_day": 101},
        {"radar_max_goals_per_week": 21},
        {"radar_enabled": None},
        {"radar_contact_email": "a@b.test\r\nX-Evil: 1"},
        {"radar_contact_email": "not an email"},
        {"radar_contact_email": "<a@b.test>"},
    ],
)
def test_radar_policy_values_are_bounded(client, cid, payload):
    assert client.patch(f"/api/companies/{cid}/policy", json=payload).status_code == 422


def test_agents_and_viewers_cannot_change_the_radar_policy(db, cid, agent, viewer):
    from app.schemas.policy import PolicyUpdate
    from app.services import policy

    for who in (agent, viewer):
        for field, value in (
            ("radar_enabled", False),
            ("radar_max_open_ideas", 500),
            ("radar_auto_accept", False),
        ):
            with pytest.raises(Forbidden):
                policy.update_policy(db, who, PolicyUpdate(**{field: value}))
    db.rollback()
    assert policy_of(db, cid).radar_enabled is True


def test_seed_slugs_resolve_to_the_documented_hosts():
    by_slug = {s.slug: s for s in seed_mod.load_seed_sources()}
    assert by_slug["techmeme"].fetch_url == "https://www.techmeme.com/feed.xml"
    assert by_slug["one-page-love"].interval_seconds == 86_400
    assert by_slug["langgraph-releases"].keep_unmatched is True
