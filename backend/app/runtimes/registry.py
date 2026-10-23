from __future__ import annotations

from app.core.config import Settings
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


def build_default_registry(settings: Settings) -> RuntimeRegistry:
    return RuntimeRegistry([HermesAdapter.from_settings(settings)])
