"""Effective permissions of one agent: what each tier and each tool it can reach resolves to today
(rules and defaults), for the Agent panel's Permissions tab."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.core.principal import CompanyContext
from app.core.time import utcnow
from app.guardrails import ratelimit, registry, rules
from app.guardrails.registry import ActionSpec
from app.models.agent import Agent
from app.services import agent_toolkit, policy
from app.services.common import get_agent_or_404


def _tools_of(db: Session, agent: Agent, sandbox_backend: str) -> list[tuple[str, str | None]]:
    """(tool name, action) for every tool the agent can call."""
    runtime_config = dict(agent.runtime_config or {})
    sandbox = "docker" if sandbox_backend == "docker" else "none"
    toolsets = agent_toolkit.toolsets_for(db, agent, sandbox=sandbox, runtime_config=runtime_config)
    out: list[tuple[str, str | None]] = [
        (name, registry.tool_action(name)) for name in registry.hermes_tools_for(toolsets)
    ]
    for name in agent_toolkit.company_tools_for(db, agent, runtime_config.get("company_tools")):
        out.append((name, registry.company_tool_action(name)))
    return out


def effective_permissions(
    db: Session, ctx: CompanyContext, agent_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    if not ctx.principal.is_human:
        raise Forbidden("Only members can read effective permissions")
    now = now or utcnow()
    agent = get_agent_or_404(db, ctx.company_id, agent_id, allow_gone=True)
    limits = policy.load_policy(db, ctx.company_id)
    facts = rules.Facts()
    tiers = []
    for tier in registry.TIERS:
        res = rules.resolve(db, agent, ActionSpec("*", tier, "tier"), limits, facts, now=now)
        tiers.append(
            {
                "tier": tier,
                "decision": res.decision,
                "source": res.source,
                "rule_id": res.rule.id if res.rule else None,
            }
        )
    tools = []
    for name, action in _tools_of(db, agent, limits.sandbox_backend):
        spec = registry.get_action(action) if action else None
        if spec is None:
            continue
        res = rules.resolve(db, agent, spec, limits, facts, now=now)
        tools.append(
            {
                "name": name,
                "tier": spec.tier,
                "decision": res.decision,
                "rule_id": res.rule.id if res.rule else None,
            }
        )
    windows = [w for w in ratelimit.status(db, ctx.company_id, now, agent.id) if w.agent_id]
    return {
        "agent_id": agent.id,
        "tiers": tiers,
        "tools": tools,
        "rate_limits": [rate_status(w) for w in windows],
    }


def rate_status(w: ratelimit.Window) -> dict[str, Any]:
    return {
        "agent_id": w.agent_id,
        "key": w.key,
        "limit": w.limit,
        "used": w.used,
        "window_seconds": w.window_seconds,
        "resets_at": w.resets_at,
    }
