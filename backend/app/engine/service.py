"""Runs the scheduler and worker loops in background threads (CLI process or embedded in the
API process via ``COMPANY_OS_EMBEDDED_WORKER=1``)."""

from __future__ import annotations

import logging
import threading

from app.engine.context import EngineContext
from app.engine.scheduler import Scheduler
from app.engine.worker import SHUTDOWN_GRACE_SECONDS, Worker

logger = logging.getLogger("company_os.engine")


class EngineService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx
        self.scheduler = Scheduler(ctx)
        self.worker = Worker(ctx)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for name, target in (
            (
                "scheduler",
                lambda: self.scheduler.run_forever(
                    self._stop, self.ctx.settings.engine_tick_seconds
                ),
            ),
            (
                "worker",
                lambda: self.worker.run_forever(self._stop, self.ctx.settings.engine_poll_seconds),
            ),
        ):
            thread = threading.Thread(target=target, name=f"engine-{name}", daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("engine started (worker id %s)", self.worker.worker_id)

    def stop(self, timeout: float = SHUTDOWN_GRACE_SECONDS + 2) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        logger.info("engine stopped")

    def wait(self) -> None:
        self._stop.wait()

    def request_stop(self) -> None:
        self._stop.set()
