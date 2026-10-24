from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.engine import recovery
from app.engine.context import EngineContext
from app.engine.dispatcher import DispatchReport, dispatch_tick
from app.services import events, jobs, schedules, wakes

logger = logging.getLogger("company_os.scheduler")

TICK_SECONDS = 2.0
PRUNE_INTERVAL_SECONDS = 3600


@dataclass
class TickReport:
    fired_task_ids: list[str] = field(default_factory=list)
    woke_task_ids: list[str] = field(default_factory=list)
    dispatch: DispatchReport = field(default_factory=DispatchReport)
    recovery: recovery.RecoveryReport | None = None


class Scheduler:
    """One ``tick`` = fire due schedules, dispatch ready tasks, and (every 30 s) recover."""

    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx
        self._last_recovery: datetime | None = None
        self._last_prune: datetime | None = None

    def start(self, now: datetime | None = None) -> recovery.RecoveryReport:
        """Startup recovery (also fires any schedule that came due while we were down)."""
        now = now or self.ctx.clock()
        self._last_recovery = now
        report = recovery.recover(self.ctx, now)
        schedules.fire_due(self.ctx.session_factory, now)
        wakes.materialize_due(self.ctx.session_factory, now)
        return report

    def tick(self, now: datetime | None = None) -> TickReport:
        now = now or self.ctx.clock()
        report = TickReport()
        report.fired_task_ids = schedules.fire_due(self.ctx.session_factory, now)
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
            db.commit()
        if pruned_events or pruned_jobs:
            logger.info("pruned %s events, %s jobs", pruned_events, pruned_jobs)

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
