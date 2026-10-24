"""The guardrail gate of the agent API: a router-level dependency that runs before EVERY
``/api/agent/v1`` handler.

Fail closed: a route that is not in the action registry is denied before its handler runs. Then, in
order: the emergency stop, the per-agent / per-company API-call rate limit, the provider-action
pre-check (credential scope, executor connected -- before anything is parked behind a review), and
``guardrails.check`` for the route's action with the request body as payload. A test enumerates all
agent-API routes and asserts each one is registered.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import DbSession, RunCtx, bearer_token
from app.core.errors import EmergencyStopped, GuardrailDenied, RateLimited
from app.core.time import utcnow
from app.guardrails import engine, log, providers, ratelimit, registry
from app.models.enums import ActionTier
from app.services import agent_auth, emergency


def guard_env(request: Request) -> engine.GuardEnv:
    state = request.app.state
    token = bearer_token(request)
    return engine.GuardEnv(
        owner_token=state.owner_token.ensure() if state.settings.auth_mode == "local" else None,
        cipher=state.cipher,
        extra_secrets=(token,) if token else (),
    )


def provider_registry(request: Request) -> providers.ProviderRegistry:
    return request.app.state.provider_executors


async def _json_body(request: Request) -> dict[str, Any]:
    if "json" not in request.headers.get("content-type", "").lower():
        return {}
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        return {}  # the handler's own validation reports the malformed body
    return body if isinstance(body, dict) else {}


def _enforce(
    db: Session,
    rc: agent_auth.AgentRunContext,
    request: Request,
    method: str,
    template: str,
    path_params: dict[str, Any],
    body: dict[str, Any],
) -> None:
    now = utcnow()
    if emergency.is_active(db):
        raise EmergencyStopped()
    route = registry.route_spec(method, template)
    base = {
        "company_id": rc.ctx.company_id,
        "agent_id": rc.agent.id,
        "run_id": rc.run.id,
        "task_id": rc.run.task_id,
        "payload_digest": log.payload_digest({"_path": path_params, **body}),
    }
    if route is None:  # fail closed
        log.record(
            db,
            action=f"{method} {template}"[:80],
            tier=ActionTier.DESTRUCTIVE.value,
            decision="deny",
            reason="Agent-API endpoint is not registered with a risk tier; denied (fail closed)",
            **base,
        )
        db.commit()
        raise GuardrailDenied(
            "This endpoint is not registered with a guardrail risk tier and is denied",
            details={"endpoint": f"{method} {template}", "rule_id": None},
        )

    # Every agent-API call counts toward the api_calls_per_min windows (agent and company).
    window = ratelimit.exceeded(db, rc.ctx.company_id, rc.agent.id, (registry.API_CALLS,), now)
    if window is not None:
        scope = "agent" if window.agent_id else "company"
        log.record(
            db,
            action=route.action or "provider.action",
            tier=ActionTier.READ.value,
            decision="rate_limited",
            reason=f"{window.key}: {window.used}/{window.limit} in "
            f"{window.window_seconds}s ({scope})",
            **base,
        )
        db.commit()
        retry = max(1, int((window.resets_at - now).total_seconds())) if window.resets_at else None
        raise RateLimited(
            f"Rate limit reached: {window.limit} API calls per minute ({scope})",
            details={
                "key": window.key,
                "scope": scope,
                "limit": window.limit,
                "used": window.used,
                "window_seconds": window.window_seconds,
                "retry_after_seconds": retry,
            },
        )
    ratelimit.record(db, rc.ctx.company_id, rc.agent.id, (registry.API_CALLS,), now)

    action = route.action
    payload: dict[str, Any] = {"_path": path_params, **body}
    if route.dynamic:
        # The body names the provider action; validate it (scope, executor) before the engine can
        # park it behind a review nobody should have to do.
        action = providers.precheck(db, rc, body, request.app.state.provider_executors)
        payload = {"_path": path_params, **body}
    assert action
    verdict = engine.check(
        db, rc.ctx, action, payload, run=rc.run, env=guard_env(request), now=now, record_hits=False
    )
    request.state.guardrail = verdict


def _record_success(db: Session, rc: agent_auth.AgentRunContext, request: Request) -> None:
    verdict = getattr(request.state, "guardrail", None)
    if verdict is not None and verdict.rate_keys:
        ratelimit.record(db, rc.ctx.company_id, rc.agent.id, verdict.rate_keys, utcnow())
        db.commit()


async def agent_guard(request: Request, rc: RunCtx, db: DbSession):
    route = request.scope.get("route")
    template = registry.relative_route(getattr(route, "path", request.url.path))
    body = await _json_body(request)
    await run_in_threadpool(
        _enforce, db, rc, request, request.method, template, dict(request.path_params), body
    )
    # The handler ran. A request that failed (validation, a limit, a conflict) raised through the
    # ``yield`` and costs no quota; only an action that really happened is counted.
    yield
    await run_in_threadpool(_record_success, db, rc, request)
