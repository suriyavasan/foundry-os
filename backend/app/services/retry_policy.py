"""How a failed run is retried: transient (provider busy, network) versus permanent failures.

A **transient** failure says nothing about the task or the agent: a 429, an overloaded provider, a
dropped connection, a worker that vanished. It refunds the task's attempt, waits ``backoff`` and
does not count for the circuit breaker. A **permanent** failure consumes an attempt, follows the
slower permanent schedule and counts for the breaker.

Classification is by error code or message text. The provider phrases are the documented ones; real
provider wording was not exercised against live APIs (spec M12 section 5).
"""

from __future__ import annotations

import re

TRANSIENT = "transient"
PERMANENT = "permanent"

# Error codes the platform itself raises when the infrastructure (not the task) failed.
TRANSIENT_CODES = frozenset({"unavailable", "worker_lost", "job_dead", "merge_conflict"})
# Transient for the circuit breaker (the work is fine, main moved on) but the attempt is spent:
# a conflict that keeps coming back is the task's problem, so ``max_attempts_per_task`` bounds it.
ATTEMPT_CONSUMING_CODES = frozenset({"merge_conflict"})
MERGE_CONFLICT = "merge_conflict"
_TRANSIENT_PHRASES = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "overload_error",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
)
# HTTP statuses only as whole numbers (not inside an id or a port such as 15032).
_TRANSIENT_STATUS = re.compile(r"(?<![0-9])(?:429|502|503|504|529)(?![0-9])")

MAX_TRANSIENT_FAILURES = 6  # in a row; the sixth consecutive one consumes the attempt after all
MAX_BACKOFF_SECONDS = 900
TRANSIENT_BASE_SECONDS = 30
# Permanent failures: the first retry is immediate, then 60 s, then 240 s (x4 each), capped.
PERMANENT_SCHEDULE_SECONDS = (0, 60, 240)


def classify(code: str | None, message: str | None = None) -> str:
    """``transient`` or ``permanent`` for a run failure."""
    if (code or "").strip().lower() in TRANSIENT_CODES:
        return TRANSIENT
    text = f"{code or ''} {message or ''}".lower()
    if any(phrase in text for phrase in _TRANSIENT_PHRASES) or _TRANSIENT_STATUS.search(text):
        return TRANSIENT
    return PERMANENT


def backoff(kind: str, n: int) -> int:
    """Seconds to wait before the next attempt after the ``n``-th consecutive failure of ``kind``
    (``n`` starts at 1). No jitter, so tests and operators can predict it.

    * permanent: 0 s, 60 s, 240 s, then 900 s (cap);
    * transient: ``30 * 2^(n-1)`` s, capped at 900 s.
    """
    n = max(1, int(n))
    if kind == TRANSIENT:
        return min(MAX_BACKOFF_SECONDS, TRANSIENT_BASE_SECONDS * 2 ** min(n - 1, 10))
    if n <= len(PERMANENT_SCHEDULE_SECONDS):
        return PERMANENT_SCHEDULE_SECONDS[n - 1]
    return MAX_BACKOFF_SECONDS


def is_transient_run(error: dict | None, exit_reason: str | None = None) -> bool:
    """Classify a stored run (its ``error`` JSON and ``exit_reason``) for the circuit breaker."""
    error = error or {}
    code = str(error.get("code") or exit_reason or "")
    return classify(code, str(error.get("message") or "")) == TRANSIENT


__all__ = [
    "ATTEMPT_CONSUMING_CODES",
    "MAX_BACKOFF_SECONDS",
    "MERGE_CONFLICT",
    "MAX_TRANSIENT_FAILURES",
    "PERMANENT",
    "TRANSIENT",
    "backoff",
    "classify",
    "is_transient_run",
]
