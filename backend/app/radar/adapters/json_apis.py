"""JSON adapters: each turns one provider's documented response into :class:`RawEntry` lists.

Defensive on purpose: a provider may add, drop or retype a field at any time, so every access
tolerates a missing or wrongly typed value and an entry without a usable URL or title is skipped
instead of failing the whole batch. A body that is not the expected top-level shape raises
:class:`ParseError` (the source then shows ``parse_error``).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.radar.normalize import https_url, parse_datetime, plain_text
from app.radar.parsers import MAX_ENTRIES, NeedsToken, ParseError, ParseResult, RawEntry, parse_feed

if TYPE_CHECKING:
    from app.radar.adapters import ParseContext

_RAW_TEXT_CAP = 20_000


def load_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ParseError("not valid JSON") from exc


def _s(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _n(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _engagement(points: Any = None, comments: Any = None, shares: Any = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in (("points", points), ("comments", comments), ("shares", shares)):
        n = _n(value)
        if n is not None and n >= 0:
            out[key] = n
    return out


def _list(data: Any, key: str | None = None) -> list[Any]:
    found = data.get(key) if key and isinstance(data, dict) else data
    if not isinstance(found, list):
        raise ParseError(
            f"unexpected response shape (expected a list{f' at {key!r}' if key else ''})"
        )
    return found[:MAX_ENTRIES]


# ------------------------------------------------------------------ Hacker News (Algolia)


def hn_algolia(body: bytes, ctx: ParseContext) -> ParseResult:
    """https://hn.algolia.com/api/v1/search_by_date -- stories, comments, Show HN."""
    out: list[RawEntry] = []
    for hit in _list(load_json(body), "hits"):
        if not isinstance(hit, dict):
            continue
        object_id = _s(hit.get("objectID"))
        comment = _s(hit.get("comment_text"))
        title = _s(hit.get("title")) or _s(hit.get("story_title"))
        permalink = f"https://news.ycombinator.com/item?id={object_id}" if object_id else ""
        is_comment = bool(comment) and not _s(hit.get("title"))
        url = (
            permalink
            if is_comment
            else (_s(hit.get("url")) or _s(hit.get("story_url")) or permalink)
        )
        if is_comment:
            title = f"Comment on: {title}" if title else "Hacker News comment"
        if not url or not title:
            continue
        out.append(
            RawEntry(
                url=url,
                title=title,
                excerpt=(comment or _s(hit.get("story_text")))[:_RAW_TEXT_CAP],
                author=_s(hit.get("author")) or None,
                published_at=parse_datetime(hit.get("created_at_i") or hit.get("created_at")),
                external_id=object_id or None,
                kind="post" if is_comment else "story",
                engagement=_engagement(hit.get("points"), hit.get("num_comments")),
            )
        )
    return ParseResult(out)


# ------------------------------------------------------------------ Lobsters


def lobsters_json(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for story in _list(load_json(body)):
        if not isinstance(story, dict):
            continue
        url = _s(story.get("url")) or _s(story.get("comments_url"))
        title = _s(story.get("title"))
        if not url or not title:
            continue
        submitter = story.get("submitter_user")
        author = _s(submitter.get("username")) if isinstance(submitter, dict) else _s(submitter)
        out.append(
            RawEntry(
                url=url,
                title=title,
                excerpt=_s(story.get("description"))[:_RAW_TEXT_CAP],
                author=author or None,
                published_at=parse_datetime(story.get("created_at")),
                external_id=_s(story.get("short_id")) or None,
                kind="story",
                engagement=_engagement(story.get("score"), story.get("comment_count")),
            )
        )
    return ParseResult(out)


# ------------------------------------------------------------------ DEV Community (Forem)


def devto_api(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for article in _list(load_json(body)):
        if not isinstance(article, dict):
            continue
        url = _s(article.get("url")) or _s(article.get("canonical_url"))
        title = _s(article.get("title"))
        if not url or not title:
            continue
        user = article.get("user")
        out.append(
            RawEntry(
                url=url,
                title=title,
                excerpt=_s(article.get("description"))[:_RAW_TEXT_CAP],
                author=(_s(user.get("name")) or _s(user.get("username")) or None)
                if isinstance(user, dict)
                else None,
                published_at=parse_datetime(article.get("published_at")),
                external_id=str(article["id"]) if article.get("id") is not None else None,
                kind="post",
                image_url=https_url(article.get("cover_image") or article.get("social_image")),
                engagement=_engagement(
                    article.get("public_reactions_count"), article.get("comments_count")
                ),
            )
        )
    return ParseResult(out)


# ------------------------------------------------------------------ GitHub


def _repo_of(html_url: str) -> str:
    match = re.match(r"https://github\.com/([^/]+/[^/]+)/", html_url)
    return match.group(1) if match else ""


def github_releases(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for rel in _list(load_json(body)):
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        url = _s(rel.get("html_url"))
        tag = _s(rel.get("tag_name"))
        name = _s(rel.get("name")) or tag
        if not url or not name:
            continue
        repo = _repo_of(url)
        author = rel.get("author")
        out.append(
            RawEntry(
                url=url,
                title=f"{repo}: {name}" if repo else name,
                excerpt=_s(rel.get("body"))[:_RAW_TEXT_CAP],
                author=_s(author.get("login")) if isinstance(author, dict) else None,
                published_at=parse_datetime(rel.get("published_at") or rel.get("created_at")),
                external_id=str(rel["id"]) if rel.get("id") is not None else tag or None,
                kind="release",
            )
        )
    return ParseResult(out)


def github_search(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for repo in _list(load_json(body), "items"):
        if not isinstance(repo, dict):
            continue
        url = _s(repo.get("html_url"))
        name = _s(repo.get("full_name"))
        if not url or not name:
            continue
        owner = repo.get("owner")
        topics = repo.get("topics")
        extra = (
            f" Topics: {', '.join(t for t in topics if isinstance(t, str))}."
            if isinstance(topics, list) and topics
            else ""
        )
        out.append(
            RawEntry(
                url=url,
                title=f"{name}: {_s(repo.get('description'))}"[:300]
                if repo.get("description")
                else name,
                excerpt=(_s(repo.get("description")) + extra)[:_RAW_TEXT_CAP],
                author=_s(owner.get("login")) if isinstance(owner, dict) else None,
                published_at=parse_datetime(repo.get("created_at")),
                external_id=str(repo["id"]) if repo.get("id") is not None else None,
                kind="post",
                engagement=_engagement(repo.get("stargazers_count"), repo.get("open_issues_count")),
            )
        )
    return ParseResult(out)


def github_issues(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for issue in _list(load_json(body)):
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        url = _s(issue.get("html_url"))
        title = _s(issue.get("title"))
        if not url or not title:
            continue
        user = issue.get("user")
        out.append(
            RawEntry(
                url=url,
                title=title,
                excerpt=_s(issue.get("body"))[:_RAW_TEXT_CAP],
                author=_s(user.get("login")) if isinstance(user, dict) else None,
                published_at=parse_datetime(issue.get("created_at")),
                external_id=str(issue["id"]) if issue.get("id") is not None else None,
                kind="question",
                engagement=_engagement(None, issue.get("comments")),
            )
        )
    return ParseResult(out)


def token_required(body: bytes, ctx: ParseContext) -> ParseResult:
    """GraphQL sources (GitHub Discussions, Product Hunt) need a token the owner has not stored
    here: they are seeded off and never reach the network."""
    raise NeedsToken("this adapter needs an API token")


# ------------------------------------------------------------------ Stack Exchange


def stackexchange(body: bytes, ctx: ParseContext) -> ParseResult:
    data = load_json(body)
    out: list[RawEntry] = []
    for question in _list(data, "items"):
        if not isinstance(question, dict):
            continue
        url = _s(question.get("link"))
        title = _s(question.get("title"))
        if not url or not title:
            continue
        owner = question.get("owner")
        tags = question.get("tags")
        out.append(
            RawEntry(
                url=url,
                title=title,
                excerpt=(
                    f"Tags: {', '.join(t for t in tags if isinstance(t, str))}"
                    if isinstance(tags, list)
                    else ""
                )[:_RAW_TEXT_CAP],
                author=_s(owner.get("display_name")) if isinstance(owner, dict) else None,
                published_at=parse_datetime(question.get("creation_date")),
                external_id=str(question["question_id"])
                if question.get("question_id") is not None
                else None,
                kind="question",
                engagement=_engagement(question.get("score"), question.get("answer_count")),
            )
        )
    backoff = _n(data.get("backoff")) if isinstance(data, dict) else None
    return ParseResult(out, backoff_seconds=backoff if backoff and backoff > 0 else None)


# ------------------------------------------------------------------ Hugging Face / research


def _paper_entry(
    paper: dict[str, Any], wrapper: dict[str, Any], ctx: ParseContext
) -> RawEntry | None:
    paper_id = _s(paper.get("id"))
    title = _s(paper.get("title")) or _s(wrapper.get("title"))
    if not paper_id or not title:
        return None
    authors = paper.get("authors")
    first = ""
    if isinstance(authors, list) and authors and isinstance(authors[0], dict):
        first = _s(authors[0].get("name"))
    summary = _s(paper.get("summary")) or _s(wrapper.get("summary")) or _s(paper.get("ai_summary"))
    return RawEntry(
        url=f"https://huggingface.co/papers/{quote(paper_id, safe='')}",
        title=title,
        excerpt=summary[:_RAW_TEXT_CAP],
        author=first or None,
        published_at=parse_datetime(paper.get("publishedAt") or wrapper.get("publishedAt")),
        external_id=paper_id,
        kind="paper",
        engagement=_engagement(paper.get("upvotes"), wrapper.get("numComments")),
    )


def hf_daily_papers(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for wrapper in _list(load_json(body)):
        if not isinstance(wrapper, dict):
            continue
        paper = wrapper.get("paper")
        entry = _paper_entry(paper if isinstance(paper, dict) else wrapper, wrapper, ctx)
        if entry:
            out.append(entry)
    return ParseResult(out)


def hf_papers(body: bytes, ctx: ParseContext) -> ParseResult:
    return hf_daily_papers(body, ctx)


def semantic_scholar(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for paper in _list(load_json(body), "data"):
        if not isinstance(paper, dict):
            continue
        paper_id = _s(paper.get("paperId"))
        title = _s(paper.get("title"))
        url = _s(paper.get("url")) or (
            f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""
        )
        if not url or not title:
            continue
        year = _n(paper.get("year"))
        out.append(
            RawEntry(
                url=url,
                title=title,
                excerpt=f"Published {year}." if year else "",
                external_id=paper_id or None,
                kind="paper",
                engagement=_engagement(paper.get("citationCount")),
            )
        )
    return ParseResult(out)


def arxiv(body: bytes, ctx: ParseContext) -> ParseResult:
    """The arXiv API / RSS are Atom or RSS: the feed parser reads them; entries are papers."""
    result = parse_feed(body)
    for entry in result.entries:
        entry.kind = "paper"
    return result


# ------------------------------------------------------------------ Bluesky and Mastodon


def bsky_search(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for post in _list(load_json(body), "posts"):
        if not isinstance(post, dict):
            continue
        author = post.get("author")
        handle = _s(author.get("handle")) if isinstance(author, dict) else ""
        record = post.get("record")
        text = _s(record.get("text")) if isinstance(record, dict) else ""
        uri = _s(post.get("uri"))
        rkey = uri.rsplit("/", 1)[-1] if uri.startswith("at://") else ""
        if not handle or not rkey or not text:
            continue
        display = _s(author.get("displayName")) if isinstance(author, dict) else ""
        out.append(
            RawEntry(
                url=(
                    f"https://bsky.app/profile/{quote(handle, safe='.')}"
                    f"/post/{quote(rkey, safe='')}"
                ),
                title=text.strip().splitlines()[0][:200] if text.strip() else "Bluesky post",
                excerpt=text[:_RAW_TEXT_CAP],
                author=display or handle,
                published_at=parse_datetime(
                    record.get("createdAt") if isinstance(record, dict) else None
                ),
                external_id=uri,
                kind="post",
                engagement=_engagement(
                    post.get("likeCount"), post.get("replyCount"), post.get("repostCount")
                ),
            )
        )
    return ParseResult(out)


def mastodon_tag(body: bytes, ctx: ParseContext) -> ParseResult:
    out: list[RawEntry] = []
    for status in _list(load_json(body)):
        if not isinstance(status, dict):
            continue
        url = _s(status.get("url")) or _s(status.get("uri"))
        content = _s(status.get("content"))
        if not url or not content:
            continue
        account = status.get("account")
        media = status.get("media_attachments")
        image = None
        if isinstance(media, list):
            for attachment in media:
                if isinstance(attachment, dict) and attachment.get("type") == "image":
                    image = https_url(attachment.get("preview_url") or attachment.get("url"))
                    if image:
                        break
        out.append(
            RawEntry(
                url=url,
                title=_s(status.get("spoiler_text"))
                or plain_text(content)[:140]
                or "Mastodon post",
                excerpt=content[:_RAW_TEXT_CAP],
                author=(_s(account.get("display_name")) or _s(account.get("acct")) or None)
                if isinstance(account, dict)
                else None,
                published_at=parse_datetime(status.get("created_at")),
                external_id=_s(status.get("id")) or None,
                kind="post",
                image_url=image,
                engagement=_engagement(
                    status.get("favourites_count"),
                    status.get("replies_count"),
                    status.get("reblogs_count"),
                ),
            )
        )
    return ParseResult(out)
