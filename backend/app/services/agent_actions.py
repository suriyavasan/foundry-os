"""Agent-API-only actions that don't belong to a shared service."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.enums import AgentKind, AgentStatus
from app.models.governance import Approval
from app.models.run import RunEvent
from app.services import events, runs
from app.services.agent_auth import AgentRunContext
from app.services.approvals import get_approval
from app.services.redaction import redact_payload

AGENT_EVENT_SEQ_BASE = 1_000_000  # keeps agent-API events clear of the adapter's own seq range


def post_progress(db: Session, rc: AgentRunContext, message: str) -> RunEvent:
    """Record a progress note: a RunEvent of type ``progress`` plus an activity update."""
    run = rc.run
    top = db.scalar(
        select(func.max(RunEvent.seq)).where(
            RunEvent.run_id == run.id, RunEvent.seq >= AGENT_EVENT_SEQ_BASE
        )
    )
    seq = (top or AGENT_EVENT_SEQ_BASE) + 1
    now = utcnow()
    payload = redact_payload({"message": message})
    event = runs.append_event(db, run, seq=seq, type_="progress", payload=payload, occurred_at=now)
    assert event is not None
    run.activity_summary = payload["message"][:300]
    run.activity_at = now
    events.emit(
        db,
        run.company_id,
        "run.event",
        {
            "run_id": run.id,
            "task_id": run.task_id,
            "agent_id": run.agent_id,
            "seq": seq,
            "type": "progress",
            "summary": payload["message"][:200],
        },
    )
    events.emit(
        db,
        run.company_id,
        "agent.activity",
        {
            "agent_id": run.agent_id,
            "state": "working",
            "task_id": run.task_id,
            "run_id": run.id,
            "summary": payload["message"][:200],
        },
    )
    db.commit()
    return event


def get_approval_for_agent(db: Session, rc: AgentRunContext, approval_id: str) -> Approval:
    """Poll an approval: only its requester or the founder may read it."""
    approval = get_approval(db, rc.ctx, approval_id)
    agent = db.get(Agent, rc.ctx.principal.id)
    is_founder = (
        agent is not None and agent.kind == AgentKind.FOUNDER and agent.status == AgentStatus.ACTIVE
    )
    if approval.requested_by_agent_id != rc.ctx.principal.id and not is_founder:
        raise Forbidden("Only the requester or the founder can read this approval")
    return approval
