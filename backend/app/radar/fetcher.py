"""The radar's HTTP client: polite, SSRF-guarded, robots-respecting. No model, no agent.

Per request:

* ``httpx.Client`` over :class:`app.core.netguard.GuardedTransport` (``PUBLIC_ONLY``) with
  ``follow_redirects=False``. Redirects are followed by hand, at most 3, each a new request through
  the same transport (so every hop is SSRF-checked and robots-checked); ``https`` to ``http`` is
  refused. The transport connects to the address it validated (no DNS-rebinding window).
* robots.txt (``robots.py``) is fetched through the same client and cached 24 h per host in
  ``radar_host_state``. A disallowed path is never requested. ``robots_override`` (owner-added
  sources only) skips the check.
* Per-host spacing (default 2 s, a table of known hosts, ``Crawl-delay``) and ``Retry-After`` live
  in ``radar_host_state`` because politeness is per host across companies. A wait over five
  seconds is not slept through: :class:`HostBusy` tells the caller to retry that source later.
* Conditional GET (``If-None-Match`` / ``If-Modified-Since``); a ``304`` parses nothing.
* Timeouts: connect 5 s, read 15 s, 20 s for the whole request (enforced while streaming). Size cap
  on the *decoded* body, enforced while streaming; over it the result is ``too_large`` and nothing
  is returned. The content type must be one the caller allows (feeds: XML/JSON, not HTML).
* Every request is counted: ``before_request`` may refuse it (company paused, emergency stop, daily
  budget) and ``on_request`` records it.

The response body never reaches a log, an audit event or ``last_error``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import netguard
from app.core.time import utcnow
from app.models.radar import FetchStatus, RadarHostState
from app.radar import (
    CONNECT_TIMEOUT,
    DEFAULT_HOST_SPACING_SECONDS,
    MAX_FEED_BYTES,
    MAX_REDIRECTS,
    MAX_RETRY_AFTER_SECONDS,
    MAX_ROBOTS_BYTES,
    MAX_SLEEP_SECONDS,
    READ_TIMEOUT,
    REQUEST_DEADLINE,
    USER_AGENT_HOME,
    USER_AGENT_TOKEN,
    offline,
    robots,
)

logger = logging.getLogger("company_os.radar")

FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/feed+json, application/json;q=0.9, "
    "text/xml;q=0.8, */*;q=0.1"
)
FEED_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/xml",
    "text/xml",
    "application/feed+json",
    "application/json",
    # Some hosts serve a feed as plain text (raw.githubusercontent.com does). The parser still has
    # to accept the body as XML or JSON; HTML is never accepted for a feed.
    "text/plain",
    "application/octet-stream",
    "binary/octet-stream",
)
JSON_TYPES = ("application/json", "text/json", "application/feed+json", "text/plain")
HTML_TYPES = ("text/html", "application/xhtml+xml")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# Known hosts: seconds between two requests (docs/specs/m16 section 5.3). Matched on the host and on
# any parent domain. Everything else gets ``DEFAULT_HOST_SPACING_SECONDS``.
HOST_SPACING_SECONDS: dict[str, float] = {
    "reddit.com": 6.0,
    "hn.algolia.com": 1.0,
    "export.arxiv.org": 3.0,
    "rss.arxiv.org": 3.0,
    "api.github.com": 65.0,  # 60 requests an hour without a token
    "api.stackexchange.com": 5.0,  # 300 requests a day without a key
    "api.bsky.app": 2.0,
    "public.api.bsky.app": 2.0,
}
DEFAULT_RATE_LIMITED_SECONDS = {429: 900, 503: 300}

# Replaced by tests: the transport every radar client uses (production: the SSRF guard).
transport_factory: Callable[[], httpx.BaseTransport] = lambda: netguard.GuardedTransport(  # noqa: E731
    netguard.PUBLIC_ONLY
)
sleep: Callable[[float], None] = time.sleep
monotonic: Callable[[], float] = time.monotonic


class _OfflineTransport(httpx.BaseTransport):
    """``COMPANY_OS_RADAR_OFFLINE``: every request fails before it leaves the process."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("the radar is offline (COMPANY_OS_RADAR_OFFLINE)", request=request)


class FetchAborted(Exception):
    """The company is paused, the radar is off or the emergency stop is on: stop now, quietly."""


class BudgetExhausted(Exception):
    """The company's daily fetch budget is spent."""


class _Blocked(Exception):
    """The SSRF guard refused the destination while reading robots.txt (nothing is cached)."""


class HostBusy(Exception):
    """The host cannot be asked yet (spacing or ``Retry-After``); try this source later."""

    def __init__(self, host: str, until: datetime) -> None:
        super().__init__(f"{host} is busy until {until.isoformat()}")
        self.host = host
        self.until = until


def user_agent(version: str, contact_email: str | None = None) -> str:
    base = f"{USER_AGENT_TOKEN}/{version} (+{USER_AGENT_HOME}; self-hosted feed reader"
    if contact_email:
        base += f"; contact: {contact_email}"
    return base + ")"


def host_key(url: str) -> str:
    """``hostname`` or ``hostname:port`` (non-default port) in lower case."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    default = 443 if parts.scheme == "https" else 80
    return host if not port or port == default else f"{host}:{port}"


def spacing_for(host: str) -> float:
    name = host.split(":", 1)[0]
    labels = name.split(".")
    for i in range(len(labels) - 1):
        found = HOST_SPACING_SECONDS.get(".".join(labels[i:]))
        if found is not None:
            return found
    return DEFAULT_HOST_SPACING_SECONDS


def type_allowed(content_type: str | None, allowed: tuple[str, ...] | None) -> bool:
    if allowed is None or not content_type:
        return True
    media = content_type.split(";", 1)[0].strip().lower()
    if media in allowed:
        return True
    if media.endswith("+json") and "application/json" in allowed:
        return True
    return bool(media.endswith("+xml") and "application/xml" in allowed)


def parse_retry_after(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = int(float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=now.tzinfo)
        seconds = int((when - now).total_seconds())
    return max(0, min(seconds, MAX_RETRY_AFTER_SECONDS))


# ------------------------------------------------------------------ host state


@dataclass(frozen=True)
class HostSnapshot:
    robots: robots.RobotsRecord | None
    next_allowed_at: datetime | None
    retry_after_until: datetime | None


class DbHostStore:
    """``radar_host_state`` through short transactions of their own (the fetcher must never hold a
    company transaction open across the network)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def load(self, host: str) -> HostSnapshot:
        with self._factory() as db:
            row = db.get(RadarHostState, host)
            if row is None:
                return HostSnapshot(None, None, None)
            record = None
            if row.robots_fetched_at is not None and row.robots_status:
                record = robots.RobotsRecord(
                    row.robots_status, row.robots_txt, row.robots_fetched_at
                )
            return HostSnapshot(record, row.next_allowed_at, row.retry_after_until)

    def _update(self, host: str, **values: object) -> None:
        for attempt in (1, 2):
            with self._factory() as db:
                row = db.get(RadarHostState, host)
                if row is None:
                    row = RadarHostState(host=host)
                    db.add(row)
                for name, value in values.items():
                    setattr(row, name, value)
                try:
                    db.commit()
                    return
                except IntegrityError:  # another worker created the row first: retry as an update
                    db.rollback()
                    if attempt == 2:
                        raise

    def save_robots(self, host: str, record: robots.RobotsRecord, crawl_delay: int | None) -> None:
        self._update(
            host,
            robots_fetched_at=record.fetched_at,
            robots_status=record.status,
            robots_txt=(record.body or "")[:MAX_ROBOTS_BYTES] if record.body is not None else None,
            crawl_delay_seconds=crawl_delay,
        )

    def schedule_next(self, host: str, when: datetime) -> None:
        self._update(host, next_allowed_at=when)

    def set_retry_after(self, host: str, until: datetime) -> None:
        self._update(host, retry_after_until=until)


# ------------------------------------------------------------------ results


@dataclass
class FetchResult:
    status: str  # a FetchStatus value
    http_status: int | None = None
    body: bytes = b""
    content_type: str | None = None
    final_url: str = ""
    etag: str | None = None
    last_modified: str | None = None
    requests: int = 0
    retry_after_seconds: int | None = None
    robots: str | None = None  # allowed | disallowed | missing | unreachable | override
    error: str | None = None
    crawl_delay_seconds: int | None = None
    redirects: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == FetchStatus.OK.value


@dataclass
class _Raw:
    """One HTTP exchange, before interpretation."""

    status_code: int | None = None
    headers: httpx.Headers | None = None
    body: bytes = b""
    error_status: str | None = None  # set when no usable response arrived
    error: str | None = None


# ------------------------------------------------------------------ the fetcher


class RadarFetcher:
    def __init__(
        self,
        *,
        store: DbHostStore,
        user_agent: str,
        clock: Callable[[], datetime] = utcnow,
        policy: netguard.Policy | None = None,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
        on_request: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.before_request = before_request
        self.on_request = on_request
        if transport is None:
            if offline():
                transport = _OfflineTransport()
            else:
                transport = (
                    netguard.GuardedTransport(policy) if policy is not None else transport_factory()
                )
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
        )
        self.requests = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RadarFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------------- one request

    def _send(self, url: str, headers: dict[str, str], max_bytes: int) -> _Raw:
        """GET ``url`` once. Never raises for network trouble (it is a result)."""
        if self.before_request is not None:
            self.before_request()  # may raise FetchAborted / BudgetExhausted
        self.requests += 1
        deadline = monotonic() + REQUEST_DEADLINE
        raw = _Raw()
        try:
            with self._client.stream("GET", url, headers=headers) as response:
                raw.status_code = response.status_code
                raw.headers = response.headers
                if response.status_code in REDIRECT_STATUSES or response.status_code >= 300:
                    return raw  # no body is read for a redirect, a 304 or an error
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raw.error_status = FetchStatus.TOO_LARGE.value
                        raw.error = f"response larger than {max_bytes} bytes"
                        return raw
                    if monotonic() > deadline:
                        raw.error_status = FetchStatus.TIMEOUT.value
                        raw.error = f"no complete response within {REQUEST_DEADLINE:.0f} s"
                        return raw
                    chunks.append(chunk)
                raw.body = b"".join(chunks)
        except netguard.BlockedDestination as exc:
            raw.error_status = FetchStatus.BLOCKED_DESTINATION.value
            raw.error = f"destination refused: {exc.reason}"
        except httpx.TimeoutException:
            raw.error_status = FetchStatus.TIMEOUT.value
            raw.error = "request timed out"
        except httpx.HTTPError as exc:
            raw.error_status = FetchStatus.NETWORK_ERROR.value
            raw.error = f"request failed ({type(exc).__name__})"
        finally:
            if self.on_request is not None:
                self.on_request()
        return raw

    # -------------------------------------------------------------- robots

    def _robots_record(self, origin_url: str, host: str) -> robots.RobotsRecord:
        now = self.clock()
        snapshot = self.store.load(host)
        if robots.is_fresh(snapshot.robots, now):
            assert snapshot.robots is not None
            return snapshot.robots
        parts = urlsplit(origin_url)
        url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        record: robots.RobotsRecord | None = None
        for _ in range(MAX_REDIRECTS + 1):
            raw = self._send(url, {"Accept": "text/plain, */*;q=0.1"}, MAX_ROBOTS_BYTES)
            if raw.error_status == FetchStatus.BLOCKED_DESTINATION.value:
                raise _Blocked(raw.error or "destination refused")
            if raw.error_status == FetchStatus.TOO_LARGE.value:
                # RFC 9309: parse at least 500 KiB; a file over our cap is cut, not refused.
                record = robots.RobotsRecord(robots.UNREACHABLE, None, self.clock())
                break
            if raw.status_code in REDIRECT_STATUSES and raw.headers is not None:
                location = raw.headers.get("location")
                if not location:
                    record = robots.record_from_response(None, None, self.clock())
                    break
                nxt = urljoin(url, location)
                if urlsplit(url).scheme == "https" and urlsplit(nxt).scheme != "https":
                    record = robots.record_from_response(None, None, self.clock())
                    break
                url = nxt
                continue
            record = robots.record_from_response(raw.status_code, raw.body, self.clock())
            break
        if record is None:  # too many redirects
            record = robots.record_from_response(None, None, self.clock())
        crawl = None
        if record.status == robots.OK:
            crawl = robots.evaluate(record, "/").crawl_delay_seconds
        self.store.save_robots(host, record, crawl)
        return record

    # -------------------------------------------------------------- host politeness

    def _wait_for(self, host: str) -> None:
        snapshot = self.store.load(host)
        now = self.clock()
        for until in (snapshot.retry_after_until, snapshot.next_allowed_at):
            if until is None or until <= now:
                continue
            wait = (until - now).total_seconds()
            if wait > MAX_SLEEP_SECONDS:
                raise HostBusy(host, until)
            sleep(wait)
            now = self.clock()

    def _after_content_request(self, host: str, crawl_delay: int | None) -> None:
        gap = max(spacing_for(host), float(crawl_delay or 0))
        self.store.schedule_next(host, self.clock() + timedelta(seconds=gap))

    # -------------------------------------------------------------- fetch

    def fetch(
        self,
        url: str,
        *,
        accept: str = FEED_ACCEPT,
        max_bytes: int = MAX_FEED_BYTES,
        allowed_types: tuple[str, ...] | None = FEED_TYPES,
        etag: str | None = None,
        last_modified: str | None = None,
        robots_override: bool = False,
    ) -> FetchResult:
        """GET ``url`` (and robots.txt first). Raises :class:`HostBusy`, :class:`FetchAborted` and
        :class:`BudgetExhausted`; everything else is in the result."""
        result = FetchResult(status=FetchStatus.CONFIG_ERROR.value, final_url=url)
        start_requests = self.requests
        current = url
        try:
            for hop in range(MAX_REDIRECTS + 1):
                parts = urlsplit(current)
                if parts.scheme not in ("http", "https") or not parts.hostname:
                    result.status = FetchStatus.BLOCKED_DESTINATION.value
                    result.error = "not an http(s) URL"
                    return result
                host = host_key(current)
                result.final_url = current

                crawl_delay: int | None = None
                if robots_override:
                    result.robots = "override"
                else:
                    try:
                        record = self._robots_record(current, host)
                    except _Blocked as blocked:
                        result.status = FetchStatus.BLOCKED_DESTINATION.value
                        result.error = str(blocked)
                        return result
                    path = parts.path or "/"
                    if parts.query:
                        path = f"{path}?{parts.query}"
                    verdict = robots.evaluate(record, path)
                    result.robots = verdict.reason
                    crawl_delay = verdict.crawl_delay_seconds
                    result.crawl_delay_seconds = crawl_delay
                    if not verdict.allowed:
                        result.status = FetchStatus.ROBOTS_DISALLOWED.value
                        result.error = (
                            "robots.txt could not be read"
                            if verdict.reason == robots.UNREACHABLE
                            else "robots.txt disallows this path"
                        )
                        return result

                self._wait_for(host)
                headers = {"Accept": accept}
                if hop == 0 and etag:
                    headers["If-None-Match"] = etag
                if hop == 0 and last_modified:
                    headers["If-Modified-Since"] = last_modified
                raw = self._send(current, headers, max_bytes)
                self._after_content_request(host, crawl_delay)

                if raw.error_status is not None:
                    result.status = raw.error_status
                    result.error = raw.error
                    return result
                assert raw.status_code is not None and raw.headers is not None
                code = raw.status_code
                result.http_status = code
                now = self.clock()

                if code in REDIRECT_STATUSES:
                    location = raw.headers.get("location")
                    if not location:
                        result.status = FetchStatus.REDIRECT_REFUSED.value
                        result.error = f"HTTP {code} without a Location"
                        return result
                    nxt = urljoin(current, location)
                    if parts.scheme == "https" and urlsplit(nxt).scheme != "https":
                        result.status = FetchStatus.REDIRECT_REFUSED.value
                        result.error = "redirect from https to http refused"
                        return result
                    if hop >= MAX_REDIRECTS:
                        result.status = FetchStatus.REDIRECT_REFUSED.value
                        result.error = f"more than {MAX_REDIRECTS} redirects"
                        return result
                    result.redirects.append(nxt)
                    current = nxt
                    continue
                if code == 304:
                    result.status = FetchStatus.NOT_MODIFIED.value
                    result.etag = etag
                    result.last_modified = last_modified
                    return result
                rate_limited = code in (429, 503) or (
                    code == 403 and raw.headers.get("x-ratelimit-remaining") == "0"
                )
                if rate_limited:
                    seconds = parse_retry_after(raw.headers.get("retry-after"), now)
                    if seconds is None and code == 403:
                        reset = raw.headers.get("x-ratelimit-reset", "")
                        if reset.isdigit():
                            seconds = max(
                                0, min(int(reset) - int(now.timestamp()), MAX_RETRY_AFTER_SECONDS)
                            )
                    if seconds is None:
                        seconds = DEFAULT_RATE_LIMITED_SECONDS.get(code, 900)
                    self.store.set_retry_after(host, now + timedelta(seconds=seconds))
                    result.status = FetchStatus.RATE_LIMITED.value
                    result.retry_after_seconds = seconds
                    result.error = f"HTTP {code}; asked to wait {seconds} s"
                    return result
                if 400 <= code < 500:
                    result.status = FetchStatus.HTTP_4XX.value
                    result.error = f"HTTP {code}"
                    return result
                if code >= 500:
                    result.status = FetchStatus.HTTP_5XX.value
                    result.error = f"HTTP {code}"
                    return result
                content_type = raw.headers.get("content-type")
                result.content_type = content_type
                if not type_allowed(content_type, allowed_types):
                    media = (content_type or "").split(";", 1)[0].strip()[:60]
                    result.status = FetchStatus.CONTENT_TYPE.value
                    result.error = f"unexpected content type {media!r}"
                    return result
                result.status = FetchStatus.OK.value
                result.body = raw.body
                result.etag = raw.headers.get("etag")
                result.last_modified = raw.headers.get("last-modified")
                return result
            result.status = FetchStatus.REDIRECT_REFUSED.value  # pragma: no cover
            return result
        finally:
            result.requests = self.requests - start_requests
