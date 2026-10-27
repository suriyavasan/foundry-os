"""The complete ``GithubClient`` (M14): headers, ETags, rate limits, token expiry, SSO, secret
masking, write spacing, the push probe and the log download. Scripted transports only; the fake
GitHub has its own self-tests (test_m14_fakes.py)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.base import ProviderAuthError, ProviderError
from app.integrations.github import (
    NOT_MODIFIED,
    GithubClient,
    GithubRateLimited,
    GithubSsoRequired,
    parse_expiration,
)
from tests.m14_helpers import TOKEN

REPO = "acme/app"


class Script:
    """A transport that answers by (method, path) and records every request."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, path: str, *responses: httpx.Response | Exception) -> Script:
        self.routes.setdefault((method, path), []).extend(responses)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            raise AssertionError(f"unscripted: {request.method} {request.url}")
        item = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(self, **kwargs) -> GithubClient:
        kwargs.setdefault("sleep", lambda s: None)
        return GithubClient(
            httpx.Client(transport=httpx.MockTransport(self)),
            TOKEN,
            base_url="https://api.github.test",
            web_url="https://web.github.test",
            **kwargs,
        )


def ok(body, **headers) -> httpx.Response:
    return httpx.Response(200, json=body, headers=headers)


def test_every_request_carries_the_bearer_token_and_the_api_version():
    s = Script().on("GET", "/user", ok({"login": "bot"}, **{"x-oauth-scopes": "repo, workflow"}))
    c = s.client()
    assert c.identity()["login"] == "bot"
    req = s.requests[0]
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-github-api-version"] == "2022-11-28"
    assert req.headers["accept"] == "application/vnd.github+json"
    assert c.scopes == ["repo", "workflow"]
    assert TOKEN not in str(req.url)  # never in the URL


def test_conditional_reads_send_the_etag_and_a_304_is_not_modified():
    s = Script().on(
        "GET",
        f"/repos/{REPO}/issues",
        ok([{"number": 1}], etag='W/"abc"'),
        httpx.Response(304, headers={"etag": 'W/"abc"'}),
    )
    etags: dict[str, str] = {}
    c = s.client(etags=etags)
    assert c.list_issues(REPO, conditional=True) == [{"number": 1}]
    assert list(etags.values()) == ['W/"abc"']
    assert "if-none-match" not in s.requests[0].headers
    again = s.client(etags=etags).list_issues(REPO, conditional=True)
    assert again is NOT_MODIFIED and not again
    assert s.requests[1].headers["if-none-match"] == 'W/"abc"'
    # an unconditional read never sends it and never stores one
    other = s.client(etags={})
    other.list_issues(REPO)
    assert other.etags == {}


def test_rate_limit_headers_and_token_expiry_are_captured_on_success_and_on_error():
    reset = 1_900_000_000
    s = (
        Script()
        .on(
            "GET",
            f"/repos/{REPO}",
            ok(
                {"full_name": REPO},
                **{
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-remaining": "4321",
                    "x-ratelimit-reset": str(reset),
                    "x-ratelimit-resource": "core",
                    "github-authentication-token-expiration": "2030-01-02 03:04:05 UTC",
                },
            ),
        )
        .on(
            "GET",
            f"/repos/{REPO}/branches/x",
            httpx.Response(
                404, json={"message": "Not Found"}, headers={"x-ratelimit-remaining": "4320"}
            ),
        )
    )
    c = s.client()
    c.get_repo(REPO)
    assert (c.last_rate.limit, c.last_rate.remaining) == (5000, 4321)
    assert c.last_rate.reset_at == datetime.fromtimestamp(reset, UTC)
    assert c.token_expires_at == datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    with pytest.raises(ProviderError):
        c.get_branch(REPO, "x")
    assert c.last_rate.remaining == 4320  # errors count too


def test_parse_expiration_accepts_the_documented_format_and_rejects_noise():
    assert parse_expiration("2026-12-01 10:00:00 UTC") == datetime(2026, 12, 1, 10, tzinfo=UTC)
    assert parse_expiration("2026-12-01T10:00:00Z") == datetime(2026, 12, 1, 10, tzinfo=UTC)
    assert parse_expiration("soon") is None and parse_expiration(None) is None


def test_secondary_primary_and_429_limits_raise_github_rate_limited():
    secondary = httpx.Response(
        403,
        json={"message": "You have exceeded a secondary rate limit."},
        headers={"retry-after": "37"},
    )
    primary = httpx.Response(
        403,
        json={"message": "API rate limit exceeded for user ID 1."},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1900000000"},
    )
    plain429 = httpx.Response(429, json={"message": "slow down"})
    s = (
        Script()
        .on("GET", f"/repos/{REPO}", secondary)
        .on("GET", f"/repos/{REPO}/labels", primary)
        .on("GET", "/user/orgs", plain429)
    )
    c = s.client()
    with pytest.raises(GithubRateLimited) as exc:
        c.get_repo(REPO)
    assert exc.value.retry_after == 37 and exc.value.secondary
    assert exc.value.wait_seconds(5) == 37
    with pytest.raises(GithubRateLimited) as exc:
        c.list_labels(REPO)
    assert exc.value.retry_after is None and not exc.value.secondary
    assert exc.value.reset_at == datetime.fromtimestamp(1_900_000_000, UTC)
    assert exc.value.wait_seconds(now=datetime.fromtimestamp(1_899_999_900, UTC)) == 101
    with pytest.raises(GithubRateLimited) as exc:
        c.list_orgs()
    # no hint at all: 60 * 2**n seconds, capped at 15 minutes
    assert [exc.value.wait_seconds(n) for n in (0, 1, 2, 10)] == [60, 120, 240, 900]


def test_a_plain_403_is_an_auth_error_not_a_rate_limit_and_names_the_reason():
    s = Script().on(
        "GET",
        f"/repos/{REPO}/dependabot/alerts",
        httpx.Response(403, json={"message": "Resource not accessible by personal access token"}),
    )
    with pytest.raises(ProviderAuthError) as exc:
        s.client().dependabot_alerts(REPO)
    assert not isinstance(exc.value, GithubRateLimited)
    assert exc.value.details["reason"] == "not_accessible" and exc.value.details["status"] == 403


def test_sso_header_becomes_github_sso_required_with_the_url():
    s = Script().on(
        "GET",
        f"/repos/{REPO}",
        httpx.Response(
            403,
            json={"message": "Resource protected by organization SAML enforcement."},
            headers={
                "x-github-sso": "required; url=https://github.com/orgs/acme/sso?authorization_request=abc"
            },
        ),
    )
    with pytest.raises(GithubSsoRequired) as exc:
        s.client().get_repo(REPO)
    assert exc.value.sso_url == "https://github.com/orgs/acme/sso?authorization_request=abc"
    assert isinstance(exc.value, ProviderAuthError)


def test_the_token_is_masked_in_every_error_message():
    leaky = httpx.Response(
        422,
        json={"message": f"Bad body, token {TOKEN} was echoed", "errors": []},
    )
    s = (
        Script()
        .on("POST", f"/repos/{REPO}/issues", leaky)
        .on(
            "POST",
            f"/repos/{REPO}/hooks",
            httpx.Response(422, json={"message": "secret hook-secret-value-99 is not allowed"}),
        )
    )
    c = s.client()
    with pytest.raises(ProviderError) as exc:
        c.create_issue(REPO, title="x")
    assert TOKEN not in exc.value.message and "[redacted]" in exc.value.message
    with pytest.raises(ProviderError) as exc:
        c.create_hook(REPO, url="https://x.test", secret="hook-secret-value-99", events=["push"])
    assert "hook-secret-value-99" not in exc.value.message
    # a connection failure never prints the token either
    boom = Script().on("GET", "/user", httpx.ConnectError(f"cannot reach with {TOKEN}"))
    with pytest.raises(ProviderError) as exc:
        boom.client().identity()
    assert TOKEN not in exc.value.message


def test_content_writes_are_spaced_but_reads_and_old_methods_are_not():
    sleeps: list[float] = []
    now = [100.0]
    s = (
        Script()
        .on("POST", f"/repos/{REPO}/issues/1/comments", httpx.Response(201, json={"id": 1}))
        .on("GET", f"/repos/{REPO}", ok({}))
        .on("POST", f"/repos/{REPO}/pulls", httpx.Response(201, json={"number": 1}))
    )
    c = GithubClient(
        httpx.Client(transport=httpx.MockTransport(s)),
        TOKEN,
        base_url="https://api.github.test",
        sleep=sleeps.append,
        clock=lambda: now[0],
        write_interval=1.0,
    )
    c.create_issue_comment(REPO, 1, "a")
    assert sleeps == []  # the first write never waits
    now[0] += 0.25
    c.create_issue_comment(REPO, 1, "b")
    assert sleeps == [pytest.approx(0.75)]
    c.get_repo(REPO)  # a read is not spaced
    c.open_pull(REPO, title="t", body="", head="h", base="b")  # the M8 method is unchanged
    assert sleeps == [pytest.approx(0.75)]
    c2 = GithubClient(
        httpx.Client(transport=httpx.MockTransport(s)),
        TOKEN,
        base_url="https://api.github.test",
        sleep=sleeps.append,
        clock=lambda: now[0],
        write_interval=0,
    )
    c2.create_issue_comment(REPO, 1, "c")
    c2.create_issue_comment(REPO, 1, "d")
    assert sleeps == [pytest.approx(0.75)]


def test_can_push_probes_the_web_host_with_basic_auth_and_no_api_quota():
    expected = "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "web.github.test"
        assert request.url.path == f"/{REPO}.git/info/refs"
        assert request.url.params["service"] == "git-receive-pack"
        assert request.headers["authorization"] == expected
        return httpx.Response(200, content=b"001f# service=git-receive-pack\n0000")

    c = GithubClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        TOKEN,
        base_url="https://api.github.test",
        web_url="https://web.github.test",
    )
    assert c.can_push(REPO) is True
    for status, expected_result in ((403, False), (401, False), (404, False)):
        denied = GithubClient(
            httpx.Client(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s))),
            TOKEN,
            web_url="https://web.github.test",
        )
        assert denied.can_push(REPO) is expected_result
    broken = GithubClient(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="oops"))),
        TOKEN,
        web_url="https://web.github.test",
    )
    with pytest.raises(ProviderError):
        broken.can_push(REPO)


def test_the_job_log_redirect_is_fetched_without_authorization_and_is_tailed_and_scrubbed():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.test":
            return httpx.Response(
                302, headers={"location": "https://logs.example.test/j/9?sig=zzz"}
            )
        assert "authorization" not in request.headers
        text = "x" * 5000 + f"\nleaked {TOKEN}\nlast line\n"
        return httpx.Response(200, text=text)

    c = GithubClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        TOKEN,
        base_url="https://api.github.test",
    )
    tail = c.job_log_tail(REPO, 9, limit=200)
    assert len(seen) == 2 and seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert "last line" in tail and TOKEN not in tail and len(tail) <= 200
    # a redirect that is not a URL is refused
    bad = GithubClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "/x"}))
        ),
        TOKEN,
        base_url="https://api.github.test",
    )
    with pytest.raises(ProviderError):
        bad.job_log_tail(REPO, 9)


def test_a_moved_repository_is_an_error_not_an_empty_success():
    s = Script().on(
        "GET",
        f"/repos/{REPO}",
        httpx.Response(301, headers={"location": "https://api.github.test/x"}),
    )
    with pytest.raises(ProviderError) as exc:
        s.client().get_repo(REPO)
    assert exc.value.details["status"] == 301


def test_merge_and_update_branch_send_the_reviewed_sha():
    s = (
        Script()
        .on("PUT", f"/repos/{REPO}/pulls/7/merge", ok({"merged": True, "sha": "abc"}))
        .on(
            "PUT",
            f"/repos/{REPO}/pulls/7/update-branch",
            httpx.Response(202, json={"message": "ok"}),
        )
    )
    c = s.client()
    c.merge_pull(REPO, 7, sha="f" * 40, method="rebase", title="T")
    assert s.requests[0].read() == (
        b'{"sha":"' + b"f" * 40 + b'","merge_method":"rebase","commit_title":"T"}'
    )
    c.update_pull_branch(REPO, 7, "e" * 40)
    assert b'"expected_head_sha":"' + b"e" * 40 in s.requests[1].read()


def test_pagination_stops_on_a_short_page_and_check_runs_are_unwrapped():
    s = Script().on("GET", f"/repos/{REPO}/commits/abc/check-runs", ok({"check_runs": [{"id": 1}]}))
    assert s.client().check_runs(REPO, "abc") == [{"id": 1}]
    pages = Script().on(
        "GET",
        f"/repos/{REPO}/labels",
        ok([{"name": str(i)} for i in range(100)]),
        ok([{"name": "last"}]),
    )
    assert len(pages.client().list_labels(REPO)) == 101
    assert [r.url.params["page"] for r in pages.requests] == ["1", "2"]
