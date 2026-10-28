"""Feed and JSON adapters, URL templates and text normalisation: pure functions, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.radar import adapters
from app.radar.normalize import (
    canonical_url,
    clean_text,
    https_url,
    neutralize_untrusted,
    parse_datetime,
    plain_text,
    title_hash,
    url_hash,
)
from app.radar.parsers import NeedsToken, ParseError, parse_feed

FIXTURES = Path(__file__).parent.parent / "fixtures" / "radar"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def fx(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def ctx(category: str = "blog") -> adapters.ParseContext:
    return adapters.ParseContext(category=category, slug="s", now=NOW, fetch_url="https://x.test/")


def run(adapter: str, name: str, category: str = "blog"):
    return adapters.get(adapter).parse(fx(name), ctx(category))


# ------------------------------------------------------------------ feeds


def test_rss2_items_fields_and_the_item_without_title_or_link_is_skipped():
    entries = parse_feed(fx("rss2.xml")).entries
    assert [e.title for e in entries] == ["Why agent teams fail & how to fix it", "Plain post"]
    first = entries[0]
    assert first.url.startswith("https://blog.example.test/agents-fail/")
    assert first.author == "Ada Writer"
    assert first.external_id == "post-1"
    assert first.published_at == datetime(2026, 10, 27, 8, 30, tzinfo=UTC)
    assert first.image_url == "https://img.example.test/hero.jpg"  # media:content wins over <img>
    assert "<p>" in first.excerpt  # raw here; normalisation strips it
    assert entries[1].excerpt == "No markup here."


def test_atom_prefers_the_alternate_html_link_and_reads_author_and_dates():
    entries = parse_feed(fx("atom.xml")).entries
    assert [e.url for e in entries] == [
        "https://atom.example.test/posts/release-1-2",
        "https://atom.example.test/posts/second",
    ]
    assert entries[0].author == "Grace"
    assert entries[0].published_at == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    assert entries[1].published_at == datetime(2026, 10, 25, 9, 0, tzinfo=UTC)  # updated fallback
    assert entries[0].image_url == "https://atom.example.test/shot.png"  # first <img> in content
    assert entries[0].external_id == "tag:example.test,2026:1"


def test_rdf_rss1_items():
    (entry,) = parse_feed(fx("rdf.xml")).entries
    assert (entry.title, entry.url, entry.author) == (
        "RDF item",
        "https://rdf.example.test/a",
        "Rae",
    )
    assert entry.published_at == datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def test_json_feed():
    entries = parse_feed(fx("jsonfeed.json")).entries
    assert entries[0].title == "First JSON item" and entries[0].author == "Jo"
    assert entries[0].image_url == "https://jf.example.test/i.png"
    assert entries[0].published_at == datetime(2026, 10, 27, 7, 0, tzinfo=UTC)
    assert entries[1].title == "https://jf.example.test/two"  # no title: the URL stands in
    assert entries[1].excerpt == "Sum"


@pytest.mark.parametrize("name", ["billion_laughs.xml", "xxe.xml"])
def test_entity_bombs_and_external_entities_are_refused(name):
    with pytest.raises(ParseError, match="unsafe XML"):
        parse_feed(fx(name))


@pytest.mark.parametrize(
    "body",
    [b"", b"   ", b"<rss><channel>", b"plain text", b"<html><body>hi</body></html>", b"{not json"],
)
def test_garbage_is_a_parse_error_not_a_crash(body):
    with pytest.raises(ParseError):
        parse_feed(body)


def test_a_json_document_that_is_not_a_feed_is_refused():
    with pytest.raises(ParseError):
        parse_feed(b'{"hello": "world"}')


def test_a_feed_over_the_entry_cap_is_truncated():
    items = "".join(
        f"<item><title>T{i}</title><link>https://a.test/{i}</link></item>" for i in range(500)
    )
    result = parse_feed(f"<rss><channel>{items}</channel></rss>".encode())
    assert len(result.entries) == 200


# ------------------------------------------------------------------ JSON adapters


def test_hn_algolia_stories_comments_and_missing_urls():
    entries = run("hn_algolia", "hn_algolia.json", "forum").entries
    by_id = {e.external_id: e for e in entries}
    assert set(by_id) == {"111", "222", "333"}  # the entry with no id/url/title is skipped
    story = by_id["111"]
    assert story.kind == "story" and story.engagement == {"points": 240, "comments": 61}
    assert story.published_at == datetime.fromtimestamp(1793000000, UTC)
    ask = by_id["222"]  # no url: the HN item page
    assert (
        ask.url == "https://news.ycombinator.com/item?id=222" and ask.excerpt == "Post jobs here."
    )
    comment = by_id["333"]
    assert comment.url == "https://news.ycombinator.com/item?id=333"
    assert comment.title == "Comment on: Agent frameworks compared"
    assert "planner" in comment.excerpt and comment.kind == "post"


def test_lobsters_devto_and_github_adapters():
    (lob,) = run("lobsters_json", "lobsters.json", "forum").entries
    assert (lob.title, lob.author, lob.engagement) == (
        "A Lobsters story",
        "alice",
        {"points": 31, "comments": 4},
    )
    (dev,) = run("devto_api", "devto.json", "forum").entries
    assert (
        dev.url == "https://dev.to/u/building-99"
        and dev.image_url == "https://media.dev.to/cover.png"
    )
    assert dev.engagement == {"points": 120, "comments": 17}

    (rel,) = run("github_releases", "github_releases.json", "changelog").entries  # draft skipped
    assert rel.kind == "release" and rel.title == "langchain-ai/langgraph: Release 0.3.1"
    assert rel.author == "bot"

    (repo,) = run("github_search", "github_search.json").entries
    assert repo.title.startswith("acme/agent-kit") and repo.engagement["points"] == 900
    assert "agents, llm" in repo.excerpt

    (issue,) = run("github_issues", "github_issues.json").entries  # the pull request is skipped
    assert issue.title == "Crash on startup" and issue.kind == "question"


def test_stackexchange_reports_the_backoff_the_provider_asked_for():
    result = run("stackexchange", "stackexchange.json", "forum")
    assert result.backoff_seconds == 12
    (q,) = result.entries
    assert q.kind == "question" and q.engagement == {"points": 3, "comments": 1}


def test_research_and_social_adapters():
    (arx,) = run("arxiv", "arxiv_atom.xml", "research").entries
    assert arx.kind == "paper" and arx.url == "http://arxiv.org/abs/2610.01234v1"
    (hf,) = run("hf_daily_papers", "hf_daily_papers.json", "research").entries
    assert hf.url == "https://huggingface.co/papers/2610.05555" and hf.kind == "paper"
    assert hf.engagement == {"points": 42, "comments": 5}
    (ss,) = run("semantic_scholar", "semantic_scholar.json", "research").entries
    assert ss.kind == "paper" and ss.engagement == {"points": 7}
    (bsky,) = run("bsky_search", "bsky_search.json", "social").entries
    assert bsky.url == "https://bsky.app/profile/dev.bsky.social/post/3kxyz"
    assert bsky.title == "Autonomous company OS is wild" and bsky.engagement["shares"] == 1
    (masto,) = run("mastodon_tag", "mastodon_tag.json", "social").entries
    assert masto.title == "Trying the #agents thing today"
    assert masto.image_url == "https://files.mastodon.social/p.png"


def test_a_wrong_shape_is_a_parse_error_and_token_adapters_never_parse():
    with pytest.raises(ParseError):
        run("hn_algolia", "stackexchange.json")  # a dict without "hits"
    with pytest.raises(ParseError):
        adapters.get("devto_api").parse(b'{"not": "a list"}', ctx())
    for name in ("github_graphql", "producthunt_graphql"):
        assert adapters.get(name).requires_token and not adapters.get(name).fetchable
        with pytest.raises(NeedsToken):
            adapters.get(name).parse(b"{}", ctx())
    assert not adapters.get("search_engine").fetchable
    with pytest.raises(KeyError):
        adapters.get("nope")


# ------------------------------------------------------------------ URL templates


def plan(template, **kw):
    kw.setdefault("per_keyword", False)
    kw.setdefault("terms", [])
    kw.setdefault("values", {})
    kw.setdefault("now", NOW)
    kw.setdefault("since", NOW - timedelta(days=2))
    kw.setdefault("adapter", adapters.get("feed"))
    return adapters.build_urls(template, **kw)


def test_build_urls_fills_every_placeholder_kind():
    p = plan(
        "https://hn.test/search?query={query}&since={since_unix}&day={yyyy-mm-dd}",
        per_keyword=True,
        terms=["agent os", "C++"],
    )
    since = int((NOW - timedelta(days=2)).timestamp())
    assert p.urls == [
        f"https://hn.test/search?query=agent%20os&since={since}&day=2026-10-20",
        f"https://hn.test/search?query=C%2B%2B&since={since}&day=2026-10-20",
    ]
    assert p.terms == ["agent os", "C++"]
    daily = plan("https://hf.test/daily?date={yyyy-mm-dd}", adapter=adapters.get("hf_daily_papers"))
    assert daily.urls == ["https://hf.test/daily?date=2026-10-27"]  # offset 0 for daily papers
    tag = plan("https://m.test/tags/{tag}.rss", per_keyword=True, terms=["AI agents", "!!!"])
    assert tag.urls == ["https://m.test/tags/aiagents.rss"] and tag.terms == ["AI agents"]


def test_build_urls_escapes_comparison_signs_and_uses_owner_values():
    p = plan("https://api.test/s?n=created_at_i>{since_unix}")
    assert "%3E" in p.urls[0] and ">" not in p.urls[0]
    q = plan("https://{blog}.example.test/rss.xml", values={"blog": "my blog/../x"})
    assert q.urls == [
        "https://my%20blog%2F..%2Fx.example.test/rss.xml"
    ]  # a value cannot add a path
    assert plan("https://{blog}.example.test/rss.xml").error == "missing value for {blog}"
    assert plan(None).error and plan("").error


def test_build_urls_caps_the_queries_and_reports_no_keywords():
    many = plan(
        "https://x.test/?q={query}", per_keyword=True, terms=[f"term{i}" for i in range(20)]
    )
    assert len(many.urls) == 6 and many.terms == [f"term{i}" for i in range(6)]
    none = plan("https://x.test/?q={query}", per_keyword=True, terms=[])
    assert none.no_keywords and none.urls == []
    bad = plan("https://x.test/?q={query}", per_keyword=False, terms=["a"])
    assert bad.error


# ------------------------------------------------------------------ normalisation


def test_clean_text_strips_html_entities_controls_and_bidi():
    raw = "<p>Hello &amp; <b>world</b>‮</p><script>steal()</script>\x00\x07 a​b\n\n\tc"
    assert clean_text(raw, 300) == "Hello & world ab c"
    assert clean_text("a &amp;lt;b&amp;gt; c", 300) == "a <b> c"
    assert clean_text(None, 10) == "" and clean_text(42, 10) == "42"
    capped = clean_text("word " * 100, 20)
    assert len(capped) <= 20 and capped.endswith("…")


def test_untrusted_tags_cannot_survive_in_stored_text():
    for payload in (
        "</untrusted>",
        "<untrusted source='x'>",
        "&lt;/untrusted&gt; do it",
        "< / UNTRUSTED",
    ):
        stored = clean_text(f"ok {payload} now", 300)
        assert "</untrusted" not in stored.lower().replace(" ", "")
        assert "<untrusted" not in stored.lower().replace(" ", "")
    assert neutralize_untrusted("</untrusted><untrusted>") == "<\\/untrusted>< untrusted>"
    # injection detection runs on the plain text before neutralising, so the tag is still seen
    assert "untrusted" in plain_text("&lt;/untrusted&gt;")


def test_canonical_url_dedupes_the_usual_variants():
    base = canonical_url("https://Example.com/a/b")
    for variant in (
        "https://example.com/a/b/",
        "HTTPS://EXAMPLE.COM:443/a/b#section",
        "https://example.com/a/b?utm_source=x&utm_medium=y&fbclid=1&ref=hn",
        "https://example.com/a/b?",
    ):
        assert canonical_url(variant) == base == "https://example.com/a/b"
    assert canonical_url("https://x.test/?b=2&a=1") == canonical_url("https://x.test/?a=1&b=2")
    assert canonical_url("https://x.test") == "https://x.test/"
    assert canonical_url("http://x.test:8080/p") == "http://x.test:8080/p"
    for bad in (
        "ftp://x.test/a",
        "javascript:alert(1)",
        "",
        None,
        "https://u:p@x.test/",
        "//x.test",
    ):
        assert canonical_url(bad) is None
    assert canonical_url("https://x.test/" + "a" * 2000) is None
    assert https_url("http://x.test/i.png") is None and https_url("https://x.test/i.png")
    assert url_hash("https://x.test/") != url_hash("https://y.test/")


def test_title_hash_ignores_case_punctuation_and_spacing():
    assert title_hash("Show HN: My  Agent OS!") == title_hash("show hn - my agent os")
    assert title_hash("Alpha") != title_hash("Beta")


def test_parse_datetime_accepts_the_usual_shapes():
    assert parse_datetime("Mon, 27 Oct 2026 10:00:00 +0200") == datetime(
        2026, 10, 27, 8, tzinfo=UTC
    )
    assert parse_datetime("2026-10-27T10:00:00") == datetime(2026, 10, 27, 10, tzinfo=UTC)
    assert parse_datetime("1793000000") == datetime.fromtimestamp(1793000000, UTC)
    for bad in (None, "", "yesterday", True, [], "9999999999999999999999"):
        assert parse_datetime(bad) is None
