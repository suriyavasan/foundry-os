"""robots.txt: RFC 9309 behaviour of the radar fetcher and its 24-hour cache."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.models.radar import FetchStatus, RadarHostState
from app.radar import robots
from app.radar.fetcher import DbHostStore, RadarFetcher, user_agent

RSS = (Path(__file__).parent.parent / "fixtures" / "radar" / "rss2.xml").read_bytes()
HOST = "robots.example.test"
FEED = f"https://{HOST}/feed.xml"
REDDIT = "User-agent: *\nDisallow: /\n"


@pytest.fixture
def fetch(sf, clock):
    made: list[RadarFetcher] = []

    def _fetch(url=FEED, **kw):
        f = RadarFetcher(store=DbHostStore(sf), user_agent=user_agent("1.0"), clock=clock)
        made.append(f)
        return f.fetch(url, **kw)

    yield _fetch
    for f in made:
        f.close()


# ------------------------------------------------------------------ evaluate (pure)


def record(text, status=robots.OK, clock=None):
    from datetime import UTC, datetime

    return robots.RobotsRecord(status, text, datetime(2026, 10, 27, tzinfo=UTC))


def test_disallow_all_for_everyone_like_reddit():
    verdict = robots.evaluate(record(REDDIT), "/r/SaaS/.rss")
    assert not verdict.allowed and verdict.reason == "disallowed"


def test_our_own_group_beats_the_wildcard_and_paths_match_by_prefix():
    text = (
        "User-agent: *\nDisallow: /\n\nUser-agent: FoundryOS-Radar\nDisallow: /private/\nAllow: /\n"
    )
    assert robots.evaluate(record(text), "/feed.xml").allowed
    assert not robots.evaluate(record(text), "/private/x").allowed
    assert not robots.evaluate(record(text), "/private/").allowed


def test_wildcards_and_end_anchors_like_githubs_atom_rule():
    text = "User-agent: *\nDisallow: /*.atom$\nDisallow: /search?\n"
    assert not robots.evaluate(record(text), "/org/repo/releases.atom").allowed
    assert robots.evaluate(record(text), "/org/repo/releases.json").allowed
    assert not robots.evaluate(record(text), "/search?q=agents").allowed
    assert robots.evaluate(record(text), "/searching").allowed


def test_a_missing_empty_or_unreadable_file():
    assert robots.evaluate(record(None, robots.MISSING), "/x") == robots.RobotsVerdict(
        True, "missing"
    )
    assert robots.evaluate(record("", robots.OK), "/x").allowed
    unreachable = robots.evaluate(record(None, robots.UNREACHABLE), "/x")
    assert not unreachable.allowed and unreachable.reason == "unreachable"


def test_crawl_delay_is_read_and_capped():
    assert robots.evaluate(record("User-agent: *\nCrawl-delay: 7\n"), "/").crawl_delay_seconds == 7
    assert (
        robots.evaluate(record("User-agent: *\nCrawl-delay: 9999\n"), "/").crawl_delay_seconds == 60
    )
    assert robots.evaluate(record("User-agent: *\nAllow: /\n"), "/").crawl_delay_seconds is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, robots.OK),
        (404, robots.MISSING),
        (401, robots.MISSING),  # RFC 9309: any 4xx means "no file"; Python's parser would disagree
        (403, robots.MISSING),
        (410, robots.MISSING),
        (429, robots.UNREACHABLE),
        (500, robots.UNREACHABLE),
        (503, robots.UNREACHABLE),
        (None, robots.UNREACHABLE),
        (302, robots.UNREACHABLE),
    ],
)
def test_response_status_to_cache_entry(status, expected):
    from datetime import UTC, datetime

    rec = robots.record_from_response(status, b"User-agent: *\nAllow: /", datetime.now(UTC))
    assert rec.status == expected


def test_freshness_windows(clock):
    now = clock()
    fresh = robots.RobotsRecord(robots.OK, "x", now - timedelta(hours=23))
    stale = robots.RobotsRecord(robots.OK, "x", now - timedelta(hours=25))
    down = robots.RobotsRecord(robots.UNREACHABLE, None, now - timedelta(hours=23))
    assert robots.is_fresh(fresh, now) and not robots.is_fresh(stale, now)
    assert robots.is_fresh(down, now) and not robots.is_fresh(None, now)


# ------------------------------------------------------------------ through the fetcher


def test_a_disallowed_path_is_never_requested(web, fetch):
    web.robots(HOST, REDDIT)
    web.add(FEED, RSS)
    result = fetch()
    assert result.status == FetchStatus.ROBOTS_DISALLOWED.value
    assert result.robots == "disallowed" and result.body == b""
    assert web.count(HOST, "/feed.xml") == 0


def test_a_404_robots_txt_means_allowed(web, fetch):
    web.site(HOST, robots=None)
    web.add(FEED, RSS)
    result = fetch()
    assert result.ok and result.robots == "missing"


def test_a_403_robots_txt_also_means_allowed(web, fetch):
    web.robots(HOST, "Forbidden", status=403)
    web.add(FEED, RSS)
    assert fetch().ok


def test_a_5xx_robots_txt_disallows_for_24_hours_without_hammering_the_host(web, fetch, clock):
    web.robots(HOST, "oops", status=503)
    web.add(FEED, RSS)
    first = fetch()
    assert first.status == FetchStatus.ROBOTS_DISALLOWED.value and first.robots == "unreachable"
    assert fetch().robots == "unreachable"
    assert web.count(HOST, "/robots.txt") == 1  # the failure is cached
    assert web.count(HOST, "/feed.xml") == 0
    clock.advance(hours=23)
    assert fetch().robots == "unreachable" and web.count(HOST, "/robots.txt") == 1
    web.site(HOST)  # the host recovers
    clock.advance(hours=2)
    assert fetch().ok and web.count(HOST, "/robots.txt") == 2


def test_the_robots_file_is_cached_for_24_hours_and_stored_per_host(web, fetch, sf, clock):
    web.site(HOST)
    web.add(FEED, RSS)
    assert fetch().ok and fetch().ok
    assert web.count(HOST, "/robots.txt") == 1
    clock.advance(hours=25)
    assert fetch().ok
    assert web.count(HOST, "/robots.txt") == 2
    with sf() as db:
        row = db.get(RadarHostState, HOST)
        assert row.robots_status == "ok" and "Allow: /" in row.robots_txt


def test_a_robots_redirect_is_followed(web, fetch):
    web.add(
        f"https://{HOST}/robots.txt",
        b"",
        status=301,
        headers={"location": f"https://{HOST}/real-robots.txt"},
    )
    web.add(f"https://{HOST}/real-robots.txt", REDDIT, content_type="text/plain")
    web.add(FEED, RSS)
    assert fetch().status == FetchStatus.ROBOTS_DISALLOWED.value


def test_a_huge_robots_file_is_not_trusted(web, fetch):
    web.robots(HOST, "User-agent: *\n" + "# filler\n" * 20_000 + "Allow: /\n")
    web.add(FEED, RSS)
    result = fetch()
    assert result.robots == "unreachable" and result.status == FetchStatus.ROBOTS_DISALLOWED.value


def test_each_host_has_its_own_rules(web, fetch):
    web.site("open.example.test")
    web.add("https://open.example.test/f", RSS)
    web.robots("closed.example.test", REDDIT)
    web.add("https://closed.example.test/f", RSS)
    assert fetch("https://open.example.test/f").ok
    assert fetch("https://closed.example.test/f").status == FetchStatus.ROBOTS_DISALLOWED.value
