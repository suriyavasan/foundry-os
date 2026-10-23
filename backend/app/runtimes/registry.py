from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.keys import SecretKeyProvider
from app.runtimes.base import RuntimeAdapter
from app.runtimes.hermes import HermesAdapter


class RuntimeRegistry:
    def __init__(self, adapters: list[RuntimeAdapter] | None = None) -> None:
        self._adapters: dict[str, RuntimeAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: RuntimeAdapter) -> None:
        if adapter.id in self._adapters:
            raise ValueError(f"runtime {adapter.id!r} already registered")
        self._adapters[adapter.id] = adapter

    def get(self, runtime_id: str) -> RuntimeAdapter | None:
        return self._adapters.get(runtime_id)

    def all(self) -> list[RuntimeAdapter]:
        return list(self._adapters.values())


def build_default_registry(
    settings: Settings,
    *,
    session_factory: sessionmaker[Session] | None = None,
    keys: SecretKeyProvider | None = None,
) -> RuntimeRegistry:
    registry = RuntimeRegistry([HermesAdapter.from_settings(settings)])
    if session_factory is not None and keys is not None:
        # The company agent runtime needs the database (agent, model provider, credentials, skills).
        from app.runtimes.company_agent import CompanyAgentAdapter

        registry.register(CompanyAgentAdapter(settings, session_factory, keys))
    if settings.enable_test_runtimes:
        # Test fixture only; reported with kind "test_fixture" and absent from default config.
        from app.runtimes.scripted import ScriptedRuntime

        registry.register(ScriptedRuntime())
    return registry
