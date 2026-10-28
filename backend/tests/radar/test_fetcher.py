"""The radar's HTTP client: SSRF guard, redirects, politeness, caps, conditional GET."""

from __future__ import annotations

import gzip
import socket
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from app.core import netguard
from app.models.radar import FetchStatus, RadarHostState
from app.radar import fetcher
from app.radar.fetcher import (
    BudgetExhausted,
    DbHostStore,
    FetchAborted,
    HostBusy,
    RadarFetcher,
    host_key,
    parse_retry_after,
    spacing_for,
    type_allowed,
    user_agent,
)

RSS = (Path(__file__).parent.parent / "fixtures" / "radar" / "rss2.xml").read_bytes()
HOST = "feeds.example.test"
FEED = f"https://{HOST}/feed.xml"


@pytest.fixture
def advancing(web, clock, monkeypatch):
    """``sleep`` records the wait and moves the fake clock, like a real one would."""

    def sleep(seconds: float) -> None:
        web.sleeps.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(fetcher, "sleep", sleep)
    return web


@pytest.fixture
def make(sf, clock):
    opened: list[RadarFetcher] = []

    def _make(**kw) -> RadarFetcher:
        f = RadarFetcher(store=DbHostStore(sf), user_agent=user_agent("1.0"), clock=clock, **kw)
        opened.append(f)
        return f

    yield _make
    for f in opened:
        f.close()


def site(web, host=HOST, robots="User-agent: *\nAllow: /\n", path="/feed.xml", body=RSS, **kw):
    web.site(host, robots)
    web.add(f"https://{host}{path}", body, **kw)


# ------------------------------------------------------------------ the happy path


def test_a_feed_is_fetched_with_our_user_agent_and_accept_header(web, make):
    site(web)
    result = make().fetch(FEED)
    assert result.ok and result.body == RSS and result.http_status == 200
    assert result.robots == "allowed" and result.requests == 2  # robots.txt + the feed
    sent = [s for s in web.seen if s.path == "/feed.xml"][0]
    assert sent.headers["user-agent"].startswith("FoundryOS-Radar/1.0 (+https://github.com/")
    assert "self-hosted feed reader" in sent.headers["user-agent"]
    assert "application/rss+xml" in sent.headers["accept"]
    assert sent.headers["host"] == HOST
    assert "contact" not in sent.headers["user-agent"]


def test_an_owner_contact_email_goes_into_the_user_agent():
    ua = user_agent("2.3", "owner@example.test")
    assert ua.endswith("; contact: owner@example.test)") and "FoundryOS-Radar/2.3" in ua


def test_a_gzip_body_is_decoded_and_parsed_input_is_the_decoded_text(web, make):
    site(web, body=gzip.compress(RSS), headers={"content-encoding": "gzip"})
    assert make().fetch(FEED).body == RSS


def test_the_default_transport_in_the_suite_is_offline(make):
    """No ``web`` fixture: the autouse guard refuses, the fetcher reports it instead of raising."""
    result = make().fetch(FEED)  # robots.txt cannot be read, so nothing else is attempted
    assert result.status == FetchStatus.ROBOTS_DISALLOWED.value
    assert result.robots == "unreachable" and "could not be read" in result.error
    direct = make().fetch(FEED, robots_override=True)  # the content request itself, offline
    assert direct.status == FetchStatus.NETWORK_ERROR.value and "failed" in direct.error


def test_the_suite_cannot_open_a_real_connection(monkeypatch):
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError, match="must not open a connection"):
            sock.connect(("93.184.216.34", 80))
        with pytest.raises(AssertionError, match="must not resolve"):
            socket.getaddrinfo("example.com", 80)
    finally:
        sock.close()


# ------------------------------------------------------------------ conditional GET


def test_etag_and_last_modified_are_sent_and_a_304_parses_nothing(web, make):
    site(
        web,
        headers={"etag": '"v1"', "last-modified": "Mon, 27 Oct 2026 08:00:00 GMT"},
        then=[dict(body=b"", status=304, content_type=None)],
    )
    first = make().fetch(FEED)
    assert first.etag == '"v1"' and first.last_modified == "Mon, 27 Oct 2026 08:00:00 GMT"
    second = make().fetch(FEED, etag=first.etag, last_modified=first.last_modified)
    assert second.status == FetchStatus.NOT_MODIFIED.value and second.body == b""
    sent = [s for s in web.seen if s.path == "/feed.xml"][-1]
    assert sent.headers["if-none-match"] == '"v1"'
    assert sent.headers["if-modified-since"] == "Mon, 27 Oct 2026 08:00:00 GMT"


# ------------------------------------------------------------------ statuses


@pytest.mark.parametrize(
    ("code", "expected"),
    [(404, FetchStatus.HTTP_4XX), (410, FetchStatus.HTTP_4XX), (500, FetchStatus.HTTP_5XX)],
)
def test_error_statuses(web, make, code, expected):
    site(web, status=code, content_type="text/html")
    result = make().fetch(FEED)
    assert result.status == expected.value and result.http_status == code and result.body == b""


def test_retry_after_is_honoured_and_blocks_the_host_for_the_next_attempt(web, make, sf, clock):
    site(web, status=429, headers={"retry-after": "120"}, content_type="text/html")
    result = make().fetch(FEED)
    assert result.status == FetchStatus.RATE_LIMITED.value and result.retry_after_seconds == 120
    with sf() as db:
        row = db.get(RadarHostState, HOST)
        assert row.retry_after_until == clock() + timedelta(seconds=120)
    with pytest.raises(HostBusy) as busy:
        make().fetch(FEED)
    assert busy.value.until == clock() + timedelta(seconds=120)
    clock.advance(121)
    assert (
        make().fetch(FEED).status == FetchStatus.RATE_LIMITED.value
    )  # it asked again, was refused


def test_rate_limit_without_a_header_gets_a_default_wait_and_github_quota_is_read(web, make):
    site(web, status=503, content_type="text/html")
    assert make().fetch(FEED).retry_after_seconds == 300
    web.site("api.github.test")
    web.add(
        "https://api.github.test/rel",
        b"{}",
        status=403,
        content_type="application/json",
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "99999999999"},
    )
    result = make().fetch("https://api.github.test/rel")
    assert result.status == FetchStatus.RATE_LIMITED.value
    assert result.retry_after_seconds == 86_400  # capped at 24 h


def test_parse_retry_after_forms(clock):
    assert parse_retry_after("30", clock()) == 30
    assert parse_retry_after("-5", clock()) == 0
    assert parse_retry_after("999999", clock()) == 86_400
    assert (
        parse_retry_after("nonsense", clock()) is None and parse_retry_after(None, clock()) is None
    )
    later = (clock() + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 85 <= parse_retry_after(later, clock()) <= 90


# ------------------------------------------------------------------ content type and size


def test_html_is_not_a_feed_and_xml_json_text_plain_are(web, make):
    site(web, content_type="text/html; charset=utf-8")
    result = make().fetch(FEED)
    assert result.status == FetchStatus.CONTENT_TYPE.value and result.body == b""
    for good in (
        "application/atom+xml",
        "application/xml",
        "text/plain; charset=utf-8",
        "application/vnd.api+json",
        "application/hal+xml",
        None,
    ):
        web.add(FEED, RSS, content_type=good)
        assert make().fetch(FEED).ok, good


def test_type_allowed_rules():
    assert type_allowed("TEXT/XML; charset=x", fetcher.FEED_TYPES)
    assert type_allowed(None, fetcher.FEED_TYPES) and type_allowed("anything", None)
    assert not type_allowed("text/html", fetcher.FEED_TYPES)
    assert not type_allowed("image/png", fetcher.JSON_TYPES)
    assert type_allowed("text/html; charset=utf-8", fetcher.HTML_TYPES)


def test_a_body_over_the_cap_is_too_large_and_nothing_is_returned(web, make):
    site(web, body=b"x" * 3_000)
    result = make().fetch(FEED, max_bytes=2_000)
    assert result.status == FetchStatus.TOO_LARGE.value and result.body == b""


def test_the_cap_applies_to_the_decoded_size(web, make):
    bomb = gzip.compress(b"a" * 3_000_000)  # a few KB on the wire, 3 MB decoded
    assert len(bomb) < 10_000
    site(web, body=bomb, headers={"content-encoding": "gzip"})
    result = make().fetch(FEED)  # default cap: 2 MB
    assert result.status == FetchStatus.TOO_LARGE.value and result.body == b""


def test_timeouts_and_the_whole_request_deadline(web, make, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    site(web)
    web.add_handler(FEED, slow)
    assert make().fetch(FEED).status == FetchStatus.TIMEOUT.value

    site(web)  # a response that arrives, but too slowly in total
    ticks = iter(range(0, 10_000, 30))
    monkeypatch.setattr(fetcher, "monotonic", lambda: next(ticks))
    assert make().fetch(FEED).status == FetchStatus.TIMEOUT.value


# ------------------------------------------------------------------ redirects and the SSRF guard


def test_a_redirect_is_followed_by_hand_and_each_hop_is_checked(web, make):
    web.site(HOST)
    web.site("cdn.example.test")
    web.add(FEED, b"", status=301, headers={"location": "https://cdn.example.test/real.xml"})
    web.add("https://cdn.example.test/real.xml", RSS)
    result = make().fetch(FEED)
    assert result.ok and result.final_url == "https://cdn.example.test/real.xml"
    assert result.redirects == ["https://cdn.example.test/real.xml"]
    assert web.count("cdn.example.test", "/robots.txt") == 1  # the new host was robots-checked
    # relative redirects resolve against the current URL
    web.add(FEED, b"", status=302, headers={"location": "/other.xml"})
    web.add(f"https://{HOST}/other.xml", RSS)
    assert make().fetch(FEED).final_url == f"https://{HOST}/other.xml"


@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254/latest/meta-data/", "https://10.0.0.5/feed", "https://meta.test/x"],
)
def test_a_redirect_to_a_metadata_or_private_address_is_refused(web, make, target):
    web.resolve_to("meta.test", "169.254.169.254")
    site(web)
    web.add(FEED, b"", status=302, headers={"location": target})
    result = make().fetch(FEED)
    # the plain-http target is refused as a downgrade before any request; the others by the guard
    refused = {FetchStatus.BLOCKED_DESTINATION.value, FetchStatus.REDIRECT_REFUSED.value}
    assert result.status in refused
    assert (result.status == FetchStatus.REDIRECT_REFUSED.value) == target.startswith("http://")
    assert all(s.host in (HOST,) for s in web.seen)  # nothing was requested from the target


def test_a_private_destination_is_refused_up_front(web, make):
    web.resolve_to("internal.test", "10.1.2.3")
    result = make().fetch("https://internal.test/feed.xml")
    assert result.status == FetchStatus.BLOCKED_DESTINATION.value and web.seen == []
    web.resolve_to("loop.test", "127.0.0.1")
    assert make().fetch("https://loop.test/f").status == FetchStatus.BLOCKED_DESTINATION.value
    assert make().fetch("ftp://x.test/f").status == FetchStatus.BLOCKED_DESTINATION.value


def test_dns_rebinding_is_refused(web, make):
    """The name answers public for robots.txt, then private for the feed request."""
    web.resolve_to("rebind.test", "93.184.216.34", "10.0.0.5")
    site(web, host="rebind.test")
    result = make().fetch("https://rebind.test/feed.xml")
    assert result.status == FetchStatus.BLOCKED_DESTINATION.value
    assert web.count("rebind.test", "/feed.xml") == 0


def test_https_to_http_downgrade_is_refused(web, make):
    site(web)
    web.add(FEED, b"", status=302, headers={"location": "http://feeds.example.test/feed.xml"})
    result = make().fetch(FEED)
    assert result.status == FetchStatus.REDIRECT_REFUSED.value and "http" in result.error


def test_a_fourth_redirect_is_refused_and_a_redirect_without_location_too(web, make):
    web.site(HOST)
    for i in range(5):
        web.add(
            f"https://{HOST}/r{i}",
            b"",
            status=302,
            headers={"location": f"https://{HOST}/r{i + 1}"},
        )
    web.add(f"https://{HOST}/r5", RSS)
    assert make().fetch(f"https://{HOST}/r0").status == FetchStatus.REDIRECT_REFUSED.value
    web.add(f"https://{HOST}/r2", RSS)  # three hops from r0 is fine
    ok = make().fetch(f"https://{HOST}/r0")
    assert ok.ok and len(ok.redirects) == 2
    web.add(f"https://{HOST}/nowhere", b"", status=302)
    assert make().fetch(f"https://{HOST}/nowhere").status == FetchStatus.REDIRECT_REFUSED.value


# ------------------------------------------------------------------ politeness


def test_requests_to_one_host_are_spaced_and_the_wait_is_slept(advancing, make):
    site(advancing, body=RSS)
    advancing.add(f"https://{HOST}/second.xml", RSS)
    f = make()
    assert f.fetch(FEED).ok and advancing.sleeps == []
    assert f.fetch(f"https://{HOST}/second.xml").ok
    assert advancing.sleeps == [2.0]  # the default spacing


def test_known_hosts_have_their_own_spacing_and_a_long_wait_defers_instead_of_sleeping(web, make):
    assert spacing_for("api.github.com") == 65.0 and spacing_for("www.reddit.com") == 6.0
    assert spacing_for("hn.algolia.com") == 1.0 and spacing_for("random.test") == 2.0
    assert spacing_for("a.b.api.stackexchange.com") == 5.0
    site(web, host="api.github.com", path="/repos/a/b/releases")
    web.add("https://api.github.com/repos/c/d/releases", b"[]", content_type="application/json")
    f = make()
    assert f.fetch("https://api.github.com/repos/a/b/releases").ok
    with pytest.raises(HostBusy) as busy:
        f.fetch("https://api.github.com/repos/c/d/releases")
    assert busy.value.host == "api.github.com" and web.sleeps == []


def test_crawl_delay_from_robots_txt_spaces_requests_and_is_capped(web, advancing, make):
    site(advancing, robots="User-agent: *\nCrawl-delay: 4\n")
    advancing.add(f"https://{HOST}/two.xml", RSS)
    f = make()
    first = f.fetch(FEED)
    assert first.crawl_delay_seconds == 4
    f.fetch(f"https://{HOST}/two.xml")
    assert advancing.sleeps == [4.0]
    site(advancing, host="slow.test", robots="User-agent: *\nCrawl-delay: 600\n")
    assert make().fetch("https://slow.test/feed.xml").crawl_delay_seconds == 60


def test_host_state_is_shared_across_fetchers_and_companies(web, make, sf):
    site(web)
    make().fetch(FEED)
    with sf() as db:
        row = db.get(RadarHostState, HOST)
        assert row.robots_status == "ok" and row.robots_txt and row.next_allowed_at is not None


def test_host_key_includes_non_default_ports_only():
    assert host_key("https://Example.COM/x") == "example.com"
    assert host_key("https://example.com:443/x") == "example.com"
    assert host_key("http://example.com:8080/x") == "example.com:8080"


# ------------------------------------------------------------------ gates: pause, stop, budget


def test_before_request_can_abort_and_every_attempt_is_counted(web, make):
    site(web)
    calls = {"before": 0, "after": 0}

    def before():
        calls["before"] += 1
        if calls["before"] == 2:  # robots.txt passes, the feed itself is refused
            raise FetchAborted("paused")

    def after():
        calls["after"] += 1

    f = make(before_request=before, on_request=after)
    with pytest.raises(FetchAborted):
        f.fetch(FEED)
    assert calls == {"before": 2, "after": 1}
    assert web.count(HOST, "/feed.xml") == 0

    def budget():
        raise BudgetExhausted("spent")

    with pytest.raises(BudgetExhausted):
        make(before_request=budget).fetch(FEED)


def test_robots_override_skips_the_robots_request_entirely(web, make):
    web.robots(HOST, "User-agent: *\nDisallow: /\n")
    web.add(FEED, RSS)
    blocked = make().fetch(FEED)
    assert blocked.status == FetchStatus.ROBOTS_DISALLOWED.value and blocked.robots == "disallowed"
    web.seen.clear()
    result = make().fetch(FEED, robots_override=True)
    assert result.ok and result.robots == "override" and result.requests == 1
    assert [s.path for s in web.seen] == ["/feed.xml"]


# ------------------------------------------------------------------ real sockets (loopback only)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/feed.xml":
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("ETag", '"loop"')
            self.end_headers()
            self.wfile.write(RSS)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def test_a_real_loopback_server_with_an_explicitly_allowed_policy(make):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}/feed.xml"
        # the production policy refuses loopback ...
        guarded = make(transport=netguard.GuardedTransport(netguard.PUBLIC_ONLY))
        assert guarded.fetch(url).status == FetchStatus.BLOCKED_DESTINATION.value
        # ... a test may allow it for itself, and nothing else changes
        local = make(transport=netguard.GuardedTransport(netguard.Policy(allow_loopback=True)))
        result = local.fetch(url)
        assert result.ok and result.body == RSS and result.etag == '"loop"'
        assert result.robots == "missing"  # /robots.txt answered 404
    finally:
        server.shutdown()
        server.server_close()
