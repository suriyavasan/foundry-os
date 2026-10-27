from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.engine import recovery
from app.engine.context import EngineContext
from app.engine.dispatcher import DispatchReport, dispatch_tick
from app.guardrails import ratelimit
from app.services import (
    autonomy_watchdog,
    card_reconcile,
    command_center,
    emergency,
    events,
    jobs,
    purchasing,
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
    cards: dict[str, int] = field(default_factory=dict)  # M15: card passes that ran this tick


class Scheduler:
    """One ``tick`` = fire due schedules, dispatch ready tasks, run the autonomy watchdog (every
    60 s) and recover (every 30 s)."""

    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx
        self._last_recovery: datetime | None = None
        self._last_prune: datetime | None = None
        self._last_watchdog: datetime | None = None
        self._last_card_sync: datetime | None = None
        self._last_card_balance: datetime | None = None
        self._last_purchase_expiry: datetime | None = None
        self._last_subscriptions: datetime | None = None

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
            self._card_passes(now, report)
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
