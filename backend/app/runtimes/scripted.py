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
    raise_config_error:   str, raise RuntimeAdapterError(CONFIG, str) first (the runtime never ran)
    usage:                {"input_tokens", "output_tokens", "cost_micros", "cost_kind"}
    artifacts:            [{"title", "content", "kind", "mime_type"}]
    agent_api:            [{"method", "path", "json", "save_as", "expect"}, ...] steps run with
                          the run token against the agent API, in-process (see "Agent API steps")

Agent API steps (Milestone 5): a scripted run can act like a real agent. Set ``api_client`` (a
TestClient bound to the app, ideally without the owner cookie) and either put ``agent_api`` steps
into the script - ``{"method": "POST", "path": "/tasks", "json": {...}, "save_as": "t1",
"expect": 201}``; ``{t1.id}``-style placeholders in strings are replaced from earlier ``save_as``
responses - or register a Python ``handlers[<agent name or id>] = fn(call)`` where ``call`` is an
``AgentCall`` (``call.api.post(...)``, ``call.request``, ``call.saved``). A failed ``expect`` or an
exception in a handler is recorded in ``runtime.errors`` and fails the run.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
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
    RuntimeAdapterError,
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
    "raise_config_error",
    "usage",
    "artifacts",
    "agent_api",
}


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)((?:\.[A-Za-z0-9_]+)*)\}")


class AgentApi:
    """The agent API as one run sees it: every call carries that run's token."""

    BASE = "/api/agent/v1"

    def __init__(self, client: Any, token: str) -> None:
        self._client, self._token = client, token

    def _call(self, method: str, path: str, **kwargs: Any):
        headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
        return self._client.request(method, self.BASE + path, headers=headers, **kwargs)

    def get(self, path: str, **kw: Any):
        return self._call("GET", path, **kw)

    def post(self, path: str, **kw: Any):
        return self._call("POST", path, **kw)

    def patch(self, path: str, **kw: Any):
        return self._call("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any):
        return self._call("DELETE", path, **kw)

    def ok(self, method: str, path: str, *, expect: int | tuple[int, ...] = (200, 201), **kw: Any):
        """Call and return the JSON body; raises AssertionError on an unexpected status."""
        resp = self._call(method, path, **kw)
        wanted = (expect,) if isinstance(expect, int) else expect
        if resp.status_code not in wanted:
            raise AssertionError(f"{method} {path} -> {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else None


@dataclass
class AgentCall:
    """What a handler receives: the API as this agent, the request and a scratch dict."""

    api: AgentApi
    request: ExecutionRequest
    saved: dict[str, Any] = field(default_factory=dict)


def _substitute(value: Any, saved: dict[str, Any]) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            cur: Any = saved[match.group(1)]
            for part in [p for p in match.group(2).split(".") if p]:
                cur = cur[part]
            return str(cur)

        return _PLACEHOLDER.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v, saved) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, saved) for k, v in value.items()}
    return value


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
        # Agent API steps (Milestone 5): see the module docstring.
        self.api_client: Any = None
        self.handlers: dict[str, Callable[[AgentCall], Any]] = {}
        self.errors: list[str] = []

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

        if script.get("raise_config_error"):
            raise RuntimeAdapterError(RuntimeErrorKind.CONFIG, str(script["raise_config_error"]))
        if script.get("raise_exception"):
            raise RuntimeError(str(script["raise_exception"]))

        failure = self._act_through_the_api(request, script, emit)
        if failure:
            emit("run.failed", {"message": failure})
            return ExecutionResult(
                status="failed", error=ExecutionError(RuntimeErrorKind.FAILED, failure)
            )

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

    def _act_through_the_api(
        self, request: ExecutionRequest, script: dict[str, Any], emit
    ) -> str | None:
        """Run the script's ``agent_api`` steps and the registered handler (if any)."""
        steps = script.get("agent_api") or []
        handler = self.handlers.get(request.agent.name) or self.handlers.get(request.agent.id)
        if not steps and handler is None:
            return None
        if self.api_client is None:
            return "ScriptedRuntime.api_client is not set; agent API steps cannot run"
        call = AgentCall(AgentApi(self.api_client, request.run_token), request)
        try:
            for step in steps:
                body = _substitute(step.get("json"), call.saved)
                path = _substitute(step["path"], call.saved)
                resp = call.api._call(
                    step.get("method", "GET"), path, **({"json": body} if body is not None else {})
                )
                expect = step.get("expect", (200, 201))
                wanted = (expect,) if isinstance(expect, int) else tuple(expect)
                if resp.status_code not in wanted:
                    raise AssertionError(
                        f"{step.get('method', 'GET')} {path} -> {resp.status_code} "
                        f"{resp.text[:300]}"
                    )
                if step.get("save_as"):
                    call.saved[step["save_as"]] = resp.json() if resp.content else None
                emit("tool.completed", {"name": f"{step.get('method', 'GET')} {path}"})
            if handler is not None:
                handler(call)
        except Exception as exc:  # recorded for the test; the run fails like a crashed agent
            message = f"{type(exc).__name__}: {exc}"
            self.errors.append(message)
            return message
        return None

    @staticmethod
    def _usage(raw: dict[str, Any] | None) -> UsageReport | None:
        if not raw:
            return None
        return UsageReport(
            input_tokens=raw.get("input_tokens"),
            output_tokens=raw.get("output_tokens"),
            cost_micros=raw.get("cost_micros"),
            cost_kind=CostKind(raw.get("cost_kind", "unknown")),
            billing_note=raw.get("billing_note"),
        )

    @staticmethod
    def _cancelled(emit) -> ExecutionResult:
        emit("run.cancelled", {})
        return ExecutionResult(
            status="cancelled",
            error=ExecutionError(RuntimeErrorKind.CANCELLED, "Cancelled"),
        )
