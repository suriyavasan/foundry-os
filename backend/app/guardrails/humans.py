"""The human-only gate for account and team routes (docs/specs/m7, m6 section 1).

Account management is not an agent capability at all. These routes live outside the agent API, but
an agent's run token is accepted by ``get_principal`` everywhere, so every account / team route
passes through ``deny_agent``: it answers 403 ``guardrail_denied`` and writes a ``guardrail_events``
row (decision ``deny``) so the attempt is visible in the Guardrails panel.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import GuardrailDenied
from app.core.principal import Principal
from app.guardrails import log, registry


def deny_agent(db: Session, principal: Principal, action: str) -> None:
    """Raise 403 for an agent principal; a no-op for humans."""
    if not principal.is_agent:
        return
    spec = registry.HUMAN_ONLY.get(action)
    tier = spec.tier if spec else "identity"
    if principal.company_id:
        log.record(
            db,
            company_id=principal.company_id,
            action=action,
            tier=tier,
            decision="deny",
            agent_id=principal.id,
            reason=f"{action} is human-only: agents can never do it",
        )
        db.commit()
    raise GuardrailDenied(
        "Agents cannot manage accounts, members, invitations, tokens or sessions",
        details={"reason": "human_only", "action": action, "rule_id": None},
    )
