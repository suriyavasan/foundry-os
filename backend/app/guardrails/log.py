"""The guardrail event log (``guardrail_events``): one row per decision, with a payload digest and
never the payload. Also mirrors each row to the live-update outbox as ``guardrail.event``."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.principal import CompanyContext
from app.models.guardrails import GuardrailEvent
from app.services import events
from app.services.pagination import paginate
from app.services.secrets_scan import scrub

VOLATILE_KEYS = frozenset({"idempotency_key", "approval_id"})
EVENT_DECISIONS = frozenset(
    {"allow", "peer_review", "cofounder_approval", "human_approval", "deny", "rate_limited"}
)
MAX_REASON = 500


def scope_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """The part of a payload an approval binds to: everything except volatile client keys."""
    return {k: v for k, v in (payload or {}).items() if k not in VOLATILE_KEYS}


def payload_digest(payload: dict[str, Any] | None) -> str:
    canonical = json.dumps(
        scope_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def record(
    db: Session,
    *,
    company_id: str,
    action: str,
    tier: str,
    decision: str,
    agent_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    rule_id: str | None = None,
    approval_id: str | None = None,
    payload_digest: str | None = None,
    reason: str | None = None,
) -> GuardrailEvent:
    """Add a guardrail event (and its outbox mirror) to the caller's transaction. No commit."""
    assert decision in EVENT_DECISIONS, decision
    row = GuardrailEvent(
        company_id=company_id,
        agent_id=agent_id,
        run_id=run_id,
        task_id=task_id,
        action=action[:80],
        tier=tier,
        decision=decision,
        rule_id=rule_id,
        approval_id=approval_id,
        payload_digest=payload_digest,
        reason=(scrub(reason) or "")[:MAX_REASON] or None,
    )
    db.add(row)
    db.flush()
    events.emit(
        db,
        company_id,
        "guardrail.event",
        {
            "event_id": row.id,
            "action": row.action,
            "tier": row.tier,
            "decision": row.decision,
            "agent_id": agent_id,
            "rule_id": rule_id,
            "approval_id": approval_id,
        },
    )
    return row


def list_events(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    decision: str | None = None,
    tier: str | None = None,
    agent_id: str | None = None,
):
    stmt = select(GuardrailEvent).where(GuardrailEvent.company_id == ctx.company_id)
    if decision:
        stmt = stmt.where(GuardrailEvent.decision == decision)
    if tier:
        stmt = stmt.where(GuardrailEvent.tier == tier)
    if agent_id:
        stmt = stmt.where(GuardrailEvent.agent_id == agent_id)
    return paginate(db, stmt, GuardrailEvent, limit=limit, cursor=cursor, descending=True)
