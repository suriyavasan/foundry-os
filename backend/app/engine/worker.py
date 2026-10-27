"""Worker: leases jobs from the durable queue and executes them."""

from __future__ import annotations

import logging
import os
import socket
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime

from app.engine.context import EngineContext
from app.engine.executor import RunExecutor
from app.services import jobs, repo_sync
from app.services import skills as skills_service
from app.services.companies import SEED_JOB_KIND

logger = logging.getLogger("company_os.worker")

JOB_KINDS = ("execute_run", SEED_JOB_KIND, *repo_sync.JOB_KINDS)
LEASE_SECONDS = 60
POLL_SECONDS = 0.5
SHUTDOWN_GRACE_SECONDS = 10.0


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident() % 10_000}"


class Worker:
    def __init__(
        self,
        ctx: EngineContext,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
        monitor_interval: float = 0.5,
        kinds: tuple[str, ...] = JOB_KINDS,
    ) -> None:
        self.ctx = ctx
        self.kinds = list(kinds)
        self.worker_id = worker_id or default_worker_id()
        self.concurrency = concurrency or ctx.settings.worker_concurrency
        self.shutdown = threading.Event()
        self.executor = RunExecutor(
            ctx, self.worker_id, monitor_interval=monitor_interval, shutdown=self.shutdown
        )

    # ------------------------------------------------------------ job handling

    def handle(self, job: jobs.LeasedJob) -> None:
        ctx = self.ctx
        try:
            if job.kind == "execute_run":
                self.executor.execute(str(job.payload["run_id"]), job.id)
            elif job.kind == SEED_JOB_KIND:
                self._seed_skill_library(str(job.payload["company_id"]))
            elif job.kind in repo_sync.JOB_KINDS:  # M14: clone / sync a GitHub-backed project
                repo_sync.run_job(ctx, job, self.worker_id)
            else:
                raise ValueError(f"unknown job kind {job.kind!r}")
        except Exception as exc:
            logger.error("job %s (%s) failed: %s", job.id, job.kind, type(exc).__name__)
            unknown = job.kind not in JOB_KINDS
            exhausted = jobs.LeasedJob(
                job.id,
                job.kind,
                job.payload,
                job.company_id,
                job.max_attempts if unknown else job.attempts,
                job.max_attempts,
            )
            with ctx.session_factory() as db:
                jobs.fail(
                    db, exhausted, self.worker_id, f"{type(exc).__name__}: {exc}", ctx.clock()
                )
            return
        with ctx.session_factory() as db:
            jobs.complete(db, job.id, self.worker_id, ctx.clock())

    def _seed_skill_library(self, company_id: str) -> None:
        assert self.ctx.skills is not None
        with self.ctx.session_factory() as db:
            skills_service.seed_company_library(db, company_id, self.ctx.skills)

    def run_once(self, now: datetime | None = None) -> bool:
        """Lease and execute one job synchronously. Returns False if the queue was empty."""
        job = jobs.lease_next(
            self.ctx.session_factory,
            self.worker_id,
            now or self.ctx.clock(),
            lease_seconds=LEASE_SECONDS,
            kinds=self.kinds,
        )
        if job is None:
            return False
        self.handle(job)
        return True

    def drain(self, max_jobs: int = 100) -> int:
        processed = 0
        while processed < max_jobs and self.run_once():
            processed += 1
        return processed

    # ------------------------------------------------------------ loop

    def run_forever(self, stop: threading.Event, poll_interval: float = POLL_SECONDS) -> None:
        pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="run")
        inflight: set[Future] = set()
        try:
            while not stop.is_set():
                inflight = {f for f in inflight if not f.done()}
                leased = False
                for _ in range(self.concurrency - len(inflight)):
                    try:
                        job = jobs.lease_next(
                            self.ctx.session_factory,
                            self.worker_id,
                            self.ctx.clock(),
                            lease_seconds=LEASE_SECONDS,
                            kinds=self.kinds,
                        )
                    except Exception:
                        logger.exception("could not lease a job")
                        break
                    if job is None:
                        break
                    leased = True
                    inflight.add(pool.submit(self.handle, job))
                if not leased:
                    stop.wait(poll_interval)
        finally:
            # Graceful stop: no new leases, cancel running executions, wait a bounded time.
            self.shutdown.set()
            wait(inflight, timeout=SHUTDOWN_GRACE_SECONDS)
            pool.shutdown(wait=False, cancel_futures=True)
