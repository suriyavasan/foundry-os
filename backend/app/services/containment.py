"""Run containment (docs/specs/m6 section 3): runaway caps, stall detection input and the
mid-run budget checkpoint. The executor feeds every runtime event through a :class:`RunGuard`.

A run is *tripped* (and then cancelled by the executor's monitor) when it:

* exceeds a per-run cap -- tool calls, artifacts, tokens (``policy.max_*_per_run``) -- or keeps
  hammering ``company_create_task`` after the planning-cycle cap refused it: status ``failed``,
  exit reason ``runaway_<what>``;
* has produced no event or progress for ``policy.stall_seconds``: status ``failed``, exit reason
  ``stalled``;
* can no longer pay for itself: its measured cost passed the reservation and the reservation cannot
  be extended inside the budgets: status ``cancelled``, exit reason ``budget_exhausted``.

Failed trips count toward the company's circuit breaker.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import BudgetExceeded, Conflict
from app.models.enums import ReservationStatus
from app.models.ledger import BudgetReservation
from app.models.policy import CompanyPolicy
from app.models.run import TaskRun
from app.services import audit, ledger

REFUSED_CREATES_LIMIT = 3
TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True)
class Trip:
    code: str  # exit_reason / error code
    message: str
    status: str  # "failed" | "cancelled"


@dataclass
class RunGuard:
    """Per-run counters and the first trip. Thread-safe: the event sink and the monitor share it."""

    max_tool_calls: int
    max_artifacts: int
    max_tokens: int
    tool_calls: int = 0
    artifacts: int = 0
    tokens: int = 0
    refused_creates: int = 0
    measured_micros: int | None = None  # highest running cost the runtime reported
    measured_kind: str = "unknown"
    _trip: Trip | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def for_policy(cls, limits: CompanyPolicy) -> RunGuard:
        return cls(
            max_tool_calls=limits.max_tool_calls_per_run,
            max_artifacts=limits.max_artifacts_per_run,
            max_tokens=limits.max_tokens_per_run,
        )

    @property
    def tripped(self) -> Trip | None:
        with self._lock:
            return self._trip

    def trip(self, code: str, message: str, status: str = "failed") -> None:
        with self._lock:
            if self._trip is None:  # the first cause wins
                self._trip = Trip(code, message, status)

    def observe(self, type_: str, payload: dict[str, Any]) -> None:
        """Count one runtime event against the caps."""
        if type_ == "tool.started":
            self.tool_calls += 1
            if self.tool_calls > self.max_tool_calls:
                self.trip(
                    "runaway_tool_calls",
                    f"The run made more than {self.max_tool_calls} tool calls",
                )
        elif type_ == "artifact":
            self.artifacts += 1
            if self.artifacts > self.max_artifacts:
                self.trip(
                    "runaway_artifacts",
                    f"The run submitted more than {self.max_artifacts} artifacts",
                )
        elif type_ == "usage":
            total = sum(
                int(payload.get(k) or 0)
                for k in TOKEN_KEYS
                if isinstance(payload.get(k), int) and not isinstance(payload.get(k), bool)
            )
            self.tokens = max(self.tokens, total)  # the runtime reports running totals
            if self.tokens > self.max_tokens:
                self.trip("runaway_tokens", f"The run used more than {self.max_tokens} tokens")
        elif type_ == "tool.completed":
            if (
                payload.get("name") == "company_create_task"
                and payload.get("is_error")
                and "limit_exceeded" in str(payload.get("result_preview", ""))
            ):
                self.refused_creates += 1
                if self.refused_creates >= REFUSED_CREATES_LIMIT:
                    self.trip(
                        "runaway_tasks_created",
                        "The run kept creating tasks after the planning-cycle cap refused it",
                    )


def measured_kind(payload: dict[str, Any]) -> str:
    billing = payload.get("billing")
    kind = billing.get("cost_kind") if isinstance(billing, dict) else None
    return kind if kind in ("verified", "estimated") else "unknown"


def measured_cost(payload: dict[str, Any]) -> int | None:
    """Running cost (micro-USD) the adapter attached to a usage event, None when unpriced."""
    billing = payload.get("billing")
    if not isinstance(billing, dict):
        return None
    cost = billing.get("cost_micros")
    return int(cost) if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0 else None


def checkpoint(
    db: Session, run: TaskRun, cost_micros: int, limits: CompanyPolicy, now: datetime
) -> str:
    """Mid-run budget checkpoint: ``ok`` | ``extended`` | ``exhausted``.

    When the measured cost passed what the run reserved, the reservation is extended (by a chunk of
    headroom, else by just what was spent) if every budget still fits; if not, the run is out of
    money. The caller already holds the budget lock (``ledger.acquire_budget_lock`` was its first
    statement). Does not commit.
    """
    reservation = db.get(BudgetReservation, run.reservation_id) if run.reservation_id else None
    if reservation is None or reservation.status != ReservationStatus.HELD:
        return "ok"
    held = reservation.amount_micros
    if cost_micros <= held:
        return "ok"
    chunk = max(limits.default_run_reservation_micros, held // 4, 1)
    for extra in (cost_micros + chunk - held, cost_micros - held):
        try:
            ledger.extend_reservation(db, reservation, extra, now=now)
        except BudgetExceeded:
            continue
        except Conflict:
            return "ok"
        audit.record(
            db,
            company_id=run.company_id,
            principal=None,
            action="budget.reservation_extended",
            target_type="run",
            target_id=run.id,
            summary=f"Run budget extended from {held} to {held + extra} micro-USD",
            data={"from_micros": held, "to_micros": held + extra, "measured_micros": cost_micros},
        )
        return "extended"
    return "exhausted"
