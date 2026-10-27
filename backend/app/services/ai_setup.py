"""The AI readiness gate (docs/specs/m10 section 1).

A company is **ready** when it has a *management model*: an enabled model provider whose latest test
passed every check (reachable, authenticated, tool calling works, served context >= 32 768, model
exists), selected as ``policy.default_model_provider_id`` + ``default_model_name``. Until then:

* the readiness pass of the task state machine keeps tasks in the backlog with
  ``blocked_reason="ai_setup_required"`` (so the dispatcher has nothing to dispatch), the wake queue
  does not wake anyone, and creating notes / commands / tasks / hires is a 409 ``ai_setup_required``;
* owner settings, providers, credentials, AI setup, auth and team stay available.

``ENFORCED`` exists for the test-suite only (older tests build companies without a provider); the
gate has no configuration switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AiNotReady, AiSetupRequired, NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.policy import CompanyPolicy
from app.models.provider import ModelProvider
from app.schemas.ai_setup import AiSetupUpdate
from app.services import audit, events, model_providers, policy

ENFORCED = True
AI_SETUP_REQUIRED = "ai_setup_required"  # Task.blocked_reason while the gate is closed
CLI_RUNTIME_PREFIX = "cli:"
WORK_RUNTIMES = (
    "company_agent",
    "cli:claude_code",
    "cli:codex",
    "cli:opencode",
    "cli:antigravity",
)


@dataclass
class Readiness:
    ready: bool
    blocking_reason: str | None
    provider: ModelProvider | None
    model: str | None
    last_test: dict[str, Any] | None


def _test_passed(last_test: dict[str, Any] | None, model: str | None) -> bool:
    return bool(
        last_test and last_test.get("ok") is True and last_test.get("model") == model and model
    )


def evaluate(db: Session, company_id: str) -> Readiness:
    limits = policy.load_policy(db, company_id)
    providers = list(
        db.scalars(
            select(ModelProvider).where(
                ModelProvider.company_id == company_id, ModelProvider.enabled.is_(True)
            )
        )
    )
    selected = next((p for p in providers if p.id == limits.default_model_provider_id), None)
    model = limits.default_model_name or (selected.default_model if selected else None)
    last_test = selected.last_test if selected else None
    if selected is None or not model:
        any_good = any(_test_passed(p.last_test, p.default_model) for p in providers)
        reason = "no_default_model" if any_good else "no_tested_provider"
        return Readiness(False, reason, selected, model, last_test)
    if not last_test:
        return Readiness(False, "no_tested_provider", selected, model, None)
    if not _test_passed(last_test, model):
        return Readiness(False, "provider_test_failed", selected, model, last_test)
    return Readiness(True, None, selected, model, last_test)


def is_ready(db: Session, company_id: str) -> bool:
    return (not ENFORCED) or evaluate(db, company_id).ready


def require_ready(db: Session, company_id: str) -> None:
    """409 ``ai_setup_required`` until the company has a working management model."""
    if not ENFORCED:
        return
    state = evaluate(db, company_id)
    if not state.ready:
        raise AiSetupRequired(details={"reason": state.blocking_reason})


def release_gate(db: Session, company_id: str) -> None:
    """The AI became ready (or work mode changed): tasks parked behind the gate are re-evaluated.
    No commit."""
    from app.services import tasks

    tasks.recompute_readiness(db, company_id)


# ---------------------------------------------------------------- status


def _work_model_block(db: Session, company_id: str, limits: CompanyPolicy) -> dict[str, Any] | None:
    if not (limits.work_model_provider_id and limits.work_model_name):
        return None
    provider = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == limits.work_model_provider_id,
            ModelProvider.company_id == company_id,
        )
    )
    return {
        "provider_id": limits.work_model_provider_id,
        "provider_name": provider.name if provider else None,
        "kind": provider.kind if provider else None,
        "model": limits.work_model_name,
        "last_test": provider.last_test if provider else None,
    }


def status(db: Session, ctx: CompanyContext, cli_env: Any) -> dict[str, Any]:
    from app.services import cli_runtimes

    state = evaluate(db, ctx.company_id)
    limits = policy.load_policy(db, ctx.company_id)
    provider = state.provider
    return {
        "ready": state.ready if ENFORCED else True,
        "blocking_reason": state.blocking_reason,
        "management": {
            "provider_id": provider.id if provider else None,
            "provider_name": provider.name if provider else None,
            "kind": provider.kind if provider else None,
            "model": state.model,
            "last_test": state.last_test,
        },
        "work_model": _work_model_block(db, ctx.company_id, limits),
        "work_mode": limits.work_mode,
        "work_runtime": limits.work_runtime,
        "clis": cli_runtimes.list_status(db, cli_env),
    }


# ---------------------------------------------------------------- update


def _require_cli_ready(db: Session, cli_env: Any, runtime: str) -> None:
    from app.services import cli_runtimes

    cli_id = runtime[len(CLI_RUNTIME_PREFIX) :]
    item = cli_runtimes.status_of(db, cli_env, cli_id)
    if not item["installed"]:
        raise AiNotReady(
            f"{item['display_name']} is not installed",
            details={"reason": "cli_not_installed", "runtime": runtime},
        )
    if not item["auth"]["configured"]:
        raise AiNotReady(
            f"Choose how {item['display_name']} signs in (own login or an API key) first",
            details={"reason": "cli_not_configured", "runtime": runtime},
        )


def _require_tested_model(
    db: Session, company_id: str, provider_id: str, model: str
) -> ModelProvider:
    """An enabled provider of this company whose latest test passed for ``model`` (the checks that
    gate the management model also gate the work model)."""
    row = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == provider_id, ModelProvider.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Model provider not found")
    if not row.enabled:
        raise AiNotReady("That provider is disabled", details={"reason": "provider_test_failed"})
    if not row.last_test:
        raise AiNotReady(
            "Test this provider first (POST .../model-providers/{id}/test)",
            details={"reason": "no_tested_provider"},
        )
    if not _test_passed(row.last_test, model):
        failed = [c for c in (row.last_test.get("checks") or []) if not c.get("ok")]
        tested = row.last_test.get("model")
        raise AiNotReady(
            "The provider's latest test did not pass for this model"
            + (f" ({failed[0]['name']}: {failed[0]['detail']})" if failed else "")
            + (f"; it tested {tested!r}" if tested and tested != model else ""),
            details={"reason": "provider_test_failed", "checks": failed},
        )
    return row


def update(db: Session, ctx: CompanyContext, data: AiSetupUpdate, cli_env: Any) -> dict[str, Any]:
    require_human_admin(ctx, "change the AI setup")
    row = _require_tested_model(
        db, ctx.company_id, data.management_provider_id, data.management_model
    )
    given = data.model_fields_set
    work_model_given = bool({"work_model_provider_id", "work_model_name"} & given)
    work_row: ModelProvider | None = None
    if work_model_given:
        if bool(data.work_model_provider_id) != bool(data.work_model_name):
            raise ValidationFailed(
                "work_model_provider_id and work_model_name must be set together (or both null)",
                details=[
                    {
                        "loc": ["body", "work_model_name"],
                        "msg": "set together with work_model_provider_id",
                    }
                ],
            )
        if data.work_model_provider_id and data.work_model_name:
            work_row = _require_tested_model(
                db, ctx.company_id, data.work_model_provider_id, data.work_model_name
            )
    work_runtime = data.work_runtime
    if data.work_mode == "api":
        work_runtime = None
    else:
        if work_runtime is None:
            raise ValidationFailed(
                "Hybrid mode needs a work_runtime",
                details=[{"loc": ["body", "work_runtime"], "msg": "required for hybrid mode"}],
            )
        if work_runtime == "company_agent":
            raise ValidationFailed(
                "Hybrid mode runs work on a coding CLI; use work_mode 'api' for the company agent",
                details=[{"loc": ["body", "work_runtime"], "msg": "must be a cli:* runtime"}],
            )
        _require_cli_ready(db, cli_env, work_runtime)

    limits: CompanyPolicy = policy.load_policy(db, ctx.company_id)
    was_ready = evaluate(db, ctx.company_id).ready
    changed: list[str] = []
    model_providers.set_company_default(db, ctx, row)  # also equips the Co-founder and HR
    if limits.default_model_name != data.management_model:
        limits.default_model_name = data.management_model
        changed.append("default_model_name")
    for field, value in (("work_mode", data.work_mode), ("work_runtime", work_runtime)):
        if getattr(limits, field) != value:
            setattr(limits, field, value)
            changed.append(field)
    if work_model_given:
        new_provider = work_row.id if work_row else None
        new_name = data.work_model_name if work_row else None
        for field, value in (
            ("work_model_provider_id", new_provider),
            ("work_model_name", new_name),
        ):
            if getattr(limits, field) != value:
                setattr(limits, field, value)
                changed.append(field)
    limits.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="ai_setup.updated",
        target_type="policy",
        target_id=limits.id,
        summary=f"AI setup: {row.name} / {data.management_model}, work mode {data.work_mode}"
        + (f" on {work_runtime}" if work_runtime else "")
        + (f", work model {work_row.name} / {limits.work_model_name}" if work_row else ""),
        data={
            "provider_id": row.id,
            "model": data.management_model,
            "work_mode": data.work_mode,
            "work_runtime": work_runtime,
            "work_model_provider_id": limits.work_model_provider_id,
            "work_model": limits.work_model_name,
            "changed": changed,
            "was_ready": was_ready,
        },
    )
    events.emit(db, ctx.company_id, "policy.updated", {"changed": changed or ["ai_setup"]})
    events.emit(db, ctx.company_id, "ai_setup.updated", {"ready": True})
    release_gate(db, ctx.company_id)
    db.commit()
    return status(db, ctx, cli_env)
