"""Hermes Agent adapter (gateway OpenAI-compatible API server).

Milestone 1 implements configuration validation and health checks only. Execution methods
raise NotImplementedError until Milestone 4.

Findings from vendor/hermes-agent (commit db1fa15), gateway/platforms/api_server.py:
  * ``GET /health`` (route table line ~1569, handler ``_handle_health`` ~2280) is NOT
    decorated with ``@_require_auth`` and returns
    ``{"status": "ok", "platform": "hermes-agent", "version": ...}``. A 200 from /health
    therefore proves the gateway is reachable but says nothing about whether our key is valid.
  * ``GET /health/detailed`` (``_handle_health_detailed`` ~2285) IS ``@_require_auth``
    (Bearer, ``_check_auth`` ~1415, 401 on mismatch). We use it as a cheap key probe: a 401
    there downgrades health to ``degraded`` instead of claiming ``ok``.
  Not exercised against a live Hermes instance in M1 (only against mocked transports).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.runtimes.base import (
    Capability,
    ConfigIssue,
    ExecutionEvent,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionResult,
    ExecutionState,
    HealthStatus,
    RuntimeAdapter,
    RuntimeHealth,
    UsageReport,
)

HEALTH_TIMEOUT_SECONDS = 2.0
_M4 = "Hermes execution is not implemented until Milestone 4"


class HermesAdapter(RuntimeAdapter):
    id = "hermes"
    display_name = "Hermes Agent"
    kind = "agent_runtime"
    config_hint = (
        "Set HERMES_API_URL (e.g. http://127.0.0.1:8642) and HERMES_API_KEY to the gateway's "
        "API_SERVER_KEY, then restart the backend. Start Hermes with `hermes gateway run`."
    )

    def __init__(
        self,
        api_url: str | None,
        api_key: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_url = (api_url or "").strip().rstrip("/") or None
        self._api_key = (api_key or "").strip() or None
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        key = settings.hermes_api_key.get_secret_value() if settings.hermes_api_key else None
        return cls(settings.hermes_api_url, key, transport=transport)

    # The key is deliberately excluded from repr/serialization.
    def __repr__(self) -> str:
        return f"HermesAdapter(configured={self._api_url is not None})"

    @property
    def capabilities(self) -> frozenset[Capability]:
        # Declared from source inspection of the Runs API; not yet exercised end to end.
        return frozenset(
            {
                Capability.STREAMING_EVENTS,
                Capability.CANCEL,
                Capability.USAGE_TOKENS,
                Capability.USAGE_COST,
                Capability.APPROVALS,
                Capability.IDEMPOTENT_START,
            }
        )

    def validate_config(self, config: dict[str, Any] | None = None) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        if not self._api_url:
            issues.append(ConfigIssue("HERMES_API_URL", "Not set"))
        else:
            parsed = urlparse(self._api_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                issues.append(ConfigIssue("HERMES_API_URL", "Must be an http(s) URL"))
        if not self._api_key:
            issues.append(
                ConfigIssue("HERMES_API_KEY", "Not set (Hermes requires API_SERVER_KEY)", "warning")
            )
        return issues

    def check_health(self) -> RuntimeHealth:
        errors = [i for i in self.validate_config() if i.severity == "error"]
        if errors:
            detail = "; ".join(f"{i.field}: {i.message}" for i in errors)
            return RuntimeHealth(HealthStatus.NOT_CONFIGURED, detail)

        assert self._api_url is not None
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            with httpx.Client(
                base_url=self._api_url,
                timeout=HEALTH_TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                resp = client.get("/health", headers=headers)
                if resp.status_code != 200:
                    return RuntimeHealth(
                        HealthStatus.UNAVAILABLE, f"Hermes /health returned HTTP {resp.status_code}"
                    )
                if not self._api_key:
                    return RuntimeHealth(
                        HealthStatus.DEGRADED,
                        "Hermes is reachable but HERMES_API_KEY is not set; runs would be rejected",
                    )
                # /health is unauthenticated, so confirm the key against an authed endpoint.
                probe = client.get("/health/detailed", headers=headers)
                if probe.status_code in (401, 403):
                    return RuntimeHealth(
                        HealthStatus.DEGRADED,
                        "Hermes is reachable but rejected HERMES_API_KEY",
                    )
                if probe.status_code == 200:
                    return RuntimeHealth(
                        HealthStatus.OK, "Hermes gateway reachable; API key accepted"
                    )
                return RuntimeHealth(
                    HealthStatus.OK, "Hermes gateway reachable; API key could not be verified"
                )
        except httpx.TimeoutException:
            return RuntimeHealth(
                HealthStatus.UNAVAILABLE,
                f"Hermes did not respond within {HEALTH_TIMEOUT_SECONDS:g}s",
            )
        except httpx.HTTPError as exc:
            # Only the exception class name: messages can embed URLs or header values.
            return RuntimeHealth(
                HealthStatus.UNAVAILABLE, f"Could not reach Hermes ({type(exc).__name__})"
            )

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        raise NotImplementedError(_M4)

    def get_status(self, handle: ExecutionHandle) -> ExecutionState:
        raise NotImplementedError(_M4)

    def stream_events(
        self, handle: ExecutionHandle, after_seq: int = 0
    ) -> Iterator[ExecutionEvent]:
        raise NotImplementedError(_M4)

    def cancel(self, handle: ExecutionHandle) -> None:
        raise NotImplementedError(_M4)

    def get_result(self, handle: ExecutionHandle) -> ExecutionResult:
        raise NotImplementedError(_M4)

    def get_usage(self, handle: ExecutionHandle) -> UsageReport:
        raise NotImplementedError(_M4)
