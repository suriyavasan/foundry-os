"""Guardrail API schemas (Milestone 6). Names match web/src/api/contract.check.ts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

ActionTierLiteral = Literal[
    "read",
    "internal_write",
    "org_change",
    "external_read",
    "external_communication",
    "external_commitment",
    "spend",
    "identity",
    "destructive",
]
GuardrailDecisionLiteral = Literal[
    "allow", "peer_review", "cofounder_approval", "human_approval", "deny"
]
GuardrailEventDecisionLiteral = Literal[
    "allow", "peer_review", "cofounder_approval", "human_approval", "deny", "rate_limited"
]
GuardrailScopeLiteral = Literal["all", "family", "agent"]
ActorTypeLiteral = Literal["human", "agent", "system"]


class GuardrailRule(ApiModel):
    id: str
    company_id: str
    tier: ActionTierLiteral
    action: str | None
    scope_type: GuardrailScopeLiteral
    scope_value: str | None
    decision: GuardrailDecisionLiteral
    conditions: dict[str, Any]
    created_by_type: ActorTypeLiteral | None = None
    created_by_id: str | None = None
    created_at: Timestamp
    updated_at: Timestamp


class GuardrailRuleCreate(RequestModel):
    tier: ActionTierLiteral
    action: str | None = Field(default=None, max_length=80)
    scope_type: GuardrailScopeLiteral
    scope_value: str | None = Field(default=None, max_length=36)
    decision: GuardrailDecisionLiteral
    conditions: dict[str, Any] = Field(default_factory=dict)


class GuardrailRuleUpdate(RequestModel):
    tier: ActionTierLiteral | None = None
    action: str | None = Field(default=None, max_length=80)
    scope_type: GuardrailScopeLiteral | None = None
    scope_value: str | None = Field(default=None, max_length=36)
    decision: GuardrailDecisionLiteral | None = None
    conditions: dict[str, Any] | None = None


class GuardrailEvent(ApiModel):
    id: str
    company_id: str
    created_at: Timestamp
    agent_id: str | None
    run_id: str | None
    task_id: str | None
    action: str
    tier: ActionTierLiteral
    decision: GuardrailEventDecisionLiteral
    rule_id: str | None
    approval_id: str | None
    payload_digest: str | None
    reason: str | None


class RateLimitStatus(BaseModel):
    agent_id: str | None
    key: str
    limit: int
    used: int
    window_seconds: int
    resets_at: Timestamp | None


class RateLimitList(BaseModel):
    items: list[RateLimitStatus]
    next_cursor: str | None = None


class EffectiveTier(BaseModel):
    tier: ActionTierLiteral
    decision: GuardrailDecisionLiteral
    source: Literal["default", "rule"]
    rule_id: str | None


class EffectiveTool(BaseModel):
    name: str
    tier: ActionTierLiteral
    decision: GuardrailDecisionLiteral
    rule_id: str | None


class EffectivePermissions(BaseModel):
    agent_id: str
    tiers: list[EffectiveTier]
    tools: list[EffectiveTool]
    rate_limits: list[RateLimitStatus] = Field(default_factory=list)


class EmergencyStopStatus(BaseModel):
    active: bool
    activated_at: Timestamp | None
    activated_by: str | None
    reason: str | None
    runs_cancelled: int | None = None


class EmergencyStopRequest(RequestModel):
    reason: str | None = Field(default=None, max_length=500)


class SystemStatus(BaseModel):
    emergency_stop: EmergencyStopStatus
    version: str


# ------------------------------------------------------------------ agent API (Milestone 6)


class ToolCheck(RequestModel):
    tool: str = Field(min_length=1, max_length=80)


class ToolCheckResult(BaseModel):
    allowed: bool
    tool: str
    tier: ActionTierLiteral


class InjectionFlag(RequestModel):
    tool: str = Field(min_length=1, max_length=80)
    source: str | None = Field(default=None, max_length=80)
    categories: list[str] = Field(default_factory=list, max_length=20)


class FlagResult(BaseModel):
    recorded: bool = True


class PeerReviewDecision(RequestModel):
    decision: Literal["approve", "reject", "request_changes"]
    note: str | None = Field(default=None, max_length=10_000)


class ProviderActionRequest(RequestModel):
    credential_id: str = Field(min_length=1, max_length=36)
    action: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


class ProviderActionResult(BaseModel):
    status: Literal["executed"] = "executed"
    action: str
    result: dict[str, Any] = Field(default_factory=dict)
