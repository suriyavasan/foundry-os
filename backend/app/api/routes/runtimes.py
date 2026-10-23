from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import (
    CurrentPrincipal,
    get_runtime_registry,  # noqa: F401
)
from app.runtimes.registry import RuntimeRegistry
from app.schemas.runtime import RuntimeHealthOut, RuntimeInfo, RuntimeList

router = APIRouter(prefix="/runtimes", tags=["runtimes"])


@router.get("", response_model=RuntimeList)
def list_runtimes(_: CurrentPrincipal, registry: RuntimeRegistry = Depends(get_runtime_registry)):
    items = []
    for adapter in registry.all():
        health = adapter.check_health()
        items.append(
            RuntimeInfo(
                id=adapter.id,
                display_name=adapter.display_name,
                kind=adapter.kind,
                capabilities=sorted(c.value for c in adapter.capabilities),
                health=RuntimeHealthOut(
                    status=health.status.value, detail=health.detail, checked_at=health.checked_at
                ),
                config_hint=adapter.config_hint,
            )
        )
    return RuntimeList(items=items)
