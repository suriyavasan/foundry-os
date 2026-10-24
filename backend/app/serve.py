"""Launcher: ``uv run python -m app.serve [--host H] [--port P]``.

uvicorn's own ``--host`` is invisible to the app, and the listening socket cannot be rebound from
inside it. This launcher closes the loop for the LAN switch (Settings > Reachability): it reads the
owner's persisted bind preference, refuses to bind to the network unless password sign-in is on and
the owner account passed the exposure gate, tells the app which interface it is bound to
(``COMPANY_OS_BIND_HOST``) and starts uvicorn. ``foundryos up`` (M9) does the same.
"""

from __future__ import annotations

import argparse
import os
import sys


def decide_host(explicit: str | None) -> tuple[str, str | None]:
    """(host, warning). An explicit --host wins but is still held to the exposure gate."""
    from sqlalchemy import inspect

    from app.core.config import Settings
    from app.core.netinfo import is_loopback_bind
    from app.db.session import build_engine, build_session_factory
    from app.services import reachability

    settings = Settings()
    host = explicit
    engine = build_engine(settings.resolved_database_url)
    try:
        if "system_state" not in inspect(engine).get_table_names():
            return (explicit or "127.0.0.1"), None
        factory = build_session_factory(engine)
        with factory() as db:
            if host is None:
                host = reachability.preferred_bind_host(reachability.load_state(db))
            if is_loopback_bind(host):
                return host, None
            block = reachability.exposure_block(db, settings)
            if block:
                return "127.0.0.1", (
                    f"Not binding to {host}: {block['reason']} Serving on 127.0.0.1 only."
                )
            return host, None
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.serve")
    parser.add_argument("--host", default=None, help="bind address (default: the saved preference)")
    parser.add_argument(
        "--port", type=int, default=None, help="port (default: FOUNDRYOS_PORT or 8700)"
    )
    args = parser.parse_args(argv)
    if args.port is None:
        from app.core.config import Settings

        args.port = Settings().port
    host, warning = decide_host(args.host)
    if warning:
        print(warning, file=sys.stderr)
    os.environ["COMPANY_OS_BIND_HOST"] = host
    os.environ["COMPANY_OS_PORT"] = str(args.port)
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=args.port)


if __name__ == "__main__":
    main()
