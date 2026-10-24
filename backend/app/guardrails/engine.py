"""``guardrails.check``: the one decision point for every agent action (docs/specs/m6 section 1).

``check(db, ctx, action, payload)`` either returns a :class:`Verdict` (the action may run now) or
raises, always after committing its audit trail:

* ``GuardrailDenied`` (403 ``guardrail_denied``) -- an unregistered action (fail closed), a ``deny``
  rule, or outbound DLP findings;
* ``RateLimited`` (429 ``rate_limited``);
* ``ApprovalPending`` (202) -- the action waits behind a peer review / Co-founder / human approval;
* ``EmergencyStopped`` (503) -- the instance-wide emergency stop is active.

Approvals are bound to the exact action: the scope is ``{action, agent, payload digest}`` and its
hash is what ``approvals.consume`` checks, so an approval authorizes one execution of one identical
payload by the agent that asked, once. The agent repeats the identical call after it is woken; the
engine finds the approved, unconsumed approval for that hash and spends it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.core.errors import (
    ApprovalPending,
    EmergencyStopped,
    Forbidden,
    GuardrailDenied,
    RateLimited,
)
from app.core.principal import CompanyContext
from app.core.time import utcnow
from app.guardrails import dlp, log, peer, ratelimit, registry, rules
from app.guardrails.registry import ActionSpec
from app.models.agent import Agent
from app.models.enums import (
    ActionTier,
    AgentKind,
    AgentStatus,
    AlertSeverity,
    ApprovalStatus,
    Approver,
    GuardrailDecision,
)
from app.models.governance import Approval
from app.models.guardrails import GuardrailEvent
from app.models.provider import Credential
from app.models.run import TaskRun
from app.services import alerts, approvals, emergency, policy
from app.services.redaction import redact_payload
from app.services.secrets_scan import scrub

D = GuardrailDecision
REJECTION_MEMORY = timedelta(hours=24)
RATE_ALERT_WINDOW = timedelta(minutes=10)
RATE_ALERT_REPEATS = 3
_KIND_BY_TIER = {
    ActionTier.EXTERNAL_COMMUNICATION: "external_communication",
    ActionTier.SPEND: "external_purchase",
    ActionTier.EXTERNAL_COMMITMENT: "sensitive_action",
    ActionTier.IDENTITY: "sensitive_action",
    ActionTier.DESTRUCTIVE: "sensitive_action",
    ActionTier.ORG_CHANGE: "sensitive_action",
}


@dataclass
class GuardEnv:
    """What the engine needs from the application to recognize the company's own secrets."""

    owner_token: str | None = None
    cipher: CredentialCipher | None = None
    extra_secrets: tuple[str, ...] = ()

    def own_secrets(self, db: Session, company_id: str) -> list[str]:
        found: list[str] = [self.owner_token or "", *self.extra_secrets]
        if self.cipher is not None:
            rows = db.scalars(
                select(Credential).where(
                    Credential.company_id == company_id, Credential.revoked_at.is_(None)
                )
            )
            for row in rows:
                try:
                    found.append(self.cipher.decrypt(row.encrypted_value))
                except (CredentialDecryptError, ValueError):
                    continue
        return [s for s in found if s and len(s) >= dlp.MIN_SECRET_LEN]


@dataclass(frozen=True)
class Verdict:
    """The action may run now."""

    action: str
    tier: str
    rule_id: str | None = None
    approval_id: str | None = None
    consumed: bool = False
    rate_keys: tuple[str, ...] = ()


def _scrub_values(node: Any) -> Any:
    """Secret-looking spans in string values replaced (keys were handled by ``redact_payload``)."""
    if isinstance(node, str):
        return scrub(node)
    if isinstance(node, dict):
        return {k: _scrub_values(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub_values(v) for v in node]
    return node


def _title(action: str, payload: dict[str, Any]) -> str:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else payload
    bits = [action]
    if params.get("to"):
        bits.append(f"to {params['to']}")
    if params.get("subject"):
        bits.append(f"- {params['subject']}")
    elif params.get("title"):
        bits.append(f"- {params['title']}")
    return (scrub(" ".join(str(b) for b in bits)) or action)[:300]


def _deny(
    db: Session,
    base: dict[str, Any],
    action: str,
    tier: str,
    reason: str,
    *,
    rule_id: str | None = None,
    details: dict[str, Any] | None = None,
    approval_id: str | None = None,
) -> NoReturn:
    log.record(
        db,
        action=action,
        tier=tier,
        decision="deny",
        rule_id=rule_id,
        approval_id=approval_id,
        reason=reason,
        **base,
    )
    db.commit()
    raise GuardrailDenied(
        reason, details={"action": action, "tier": tier, "rule_id": rule_id, **(details or {})}
    )


def _maybe_alert_repeated(db: Session, base: dict[str, Any], key: str, now: datetime) -> None:
    count = db.scalar(
        select(func.count(GuardrailEvent.id)).where(
            GuardrailEvent.company_id == base["company_id"],
            GuardrailEvent.agent_id == base["agent_id"],
            GuardrailEvent.decision == "rate_limited",
            GuardrailEvent.reason.like(f"{key}:%"),
            GuardrailEvent.created_at >= now - RATE_ALERT_WINDOW,
        )
    )
    if (count or 0) >= RATE_ALERT_REPEATS:
        alerts.raise_alert(
            db,
            base["company_id"],
            kind="rate_limit",
            severity=AlertSeverity.WARNING,
            title=f"An agent keeps hitting a rate limit ({key})",
            body="The agent was refused repeatedly within ten minutes. It may be stuck in a loop; "
            "look at its run.",
            related_type="agent",
            related_id=base["agent_id"],
            dedupe_key=f"rate_limit:{base['agent_id']}:{key}",
            wake_founder=False,
        )


def _existing_approval(
    db: Session, company_id: str, scope_hash: str, now: datetime
) -> Approval | None:
    """The approval to act on for this exact scope: an approved unconsumed one, else a pending
    one, else a recent rejection (so identical content is not resubmitted in a loop)."""
    rows = list(
        db.scalars(
            select(Approval)
            .where(
                Approval.company_id == company_id,
                Approval.scope_hash == scope_hash,
                Approval.subject_type == "guardrail",
                Approval.consumed_at.is_(None),
            )
            .order_by(Approval.created_at.desc(), Approval.id.desc())
        )
    )
    for wanted in (ApprovalStatus.APPROVED, ApprovalStatus.PENDING):
        for row in rows:
            if row.status == wanted:
                return row
    for row in rows:
        if row.status in (ApprovalStatus.REJECTED, ApprovalStatus.CHANGES_REQUESTED) and (
            row.decided_at is None or now - row.decided_at < REJECTION_MEMORY
        ):
            return row
    return None


def _pending_error(approval: Approval, decision: str, rule_id: str | None) -> ApprovalPending:
    who = {
        Approver.PEER: "a peer reviewer",
        Approver.FOUNDER: "the AI Co-founder",
        Approver.HUMAN: "the Founder",
    }[Approver(approval.approver)]
    return ApprovalPending(
        f"This action waits for {who}. Finish what you can and stop; you are woken when it is "
        "decided. Then repeat the IDENTICAL action (same content): the approval is single-use.",
        approval_id=approval.id,
        approver=approval.approver,
        decision=decision,
        reviewer_agent_id=approval.reviewer_agent_id,
        tier=approval.tier,
        action=approval.action,
        rule_id=rule_id,
    )


def check(
    db: Session,
    ctx: CompanyContext,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    run: TaskRun | None = None,
    env: GuardEnv | None = None,
    now: datetime | None = None,
    record_hits: bool = True,
) -> Verdict:
    """``record_hits=False``: the caller records ``Verdict.rate_keys`` itself once the action
    actually succeeded (the agent-API gate does: a request that fails validation costs no quota)."""
    now = now or utcnow()
    payload = payload or {}
    agent = db.get(Agent, ctx.principal.id) if ctx.principal.is_agent else None
    if agent is None or agent.company_id != ctx.company_id:
        raise Forbidden("Guardrails apply to agent actions")
    base: dict[str, Any] = {
        "company_id": ctx.company_id,
        "agent_id": agent.id,
        "run_id": run.id if run else None,
        "task_id": run.task_id if run else None,
        "payload_digest": log.payload_digest(payload),
    }

    spec: ActionSpec | None = registry.get_action(action)
    if spec is None:  # fail closed: an action nobody classified has no tier and no way through
        _deny(
            db,
            base,
            action,
            ActionTier.DESTRUCTIVE.value,
            f"Action {action!r} is not registered with a risk tier; unregistered actions are "
            "denied",
        )
    assert spec is not None
    if emergency.is_active(db):
        raise EmergencyStopped()

    limits = policy.load_policy(db, ctx.company_id)
    facts = rules.facts_from_payload(payload)
    res = rules.resolve(db, agent, spec, limits, facts, now=now)
    rule_id = res.rule.id if res.rule else None

    if res.decision == D.DENY:
        _deny(db, base, action, spec.tier, f"Denied: {res.reason}", rule_id=rule_id)

    outbound: dict[str, Any] | None = None
    findings: list[dict[str, Any]] | None = None
    if spec.tier == ActionTier.EXTERNAL_COMMUNICATION:
        outbound = dlp.extract_outbound(action, payload)
        found = dlp.scan_outbound(
            db,
            ctx.company_id,
            outbound,
            own_secrets=env.own_secrets(db, ctx.company_id) if env else (),
            allowed_domains=(res.rule.conditions or {}).get("allowed_domains", [])
            if res.rule
            else [],
        )
        findings = [f.as_dict() for f in found]
        if found:
            kinds = ", ".join(sorted({f.kind for f in found}))
            _deny(
                db,
                base,
                action,
                spec.tier,
                f"Outbound DLP blocked this content ({kinds}). Remove it and submit a revised "
                "version; nothing was sent.",
                rule_id=rule_id,
                details={"reason": "dlp_blocked", "dlp_findings": findings},
            )

    window = ratelimit.exceeded(db, ctx.company_id, agent.id, spec.rate_keys, now)
    if window is not None:
        scope = "agent" if window.agent_id else "company"
        retry = max(1, int((window.resets_at - now).total_seconds())) if window.resets_at else None
        log.record(
            db,
            action=action,
            tier=spec.tier,
            decision="rate_limited",
            rule_id=rule_id,
            reason=f"{window.key}: {window.used}/{window.limit} in "
            f"{window.window_seconds}s ({scope})",
            **base,
        )
        _maybe_alert_repeated(db, base, window.key, now)
        db.commit()
        raise RateLimited(
            f"Rate limit reached: {window.limit} {window.key.replace('_', ' ')} ({scope})",
            details={
                "key": window.key,
                "scope": scope,
                "limit": window.limit,
                "used": window.used,
                "window_seconds": window.window_seconds,
                "retry_after_seconds": retry,
            },
        )

    if res.decision == D.ALLOW:
        if record_hits:
            ratelimit.record(db, ctx.company_id, agent.id, spec.rate_keys, now)
        if spec.tier != ActionTier.READ:
            log.record(
                db,
                action=action,
                tier=spec.tier,
                decision="allow",
                rule_id=rule_id,
                reason=res.reason,
                **base,
            )
        db.commit()
        return Verdict(action, spec.tier, rule_id, rate_keys=spec.rate_keys)

    return _approval_path(
        db, ctx, agent, spec, res, payload, base, outbound, findings, run, limits, now, record_hits
    )


def _approval_path(
    db: Session,
    ctx: CompanyContext,
    agent: Agent,
    spec: ActionSpec,
    res: rules.Resolution,
    payload: dict[str, Any],
    base: dict[str, Any],
    outbound: dict[str, Any] | None,
    findings: list[dict[str, Any]] | None,
    run: TaskRun | None,
    limits: Any,
    now: datetime,
    record_hits: bool,
) -> Verdict:
    action = spec.name
    rule_id = res.rule.id if res.rule else None
    scope = {
        "v": 1,
        "action": action,
        "agent_id": agent.id,
        "payload_digest": base["payload_digest"],
    }
    scope_hash = approvals.compute_scope_hash(scope)
    existing = _existing_approval(db, ctx.company_id, scope_hash, now)
    if existing is not None:
        if existing.status == ApprovalStatus.APPROVED:
            approvals.consume(db, ctx, existing.id, scope, now=now)  # single use; commits
            if record_hits:
                ratelimit.record(db, ctx.company_id, agent.id, spec.rate_keys, now)
            log.record(
                db,
                action=action,
                tier=spec.tier,
                decision="allow",
                rule_id=rule_id,
                approval_id=existing.id,
                reason="approval consumed: the approved action runs",
                **base,
            )
            db.commit()
            return Verdict(
                action, spec.tier, rule_id, existing.id, consumed=True, rate_keys=spec.rate_keys
            )
        if existing.status == ApprovalStatus.PENDING:
            raise _pending_error(existing, res.decision, rule_id)
        note = existing.decision_note or "no reason given"
        _deny(
            db,
            base,
            action,
            spec.tier,
            f"The reviewer did not approve this content ({existing.status}: {note}). Do not "
            "resend it; change it or pick another approach.",
            rule_id=rule_id,
            approval_id=existing.id,
        )

    decision = res.decision
    reviewer: Agent | None = None
    note = ""
    if decision == D.PEER_REVIEW:
        reviewer = peer.pick_reviewer(db, agent)
        if reviewer is None:
            decision, note = (
                D.HUMAN_APPROVAL,
                " (no independent reviewer exists, so a human decides)",
            )
    if decision == D.COFOUNDER_APPROVAL:
        founder = db.scalar(
            select(Agent).where(
                Agent.company_id == ctx.company_id,
                Agent.kind == AgentKind.FOUNDER,
                Agent.status == AgentStatus.ACTIVE,
            )
        )
        if founder is None or founder.id == agent.id:
            decision, note = D.HUMAN_APPROVAL, " (the Co-founder cannot approve its own action)"
    approver = {
        D.PEER_REVIEW: Approver.PEER,
        D.COFOUNDER_APPROVAL: Approver.FOUNDER,
        D.HUMAN_APPROVAL: Approver.HUMAN,
    }[decision]

    evidence: dict[str, Any] = {"run_id": run.id if run else None}
    if outbound is None:
        evidence["payload_preview"] = _scrub_values(redact_payload(log.scope_payload(payload)))
    internal = spec.kind == "internal"
    approval = approvals.create_approval_record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        kind=_KIND_BY_TIER.get(ActionTier(spec.tier), "other"),
        title=_title(action, payload),
        reason=res.reason + note,
        amount_micros=rules.facts_from_payload(payload).amount_micros,
        risk="high" if spec.tier in ("destructive", "identity", "spend") else "medium",
        evidence=evidence,
        scope=scope,
        task_id=run.task_id if run else None,
        subject=("guardrail", base["payload_digest"][:36]),
        approver=approver,
        tier=spec.tier,
        action=action,
        reviewer_agent_id=reviewer.id if reviewer else None,
        outbound=outbound,
        dlp_findings=findings,
        requested_by_agent_id=None if internal else agent.id,
    )
    if reviewer is not None:
        peer.open_review(
            db,
            approval=approval,
            author=agent,
            reviewer=reviewer,
            author_task_id=run.task_id if run else None,
        )
    elif approver == Approver.FOUNDER:
        from app.services import wakes

        wakes.wake_founder(
            db,
            ctx.company_id,
            "approval",
            f"Approval needed: {approval.title}",
            ref_type="approval",
            ref_id=approval.id,
            actor_agent_id=agent.id,
        )
    log.record(
        db,
        action=action,
        tier=spec.tier,
        decision=str(decision),
        rule_id=rule_id,
        approval_id=approval.id,
        reason=res.reason + note,
        **base,
    )
    db.commit()
    raise _pending_error(approval, str(decision), rule_id)
