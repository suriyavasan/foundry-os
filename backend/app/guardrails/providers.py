"""Credential grants and control-plane provider actions (docs/specs/m6 section 5).

Agents never hold a credential. To act on the outside world they ask the control plane to perform a
*provider action* (``email.send``, ``github.pr.open``...) with a stored credential. The control
plane
checks that the credential's scope allows that action for that agent's role family, runs the
guardrail engine (tier, rules, rate limits, outbound DLP, peer review / approvals), reveals the
secret *inside this process* for the single call, hands it to a provider executor and returns only
the executor's (scrubbed) result.

The framework is built and tested in M6. Executors for real providers (SMTP/IMAP, GitHub, ...)
arrive in M8: until one is registered for a credential's provider, the action answers 501
``{available: false, milestone: "M8"}`` -- nothing is faked.
"""

from __future__ import annotations

import fnmatch
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.crypto import CredentialCipher
from app.core.errors import Forbidden, GuardrailDenied, NotAvailable, ValidationFailed
from app.guardrails import log, registry
from app.guardrails.engine import GuardEnv
from app.models.agent import Agent
from app.models.enums import ActionTier
from app.models.policy import CompanyPolicy
from app.models.provider import Credential
from app.services import audit, credentials
from app.services.agent_auth import AgentRunContext
from app.services.redaction import redact_payload

DEFAULT_DISCLOSURE = "This message was written by an AI agent working for {company}."


@dataclass(frozen=True)
class ProviderCall:
    """One call handed to an executor. ``secret`` exists only for the duration of the call."""

    action: str
    params: dict[str, Any]
    secret: str = field(repr=False)
    company_id: str
    agent_id: str
    run_id: str


class ProviderExecutor(Protocol):
    provider: str
    actions: frozenset[str]

    def execute(self, call: ProviderCall) -> dict[str, Any]: ...


class ProviderRegistry:
    """Executors by provider kind. Empty by default (M8 registers the real ones)."""

    def __init__(self) -> None:
        self._executors: dict[str, ProviderExecutor] = {}
        self._lock = threading.Lock()

    def register(self, executor: ProviderExecutor) -> None:
        with self._lock:
            self._executors[executor.provider] = executor

    def get(self, provider: str | None) -> ProviderExecutor | None:
        with self._lock:
            return self._executors.get(provider or "")

    def providers(self) -> list[str]:
        with self._lock:
            return sorted(self._executors)


def action_allowed(allowed: list[str], action: str) -> bool:
    """``allowed`` holds exact actions or ``prefix.*`` patterns. Empty = nothing is allowed."""
    return any(fnmatch.fnmatchcase(action, pattern) for pattern in allowed)


def check_grant(credential: Credential, agent: Agent, action: str) -> None:
    """The credential's scope must allow ``action`` for the agent's role family (403 otherwise)."""
    if not action_allowed(list(credential.allowed_actions or []), action):
        raise Forbidden(
            "This credential is not scoped for that action",
            details={"credential_id": credential.id, "action": action},
        )
    families = list(credential.allowed_families or [])
    if families and agent.role_family not in families:
        raise Forbidden(
            "This credential is not available to your role family",
            details={"credential_id": credential.id, "family": agent.role_family},
        )


def _provider_spec(action: str) -> registry.ActionSpec | None:
    return registry.PROVIDER_ACTIONS.get(action)


def precheck(
    db: Session,
    rc: AgentRunContext,
    body: dict[str, Any],
    providers: ProviderRegistry,
) -> str:
    """Validate a provider-action request BEFORE the guardrail engine can park it behind a review
    (nobody should review an action that could never run). Returns the action name."""
    action = body.get("action")
    spec = _provider_spec(action) if isinstance(action, str) else None
    if spec is None:
        raise GuardrailDenied(
            f"Unknown provider action {action!r}: it is not in the action registry (denied)",
            details={"action": action},
        )
    credential_id = body.get("credential_id")
    if not isinstance(credential_id, str) or not credential_id:
        raise ValidationFailed("credential_id is required")
    credential = credentials.require_usable(db, rc.ctx.company_id, credential_id)
    check_grant(credential, rc.agent, action)
    executor = providers.get(credential.provider)
    if executor is None or action not in executor.actions:
        raise NotAvailable(
            f"No provider is connected for {action!r}: connect one first",
            milestone="M8",
            details={"action": action, "provider": credential.provider},
        )
    return action


def disclosure_footer(limits: CompanyPolicy, company_name: str) -> str | None:
    if not limits.ai_disclosure_enabled:
        return None
    text = (limits.ai_disclosure_text or DEFAULT_DISCLOSURE).strip()
    return text.replace("{company}", company_name)


def with_disclosure(
    action: str, params: dict[str, Any], limits: CompanyPolicy, company_name: str
) -> dict[str, Any]:
    """Outbound e-mail carries the AI disclosure footer (agents must not pass as humans)."""
    if action != "email.send":
        return params
    footer = disclosure_footer(limits, company_name)
    body = str(params.get("body") or "")
    if footer and footer not in body:
        params = {**params, "body": f"{body.rstrip()}\n\n-- \n{footer}"}
    return params


def execute(
    db: Session,
    rc: AgentRunContext,
    cipher: CredentialCipher,
    providers: ProviderRegistry,
    body: dict[str, Any],
    *,
    limits: CompanyPolicy,
    company_name: str,
) -> dict[str, Any]:
    """Run the provider action (the guardrail engine already allowed it). The secret is revealed
    here, used for this one call and never returned, logged or audited."""
    action = body["action"]
    credential = credentials.require_usable(db, rc.ctx.company_id, body["credential_id"])
    check_grant(credential, rc.agent, action)
    executor = providers.get(credential.provider)
    if executor is None or action not in executor.actions:
        raise NotAvailable("No provider is connected for this action", milestone="M8")
    params = with_disclosure(action, dict(body.get("params") or {}), limits, company_name)
    secret = credentials.reveal(db, cipher, rc.ctx.company_id, credential.id)
    call = ProviderCall(
        action=action,
        params=params,
        secret=secret,
        company_id=rc.ctx.company_id,
        agent_id=rc.agent.id,
        run_id=rc.run.id,
    )
    try:
        raw = executor.execute(call)
    finally:
        del call
    result = redact_payload(raw if isinstance(raw, dict) else {"value": raw}, [secret])
    audit.record(
        db,
        company_id=rc.ctx.company_id,
        principal=rc.ctx.principal,
        action="provider_action.executed",
        target_type="credential",
        target_id=credential.id,
        summary=f"{rc.agent.name} ran {action}",
        data={
            "action": action,
            "provider": credential.provider,
            "payload_digest": log.payload_digest({"action": action, "params": params}),
        },
    )
    db.commit()
    return result


def tier_of(action: str) -> str:
    spec = _provider_spec(action)
    return spec.tier if spec else ActionTier.DESTRUCTIVE.value


Executor = Callable[[ProviderCall], dict[str, Any]]


class FunctionExecutor:
    """Adapts a plain function into a ``ProviderExecutor`` (used by tests and small providers)."""

    def __init__(self, provider: str, actions: set[str] | frozenset[str], fn: Executor) -> None:
        self.provider = provider
        self.actions = frozenset(actions)
        self._fn = fn

    def execute(self, call: ProviderCall) -> dict[str, Any]:
        return self._fn(call)


__all__ = [
    "FunctionExecutor",
    "GuardEnv",
    "ProviderCall",
    "ProviderExecutor",
    "ProviderRegistry",
    "action_allowed",
    "check_grant",
    "disclosure_footer",
    "execute",
    "precheck",
    "tier_of",
    "with_disclosure",
]
