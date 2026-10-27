from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.keys import SecretKeyProvider
from app.core.time import utcnow
from app.db.session import build_engine, build_session_factory
from app.integrations.base import IntegrationHttp
from app.runtimes.base import HealthStatus
from app.runtimes.registry import RuntimeRegistry, build_default_registry
from app.skills.env import SkillsEnv, build_skills_env
from app.storage.base import ArtifactStorage
from app.storage.local import LocalFileStorage


class HealthCache:
    """Runtime health checks can be slow HTTP calls: cache per runtime for a short TTL."""

    def __init__(self, registry: RuntimeRegistry, ttl_seconds: float = 30.0) -> None:
        self._registry = registry
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, bool]] = {}
        self._lock = threading.Lock()

    def usable(self, runtime_id: str) -> bool:
        """True if the runtime is registered, can execute, and is ok/degraded."""
        adapter = self._registry.get(runtime_id)
        if adapter is None or not adapter.supports_execution:
            return False
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(runtime_id)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        try:
            ok = adapter.check_health().status in (HealthStatus.OK, HealthStatus.DEGRADED)
        except Exception:
            ok = False
        with self._lock:
            self._cache[runtime_id] = (now, ok)
        return ok


@dataclass
class EngineContext:
    settings: Settings
    session_factory: sessionmaker[Session]
    registry: RuntimeRegistry
    keys: SecretKeyProvider
    storage: ArtifactStorage
    clock: Callable[[], datetime] = utcnow
    skills: SkillsEnv | None = None
    # M14: the worker has no IntegrationContext; the repository sync loop talks to GitHub through
    # this HTTP factory (tests replace its transport with the fake GitHub).
    integration_http: IntegrationHttp = field(default_factory=IntegrationHttp)
    health: HealthCache = field(init=False)

    def __post_init__(self) -> None:
        self.health = HealthCache(self.registry)
        if self.skills is None:
            self.skills = build_skills_env(self.settings)


def build_context(
    settings: Settings,
    *,
    session_factory: sessionmaker[Session] | None = None,
    registry: RuntimeRegistry | None = None,
    keys: SecretKeyProvider | None = None,
    storage: ArtifactStorage | None = None,
    clock: Callable[[], datetime] = utcnow,
    skills: SkillsEnv | None = None,
    integration_http: IntegrationHttp | None = None,
) -> EngineContext:
    if session_factory is None:
        session_factory = build_session_factory(build_engine(settings.resolved_database_url))
    keys = keys or SecretKeyProvider(settings)
    return EngineContext(
        settings=settings,
        session_factory=session_factory,
        registry=registry
        or build_default_registry(settings, session_factory=session_factory, keys=keys),
        keys=keys,
        storage=storage or LocalFileStorage(settings.artifacts_dir),
        clock=clock,
        skills=skills,
        integration_http=integration_http or IntegrationHttp(),
    )
