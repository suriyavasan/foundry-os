"""Runtime adapter contract (architecture section 6).

Every agent runtime is reached only through ``RuntimeAdapter``. Company OS owns business
state and governance; the runtime owns execution. These types are the normalized vocabulary
between the two.

``execute`` runs synchronously in a worker thread: it streams events through ``sink``,
checks ``cancel`` cooperatively and returns an ``ExecutionResult``. The worker owns timeouts
(it sets the cancel token at ``limits.max_run_seconds``). Adapters for remote runtimes
implement start/stream/stop internally.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from app.core.time import utcnow
from app.models.enums import CostKind

__all__ = [
    "AgentSpec",
    "ArtifactRef",
    "CancelToken",
    "Capability",
    "CompanySpec",
    "ConfigIssue",
    "CostKind",
    "EventSink",
    "ExecutionError",
    "ExecutionEvent",
    "ExecutionRequest",
    "ExecutionResult",
    "HealthStatus",
    "Limits",
    "RuntimeAdapter",
    "RuntimeAdapterError",
    "RuntimeErrorKind",
    "RuntimeHealth",
    "TaskSpec",
    "UsageReport",
]


class Capability(StrEnum):
    STREAMING_EVENTS = "streaming_events"
    CANCEL = "cancel"
    USAGE_TOKENS = "usage_tokens"
    USAGE_COST = "usage_cost"
    APPROVALS = "approvals"
    ARTIFACTS = "artifacts"
    IDEMPOTENT_START = "idempotent_start"


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class RuntimeErrorKind(StrEnum):
    CONFIG = "config"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RuntimeAdapterError(Exception):
    """Normalized runtime failure. ``message`` must already be safe to show (no secrets)."""

    def __init__(self, kind: RuntimeErrorKind, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ConfigIssue:
    field: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass(frozen=True)
class RuntimeHealth:
    status: HealthStatus
    detail: str
    checked_at: datetime = field(default_factory=utcnow)


# ---------------------------------------------------------------- execution request


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    description: str | None
    acceptance_criteria: str | None
    kind: str
    priority: str
    input_refs: list[str] = field(default_factory=list)
    review_note: str | None = None  # feedback to address on a revision


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    role_title: str
    instructions: str | None
    responsibilities: str | None
    model_provider: str | None
    model_name: str | None
    runtime_config: dict[str, Any]  # non-secret by construction
    tool_permissions: list[str]


@dataclass(frozen=True)
class CompanySpec:
    id: str
    name: str
    strategic_goal: str | None
    constraints: list[str]


@dataclass(frozen=True)
class Limits:
    max_run_seconds: int
    budget_micros: int | None  # the reservation for this run


@dataclass(frozen=True)
class ExecutionRequest:
    run_id: str
    attempt_number: int
    task: TaskSpec
    agent: AgentSpec
    company: CompanySpec
    limits: Limits
    run_token: str  # secret: pass to the agent API only; never log or echo into events
    agent_api_url: str
    workspace_dir: str

    def __repr__(self) -> str:  # the token must never reach logs
        return f"ExecutionRequest(run_id={self.run_id!r}, task_id={self.task.id!r})"


@dataclass(frozen=True)
class ExecutionEvent:
    seq: int
    type: str
    payload: dict[str, Any]
    ts: datetime = field(default_factory=utcnow)


class EventSink(Protocol):
    def emit(self, event: ExecutionEvent) -> None: ...


class CancelToken:
    """Cooperative cancellation flag shared between the worker and the adapter."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)


# ---------------------------------------------------------------- execution result


@dataclass(frozen=True)
class UsageReport:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_micros: int | None = None  # integer micro-USD
    cost_kind: CostKind = CostKind.UNKNOWN
    provider_event_id: str | None = None
    # "subscription": the run used the person's own CLI login. The cost is unknown (``cost_kind``
    # stays unknown) but nothing is charged against the budgets either (M10).
    billing_note: str | None = None


@dataclass(frozen=True)
class ExecutionError:
    kind: RuntimeErrorKind
    message: str  # must be safe to show (no secrets)
    retryable: bool = False


@dataclass(frozen=True)
class ArtifactRef:
    """A deliverable produced by the run. Content is stored by Company OS (never in the DB)."""

    title: str
    content: bytes | str
    kind: str = "document"
    mime_type: str = "text/markdown"


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # "succeeded" | "failed" | "cancelled"
    summary: str | None = None
    error: ExecutionError | None = None
    usage: UsageReport | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)


# ---------------------------------------------------------------- adapter


class RuntimeAdapter(abc.ABC):
    id: str
    display_name: str
    kind: str = "agent_runtime"
    config_hint: str = ""
    # False for adapters that can report health but cannot run tasks yet; the dispatcher
    # leaves tasks assigned to such runtimes "waiting for runtime".
    supports_execution: bool = True

    @property
    @abc.abstractmethod
    def capabilities(self) -> frozenset[Capability]: ...

    @abc.abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[ConfigIssue]: ...

    @abc.abstractmethod
    def check_health(self) -> RuntimeHealth: ...

    @abc.abstractmethod
    def execute(
        self, request: ExecutionRequest, sink: EventSink, cancel: CancelToken
    ) -> ExecutionResult: ...
