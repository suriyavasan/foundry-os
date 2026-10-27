"""System GitHub operations go through the guardrail engine (decision D44).

Push, PR open/update, merge and branch delete are registered actions of ``kind="system"``. They are
checked with ``engine.check`` under the project lead's context (else the AI Co-founder's), so the
emergency stop, human deny / approval rules, rate limits and the guardrail log all apply. Their
action-level ``default_decision`` is ``allow`` (consent lives in the repository policy).

Payloads are deterministic dicts, so an approval binds to exactly what will happen:

* ``github.branch.push`` / ``github.pr.update`` / ``github.push.default``:
  ``{repo, branch, from_sha, to_sha, commits, files}``
* ``github.pr.open``: ``{repo, head, base, title, body}``
* ``github.pr.merge``: ``{repo, number, head_sha, method}``
* ``github.branch.delete``: ``{repo, branch, sha}``


"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.errors import (
    AppError,
    ApprovalPending,
    EmergencyStopped,
    GuardrailDenied,
    RateLimited,
)
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.guardrails import engine as guard_engine
from app.models.repository import ProjectRepository

if TYPE_CHECKING:
    from app.services.repo_sync import RepoEnv


@dataclass(frozen=True)
class GateDecision:
    """``allowed`` is False when the engine parked the action behind an approval
    (``approval_id`` set) or refused it (``reason`` says why: guardrail denied, rate limited,
    emergency stop)."""

    allowed: bool
    approval_id: str | None = None
    reason: str | None = None


def agent_ctx(agent: Any) -> CompanyContext:
    principal = Principal(PrincipalType.AGENT, agent.id, agent.name, agent.company_id)
    return CompanyContext(agent.company_id, principal, Role.AGENT)


def authorize(
    db: Session,
    binding: ProjectRepository,
    action: str,
    payload: dict[str, Any],
    *,
    env: RepoEnv,
) -> GateDecision:
    """Ask the guardrail engine whether the control plane may perform ``action`` now.

    The actor is ``repo_sync.actor_for(binding)``. ``ApprovalPending`` -> ``allowed=False`` with
    the approval id (the sync then keeps the exact payload until the approval is decided);
    ``GuardrailDenied`` / ``RateLimited`` / ``EmergencyStopped`` -> ``allowed=False``.

    The engine commits the session (its log, rate hits and approvals are part of the answer)."""
    from app.services import repo_sync  # local: repo_sync imports this module

    try:
        actor = repo_sync.actor_for(db, binding)
    except AppError as exc:
        return GateDecision(False, reason=f"no_actor: {exc.message}")
    try:
        guard_engine.check(
            db,
            agent_ctx(actor),
            action,
            payload,
            env=guard_engine.GuardEnv(cipher=env.cipher),
            now=env.now(),
        )
    except ApprovalPending as exc:
        return GateDecision(
            False, approval_id=exc.fields.get("approval_id"), reason="approval_pending"
        )
    except EmergencyStopped:
        return GateDecision(False, reason="emergency_stop")
    except RateLimited as exc:
        return GateDecision(False, reason=f"rate_limited: {exc.message}")
    except GuardrailDenied as exc:
        return GateDecision(False, reason=f"guardrail_denied: {exc.message}"[:500])
    return GateDecision(True)
