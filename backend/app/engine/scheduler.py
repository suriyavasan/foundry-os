from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.engine import recovery
from app.engine.context import EngineContext
from app.engine.dispatcher import DispatchReport, dispatch_tick
from app.guardrails import ratelimit
from app.radar import ENQUEUE_INTERVAL_SECONDS as RADAR_ENQUEUE_INTERVAL_SECONDS  # M16 radar
from app.radar import jobs as radar_jobs  # M16 radar
from app.services import (
    approvals,  # M18 deadline sweep
    autonomy_watchdog,
    card_reconcile,
    command_center,
    emergency,
    events,
    human_work,  # M22 overdue human work reminders
    jobs,
    meetings,  # M21 agent meetings
    purchasing,
    questions,  # M18 deadline sweep
    repo_sync,
    schedules,
    subscriptions,
    wakes,
)

logger = logging.getLogger("company_os.scheduler")


def expire_purchases(db, *, now: datetime) -> int:
    """M15: per company with open purchase requests, ``purchasing.expire_stale`` (undecided
    approvals, unpaid cards past their TTL, issuance retries). One company's failure never stops
    the others; each company commits on its own."""
    from sqlalchemy import select

    from app.models.company import Company
    from app.models.enums import TERMINAL_PURCHASE_STATUSES, CompanyStatus
    from app.models.purchasing import PurchaseRequest

    company_ids = list(
        db.scalars(
            select(PurchaseRequest.company_id)
            .join(Company, Company.id == PurchaseRequest.company_id)
            .where(
                Company.status == CompanyStatus.ACTIVE,
                PurchaseRequest.status.not_in([s.value for s in TERMINAL_PURCHASE_STATUSES]),
            )
            .distinct()
        )
    )
    expired = 0
    for company_id in company_ids:
        try:
            expired += purchasing.expire_stale(db, company_id, now=now)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("purchase expiry failed for company %s", company_id)
    return expired


TICK_SECONDS = 2.0
PRUNE_INTERVAL_SECONDS = 3600
WATCHDOG_INTERVAL_SECONDS = autonomy_watchdog.INTERVAL_SECONDS
DEADLINE_SWEEP_SECONDS = 60  # M18: approval and founder-question deadlines
# M15 agent cards: three deterministic passes, skipped during the emergency stop.
CARD_SYNC_INTERVAL_SECONDS = 15 * 60
CARD_BALANCE_INTERVAL_SECONDS = 60 * 60
SUBSCRIPTION_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass
class TickReport:
    fired_task_ids: list[str] = field(default_factory=list)
    woke_task_ids: list[str] = field(default_factory=list)
    dispatch: DispatchReport = field(default_factory=DispatchReport)
    recovery: recovery.RecoveryReport | None = None
    watchdog: dict[str, int] = field(default_factory=dict)  # interventions per detector (M12)
    repo_syncs: int = 0  # repository syncs enqueued this tick (M14)
    cards: dict[str, int] = field(default_factory=dict)  # M15: card passes that ran this tick
    radar_jobs: int = 0  # M16 radar: radar_fetch jobs enqueued this tick
    deadlines: dict[str, int] = field(default_factory=dict)  # M18: approvals / questions swept
    meetings: dict[str, int] = field(default_factory=dict)  # M21: meetings started / resumed


class Scheduler:
    """One ``tick`` = fire due schedules, dispatch ready tasks, run the autonomy watchdog (every
    60 s) and recover (every 30 s)."""

    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx
        self._last_recovery: datetime | None = None
        self._last_prune: datetime | None = None
        self._last_watchdog: datetime | None = None
        self._last_repo_sync: datetime | None = None
        self._last_card_sync: datetime | None = None
        self._last_card_balance: datetime | None = None
        self._last_purchase_expiry: datetime | None = None
        self._last_subscriptions: datetime | None = None
        self._last_radar_enqueue: datetime | None = None  # M16 radar
        self._last_deadline_sweep: datetime | None = None  # M18

    def start(self, now: datetime | None = None) -> recovery.RecoveryReport:
        """Startup recovery (also fires any schedule that came due while we were down)."""
        now = now or self.ctx.clock()
        self._last_recovery = now
        report = recovery.recover(self.ctx, now)
        if not self._stopped():
            schedules.fire_due(self.ctx.session_factory, now)
            wakes.materialize_due(self.ctx.session_factory, now)
        return report

    def _stopped(self) -> bool:
        """The emergency stop is active: no schedules fire, no wakes, no dispatch."""
        with self.ctx.session_factory() as db:
            return emergency.is_active(db)

    def tick(self, now: datetime | None = None) -> TickReport:
        now = now or self.ctx.clock()
        report = TickReport()
        if not self._stopped():
            report.fired_task_ids = schedules.fire_due(self.ctx.session_factory, now)
            try:  # before the wakes: a retry joins the request that is already waiting
                command_center.sweep(self.ctx.session_factory, now)
            except Exception:  # a sweep problem must not stop dispatching
                logger.exception("command center sweep failed")
            if self._last_watchdog is None or now - self._last_watchdog >= timedelta(
                seconds=WATCHDOG_INTERVAL_SECONDS
            ):
                self._last_watchdog = now
                try:  # before the wakes: its wake requests materialize in this very tick
                    report.watchdog = autonomy_watchdog.run_all(self.ctx.session_factory, now)
                except Exception:  # the watchdog must never stop dispatching
                    logger.exception("autonomy watchdog failed")
            if self._last_repo_sync is None or now - self._last_repo_sync >= timedelta(
                seconds=self.ctx.settings.repo_sync_tick_seconds
            ):
                self._last_repo_sync = now
                try:  # M14: repository syncs that came due become jobs for the worker
                    report.repo_syncs = repo_sync.enqueue_due(self.ctx.session_factory, now)
                except Exception:  # a sync problem must not stop dispatching
                    logger.exception("repository sync enqueue failed")
            self._card_passes(now, report)
            if self._last_radar_enqueue is None or now - self._last_radar_enqueue >= timedelta(
                seconds=RADAR_ENQUEUE_INTERVAL_SECONDS
            ):  # M16 radar: due sources become a job for the worker; no network in the tick
                self._last_radar_enqueue = now
                try:
                    report.radar_jobs = radar_jobs.enqueue_due(self.ctx.session_factory, now)
                except Exception:  # a radar problem must not stop dispatching
                    logger.exception("radar fetch enqueue failed")
            if self._last_deadline_sweep is None or now - self._last_deadline_sweep >= timedelta(
                seconds=DEADLINE_SWEEP_SECONDS
            ):  # M18: before the wakes, so the agents it wakes are materialized in this tick
                self._last_deadline_sweep = now
                try:
                    report.deadlines = self.deadline_sweep(now)
                except Exception:  # a sweep problem must not stop dispatching
                    logger.exception("deadline sweep failed")
            try:  # M21: start / resume meeting graphs (short writes, no model work)
                report.meetings = meetings.advance(self.ctx.session_factory, now)
            except Exception:  # a meeting problem must not stop dispatching
                logger.exception("meeting sweep failed")
            report.woke_task_ids = wakes.materialize_due(self.ctx.session_factory, now)
            report.dispatch = dispatch_tick(self.ctx, now)
        if self._last_recovery is None or now - self._last_recovery >= timedelta(
            seconds=recovery.RECOVERY_INTERVAL_SECONDS
        ):
            self._last_recovery = now
            report.recovery = recovery.recover(self.ctx, now)
        if self._last_prune is None or now - self._last_prune >= timedelta(
            seconds=PRUNE_INTERVAL_SECONDS
        ):
            self._last_prune = now
            self.prune(now)
        return report

    def deadline_sweep(self, now: datetime) -> dict[str, int]:
        """M18: pending approvals past their deadline (Co-founder -> a person, once; a person's ->
        declined) and open founder questions past theirs (the agent's default stands). Bounded
        batches; each item commits on its own. Timeouts never approve anything."""
        with self.ctx.session_factory() as db:
            result = approvals.sweep_deadlines(db, now)
        with self.ctx.session_factory() as db:
            result["questions_defaulted"] = questions.sweep_deadlines(db, now)
        with self.ctx.session_factory() as db:  # M22: a reminder for human work 24 h overdue
            result["human_work_reminded"] = human_work.sweep_reminders(db, now)
        return result

    def _card_passes(self, now: datetime, report: TickReport) -> None:
        """Card sync (15 min), balance check (hourly) and subscription check (daily). Each runs in
        its own session and try/except: a provider outage or a bug must never break the tick, and
        what a pass did is committed even when a later one fails. Before the wakes, so the wake
        requests they make materialize in this very tick."""
        for name, attr, seconds, run in (
            ("sync", "_last_card_sync", CARD_SYNC_INTERVAL_SECONDS, card_reconcile.run_all),
            ("purchases", "_last_purchase_expiry", CARD_SYNC_INTERVAL_SECONDS, expire_purchases),
            (
                "balance",
                "_last_card_balance",
                CARD_BALANCE_INTERVAL_SECONDS,
                card_reconcile.check_balances,
            ),
            (
                "subscriptions",
                "_last_subscriptions",
                SUBSCRIPTION_INTERVAL_SECONDS,
                lambda db, *, now: subscriptions.check(db, now=now),
            ),
        ):
            last = getattr(self, attr)
            if last is not None and now - last < timedelta(seconds=seconds):
                continue
            setattr(self, attr, now)
            try:
                with self.ctx.session_factory() as db:
                    report.cards[name] = run(db, now=now)
                    db.commit()
            except Exception:
                logger.exception("card %s pass failed", name)

    def prune(self, now: datetime) -> None:
        with self.ctx.session_factory() as db:
            pruned_events = events.prune(db, now)
            pruned_jobs = jobs.prune_finished(db, now)
            pruned_hits = ratelimit.prune(db, now)
            db.commit()
        if pruned_events or pruned_jobs or pruned_hits:
            logger.info(
                "pruned %s events, %s jobs, %s rate-limit hits",
                pruned_events,
                pruned_jobs,
                pruned_hits,
            )

    def run_forever(self, stop: threading.Event, interval: float = TICK_SECONDS) -> None:
        try:
            self.start()
        except Exception:
            logger.exception("scheduler startup recovery failed")
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            stop.wait(interval)
