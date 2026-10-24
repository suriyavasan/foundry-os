"""``companyos-worker``: runs the worker and scheduler loops until SIGTERM/SIGINT."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from sqlalchemy import inspect

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.engine.context import build_context
from app.engine.service import EngineService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="companyos-worker",
        description="Company OS worker + scheduler (dispatch, runs, schedules, recovery).",
    )
    parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("company_os.worker")

    settings = get_settings()
    settings.prepare_data_dir()
    from app.ops import pidfile

    runfile = pidfile.register(settings.resolved_data_dir, "worker")
    engine = build_engine(settings.resolved_database_url)
    if "task_runs" not in inspect(engine).get_table_names():
        log.error("Database schema is missing. Run `uv run alembic upgrade head` first.")
        return 1
    ctx = build_context(settings, session_factory=build_session_factory(engine))

    service = EngineService(ctx)

    def _stop(signum, _frame):
        log.info("received signal %s: shutting down", signal.Signals(signum).name)
        service.request_stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    service.start()
    service.wait()
    service.stop()
    pidfile.unregister(runfile)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
