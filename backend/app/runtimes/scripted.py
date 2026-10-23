"""Deterministic test runtime. Registered ONLY when ``COMPANY_OS_ENABLE_TEST_RUNTIMES=1`` and
always reported as ``kind: "test_fixture"`` -- it executes nothing real.

Behaviour comes from ``agent.runtime_config["script"]`` (JSON, non-secret) layered over the
constructor's default script. Script keys (all optional):

    events:               [{"type": "message", "payload": {...}}, ...]  emitted in order
    delay_seconds:        float, waits (honouring cancel) before finishing
    block_until_cancel:   bool, wait for the cancel token (capped by ``block_cap_seconds``)
    result:               "succeeded" (default) | "failed" | "cancelled"
    summary:              str, result summary
    error_message:        str, used when result is "failed"
    fail_first_attempts:  int, fail while request.attempt_number <= N, then succeed
    raise_exception:      str, raise RuntimeError(str) (tests worker exception handling)
    usage:                {"input_tokens", "output_tokens", "cost_micros", "cost_kind"}
    artifacts:            [{"title", "content", "kind", "mime_type"}]
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.time import utcnow
from app.runtimes.base import (
    ArtifactRef,
    CancelToken,
    Capability,
    ConfigIssue,
    CostKind,
    EventSink,
    ExecutionError,
    ExecutionEvent,
    ExecutionRequest,
    ExecutionResult,
    HealthStatus,
    RuntimeAdapter,
    RuntimeErrorKind,
    RuntimeHealth,
    UsageReport,
)

_KNOWN_KEYS = {
    "events",
    "delay_seconds",
    "block_until_cancel",
    "block_cap_seconds",
    "result",
    "summary",
    "error_message",
    "fail_first_attempts",
    "raise_exception",
    "usage",
    "artifacts",
}


class ScriptedRuntime(RuntimeAdapter):
    id = "scripted"
    display_name = "Scripted (test fixture)"
    kind = "test_fixture"
    config_hint = (
        "Deterministic fixture for tests and demos of the work engine. Enabled by "
        "COMPANY_OS_ENABLE_TEST_RUNTIMES=1; it never calls a model."
    )

    def __init__(self, default_script: dict[str, Any] | None = None) -> None:
        self._default = dict(default_script or {})
        self.requests: list[ExecutionRequest] = []  # recorded for assertions
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.STREAMING_EVENTS,
                Capability.CANCEL,
                Capability.USAGE_TOKENS,
                Capability.USAGE_COST,
                Capability.ARTIFACTS,
                Capability.IDEMPOTENT_START,
            }
        )

    def validate_config(self, config: dict[str, Any]) -> list[ConfigIssue]:
        script = (config or {}).get("script", {})
        if not isinstance(script, dict):
            return [ConfigIssue("script", "must be an object")]
        return [
            ConfigIssue(f"script.{key}", "unknown script key", "warning")
            for key in script
            if key not in _KNOWN_KEYS
        ]

    def check_health(self) -> RuntimeHealth:
        return RuntimeHealth(HealthStatus.OK, "Scripted test fixture (executes nothing real)")

    def _script_for(self, request: ExecutionRequest) -> dict[str, Any]:
        merged = dict(self._default)
        override = request.agent.runtime_config.get("script")
        if isinstance(override, dict):
            merged.update(override)
        return merged

    def execute(
        self, request: ExecutionRequest, sink: EventSink, cancel: CancelToken
    ) -> ExecutionResult:
        with self._lock:
            self.requests.append(request)
        script = self._script_for(request)
        seq = 0

        def emit(type_: str, payload: dict[str, Any]) -> None:
            nonlocal seq
            seq += 1
            sink.emit(ExecutionEvent(seq=seq, type=type_, payload=payload, ts=utcnow()))

        emit(
            "run.started",
            {
                "summary": f"Started: {request.task.title}",
                "attempt": request.attempt_number,
            },
        )
        for item in script.get("events", []):
            if cancel.is_set():
                return self._cancelled(emit)
            emit(str(item.get("type", "message")), dict(item.get("payload", {})))

        if script.get("raise_exception"):
            raise RuntimeError(str(script["raise_exception"]))

        delay = float(script.get("delay_seconds", 0) or 0)
        if delay and cancel.wait(delay):
            return self._cancelled(emit)
        if script.get("block_until_cancel") and cancel.wait(
            float(script.get("block_cap_seconds", 30))
        ):
            return self._cancelled(emit)
        if cancel.is_set():
            return self._cancelled(emit)

        usage = self._usage(script.get("usage"))
        outcome = str(script.get("result", "succeeded"))
        if request.attempt_number <= int(script.get("fail_first_attempts", 0) or 0):
            outcome = "failed"
        if outcome == "failed":
            message = str(script.get("error_message", "Scripted failure"))
            emit("run.failed", {"message": message})
            return ExecutionResult(
                status="failed",
                error=ExecutionError(RuntimeErrorKind.FAILED, message, retryable=True),
                usage=usage,
            )
        if outcome == "cancelled":
            return self._cancelled(emit)
        emit("run.completed", {"summary": script.get("summary")})
        artifacts = [
            ArtifactRef(
                title=str(a.get("title", "Artifact")),
                content=a.get("content", ""),
                kind=str(a.get("kind", "document")),
                mime_type=str(a.get("mime_type", "text/markdown")),
            )
            for a in script.get("artifacts", [])
        ]
        return ExecutionResult(
            status="succeeded",
            summary=script.get("summary", f"Completed: {request.task.title}"),
            usage=usage,
            artifacts=artifacts,
        )

    @staticmethod
    def _usage(raw: dict[str, Any] | None) -> UsageReport | None:
        if not raw:
            return None
        return UsageReport(
            input_tokens=raw.get("input_tokens"),
            output_tokens=raw.get("output_tokens"),
            cost_micros=raw.get("cost_micros"),
            cost_kind=CostKind(raw.get("cost_kind", "unknown")),
        )

    @staticmethod
    def _cancelled(emit) -> ExecutionResult:
        emit("run.cancelled", {})
        return ExecutionResult(
            status="cancelled",
            error=ExecutionError(RuntimeErrorKind.CANCELLED, "Cancelled"),
        )
