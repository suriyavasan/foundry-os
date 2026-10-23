"""Runtime adapter contract (architecture section 6).

Every agent runtime (Hermes first) is reached only through ``RuntimeAdapter``. Company OS
owns business state and governance; the runtime owns execution. These types are the
normalized vocabulary between the two. Only health/config validation is implemented in
Milestone 1; execution methods arrive with Milestone 4.
"""

from __future__ import annotations

import abc
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.time import utcnow


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


class CostKind(StrEnum):
    """How trustworthy a cost figure is. An estimate is never presented as a charge."""

    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


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


class ExecutionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


@dataclass(frozen=True)
class ExecutionRequest:
    company_id: str
    agent_id: str
    run_id: str
    input: str
    idempotency_key: str  # required: retries must not start a second execution
    instructions: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    config: dict[str, Any] = field(default_factory=dict)  # non-secret runtime_config
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionHandle:
    runtime_id: str
    execution_id: str


@dataclass(frozen=True)
class ExecutionEvent:
    seq: int
    type: str
    payload: dict[str, Any]
    ts: datetime


@dataclass(frozen=True)
class UsageReport:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_micros: int | None = None  # integer micro-USD
    cost_kind: CostKind = CostKind.UNKNOWN
    provider_event_id: str | None = None  # dedupe key for the usage ledger (M6)


@dataclass(frozen=True)
class ExecutionResult:
    state: ExecutionState
    output: str | None = None
    error_kind: RuntimeErrorKind | None = None
    error_message: str | None = None
    usage: UsageReport | None = None


class RuntimeAdapter(abc.ABC):
    id: str
    display_name: str
    kind: str = "agent_runtime"
    config_hint: str = ""

    @property
    @abc.abstractmethod
    def capabilities(self) -> frozenset[Capability]: ...

    @abc.abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[ConfigIssue]: ...

    @abc.abstractmethod
    def check_health(self) -> RuntimeHealth: ...

    @abc.abstractmethod
    def start(self, request: ExecutionRequest) -> ExecutionHandle: ...

    @abc.abstractmethod
    def get_status(self, handle: ExecutionHandle) -> ExecutionState: ...

    @abc.abstractmethod
    def stream_events(
        self, handle: ExecutionHandle, after_seq: int = 0
    ) -> Iterator[ExecutionEvent]: ...

    @abc.abstractmethod
    def cancel(self, handle: ExecutionHandle) -> None: ...

    @abc.abstractmethod
    def get_result(self, handle: ExecutionHandle) -> ExecutionResult: ...

    @abc.abstractmethod
    def get_usage(self, handle: ExecutionHandle) -> UsageReport: ...
