"""Model routing (docs/specs/m6 section 6): which provider/model a run uses, and why.

``policy.model_routing``:

* ``pinned`` -- the agent's own provider and model, whatever they cost;
* ``free_first`` (default) -- a free model when one exists (a local model, or a provider priced at
  zero), else the agent's own paid one. Using the paid one is allowed when
  ``policy.allow_paid_fallback`` is true; otherwise it is a ``spend``-tier action
  (``model.paid_fallback``) that needs the Co-founder's or a human's approval, per the rules
  and ``policy.auto_spend_limit_micros``;
* ``quality_first`` -- the best paid model (the highest output price is the only quality
  signal we have; an agent's own paid model wins), else a free one. The owner chose cost to matter
  less here, so there is no approval step; budgets still apply.

Routing only applies to the company-agent runtime (the only one that talks to model providers). The
decision is stored on the run (``routing_reason``, ``model_provider_id``, ``model_used``), shown in
finance reports.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.enums import ModelRouting
from app.models.policy import CompanyPolicy
from app.models.provider import ModelProvider
from app.services import model_providers

COMPANY_AGENT = "company_agent"


@dataclass(frozen=True)
class Route:
    provider_id: str | None = None
    model: str | None = None
    reason: str | None = None
    free: bool = False  # no provider charge: nothing to reserve
    needs_paid_approval: bool = False  # paid fallback while policy forbids it without approval

    @property
    def routed(self) -> bool:
        return self.provider_id is not None


def is_free(provider: ModelProvider) -> bool:
    if provider.is_local:
        return True
    return provider.input_price_per_mtok_micros == 0 and provider.output_price_per_mtok_micros == 0


def _model_of(limits: CompanyPolicy, provider: ModelProvider) -> str:
    if limits.default_model_provider_id == provider.id and limits.default_model_name:
        return limits.default_model_name
    return provider.default_model


def _candidates(db: Session, company_id: str, limits: CompanyPolicy) -> list[ModelProvider]:
    rows = list(
        db.scalars(
            select(ModelProvider)
            .where(ModelProvider.company_id == company_id, ModelProvider.enabled.is_(True))
            .order_by(ModelProvider.created_at, ModelProvider.id)
        )
    )
    allowed = list(limits.allowed_model_providers or [])
    if allowed:
        rows = [r for r in rows if r.id in allowed or r.name in allowed]
    rows.sort(key=lambda r: 0 if r.id == limits.default_model_provider_id else 1)
    return rows


def choose(db: Session, agent: Agent, limits: CompanyPolicy) -> Route:
    if agent.runtime != COMPANY_AGENT:
        return Route()
    try:
        own = model_providers.resolve_for_agent(db, agent)
    except model_providers.ModelResolutionError:
        return Route()  # the adapter reports the configuration error as it always did
    own_row = db.get(ModelProvider, own.provider_id)
    own_free = own_row is not None and is_free(own_row)
    own_route = Route(own.provider_id, own.model, None, free=own_free)

    mode = limits.model_routing
    if mode == ModelRouting.PINNED:
        return Route(own.provider_id, own.model, "pinned: the agent's own model", free=own_free)

    others = _candidates(db, agent.company_id, limits)
    free_list = [p for p in others if is_free(p)]
    if mode == ModelRouting.QUALITY_FIRST:
        paid = [p for p in others if not is_free(p)]
        if own_row is not None and not own_free:
            return Route(
                own.provider_id, own.model, "quality_first: the agent's own paid model", free=False
            )
        if paid:
            best = max(paid, key=lambda p: p.output_price_per_mtok_micros or 0)
            return Route(
                best.id,
                _model_of(limits, best),
                f"quality_first: the best paid model ({best.name})",
                free=False,
            )
        return Route(
            own.provider_id,
            own.model,
            "quality_first: no paid model exists; used the free one",
            free=own_free,
        )

    # free_first
    if own_free:
        return Route(
            own.provider_id, own.model, "free_first: the agent's own model is free", free=True
        )
    if free_list:
        pick = free_list[0]
        return Route(
            pick.id,
            _model_of(limits, pick),
            f"free_first: used the free model {pick.name} instead of paid {own.provider_name}",
            free=True,
        )
    if limits.allow_paid_fallback:
        return Route(
            own_route.provider_id,
            own_route.model,
            "free_first: no free model exists; paid fallback is allowed by policy",
            free=False,
        )
    return Route(
        own_route.provider_id,
        own_route.model,
        "free_first: no free model exists; the paid fallback needs approval",
        free=False,
        needs_paid_approval=True,
    )
