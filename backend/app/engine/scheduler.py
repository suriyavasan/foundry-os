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
    command_center,
    emergency,
    events,
    jobs,
    schedules,
    wakes,
)

logger = logging.getLogger("company_os.scheduler")

TICK_SECONDS = 2.0
PRUNE_INTERVAL_SECONDS = 3600
WATCHDOG_INTERVAL_SECONDS = autonomy_watchdog.INTERVAL_SECONDS


@dataclass
class TickReport:
    fired_task_ids: list[str] = field(default_factory=list)
    woke_task_ids: list[str] = field(default_factory=list)
    dispatch: DispatchReport = field(default_factory=DispatchReport)
    recovery: recovery.RecoveryReport | None = None
    watchdog: dict[str, int] = field(default_factory=dict)  # interventions per detector (M12)


class Scheduler:
    """One ``tick`` = fire due schedules, dispatch ready tasks, run the autonomy watchdog (every
    60 s) and recover (every 30 s)."""

    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx
        self._last_recovery: datetime | None = None
        self._last_prune: datetime | None = None
        self._last_watchdog: datetime | None = None

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
