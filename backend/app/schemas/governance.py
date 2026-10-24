from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp
from app.schemas.guardrails import ActionTierLiteral
from app.schemas.policy import ApprovalKindLiteral
from app.schemas.work import ActorTypeLiteral

ApprovalStatusLiteral = Literal["pending", "approved", "rejected", "changes_requested", "expired"]
ApproverLiteral = Literal["human", "founder", "peer"]
RecurrenceLiteral = Literal["one_time", "monthly", "yearly"]
RiskLiteral = Literal["low", "medium", "high"]
MAX_JSON_BYTES = 50_000


def _check_json(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, default=str)) > MAX_JSON_BYTES:
        raise ValueError("must be under 50KB")
    return value


class DlpFinding(ApiModel):
    """A finding of the outbound DLP scan. The matched text is never part of it."""

    kind: str
    severity: Literal["low", "medium", "high"] | None = None
    description: str | None = None
    count: int | None = None


class ApprovalOutbound(ApiModel):
    channel: str | None = None
    to: str | None = None
    subject: str | None = None
    body: str


class Approval(ApiModel):
    id: str
    company_id: str
    kind: ApprovalKindLiteral
    status: ApprovalStatusLiteral
    approver: ApproverLiteral
    requested_by_agent_id: str | None
    task_id: str | None
    title: str
    reason: str | None
    amount_micros: int | None
    vendor: str | None
    recurrence: RecurrenceLiteral | None
    risk: RiskLiteral
    evidence: dict[str, Any]
    scope: dict[str, Any]
    scope_hash: str
    decided_by_type: ActorTypeLiteral | None
    decided_by_id: str | None
    decided_at: Timestamp | None
    decision_note: str | None
    consumed_at: Timestamp | None
    subject_type: str | None = None  # e.g. "agent_hire"
    subject_id: str | None = None
    # M6: the guarded action (guardrail approvals), the peer reviewer and what is under review.
    tier: ActionTierLiteral | None = None
    action: str | None = None
    reviewer_agent_id: str | None = None
    approver_ref: str | None = None  # M7: `member:<id>` when scoped to one human
    outbound: ApprovalOutbound | None = None
    dlp_findings: list[DlpFinding] | None = None
    created_at: Timestamp
    updated_at: Timestamp


class ApprovalCreate(RequestModel):
    kind: ApprovalKindLiteral
    title: str = Field(min_length=1, max_length=300)
    reason: str | None = Field(default=None, max_length=10_000)
    amount_micros: int | None = Field(default=None, ge=0, le=10**15)
    vendor: str | None = Field(default=None, max_length=200)
    recurrence: RecurrenceLiteral | None = None
    risk: RiskLiteral = "low"
    evidence: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    # M7: route the decision to one human (`member:<id>`); only owners/admins may set it.
    approver_ref: str | None = Field(default=None, max_length=48)

    _evidence = field_validator("evidence")(_check_json)
    _scope = field_validator("scope")(_check_json)


class ApproverAssign(RequestModel):
    """Route a pending human approval to one person (or back to any owner/admin with null)."""

    approver_ref: str | None = Field(default=None, max_length=48)  # member:<id> | null


class ApprovalCreated(ApiModel):
    id: str
    status: ApprovalStatusLiteral
    approver: ApproverLiteral


class DecisionBody(RequestModel):
    note: str | None = Field(default=None, max_length=10_000)


class AgentDecideBody(RequestModel):
    decision: Literal["approve", "reject", "request_changes"]
    note: str | None = Field(default=None, max_length=10_000)


class Decision(ApiModel):
    id: str
    company_id: str
    made_by_type: ActorTypeLiteral
    made_by_id: str | None
    title: str
    rationale: str | None
    related_task_id: str | None
    related_project_id: str | None
    context: dict[str, Any]
    created_at: Timestamp


class DecisionCreate(RequestModel):
    title: str = Field(min_length=1, max_length=300)
    rationale: str | None = Field(default=None, max_length=20_000)
    related_task_id: str | None = None
    related_project_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)

    _context = field_validator("context")(_check_json)
