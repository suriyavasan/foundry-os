"""Model providers: where an AI employee gets its LLM (local Ollama first, then cloud).

Owners/admins manage them; members read; agents get nothing (a provider decides what money
or hardware a run uses). A company default (``policy.default_model_*``) applies to agents that
do not name their own provider. Credentials are referenced, never embedded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import netguard
from app.core.crypto import CredentialCipher
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.enums import AgentKind, AgentStatus, CostKind, RoleFamily
from app.models.guardrails import SystemState
from app.models.policy import CompanyPolicy
from app.models.provider import ModelProvider
from app.providers import probe
from app.schemas.providers import (
    DetectedModel,
    DetectRequest,
    DetectResult,
    ModelProviderCreate,
    ModelProviderUpdate,
)
from app.services import audit, credentials, events, policy
from app.services.pagination import paginate

COMPANY_AGENT_RUNTIME = "company_agent"

# kind -> (default base URL, default is_local, runtime provider id in the run spec)
KINDS: dict[str, tuple[str | None, bool, str]] = {
    "ollama": (probe.OLLAMA_DEFAULT_URL, True, "custom"),
    "openrouter": ("https://openrouter.ai/api/v1", False, "openrouter"),
    "anthropic": ("https://api.anthropic.com", False, "anthropic"),
    "openai": ("https://api.openai.com/v1", False, "custom"),
    "openai_compatible": (None, False, "custom"),
}
CREDENTIAL_REQUIRED = {"openrouter", "anthropic", "openai"}
LOCAL_API_KEY_PLACEHOLDER = "ollama"  # Hermes needs a non-empty key; Ollama ignores it


def _require_human(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot access model providers")


# ---------------------------------------------------------------- private-network opt-in
# The owner's per-provider opt-in lives in the existing ``system_state`` key/value table (one row
# per provider that has it on, key ``pn:<provider id>``): no schema change, and it is only ever
# read after the provider itself was loaded through its company.


def _net_key(provider_id: str) -> str:
    return f"pn:{provider_id}"


def allows_private_network(db: Session, provider_id: str) -> bool:
    row = db.get(SystemState, _net_key(provider_id))
    return bool(row and (row.value or {}).get("allow_private_network"))


def _set_private_network(db: Session, provider_id: str, allowed: bool) -> None:
    row = db.get(SystemState, _net_key(provider_id))
    if allowed:
        if row is None:
            db.add(
                SystemState(
                    key=_net_key(provider_id),
                    value={"allow_private_network": True},
                    updated_at=utcnow(),
                )
            )
        else:
            row.value = {"allow_private_network": True}
            row.updated_at = utcnow()
    elif row is not None:
        db.delete(row)


def _policy_for(db: Session, row: ModelProvider, override: bool = False) -> netguard.Policy:
    return netguard.provider_policy(row.kind, override or allows_private_network(db, row.id))


def _load(db: Session, company_id: str, provider_id: str) -> ModelProvider:
    row = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == provider_id, ModelProvider.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Model provider not found")
    return row


def list_providers(
    db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None
) -> tuple[list[ModelProvider], str | None]:
    _require_human(ctx)
    stmt = select(ModelProvider).where(ModelProvider.company_id == ctx.company_id)
    return paginate(db, stmt, ModelProvider, limit=limit, cursor=cursor, descending=False)


def get_provider(db: Session, ctx: CompanyContext, provider_id: str) -> ModelProvider:
    _require_human(ctx)
    return _load(db, ctx.company_id, provider_id)


def serialize(db: Session, company_id: str, rows: list[ModelProvider]) -> list[dict]:
    default_id = policy.load_policy(db, company_id).default_model_provider_id
    keys = {_net_key(r.id): r.id for r in rows}
    opted_in = (
        {
            keys[k]
            for k in db.scalars(select(SystemState.key).where(SystemState.key.in_(list(keys))))
        }
        if keys
        else set()
    )
    out = []
    for row in rows:
        out.append(
            {
                "id": row.id,
                "company_id": row.company_id,
                "name": row.name,
                "kind": row.kind,
                "base_url": row.base_url,
                "credential_id": row.credential_id,
                "default_model": row.default_model,
                "context_length": row.context_length,
                "is_local": row.is_local,
                "enabled": row.enabled,
                "allow_private_network": row.id in opted_in,
                "input_price_per_mtok_micros": row.input_price_per_mtok_micros,
                "output_price_per_mtok_micros": row.output_price_per_mtok_micros,
                "last_test": row.last_test,
                "is_company_default": row.id == default_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    return out


def _normalize_url(kind: str, base_url: str | None, allow_private: bool = False) -> str | None:
    default, _, _ = KINDS[kind]
    url = (base_url or "").strip() or default
    if url is None:
        raise ValidationFailed(
            "base_url is required for openai_compatible providers",
            details=[{"loc": ["body", "base_url"], "msg": "required for this provider kind"}],
        )
    problem = probe.validate_http_url(url)
    if problem:
        raise ValidationFailed(
            f"base_url {problem}", details=[{"loc": ["body", "base_url"], "msg": problem}]
        )
    require_valid_destination(kind, url, allow_private)
    return probe.ollama_root(url) if kind == "ollama" else url.rstrip("/")


def require_valid_destination(kind: str, url: str, allow_private: bool = False) -> None:
    """SSRF guard for a URL an owner/admin supplies (422 ``unsafe_url``). Resolves the host and
    refuses link-local / cloud-metadata / reserved addresses always, loopback for cloud kinds,
    private networks without the opt-in. A name that does not resolve *yet* is accepted here (it
    fails at test time); every real request is checked again and pinned to the validated address
    (``netguard``)."""
    if allow_private and kind not in netguard.LOCAL_MODEL_KINDS:
        raise ValidationFailed(
            f"{kind} providers must use a public address; allow_private_network is only for "
            "ollama and openai_compatible",
            details=[
                {"loc": ["body", "allow_private_network"], "msg": "not allowed for this kind"}
            ],
        )
    pol = netguard.provider_policy(kind, allow_private)
    netguard.check_url(
        url,
        allow_loopback=pol.allow_loopback,
        allow_private=pol.allow_private,
        require_resolution=False,
    )


def create_provider(
    db: Session, ctx: CompanyContext, data: ModelProviderCreate, *, commit: bool = True
) -> ModelProvider:
    require_human_admin(ctx, "register model providers")
    kind = data.kind
    if kind in CREDENTIAL_REQUIRED and not data.credential_id:
        raise ValidationFailed(
            f"{kind} providers need a credential",
            details=[{"loc": ["body", "credential_id"], "msg": "required for this provider kind"}],
        )
    if data.credential_id:
        credentials.require_usable(db, ctx.company_id, data.credential_id)
    base_url = _normalize_url(kind, data.base_url, data.allow_private_network)
    row = ModelProvider(
        company_id=ctx.company_id,
        name=data.name,
        kind=kind,
        base_url=base_url,
        credential_id=data.credential_id,
        default_model=data.default_model,
        context_length=data.context_length,
        is_local=KINDS[kind][1] if data.is_local is None else data.is_local,
        enabled=data.enabled,
        input_price_per_mtok_micros=data.input_price_per_mtok_micros,
        output_price_per_mtok_micros=data.output_price_per_mtok_micros,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(f"A model provider named {data.name!r} already exists") from exc
    _set_private_network(db, row.id, data.allow_private_network)
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="model_provider.created",
        target_type="model_provider",
        target_id=row.id,
        summary=f"Registered model provider {row.name} ({row.kind}, {row.default_model})",
        data={
            "name": row.name,
            "kind": row.kind,
            "default_model": row.default_model,
            "is_local": row.is_local,
            "allow_private_network": data.allow_private_network,
            "has_credential": row.credential_id is not None,
        },
    )
    events.emit(db, ctx.company_id, "model_provider.created", {"provider_id": row.id})
    current = policy.load_policy(db, ctx.company_id)
    if row.enabled and (data.make_default or current.default_model_provider_id is None):
        set_company_default(db, ctx, row)
    if commit:
        db.commit()
    return row


def set_company_default(db: Session, ctx: CompanyContext, provider: ModelProvider) -> None:
    """Make ``provider`` the company default and equip the founder/HR (no commit)."""
    current = policy.load_policy(db, ctx.company_id)
    if (
        current.default_model_provider_id == provider.id
        and current.default_model_name == provider.default_model
    ):
        equip_default_agents(db, ctx, provider)
        return
    current.default_model_provider_id = provider.id
    current.default_model_name = provider.default_model
    current.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="policy.updated",
        target_type="policy",
        target_id=current.id,
        summary=f"Company default model set to {provider.name} / {provider.default_model}",
        data={"changed": ["default_model_provider_id", "default_model_name"]},
    )
    events.emit(db, ctx.company_id, "policy.updated", {"changed": ["default_model_provider_id"]})
    equip_default_agents(db, ctx, provider)


def equip_default_agents(db: Session, ctx: CompanyContext, provider: ModelProvider) -> list[str]:
    """Founder and HR agents that have no runtime yet get ``company_agent`` plus the provider's
    model, so a company with a working provider can run tasks out of the box. Agents that already
    have a runtime or a model are left alone (the owner chose those)."""
    rows = list(
        db.scalars(
            select(Agent).where(
                Agent.company_id == ctx.company_id,
                Agent.runtime == "unassigned",
                Agent.status.notin_([AgentStatus.RETIRED, AgentStatus.REJECTED]),
            )
        )
    )
    changed_ids: list[str] = []
    for agent in rows:
        if agent.kind != AgentKind.FOUNDER and agent.role_family != RoleFamily.HR:
            continue
        changed = ["runtime"]
        agent.runtime = COMPANY_AGENT_RUNTIME
        if not agent.model_provider:
            agent.model_provider = provider.id
            agent.model_name = agent.model_name or provider.default_model
            changed += ["model_provider", "model_name"]
        agent.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="agent.updated",
            target_type="agent",
            target_id=agent.id,
            summary=f"Updated agent {agent.name}: {', '.join(changed)}",
            data={"changed": changed, "reason": "default_model_provider_registered"},
        )
        events.emit(
            db, ctx.company_id, "agent.updated", {"agent_id": agent.id, "status": agent.status}
        )
        changed_ids.append(agent.id)
    return changed_ids


def update_provider(
    db: Session, ctx: CompanyContext, provider_id: str, data: ModelProviderUpdate
) -> ModelProvider:
    require_human_admin(ctx, "change model providers")
    row = _load(db, ctx.company_id, provider_id)
    fields = data.model_fields_set
    changed: list[str] = []
    was_private = allows_private_network(db, row.id)
    now_private = data.allow_private_network if "allow_private_network" in fields else was_private
    if "base_url" in fields or now_private != was_private:
        # the (url, opt-in) pair is re-validated whenever either half changes
        value = _normalize_url(
            row.kind,
            data.base_url if "base_url" in fields else row.base_url,
            bool(now_private),
        )
        if value != row.base_url:
            row.base_url = value
            changed.append("base_url")
    if now_private != was_private:
        _set_private_network(db, row.id, bool(now_private))
        changed.append("allow_private_network")
    if "credential_id" in fields:
        if data.credential_id:
            credentials.require_usable(db, ctx.company_id, data.credential_id)
        elif row.kind in CREDENTIAL_REQUIRED:
            raise ValidationFailed(f"{row.kind} providers need a credential")
        if data.credential_id != row.credential_id:
            row.credential_id = data.credential_id
            changed.append("credential_id")
    for name in (
        "name",
        "default_model",
        "context_length",
        "is_local",
        "enabled",
        "input_price_per_mtok_micros",
        "output_price_per_mtok_micros",
    ):
        if name in fields and getattr(data, name) != getattr(row, name):
            setattr(row, name, getattr(data, name))
            changed.append(name)
    if "enabled" in changed and not row.enabled:
        pol = policy.load_policy(db, ctx.company_id)
        if pol.default_model_provider_id == row.id:
            pol.default_model_provider_id = None
            pol.default_model_name = None
            pol.updated_at = utcnow()
            changed.append("company_default_cleared")
    if changed:
        row.updated_at = utcnow()
        row.last_test = None  # the old test no longer describes this configuration
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="model_provider.updated",
            target_type="model_provider",
            target_id=row.id,
            summary=f"Updated model provider {row.name}: {', '.join(changed)}",
            data={"changed": changed},  # names only
        )
        events.emit(db, ctx.company_id, "model_provider.updated", {"provider_id": row.id})
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise Conflict("A model provider with that name already exists") from exc
    return row


def delete_provider(db: Session, ctx: CompanyContext, provider_id: str) -> None:
    require_human_admin(ctx, "delete model providers")
    row = _load(db, ctx.company_id, provider_id)
    users = list(
        db.scalars(
            select(Agent.name).where(
                Agent.company_id == ctx.company_id,
                Agent.model_provider == row.id,
                Agent.status.notin_([AgentStatus.RETIRED, AgentStatus.REJECTED]),
            )
        )
    )
    if users:
        raise Conflict(
            "Model provider is used by agents: " + ", ".join(users[:5]) + ". Reassign them first."
        )
    pol = policy.load_policy(db, ctx.company_id)
    if pol.default_model_provider_id == row.id:
        pol.default_model_provider_id = None
        pol.default_model_name = None
        pol.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="model_provider.deleted",
        target_type="model_provider",
        target_id=row.id,
        summary=f"Deleted model provider {row.name}",
        data={"name": row.name, "kind": row.kind},
    )
    events.emit(db, ctx.company_id, "model_provider.deleted", {"provider_id": row.id})
    _set_private_network(db, row.id, False)
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------- test + detect


def test_provider(
    db: Session,
    ctx: CompanyContext,
    provider_id: str,
    cipher: CredentialCipher,
    *,
    model: str | None = None,
    allow_private_network: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> ModelProvider:
    require_human_admin(ctx, "test model providers")
    row = _load(db, ctx.company_id, provider_id)
    outcome = _run_test(
        db, row, cipher, transport, model=model, allow_private_network=allow_private_network
    )
    row.last_test = outcome.as_dict()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="model_provider.tested",
        target_type="model_provider",
        target_id=row.id,
        summary=f"Tested model provider {row.name}: {outcome.status}",
        data={
            "status": outcome.status,
            "ok": outcome.ok,
            "model": outcome.model,
            "failed_checks": [c.name for c in outcome.checks if not c.ok],
            "warnings": len(outcome.warnings),
        },
    )
    events.emit(db, ctx.company_id, "model_provider.updated", {"provider_id": row.id})
    db.commit()
    return row


def list_models(
    db: Session,
    ctx: CompanyContext,
    provider_id: str,
    cipher: CredentialCipher,
    *,
    transport: httpx.BaseTransport | None = None,
) -> probe.ModelListing:
    """The provider's own model list for a picker (``GET .../models``). Never echoes keys."""
    require_human_admin(ctx, "list a provider's models")
    row = _load(db, ctx.company_id, provider_id)
    api_key = None
    if row.credential_id:
        try:
            api_key = credentials.reveal(db, cipher, row.company_id, row.credential_id)
        except Exception:
            return probe.ModelListing(
                [], False, "The provider's credential is missing or unreadable"
            )
    base = row.base_url or KINDS[row.kind][0] or ""
    return probe.list_models(
        row.kind, base, api_key, transport=transport, policy=_policy_for(db, row)
    )


def _run_test(
    db: Session,
    row: ModelProvider,
    cipher: CredentialCipher,
    transport: httpx.BaseTransport | None,
    *,
    model: str | None = None,
    allow_private_network: bool = False,
) -> probe.CheckOutcome:
    model = model or row.default_model
    net = _policy_for(db, row, allow_private_network)
    if row.kind == "ollama":
        return probe.check_ollama(
            row.base_url, model, row.context_length, transport=transport, policy=net
        )
    api_key = None
    if row.credential_id:
        try:
            api_key = credentials.reveal(db, cipher, row.company_id, row.credential_id)
        except Exception:
            return probe.CheckOutcome(
                status="error",
                message="The provider's credential is missing, revoked or unreadable",
            )
    elif row.kind in CREDENTIAL_REQUIRED:
        return probe.CheckOutcome(status="error", message="No credential is attached")
    base = row.base_url or KINDS[row.kind][0] or ""
    return probe.check_http_provider(
        row.kind,
        base,
        api_key,
        model,
        configured_context=row.context_length,
        transport=transport,
        policy=net,
    )


def detect_ollama(
    db: Session,
    ctx: CompanyContext,
    request: DetectRequest | None,
    cipher: CredentialCipher,
    *,
    transport: httpx.BaseTransport | None = None,
) -> DetectResult:
    """Probe local Ollama. With ``request.register`` and a tool-capable model, register it in one
    call (idempotent: an existing provider for the same URL is returned, never duplicated)."""
    require_human_admin(ctx, "detect model providers")
    request = request or DetectRequest()
    if request.base_url and request.base_url.strip():
        problem = probe.validate_http_url(request.base_url.strip())
        if problem:
            raise ValidationFailed(
                f"base_url {problem}",
                details=[{"loc": ["body", "base_url"], "msg": problem}],
            )
        require_valid_destination("ollama", request.base_url.strip(), request.allow_private_network)
    scan = probe.scan_ollama(
        request.base_url,
        transport=transport,
        policy=netguard.provider_policy("ollama", request.allow_private_network),
    )
    recommended = probe.recommend(scan.models)
    existing = db.scalar(
        select(ModelProvider).where(
            ModelProvider.company_id == ctx.company_id,
            ModelProvider.kind == "ollama",
            ModelProvider.base_url == scan.base_url,
        )
    )
    result = DetectResult(
        base_url=scan.base_url,
        reachable=scan.reachable,
        models=[
            DetectedModel(
                name=m.name,
                parameter_size=m.parameter_size,
                quantization=m.quantization,
                context_length_native=m.context_length_native,
                tool_calling=m.tool_calling,
                recommended=recommended is not None and m.name == recommended.name,
            )
            for m in scan.models
        ],
        recommended_model=recommended.name if recommended else None,
        served_context_length=max(scan.served_context.values(), default=None),
        warnings=[],
        already_registered_provider_id=existing.id if existing else None,
    )
    if not scan.reachable:
        result.warnings.append(scan.error or "Ollama is not reachable")
    elif recommended is None:
        result.warnings.append(
            "No tool-capable chat model found. AI employees need tool calling: "
            "run `ollama pull qwen2.5:7b`."
        )
    if scan.reachable and not scan.served_context:
        result.warnings.append(
            "No model is loaded, so the served context window is unknown; the provider test "
            "loads the model and reports it."
        )
    if not request.register_now:
        return result

    if not scan.reachable:
        raise Conflict(scan.error or "Ollama is not reachable")
    chosen = recommended
    if request.model:
        chosen = next((m for m in scan.models if m.name == request.model), None)
        if chosen is None:
            raise NotFound(f"Model {request.model!r} is not available in Ollama")
        if chosen.tool_calling is False or not chosen.can_chat:
            raise Conflict(
                f"Model {request.model!r} cannot be used (no tool calling / not a chat model)"
            )
    if chosen is None:
        raise Conflict("No tool-capable model found in Ollama; run `ollama pull qwen2.5:7b`")
    if existing is not None:
        row = existing
        if request.make_default and row.enabled:
            set_company_default(db, ctx, row)
            db.commit()
    else:
        row = create_provider(
            db,
            ctx,
            ModelProviderCreate(
                name=_ollama_name(db, ctx.company_id, scan.base_url),
                kind="ollama",
                base_url=scan.base_url,
                default_model=chosen.name,
                context_length=request.context_length,
                make_default=request.make_default,
                allow_private_network=request.allow_private_network,
            ),
        )
    outcome = _run_test(
        db, row, cipher, transport, allow_private_network=request.allow_private_network
    )
    row.last_test = outcome.as_dict()
    db.commit()
    result.registered = serialize_one(db, ctx.company_id, row)  # type: ignore[assignment]
    result.already_registered_provider_id = row.id
    return result


def _ollama_name(db: Session, company_id: str, base_url: str) -> str:
    from urllib.parse import urlparse

    name = (
        "Local Ollama"
        if base_url == probe.OLLAMA_DEFAULT_URL
        else f"Ollama {urlparse(base_url).netloc}"
    )
    taken = set(
        db.scalars(select(ModelProvider.name).where(ModelProvider.company_id == company_id))
    )
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name} ({n})", n + 1
    return candidate


def serialize_one(db: Session, company_id: str, row: ModelProvider):
    from app.schemas.providers import ModelProvider as ModelProviderOut

    return ModelProviderOut.model_validate(serialize(db, company_id, [row])[0])


# ---------------------------------------------------------------- resolution for a run


@dataclass(frozen=True)
class ResolvedModel:
    provider_id: str
    provider_name: str
    kind: str
    runtime_provider: str  # custom | openrouter | anthropic
    base_url: str
    credential_id: str | None
    model: str
    context_length: int
    is_local: bool
    input_price_per_mtok_micros: int | None
    output_price_per_mtok_micros: int | None

    def runtime_base_url(self) -> str:
        """URL as the runtime wants it (Ollama is served under ``/v1``)."""
        if self.kind == "ollama":
            return self.base_url.rstrip("/") + "/v1"
        default = KINDS[self.kind][0]
        if self.runtime_provider != "custom" and self.base_url == default:
            return ""  # the runtime knows its own endpoint for openrouter/anthropic
        return self.base_url


class ModelResolutionError(Exception):
    """No usable provider/model for this agent. The message is safe to show."""


def resolve_for_agent(
    db: Session,
    agent: Agent,
    *,
    provider_id: str | None = None,
    model_name: str | None = None,
) -> ResolvedModel:
    """The provider + model for a run. ``provider_id`` / ``model_name`` are a routing decision
    (M6) that replaces the agent's own choice for one run."""
    pol: CompanyPolicy = policy.load_policy(db, agent.company_id)
    pinned_by_routing = provider_id is not None
    provider_id = provider_id or agent.model_provider or pol.default_model_provider_id
    if not provider_id:
        raise ModelResolutionError(
            "No model provider: set the agent's model provider or a company default model"
        )
    row = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == provider_id, ModelProvider.company_id == agent.company_id
        )
    )
    if row is None:
        raise ModelResolutionError(f"Model provider {provider_id!r} does not exist in this company")
    if not row.enabled:
        raise ModelResolutionError(f"Model provider {row.name!r} is disabled")
    model = model_name if pinned_by_routing and model_name else None
    model = (
        model
        or (agent.model_name if not pinned_by_routing else None)
        or (pol.default_model_name if pol.default_model_provider_id == row.id else None)
    )
    return ResolvedModel(
        provider_id=row.id,
        provider_name=row.name,
        kind=row.kind,
        runtime_provider=KINDS[row.kind][2],
        base_url=row.base_url or KINDS[row.kind][0] or "",
        credential_id=row.credential_id,
        model=model or row.default_model,
        context_length=row.context_length,
        is_local=row.is_local,
        input_price_per_mtok_micros=row.input_price_per_mtok_micros,
        output_price_per_mtok_micros=row.output_price_per_mtok_micros,
    )


def priced_cost(
    model: ResolvedModel, input_tokens: int, output_tokens: int
) -> tuple[int | None, CostKind]:
    """Cost of a run for ``model``. Local = $0 verified (no provider charge). A price table gives an
    *estimate*; no price = unknown (None)."""
    if model.is_local:
        return 0, CostKind.VERIFIED
    if model.input_price_per_mtok_micros is None or model.output_price_per_mtok_micros is None:
        return None, CostKind.UNKNOWN
    micros = (
        input_tokens * model.input_price_per_mtok_micros
        + output_tokens * model.output_price_per_mtok_micros
    ) / 1_000_000
    return int(math.ceil(micros)), CostKind.ESTIMATED
