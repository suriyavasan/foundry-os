"""M18 Part B: one event stream and one usage total for the several runtime calls of a graph run.

A graph run calls its runtime more than once (each act, the self-check). Every adapter numbers its
events from 1 and reports running token/cost totals of its own call, while the run's events are
idempotent on ``seq`` and the budget guard reads running totals. ``GraphSink`` therefore shifts the
sequence numbers of each call behind the previous one and adds the earlier calls' usage to the
running totals of the later ones; ``UsageTotals`` sums the final reports.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from app.core.time import utcnow
from app.models.enums import CostKind
from app.runtimes.base import EventSink, ExecutionEvent, UsageReport

_WORSE = {CostKind.UNKNOWN: 0, CostKind.ESTIMATED: 1, CostKind.VERIFIED: 2}


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    cost_known: bool = True
    kind: CostKind | None = None
    subscription: bool = False
    calls: int = 0

    def add(self, usage: UsageReport | None) -> None:
        if usage is None:
            return
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        if usage.billing_note == "subscription":
            self.subscription = True
        if usage.cost_micros is None:
            self.cost_known = False
        else:
            self.cost_micros += max(0, int(usage.cost_micros))
        kind = CostKind(usage.cost_kind)
        if self.kind is None or _WORSE[kind] < _WORSE[self.kind]:
            self.kind = kind

    def report(self) -> UsageReport | None:
        if not self.calls:
            return None
        return UsageReport(
            input_tokens=self.input_tokens or None,
            output_tokens=self.output_tokens or None,
            cost_micros=self.cost_micros if self.cost_known else None,
            cost_kind=(self.kind or CostKind.UNKNOWN) if self.cost_known else CostKind.UNKNOWN,
            billing_note="subscription" if self.subscription else None,
        )


class GraphSink:
    def __init__(self, inner: EventSink, totals: UsageTotals) -> None:
        self._inner = inner
        self._totals = totals
        self._lock = threading.Lock()
        self._next_seq = 1
        self._offset = 0

    def begin_call(self) -> None:
        with self._lock:
            self._offset = self._next_seq - 1

    def emit(self, event: ExecutionEvent) -> None:
        with self._lock:
            seq = event.seq + self._offset
            self._next_seq = max(self._next_seq, seq + 1)
            offset_tokens = self._totals.input_tokens + self._totals.output_tokens
            offset_cost = self._totals.cost_micros
        payload = event.payload
        if event.type == "usage" and isinstance(payload, dict):
            payload = self._shift_usage(payload, offset_tokens, offset_cost)
        self._inner.emit(ExecutionEvent(seq=seq, type=event.type, payload=payload, ts=event.ts))

    @staticmethod
    def _shift_usage(payload: dict[str, Any], tokens: int, cost: int) -> dict[str, Any]:
        out = dict(payload)
        if tokens and isinstance(out.get("input_tokens"), int):
            out["input_tokens"] = out["input_tokens"] + tokens
        billing = out.get("billing")
        if cost and isinstance(billing, dict) and isinstance(billing.get("cost_micros"), int):
            out["billing"] = {**billing, "cost_micros": billing["cost_micros"] + cost}
        return out

    def node(self, node: str, outcome: str, **extra: Any) -> None:
        """A graph transition, as a run event (``graph.node``) on the run timeline."""
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
        text = f"{node}: {outcome}"
        self._inner.emit(
            ExecutionEvent(
                seq=seq,
                type="graph.node",
                payload={"node": node, "outcome": outcome, "summary": text, **extra},
                ts=utcnow(),
            )
        )
