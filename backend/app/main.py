"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect

from app.api.routes import activity, agents, companies, departments, runtimes, system
from app.core.config import APP_VERSION, Settings, get_settings
from app.core.errors import register_exception_handlers
from app.db.session import build_engine, build_session_factory
from app.runtimes.registry import RuntimeRegistry, build_default_registry
from app.services.users import ensure_local_owner

logger = logging.getLogger("company_os")


def create_app(
    settings: Settings | None = None, runtime_registry: RuntimeRegistry | None = None
) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings.resolved_database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auth_mode == "local":
            logger.warning(
                "COMPANY_OS_AUTH_MODE=local: every request is treated as the local owner with "
                "NO authentication. Only run this bound to localhost (127.0.0.1)."
            )
        settings.prepare_data_dir()
        if "users" not in inspect(engine).get_table_names():
            raise RuntimeError(
                "Database schema is missing. Run `uv run alembic upgrade head` in backend/ first."
            )
        ensure_local_owner(session_factory)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Company OS API",
        version=APP_VERSION,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.runtimes = runtime_registry or build_default_registry(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    for router in (
        system.router,
        companies.router,
        departments.router,
        agents.router,
        activity.router,
        runtimes.router,
    ):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
