"""Scheduling and the ``radar_fetch`` job.

The scheduler tick only **enqueues** (``enqueue_due``): no network in the tick. The worker runs the
job (``run_job``): up to 25 due sources, earliest first, one at a time, each in its own transaction
so one source's failure never loses another's items.

Stop conditions are checked before *every* request: the company paused, the radar switched off, the
emergency stop, and the company's daily request budget (``radar_max_fetches_per_day``, counted in
``rate_limit_hits``). Stopping is quiet: the job ends normally and the remaining sources stay due.

Failure accounting per source: ``consecutive_failures`` backs the next attempt off to
``min(24 h, interval * 2^n)``; ten failures in a row disable the source (``disabled_reason =
failing``) with an info alert. A robots.txt that disallows the path disables the source at once
(``robots_disallowed``). A rate limit (429/503, GitHub's exhausted quota) is not a failure: the next
attempt waits for ``Retry-After``.
"""

from __future__ import annotations

import logging
import threading
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import APP_VERSION
from app.core.time import utcnow
from app.engine.context import EngineContext
from app.guardrails import ratelimit
from app.models.company import Company
from app.models.enums import AlertSeverity, CompanyStatus
from app.models.policy import CompanyPolicy
from app.models.radar import DisabledReason, FetchStatus, RadarKeyword, RadarSource
from app.models.run import Job
from app.radar import (
    DISABLE_AFTER_FAILURES,
    DUE_SOURCES_PER_JOB,
    ENQUEUE_INTERVAL_SECONDS,
    JOB_KIND,
    MAX_BACKOFF_SECONDS,
    MAX_QUERIES_PER_SOURCE,
    RADAR_FETCHES_KEY,
    offline,
)
from app.radar import adapters as adapter_registry
from app.radar import fetcher as fetch_mod
from app.radar import ingest as ingest_mod
from app.radar.matching import KeywordRef
from app.radar.parsers import NeedsToken, ParseError, RawEntry
from app.services import ai_setup, alerts, audit, emergency, jobs, policy

logger = logging.getLogger("company_os.radar")

LEASE_EXTEND_SECONDS = 20
S = FetchStatus


# ------------------------------------------------------------------ plain data


@dataclass(frozen=True)
class SourceSpec:
    """A source as plain data, so the network code never holds an ORM row."""

    id: str | None
    slug: str
    category: str
    adapter: str
    fetch_url: str | None
    query_mode: str
    template_values: dict[str, str]
    etag: str | None = None
    last_modified: str | None = None
    last_fetched_at: datetime | None = None
    robots_override: bool = False

    @classmethod
    def of(cls, source: RadarSource) -> SourceSpec:
        return cls(
            id=source.id,
            slug=source.slug,
            category=source.category,
            adapter=source.adapter,
            fetch_url=source.fetch_url,
            query_mode=source.query_mode,
            template_values={str(k): str(v) for k, v in (source.template_values or {}).items()},
            etag=source.etag,
            last_modified=source.last_modified,
            last_fetched_at=source.last_fetched_at,
            robots_override=bool(source.robots_override),
        )


@dataclass
class Collected:
    """What fetching one source produced (nothing stored yet)."""

    batches: list[tuple[str | None, list[RawEntry]]] = field(default_factory=list)
    status: str = S.OK.value
    error: str | None = None
    http_status: int | None = None
    requests: int = 0
    ok_requests: int = 0
    etag: str | None = None
    last_modified: str | None = None
    retry_after_seconds: int | None = None
    backoff_seconds: int | None = None
    robots: str | None = None
    deferred_until: datetime | None = None  # the host cannot be asked yet: nothing was fetched
    stopped: str | None = None  # "aborted" | "budget": the whole job must stop


@dataclass
class JobReport:
    company_id: str
    sources: int = 0
    requests: int = 0
    new_items: int = 0
    flagged: int = 0
    errors: int = 0
    deferred: int = 0
    disabled: int = 0
    stopped: str | None = None
    by_status: dict[str, int] = field(default_factory=dict)

    def as_data(self) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "requests": self.requests,
            "new_items": self.new_items,
            "flagged": self.flagged,
            "errors": self.errors,
            "deferred": self.deferred,
            "disabled": self.disabled,
            "stopped": self.stopped,
            "by_status": dict(self.by_status),
        }


# ------------------------------------------------------------------ gates


def company_may_fetch(db: Session, company_id: str) -> bool:
    """Active company, radar on, no emergency stop."""
    if emergency.is_active(db):
        return False
    company = db.get(Company, company_id)
    if company is None or company.status != CompanyStatus.ACTIVE:
        return False
    row = db.scalar(select(CompanyPolicy).where(CompanyPolicy.company_id == company_id))
    return bool(row is not None and row.radar_enabled)


def daily_fetches_used(db: Session, company_id: str, now: datetime) -> int:
    return ratelimit.window_for(db, company_id, RADAR_FETCHES_KEY, now, None).used


def make_gates(
    session_factory: sessionmaker[Session],
    company_id: str,
    clock: Callable[[], datetime],
    *,
    require_enabled: bool = True,
) -> tuple[Callable[[], None], Callable[[], None]]:
    """``(before_request, on_request)`` for a :class:`RadarFetcher` working for ``company_id``."""

    def before() -> None:
        with session_factory() as db:
            if require_enabled and not company_may_fetch(db, company_id):
                raise fetch_mod.FetchAborted("company paused, radar off or emergency stop")
            limits = policy.load_policy(db, company_id)
            if daily_fetches_used(db, company_id, clock()) >= int(limits.radar_max_fetches_per_day):
                raise fetch_mod.BudgetExhausted("daily radar request budget spent")

    def after() -> None:
        with session_factory() as db:
            ratelimit.record(db, company_id, None, (RADAR_FETCHES_KEY,), clock())
            db.commit()

    return before, after


def make_fetcher(
    session_factory: sessionmaker[Session],
    company_id: str,
    clock: Callable[[], datetime],
    *,
    contact_email: str | None,
    require_enabled: bool = True,
) -> fetch_mod.RadarFetcher:
    before, after = make_gates(session_factory, company_id, clock, require_enabled=require_enabled)
    return fetch_mod.RadarFetcher(
        store=fetch_mod.DbHostStore(session_factory),
        user_agent=fetch_mod.user_agent(APP_VERSION, contact_email),
        clock=clock,
        before_request=before,
        on_request=after,
    )


# ------------------------------------------------------------------ collecting one source


def collect(
    fetcher: fetch_mod.RadarFetcher,
    spec: SourceSpec,
    terms: Sequence[str],
    now: datetime,
    *,
    first_only: bool = False,
) -> Collected:
    """Fetch and parse the URL(s) of one source. Never raises: the outcome is in the result."""
    out = Collected()
    adapter = adapter_registry.get(spec.adapter)
    if adapter.requires_token:
        out.status = S.NEEDS_TOKEN.value
        out.error = "this source needs an API token that is not stored"
        return out
    if not adapter.fetchable:
        out.status = S.CONFIG_ERROR.value
        out.error = "search-only sources are never fetched"
        return out
    since = now - timedelta(days=2)
    if spec.last_fetched_at is not None:
        since = max(spec.last_fetched_at - timedelta(hours=1), now - timedelta(days=7))
    plan = adapter_registry.build_urls(
        spec.fetch_url,
        per_keyword=spec.query_mode == "per_keyword",
        terms=list(terms),
        values=spec.template_values,
        now=now,
        since=since,
        adapter=adapter,
    )
    if plan.error:
        out.status = S.CONFIG_ERROR.value
        out.error = plan.error
        return out
    if plan.no_keywords:
        out.status = S.NO_KEYWORDS.value
        out.error = "no enabled keyword to search for"
        return out
    accept = "application/json" if adapter.is_json else fetch_mod.FEED_ACCEPT
    allowed = fetch_mod.JSON_TYPES if adapter.is_json else fetch_mod.FEED_TYPES
    single = len(plan.urls) == 1
    ctx = adapter_registry.ParseContext(
        category=spec.category, slug=spec.slug, now=now, fetch_url=spec.fetch_url or ""
    )
    failure: Collected | None = None
    for index, url in enumerate(plan.urls):
        if first_only and index:
            break
        term = plan.terms[index] if index < len(plan.terms) else None
        try:
            result = fetcher.fetch(
                url,
                accept=accept,
                allowed_types=allowed,
                etag=spec.etag if single else None,
                last_modified=spec.last_modified if single else None,
                robots_override=spec.robots_override,
            )
        except fetch_mod.HostBusy as busy:
            if out.requests == 0 and not out.batches and failure is None:
                out.deferred_until = busy.until
                out.status = S.RATE_LIMITED.value
                out.error = f"{busy.host} cannot be asked before {busy.until:%H:%M:%S} UTC"
            break
        except fetch_mod.FetchAborted:
            out.stopped = "aborted"
            break
        except fetch_mod.BudgetExhausted:
            out.stopped = "budget"
            break
        out.requests += result.requests
        out.robots = result.robots or out.robots
        out.http_status = result.http_status
        if result.status == S.NOT_MODIFIED.value:
            out.ok_requests += 1
            out.etag, out.last_modified = result.etag, result.last_modified
            continue
        if not result.ok:
            f = Collected(
                status=result.status,
                error=result.error,
                http_status=result.http_status,
                retry_after_seconds=result.retry_after_seconds,
                robots=result.robots,
            )
            failure = f
            if result.status in (S.ROBOTS_DISALLOWED.value, S.BLOCKED_DESTINATION.value):
                break  # the same host again would fail the same way
            if result.status == S.RATE_LIMITED.value:
                break
            continue
        try:
            parsed = adapter.parse(result.body, ctx)
        except NeedsToken:
            failure = Collected(status=S.NEEDS_TOKEN.value, error="needs an API token")
            break
        except ParseError as exc:
            failure = Collected(status=S.PARSE_ERROR.value, error=str(exc)[:300])
            continue
        out.ok_requests += 1
        out.batches.append((term, parsed.entries))
        if single:
            out.etag, out.last_modified = result.etag, result.last_modified
        if parsed.backoff_seconds:
            out.backoff_seconds = max(out.backoff_seconds or 0, parsed.backoff_seconds)
    if out.deferred_until is not None or out.stopped:
        return out
    if out.ok_requests:
        out.status = (
            S.NOT_MODIFIED.value if not out.batches else S.OK.value
        )  # 304 only: nothing to parse
        out.error = None
    elif failure is not None:
        out.status, out.error = failure.status, failure.error
        out.http_status = failure.http_status or out.http_status
        out.retry_after_seconds = failure.retry_after_seconds
        out.robots = failure.robots or out.robots
    return out


# ------------------------------------------------------------------ one source, stored


def _jitter(slug: str, interval: int) -> int:
    """A stable per-source spread of up to 10% so sources do not all come due in the same minute."""
    return int((zlib.crc32(slug.encode()) % 1000) / 1000 * 0.1 * interval)


def _disable(
    db: Session, source: RadarSource, reason: DisabledReason, title: str, now: datetime
) -> None:
    source.enabled = False
    source.disabled_reason = reason.value
    audit.record(
        db,
        company_id=source.company_id,
        principal=None,
        action="radar.source.disabled",
        target_type="radar_source",
        target_id=source.id,
        summary=f"Radar source {source.name} was switched off: {reason.value}",
        data={"reason": reason.value, "slug": source.slug},
    )
    alerts.raise_alert(
        db,
        source.company_id,
        kind="radar_source",
        severity=AlertSeverity.INFO,
        title=title,
        body=source.last_error,
        related_type="radar_source",
        related_id=source.id,
        dedupe_key=f"radar_source:{source.id}:{reason.value}",
        wake_founder=False,
    )


def apply_outcome(db: Session, source: RadarSource, collected: Collected, now: datetime) -> bool:
    """Record the result of a fetch on the source. Returns True when the source was disabled."""
    status = collected.status
    source.last_fetched_at = now
    source.last_status = status
    source.last_error = (collected.error or None) and collected.error[:300]
    interval = int(source.interval_seconds)
    disabled = False
    if status in (S.OK.value, S.NOT_MODIFIED.value):
        source.consecutive_failures = 0
        source.next_fetch_at = now + timedelta(seconds=interval + _jitter(source.slug, interval))
        if collected.etag is not None or collected.last_modified is not None:
            source.etag = collected.etag
            source.last_modified = collected.last_modified
    elif status == S.NO_KEYWORDS.value:
        source.next_fetch_at = now + timedelta(seconds=interval)
    elif status == S.RATE_LIMITED.value:
        wait = collected.retry_after_seconds or interval
        source.next_fetch_at = now + timedelta(seconds=min(wait, MAX_BACKOFF_SECONDS))
    elif status == S.NEEDS_TOKEN.value:
        source.next_fetch_at = None
        _disable(db, source, DisabledReason.NEEDS_TOKEN, f"{source.name} needs an API token", now)
        disabled = True
    elif status == S.ROBOTS_DISALLOWED.value and collected.robots == "disallowed":
        source.next_fetch_at = None
        _disable(
            db,
            source,
            DisabledReason.ROBOTS_DISALLOWED,
            f"{source.name}: robots.txt does not allow the radar to read it",
            now,
        )
        disabled = True
    else:  # a failure (including a robots.txt that could not be read)
        source.consecutive_failures = int(source.consecutive_failures or 0) + 1
        backoff = min(MAX_BACKOFF_SECONDS, interval * 2 ** min(source.consecutive_failures, 10))
        source.next_fetch_at = now + timedelta(seconds=backoff)
        if source.consecutive_failures >= DISABLE_AFTER_FAILURES:
            _disable(
                db,
                source,
                DisabledReason.FAILING,
                f"{source.name} failed {source.consecutive_failures} times in a row",
                now,
            )
            disabled = True
    return disabled


def _keyword_refs(db: Session, company_id: str) -> list[RadarKeyword]:
    return list(
        db.scalars(
            select(RadarKeyword)
            .where(RadarKeyword.company_id == company_id, RadarKeyword.enabled.is_(True))
            .order_by(RadarKeyword.weight.desc(), RadarKeyword.term)
        )
    )


def fetch_source(
    session_factory: sessionmaker[Session],
    fetcher: fetch_mod.RadarFetcher,
    company_id: str,
    source_id: str,
    clock: Callable[[], datetime],
    report: JobReport,
) -> None:
    """Fetch one source and store the outcome and its items in one transaction."""
    with session_factory() as db:
        source = db.scalar(
            select(RadarSource).where(
                RadarSource.id == source_id, RadarSource.company_id == company_id
            )
        )
        if source is None or not source.enabled:
            return
        spec = SourceSpec.of(source)
        keywords = _keyword_refs(db, company_id)
        limits = policy.load_policy(db, company_id)
        terms = [k.term for k in keywords if k.kind != "negative"][:MAX_QUERIES_PER_SOURCE]
        refs = [KeywordRef(k.term, k.kind, k.weight) for k in keywords]
        db.rollback()  # no transaction held across the network
        now = clock()
        collected = collect(fetcher, spec, terms, now)

        if collected.stopped:
            report.stopped = collected.stopped
            return
        # reload: the row may have changed while we were on the network
        source = db.scalar(select(RadarSource).where(RadarSource.id == source_id))
        if source is None:
            return
        report.sources += 1
        if collected.deferred_until is not None:
            source.next_fetch_at = collected.deferred_until
            report.deferred += 1
            db.commit()
            return
        if collected.backoff_seconds and source.fetch_url:
            fetch_mod.DbHostStore(session_factory).set_retry_after(
                fetch_mod.host_key(source.fetch_url or ""),
                clock() + timedelta(seconds=collected.backoff_seconds),
            )
        if collected.batches:
            stats = ingest_mod.ingest(
                db,
                company_id=company_id,
                source=source,
                adapter=adapter_registry.get(source.adapter),
                batches=collected.batches,
                keywords=refs,
                policy=limits,
                now=now,
            )
            report.new_items += stats.new
            report.flagged += stats.flagged
        if apply_outcome(db, source, collected, clock()):
            report.disabled += 1
        if collected.status not in (
            S.OK.value,
            S.NOT_MODIFIED.value,
            S.NO_KEYWORDS.value,
            S.RATE_LIMITED.value,
        ):
            report.errors += 1
        report.by_status[collected.status] = report.by_status.get(collected.status, 0) + 1
        try:
            db.commit()
        except IntegrityError:
            # Another job stored the same item first (a "fetch now" overlapping a scheduled job):
            # nothing is lost, the next run sees the item as a duplicate.
            db.rollback()
            report.errors += 1
            logger.info("radar source %s: concurrent insert, skipped this round", source_id)


# ------------------------------------------------------------------ the job


def due_source_ids(
    db: Session,
    company_id: str,
    now: datetime,
    *,
    force: bool = False,
    limit: int = DUE_SOURCES_PER_JOB,
) -> list[str]:
    stmt = select(RadarSource.id).where(
        RadarSource.company_id == company_id,
        RadarSource.enabled.is_(True),
        RadarSource.adapter != "search_engine",
    )
    if not force:
        stmt = stmt.where(
            or_(RadarSource.next_fetch_at.is_(None), RadarSource.next_fetch_at <= now)
        )
    stmt = stmt.order_by(
        RadarSource.next_fetch_at.is_(None).desc(),
        RadarSource.next_fetch_at,
        RadarSource.created_at,
    ).limit(limit)
    return list(db.scalars(stmt))


def run_company(
    session_factory: sessionmaker[Session],
    company_id: str,
    clock: Callable[[], datetime],
    *,
    force: bool = False,
    fetcher: fetch_mod.RadarFetcher | None = None,
) -> JobReport:
    """Fetch the due sources of one company and write one ``radar.fetched`` audit event."""
    report = JobReport(company_id)
    now = clock()
    if offline():
        report.stopped = "offline"
        return report
    with session_factory() as db:
        if not company_may_fetch(db, company_id):
            report.stopped = "aborted"
            return report
        contact = policy.load_policy(db, company_id).radar_contact_email
        ids = due_source_ids(db, company_id, now, force=force)
    own = fetcher is None
    if fetcher is None:
        fetcher = make_fetcher(session_factory, company_id, clock, contact_email=contact)
    try:
        for source_id in ids:
            fetch_source(session_factory, fetcher, company_id, source_id, clock, report)
            if report.stopped:
                break
    finally:
        report.requests = fetcher.requests  # includes the request a stop refused to finish
        if own:
            fetcher.close()
    with session_factory() as db:
        pruned = ingest_mod.prune(db, clock())
        if report.sources or report.stopped:
            audit.record(
                db,
                company_id=company_id,
                principal=None,
                action="radar.fetched",
                target_type="radar",
                target_id=None,
                summary=(
                    f"Radar fetched {report.sources} source(s): {report.new_items} new item(s), "
                    f"{report.errors} error(s), {report.requests} request(s)"
                ),
                data={**report.as_data(), "pruned": pruned},
            )
        db.commit()
    return report


def run_job(ctx: EngineContext, job: jobs.LeasedJob, worker_id: str | None = None) -> None:
    """Worker handler of ``radar_fetch``, with a lease-extension thread: a job of 25 sequential
    sources can outlast ``LEASE_SECONDS``."""
    company_id = str(job.payload["company_id"])
    stop = threading.Event()

    def keep_lease() -> None:
        while not stop.wait(LEASE_EXTEND_SECONDS):
            try:
                with ctx.session_factory() as db:
                    jobs.extend_lease(db, job.id, worker_id or "", ctx.clock())
            except Exception:
                logger.warning("could not extend the lease of job %s", job.id, exc_info=True)

    thread = threading.Thread(target=keep_lease, name=f"radar-lease-{job.id[:8]}", daemon=True)
    if worker_id:
        thread.start()
    try:
        run_company(
            ctx.session_factory, company_id, ctx.clock, force=bool(job.payload.get("force"))
        )
    finally:
        stop.set()
        if worker_id:
            thread.join(timeout=2)


# ------------------------------------------------------------------ enqueueing


def _slot(now: datetime) -> int:
    return int(now.timestamp() // ENQUEUE_INTERVAL_SECONDS)


def _has_open_job(db: Session, company_id: str) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    Job.kind == JOB_KIND,
                    Job.company_id == company_id,
                    Job.status.in_(["queued", "leased"]),
                )
            )
        )
    )


def enqueue_due(session_factory: sessionmaker[Session], now: datetime | None = None) -> int:
    """Called by ``Scheduler.tick`` every 300 s: one ``radar_fetch`` job (``dedupe_key =
    radar:<company>:<5-minute slot>``) for each active, AI-ready company with the radar on and at
    least one enabled source whose ``next_fetch_at`` has passed. Returns the jobs enqueued."""
    now = now or utcnow()
    count = 0
    if offline():
        return 0
    with session_factory() as db:
        if emergency.is_active(db):
            return 0
        company_ids = list(
            db.scalars(
                select(Company.id)
                .join(CompanyPolicy, CompanyPolicy.company_id == Company.id)
                .where(
                    Company.status == CompanyStatus.ACTIVE, CompanyPolicy.radar_enabled.is_(True)
                )
                .order_by(Company.created_at)
            )
        )
        for company_id in company_ids:
            if not ai_setup.is_ready(db, company_id):
                continue
            if not due_source_ids(db, company_id, now, limit=1):
                continue
            if _has_open_job(db, company_id):
                continue
            key = f"radar:{company_id}:{_slot(now)}"
            before = db.scalar(select(Job.id).where(Job.dedupe_key == key))
            jobs.enqueue(
                db,
                kind=JOB_KIND,
                payload={"company_id": company_id},
                company_id=company_id,
                run_after=now,
                dedupe_key=key,
                max_attempts=3,
            )
            if before is None:
                count += 1
        db.commit()
    return count


def enqueue_now(db: Session, company_id: str, now: datetime) -> Job:
    """Owner "fetch now": a job that ignores ``next_fetch_at`` (host spacing still applies). The
    caller commits. A repeat within the same minute returns the job already queued."""
    return jobs.enqueue(
        db,
        kind=JOB_KIND,
        payload={"company_id": company_id, "force": True},
        company_id=company_id,
        run_after=now,
        dedupe_key=f"radar_now:{company_id}:{int(now.timestamp() // 60)}",
        max_attempts=3,
    )


__all__ = [
    "Collected",
    "JobReport",
    "SourceSpec",
    "apply_outcome",
    "collect",
    "company_may_fetch",
    "enqueue_due",
    "enqueue_now",
    "fetch_source",
    "make_fetcher",
    "run_company",
    "run_job",
]
