"""Guardrail rules: the built-in defaults, how a decision is resolved, and the rules' CRUD.

**Defaults live in code** (``DEFAULT_DECISIONS``), not in the database: a company has no rows until
a human adds an override, so existing companies get the spec defaults without a data migration and
the table only ever holds deliberate choices.

**Resolution (documented precedence).** The rules that apply to an action by an agent are those
whose
target matches (the rule's ``action`` equals the action, or the rule has no ``action`` and its
``tier`` equals the action's tier) and whose scope matches (``all``, the agent's role ``family``, or
the ``agent`` itself) and whose conditions hold. Among them the winner is chosen by, in order:

1. **target**: a rule for the specific action beats a tier rule;
2. **scope**: agent beats family beats all;
3. **recency**: the newest rule (``created_at``, then id) wins a tie.

No applicable rule -> the built-in default of the action's tier. Conditions: ``max_amount_micros``
(applies while the action's amount is <= it), ``max_per_day`` (applies while fewer than N such
actions by this agent were allowed today in the company's timezone), ``allowed_domains`` (applies to
actions whose target host / e-mail domain is on the list). A rule whose condition is not met is
skipped, so "allow up to $5, then ask a human" is two rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.guardrails import registry
from app.guardrails.registry import ActionSpec
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import ActionTier, GuardrailDecision, GuardrailScope, RoleFamily
from app.models.guardrails import GuardrailEvent, GuardrailRule
from app.models.policy import CompanyPolicy
from app.schemas.guardrails import GuardrailRuleCreate, GuardrailRuleUpdate
from app.services import audit, events
from app.services.pagination import paginate

D = GuardrailDecision
T = ActionTier

DEFAULT_DECISIONS: dict[str, str] = {
    T.READ: D.ALLOW,
    T.INTERNAL_WRITE: D.ALLOW,
    T.ORG_CHANGE: D.ALLOW,  # inside the M4 headcount / budget rules (the hiring service)
    T.EXTERNAL_READ: D.ALLOW,  # rate limited
    T.EXTERNAL_COMMUNICATION: D.PEER_REVIEW,  # another agent checks the content, then it goes out
    T.EXTERNAL_COMMITMENT: D.HUMAN_APPROVAL,
    T.SPEND: D.COFOUNDER_APPROVAL,  # human above auto_spend_limit_micros (see default_decision)
    T.IDENTITY: D.COFOUNDER_APPROVAL,  # human for domain / DNS ownership (ActionSpec.human_default)
    T.DESTRUCTIVE: D.HUMAN_APPROVAL,
}
_SCOPE_RANK = {GuardrailScope.AGENT: 2, GuardrailScope.FAMILY: 1, GuardrailScope.ALL: 0}
CONDITION_KEYS = ("max_amount_micros", "max_per_day", "allowed_domains")


@dataclass(frozen=True)
class Facts:
    """What the conditions of a rule look at."""

    amount_micros: int | None = None
    domain: str | None = None  # host / e-mail domain of the action's target
    # M15: set only by ``services.purchasing`` (the purchase request flow). A purchase.make that
    # arrives as a provider action keeps the old SPEND default (``auto_spend_limit_micros``).
    purchasing: bool = False


@dataclass(frozen=True)
class Resolution:
    decision: str
    rule: GuardrailRule | None
    source: str  # "rule" | "default"
    reason: str


PURCHASE_ACTIONS = frozenset({"purchase.make", "subscription.start"})
# Strictness of a decision, loosest first: used to combine the purchasing policy with the rules.
_STRICTNESS = {
    D.ALLOW: 0,
    D.PEER_REVIEW: 1,
    D.COFOUNDER_APPROVAL: 2,
    D.HUMAN_APPROVAL: 3,
    D.DENY: 4,
}


def strictest(*decisions: str) -> str:
    """The most restrictive decision (a guardrail rule can only tighten a purchase)."""
    return max(decisions, key=lambda d: _STRICTNESS[GuardrailDecision(d)])


def purchase_band(limits: CompanyPolicy, amount_micros: int) -> str:
    """The M15 band for one charge from the amounts alone (docs/specs/m15, section 4.4.2): nobody
    approves up to ``purchase_auto_approve_micros``, the AI Co-founder up to
    ``purchase_cofounder_limit_micros``, the human above. A band at 0 is off, so the defaults send
    everything to the human. The monthly totals, the recurring / new-vendor floors and the
    Co-founder-requests-its-own rule are applied by ``services.purchasing.decide_band``."""
    auto, cofounder = limits.purchase_auto_approve_micros, limits.purchase_cofounder_limit_micros
    if auto > 0 and amount_micros <= auto:
        return D.ALLOW
    if cofounder > 0 and amount_micros <= cofounder:
        return D.COFOUNDER_APPROVAL
    return D.HUMAN_APPROVAL


def default_decision(spec: ActionSpec, limits: CompanyPolicy, facts: Facts) -> str:
    if facts.purchasing and spec.name in PURCHASE_ACTIONS:  # M15: the bands, not auto_spend_limit
        return purchase_band(limits, facts.amount_micros or 0)
    if spec.default_decision is not None:  # M14: an action-level default replaces the tier's
        return spec.default_decision
    if spec.tier == T.SPEND:
        amount = facts.amount_micros or 0
        return D.HUMAN_APPROVAL if amount > limits.auto_spend_limit_micros else D.COFOUNDER_APPROVAL
    if spec.human_default and DEFAULT_DECISIONS[spec.tier] == D.COFOUNDER_APPROVAL:
        return D.HUMAN_APPROVAL
    return DEFAULT_DECISIONS[spec.tier]


def facts_from_payload(payload: dict[str, Any] | None) -> Facts:
    payload = payload or {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else payload
    amount = params.get("amount_micros")
    if amount is None:  # M8: a purchase is bound to its price ceiling
        amount = params.get("max_price_micros")
    amount = amount if isinstance(amount, int) and not isinstance(amount, bool) else None
    target = None
    for key in ("url", "to", "domain", "host", "repo_url", "vendor_domain"):
        value = params.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, str) and value.strip():
            target = value.strip()
            break
    return Facts(amount_micros=amount, domain=_domain_of(target))


def _domain_of(target: str | None) -> str | None:
    if not target:
        return None
    if "@" in target and "://" not in target:
        return target.rsplit("@", 1)[-1].strip("<> ").lower() or None
    host = urlparse(target if "://" in target else f"//{target}").hostname
    return host.lower() if host else None


def _domain_listed(domain: str, allowed: list[str]) -> bool:
    return any(
        domain == d or domain.endswith("." + d) for d in (a.lower().lstrip("@.") for a in allowed)
    )


def _actions_today(db: Session, company: Company, agent_id: str, action: str, now: datetime) -> int:
    tz = ZoneInfo(company.timezone or "UTC")
    local = now.astimezone(tz)
    start = datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(UTC)
    return int(
        db.scalar(
            select(func.count(GuardrailEvent.id)).where(
                GuardrailEvent.company_id == company.id,
                GuardrailEvent.agent_id == agent_id,
                GuardrailEvent.action == action,
                GuardrailEvent.decision == D.ALLOW,
                GuardrailEvent.created_at >= start,
            )
        )
        or 0
    )


def _conditions_hold(
    db: Session,
    rule: GuardrailRule,
    agent: Agent,
    spec: ActionSpec,
    facts: Facts,
    now: datetime,
) -> bool:
    cond = rule.conditions or {}
    cap = cond.get("max_amount_micros")
    if cap is not None and (facts.amount_micros or 0) > int(cap):
        return False
    domains = cond.get("allowed_domains")
    if domains and (facts.domain is None or not _domain_listed(facts.domain, domains)):
        return False
    per_day = cond.get("max_per_day")
    if per_day is not None:
        company = db.get(Company, agent.company_id)
        assert company is not None
        if _actions_today(db, company, agent.id, spec.name, now) >= int(per_day):
            return False
    return True


def _applies(rule: GuardrailRule, agent: Agent, spec: ActionSpec) -> bool:
    if rule.action:
        if rule.action != spec.name:
            return False
    elif rule.tier != spec.tier:
        return False
    if rule.scope_type == GuardrailScope.ALL:
        return True
    if rule.scope_type == GuardrailScope.FAMILY:
        return rule.scope_value == agent.role_family
    if rule.scope_type == GuardrailScope.AGENT:
        return rule.scope_value == agent.id
    return False


def _order(rule: GuardrailRule) -> tuple:
    return (1 if rule.action else 0, _SCOPE_RANK.get(rule.scope_type, 0), rule.created_at, rule.id)


def resolve(
    db: Session,
    agent: Agent,
    spec: ActionSpec,
    limits: CompanyPolicy,
    facts: Facts,
    *,
    now: datetime | None = None,
) -> Resolution:
    now = now or utcnow()
    if spec.human_only:  # accounts and team: before any rule is read, nothing can allow it
        return Resolution(
            D.DENY, None, "default", f"{spec.name} is human-only: agents can never do it"
        )
    rules = list(
        db.scalars(select(GuardrailRule).where(GuardrailRule.company_id == agent.company_id))
    )
    for rule in sorted((r for r in rules if _applies(r, agent, spec)), key=_order, reverse=True):
        if _conditions_hold(db, rule, agent, spec, facts, now):
            target = rule.action or f"tier {rule.tier}"
            return _floor(
                spec,
                Resolution(
                    rule.decision,
                    rule,
                    "rule",
                    f"rule {rule.id} ({target}, {rule.scope_type}"
                    + (f" {rule.scope_value}" if rule.scope_value else "")
                    + f") says {rule.decision}",
                ),
            )
    decision = default_decision(spec, limits, facts)
    return _floor(
        spec, Resolution(decision, None, "default", f"default for tier {spec.tier} is {decision}")
    )


def _floor(spec: ActionSpec, res: Resolution) -> Resolution:
    """M8: actions that spend money or take a domain need a human, whatever the rules say (a
    ``deny`` rule is still honoured)."""
    if spec.human_required and res.decision in (D.ALLOW, D.PEER_REVIEW, D.COFOUNDER_APPROVAL):
        return Resolution(
            D.HUMAN_APPROVAL,
            res.rule,
            res.source,
            f"{res.reason}; {spec.name} always needs a human decision (no rule can lower it)",
        )
    return res


# ---------------------------------------------------------------- CRUD (humans only)


def _validate_conditions(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(raw) - set(CONDITION_KEYS))
    if unknown:
        raise ValidationFailed(
            f"Unknown condition(s): {', '.join(unknown)} (allowed: {', '.join(CONDITION_KEYS)})"
        )
    out: dict[str, Any] = {}
    for key in ("max_amount_micros", "max_per_day"):
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValidationFailed(f"{key} must be an integer >= 0")
        out[key] = value
    domains = raw.get("allowed_domains")
    if domains is not None:
        if not isinstance(domains, list) or len(domains) > 50:
            raise ValidationFailed("allowed_domains must be a list of up to 50 domains")
        cleaned = []
        for d in domains:
            if not isinstance(d, str) or not re.fullmatch(r"@?[A-Za-z0-9.\-]{1,253}", d.strip()):
                raise ValidationFailed(f"Invalid domain {d!r}")
            cleaned.append(d.strip().lstrip("@").lower())
        out["allowed_domains"] = list(dict.fromkeys(cleaned))
    return out


def _validate_target(db: Session, company_id: str, tier: str, action: str | None) -> None:
    if action is not None:
        spec = registry.get_action(action)
        if spec is None:
            raise ValidationFailed(f"Unknown action {action!r}: it is not in the action registry")
        if spec.human_only:
            raise ValidationFailed(f"Action {action!r} is human-only: no rule can allow it")
        if spec.tier != tier:
            raise ValidationFailed(f"Action {action!r} belongs to tier {spec.tier!r}, not {tier!r}")


def _validate_scope(db: Session, company_id: str, scope_type: str, scope_value: str | None) -> None:
    if scope_type == GuardrailScope.ALL:
        if scope_value:
            raise ValidationFailed("scope_value must be empty when scope_type is 'all'")
        return
    if not scope_value:
        raise ValidationFailed(f"scope_value is required when scope_type is {scope_type!r}")
    if scope_type == GuardrailScope.FAMILY:
        if scope_value not in {f.value for f in RoleFamily}:
            raise ValidationFailed(f"Unknown role family {scope_value!r}")
    elif (
        db.scalar(select(Agent.id).where(Agent.id == scope_value, Agent.company_id == company_id))
        is None
    ):
        raise NotFound("Agent not found")


def get_rule(db: Session, ctx: CompanyContext, rule_id: str) -> GuardrailRule:
    _readable(ctx)
    rule = db.scalar(
        select(GuardrailRule).where(
            GuardrailRule.id == rule_id, GuardrailRule.company_id == ctx.company_id
        )
    )
    if rule is None:
        raise NotFound("Guardrail rule not found")
    return rule


def _readable(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        from app.core.errors import Forbidden

        raise Forbidden("Agents cannot read or change guardrail rules")


def list_rules(db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None):
    _readable(ctx)
    stmt = select(GuardrailRule).where(GuardrailRule.company_id == ctx.company_id)
    return paginate(db, stmt, GuardrailRule, limit=limit, cursor=cursor, descending=True)


def _emit(db: Session, rule: GuardrailRule, change: str) -> None:
    events.emit(
        db,
        rule.company_id,
        "guardrail.rule_changed",
        {"rule_id": rule.id, "change": change, "tier": rule.tier, "action": rule.action},
    )


def create_rule(db: Session, ctx: CompanyContext, data: GuardrailRuleCreate) -> GuardrailRule:
    require_human_admin(ctx, "change guardrail rules")  # agents (even with a run token): 403
    _validate_target(db, ctx.company_id, data.tier, data.action)
    _validate_scope(db, ctx.company_id, data.scope_type, data.scope_value)
    rule = GuardrailRule(
        company_id=ctx.company_id,
        tier=data.tier,
        action=data.action,
        scope_type=data.scope_type,
        scope_value=data.scope_value,
        decision=data.decision,
        conditions=_validate_conditions(data.conditions),
        created_by_type=ctx.principal.type.value,
        created_by_id=ctx.principal.id,
    )
    db.add(rule)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="guardrail_rule.created",
        target_type="guardrail_rule",
        target_id=rule.id,
        summary=f"Guardrail rule: {rule.action or 'tier ' + rule.tier} -> {rule.decision}",
        data=_audit_data(rule),
    )
    _emit(db, rule, "created")
    db.commit()
    return rule


def update_rule(
    db: Session, ctx: CompanyContext, rule_id: str, data: GuardrailRuleUpdate
) -> GuardrailRule:
    require_human_admin(ctx, "change guardrail rules")
    rule = get_rule(db, ctx, rule_id)
    given = data.model_fields_set
    tier = data.tier if "tier" in given and data.tier else rule.tier
    action = data.action if "action" in given else rule.action
    scope_type = data.scope_type if "scope_type" in given and data.scope_type else rule.scope_type
    scope_value = data.scope_value if "scope_value" in given else rule.scope_value
    if scope_type == GuardrailScope.ALL:
        scope_value = None
    _validate_target(db, ctx.company_id, tier, action)
    _validate_scope(db, ctx.company_id, scope_type, scope_value)
    rule.tier, rule.action, rule.scope_type, rule.scope_value = (
        tier,
        action,
        scope_type,
        scope_value,
    )
    if "decision" in given and data.decision:
        rule.decision = data.decision
    if "conditions" in given and data.conditions is not None:
        rule.conditions = _validate_conditions(data.conditions)
    rule.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="guardrail_rule.updated",
        target_type="guardrail_rule",
        target_id=rule.id,
        summary=f"Updated guardrail rule: {rule.action or 'tier ' + rule.tier} -> {rule.decision}",
        data=_audit_data(rule),
    )
    _emit(db, rule, "updated")
    db.commit()
    return rule


def delete_rule(db: Session, ctx: CompanyContext, rule_id: str) -> None:
    require_human_admin(ctx, "change guardrail rules")
    rule = get_rule(db, ctx, rule_id)
    data = _audit_data(rule)
    db.delete(rule)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="guardrail_rule.deleted",
        target_type="guardrail_rule",
        target_id=rule_id,
        summary=f"Deleted guardrail rule: {rule.action or 'tier ' + rule.tier}",
        data=data,
    )
    events.emit(
        db,
        ctx.company_id,
        "guardrail.rule_changed",
        {"rule_id": rule_id, "change": "deleted", "tier": rule.tier, "action": rule.action},
    )
    db.commit()


def _audit_data(rule: GuardrailRule) -> dict[str, Any]:
    return {
        "tier": rule.tier,
        "action": rule.action,
        "scope_type": rule.scope_type,
        "scope_value": rule.scope_value,
        "decision": rule.decision,
        "conditions": rule.conditions,
    }
