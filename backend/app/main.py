"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect

from app.api.routes import (
    activity,
    agent_api,
    agents,
    alerts,
    approvals,
    artifacts,
    auth,
    cofounder,
    companies,
    control,
    departments,
    goals,
    memory,
    notes,
    policy,
    projects,
    providers,
    runtimes,
    schedules,
    skills,
    system,
    tasks,
)
from app.core.config import APP_VERSION, Settings, get_settings
from app.core.crypto import CredentialCipher
from app.core.errors import register_exception_handlers
from app.core.keys import SecretKeyProvider
from app.core.owner_token import OwnerTokenProvider, install_log_redaction
from app.db.session import build_engine, build_session_factory
from app.runtimes.registry import RuntimeRegistry, build_default_registry
from app.services.users import ensure_local_owner
from app.skills.env import SkillsEnv, build_skills_env
from app.storage.local import LocalFileStorage

logger = logging.getLogger("company_os")

# One table per migration generation: absent means `alembic upgrade head` has not been run.
REQUIRED_TABLES = {
    "users",
    "company_policies",
    "task_runs",
    "company_events",
    "skills",
    "model_providers",
    "founder_notes",
}


def create_app(
    settings: Settings | None = None,
    runtime_registry: RuntimeRegistry | None = None,
    skills_env: SkillsEnv | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    install_log_redaction()
    engine = build_engine(settings.resolved_database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.prepare_data_dir()
        present = set(inspect(engine).get_table_names())
        if not present >= REQUIRED_TABLES:
            raise RuntimeError(
                "Database schema is missing or out of date. "
                "Run `uv run alembic upgrade head` in backend/ first."
            )
        ensure_local_owner(session_factory)
        if settings.auth_mode == "local":
            # Owner credential (M3b-sec): without it every API request is a 401.
            app.state.owner_token.ensure()
            app.state.owner_token.announce()
        service = None
        if settings.embedded_worker:
            from app.engine.context import build_context
            from app.engine.service import EngineService

            service = EngineService(
                build_context(
                    settings,
                    session_factory=session_factory,
                    registry=app.state.runtimes,
                    keys=app.state.keys,
                    storage=app.state.storage,
                    skills=app.state.skills,
                )
            )
            service.start()
            logger.warning("Embedded worker + scheduler running inside the API process")
        try:
            yield
        finally:
            if service is not None:
                service.stop()
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
    app.state.keys = SecretKeyProvider(settings)
    app.state.owner_token = OwnerTokenProvider(settings)
    app.state.cipher = CredentialCipher(app.state.keys)
    app.state.runtimes = runtime_registry or build_default_registry(
        settings, session_factory=session_factory, keys=app.state.keys
    )
    app.state.storage = LocalFileStorage(settings.artifacts_dir)
    app.state.skills = skills_env or build_skills_env(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # explicit origins only; the owner cookie is SameSite=Strict
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    for router in (
        system.router,
        auth.router,
        companies.router,
        departments.router,
        # before agents: /agents/{id}/skills must win over the /agents/{id}/{action} catch-all
        skills.router,
        agents.router,
        activity.router,
        runtimes.router,
        policy.router,
        providers.router,
        goals.router,
        projects.router,
        tasks.router,
        artifacts.router,
        approvals.router,
        schedules.router,
        control.router,
        notes.router,
        memory.router,
        alerts.router,
        cofounder.router,
        agent_api.router,
    ):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
