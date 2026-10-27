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

Step 0: contracts only; WP-B implements ``authorize``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.models.repository import ProjectRepository
from app.services.repo_errors import not_built

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
    ``GuardrailDenied`` / ``RateLimited`` / ``EmergencyStopped`` -> ``allowed=False``."""
    raise not_built()
