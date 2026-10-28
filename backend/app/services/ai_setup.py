"""The AI readiness gate (docs/specs/m10 section 1, replaced by the AI stack in M17).

A company is **ready** when its AI stack (``app.services.ai_stack``) has an enabled connection with a
passing test that covers heavy work and one that covers everyday work (may be the same). Until then:

* the readiness pass of the task state machine keeps tasks in the backlog with
  ``blocked_reason="ai_setup_required"`` (so the dispatcher has nothing to dispatch), the wake queue
  does not wake anyone, and creating notes / commands / tasks / hires is a 409 ``ai_setup_required``;
* owner settings, providers, credentials, the AI stack, auth and team stay available.

``ENFORCED`` exists for the test-suite only (older tests build companies without an AI connection); the
gate has no configuration switch. ``PUT /ai-setup`` is gone (410): the stack is changed through ``/ai-stack``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AiSetupRequired
from app.core.principal import CompanyContext
from app.services import ai_stack

ENFORCED = True
AI_SETUP_REQUIRED = "ai_setup_required"  # Task.blocked_reason while the gate is closed


@dataclass
class Readiness:
    ready: bool
    blocking_reason: str | None
    stack_count: int = 0


def evaluate(db: Session, company_id: str, cli_env: Any = None) -> Readiness:
    ready, reason, count = ai_stack.readiness(db, company_id)
    return Readiness(ready, reason, count)


def is_ready(db: Session, company_id: str) -> bool:
    return (not ENFORCED) or evaluate(db, company_id).ready


def require_ready(db: Session, company_id: str) -> None:
    """409 ``ai_setup_required`` until the company has a working AI connection."""
    if not ENFORCED:
        return
    state = evaluate(db, company_id)
    if not state.ready:
        raise AiSetupRequired(details={"reason": state.blocking_reason})


def release_gate(db: Session, company_id: str) -> None:
    """The AI became ready (or the stack changed): tasks parked behind the gate are re-evaluated.
    No commit."""
    from app.services import tasks

    tasks.recompute_readiness(db, company_id)


def status(db: Session, ctx: CompanyContext, cli_env: Any) -> dict[str, Any]:
    from app.services import cli_runtimes

    state = evaluate(db, ctx.company_id, cli_env)
    return {
        "ready": state.ready if ENFORCED else True,
        "blocking_reason": state.blocking_reason,
        "stack_count": state.stack_count,
        "clis": cli_runtimes.list_status(db, cli_env),
    }
