"""M17 "One AI stack": the company's ordered list of AI connections (docs/specs/m17-ai-stack.md).

A connection is a model API (a company ``ModelProvider`` + model; runs on ``company_agent``) or a
coding CLI
(Claude Code / Cursor signed in with a plan, or a CLI with an API key / OpenCode provider; runs on
``cli:<id>``). This module owns: the option catalog, CRUD + order, per-connection tests and plan
sign-in,
readiness, the one-time bootstrap from the legacy settings, and the functions the routing step calls
(``run_auth``, ``usable``, ``record_plan_event``, ``clear_plan_event``).

Rules (AGENTS.md): every query is filtered by the caller's company; writes need a human owner/admin
(agents get 403, viewers read); every state change writes an AuditEvent in the same transaction; no
secret
is ever stored, returned, audited or logged here (credentials and providers are referenced by id;
plan
sign-in state comes only from the CLI's own ``status`` command and is never read from its files)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.crypto import CredentialCipher
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import format_ts, utcnow
from app.models.ai_stack import AiConnection
from app.models.provider import Credential, ModelProvider
from app.models.run import TaskRun
from app.runtimes.cli import store
from app.runtimes.cli.catalog import CLIS
from app.runtimes.cli.env import CliEnvironment
from app.schemas.ai_stack import AiConnectionCreate, AiConnectionPatch
from app.services import audit, credentials, events, model_providers, plan_login, policy

logger = logging.getLogger("company_os.ai_stack")

HEAVY = "heavy"
EVERYDAY = "everyday"
EVERYTHING = "everything"
USAGE_LIMIT = "usage_limit"
LOGIN_EXPIRED = "login_expired"
RANKS = ("Primary", "Secondary", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth")
SHARE_LABELS = {4: "Most", 3: "More", 2: "Normal", 1: "Less", 0: "Backup only"}
MAX_CONNECTIONS = 50
CLAUDE_HOME = "claude-code"  # the B9 directory, reused by a bootstrapped Claude plan connection
CLAUDE_WARNING = (
    "Personal plans assume ordinary individual use. An always-on company may hit plan limits or be "
    "restricted by Anthropic; an API key is the safe choice for heavy autonomy."
)
CURSOR_WARNING = (
    "Cursor recommends API keys for automation. Sign-in through Foundry was never exercised "
    "against the real Cursor CLI. One run at a time uses this sign-in."
)


class PlanAlreadyConnected(Conflict):
    code = "plan_already_connected"
    default_message = "That plan is already connected"


@dataclass(frozen=True)
class OptionSpec:
    key: str
    label: str
    group: str  # plan | api | local
    engine: str  # api | cli
    cli_id: str | None = None
    provider_kind: str | None = None
    auth_mode: str | None = None
    warning: str | None = None


OPTIONS: dict[str, OptionSpec] = {
    o.key: o
    for o in (
        OptionSpec(
            "claude_plan",
            "Claude: your plan (runs Claude Code)",
            "plan",
            "cli",
            "claude_code",
            auth_mode="plan",
            warning=CLAUDE_WARNING,
        ),
        OptionSpec("anthropic_api", "Anthropic API", "api", "api", provider_kind="anthropic"),
        OptionSpec("openai_api", "OpenAI API", "api", "api", provider_kind="openai"),
        OptionSpec("openrouter", "OpenRouter", "api", "api", provider_kind="openrouter"),
        OptionSpec(
            "openai_compatible",
            "OpenAI-compatible",
            "api",
            "api",
            provider_kind="openai_compatible",
        ),
        OptionSpec("ollama", "Ollama (on this computer)", "local", "api", provider_kind="ollama"),
        OptionSpec(
            "cursor_plan",
            "Cursor: your plan (runs Cursor CLI)",
            "plan",
            "cli",
            "cursor",
            auth_mode="plan",
            warning=CURSOR_WARNING,
        ),
        OptionSpec("cursor_api", "Cursor API key", "api", "cli", "cursor", auth_mode="api_key"),
        OptionSpec("codex_api", "Codex (OpenAI key)", "api", "cli", "codex", auth_mode="api_key"),
        OptionSpec("opencode", "OpenCode", "api", "cli", "opencode", auth_mode="api_key"),
        OptionSpec(
            "antigravity_api",
            "Antigravity (Gemini key)",
            "api",
            "cli",
            "antigravity",
            auth_mode="api_key",
        ),
    )
}
_KIND_OPTION = {o.provider_kind: o.key for o in OPTIONS.values() if o.provider_kind}


# ---------------------------------------------------------------- small helpers


def _require_human(ctx: CompanyContext) -> None:
    if not ctx.principal.is_human:
        raise Forbidden("Agents cannot read or change the company's AI stack")


def _write(ctx: CompanyContext, action: str) -> None:
    require_human_admin(ctx, action)


def _rows(db: Session, company_id: str) -> list[AiConnection]:
    return list(
        db.scalars(
            select(AiConnection)
            .where(AiConnection.company_id == company_id)
            .order_by(AiConnection.position, AiConnection.created_at, AiConnection.id)
        )
    )


def _load(db: Session, company_id: str, connection_id: str) -> AiConnection:
    row = db.scalar(
        select(AiConnection).where(
            AiConnection.id == connection_id, AiConnection.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("AI connection not found")
    return row


def option_of(row: AiConnection, provider: ModelProvider | None = None) -> str:
    if row.engine == "api":
        return _KIND_OPTION.get(provider.kind if provider else "", "openai_compatible")
    if row.cli_id == "claude_code":
        return "claude_plan"
    if row.cli_id == "cursor":
        return "cursor_plan" if row.auth_mode == "plan" else "cursor_api"
    return {"codex": "codex_api", "opencode": "opencode", "antigravity": "antigravity_api"}.get(
        row.cli_id or "", "opencode"
    )


def rank_label(position_index: int) -> str:
    return RANKS[position_index] if position_index < len(RANKS) else f"#{position_index + 1}"


def is_plan(row: AiConnection) -> bool:
    return row.engine == "cli" and row.auth_mode == "plan"


def covers(row: AiConnection, klass: str) -> bool:
    return row.use_for in (EVERYTHING, klass)


def max_concurrency(row: AiConnection) -> int | None:
    """A Cursor plan connection runs one process at a time: the adapter writes its MCP wiring and
    permissions into the sign-in's shared HOME for every run, so two runs would overwrite each
    other's."""
    return 1 if is_plan(row) and row.cli_id == "cursor" else None


def _home(row: AiConnection, data_dir: Path | None) -> Path | None:
    if not is_plan(row) or not row.home_dir or data_dir is None:
        return None
    return Path(data_dir) / row.home_dir


def _data_dir(cli_env: CliEnvironment | None) -> Path | None:
    return cli_env.data_dir if cli_env is not None else None


def _last_test_ok(row: AiConnection) -> bool:
    return bool(row.last_test and row.last_test.get("ok") is True)


def _paused(row: AiConnection, now: datetime) -> bool:
    return bool(row.paused_until and row.paused_until > now and row.pause_reason != LOGIN_EXPIRED)


def _commit(db: Session, company_id: str) -> None:
    """Re-evaluate tasks parked behind the AI gate (the stack changed), then commit."""
    from app.services import ai_setup

    ai_setup.release_gate(db, company_id)
    db.commit()


def _emit(db: Session, company_id: str, connection_id: str | None) -> None:
    events.emit(db, company_id, "ai_stack.updated", {"connection_id": connection_id})


def _audit(
    db: Session,
    ctx: CompanyContext | None,
    company_id: str,
    action: str,
    row: AiConnection | None,
    summary: str,
    **data: Any,
) -> None:
    payload = {"option": option_of(row), "engine": row.engine, "cli_id": row.cli_id} if row else {}
    audit.record(
        db,
        company_id=company_id,
        principal=ctx.principal if ctx else None,
        action=action,
        target_type="ai_connection",
        target_id=row.id if row else None,
        summary=summary,
        data={**payload, **data},
    )
    _emit(db, company_id, row.id if row else None)


# ---------------------------------------------------------------- readiness


def readiness(db: Session, company_id: str) -> tuple[bool, str | None, int]:
    """(ready, blocking_reason, connection count). Ready: an enabled connection with a passing test
    covers heavy work and one covers everyday work (may be the same). A paused (usage-limited) or
    signed-out connection still counts: it recovers on its own. No subprocess runs here."""
    ensure_bootstrapped(db, company_id)
    rows = _rows(db, company_id)
    off = set(
        db.scalars(
            select(ModelProvider.id).where(
                ModelProvider.company_id == company_id, ModelProvider.enabled.is_(False)
            )
        )
    )
    # an API connection whose provider is switched off cannot run
    enabled = [
        r for r in rows if r.enabled and not (r.engine == "api" and r.model_provider_id in off)
    ]
    if not enabled:
        return False, "no_ai", len(rows)
    tested = [r for r in enabled if _last_test_ok(r)]
    if not tested:
        return False, "no_tested_ai", len(rows)
    if not any(covers(r, HEAVY) for r in tested):
        return False, "no_heavy_ai", len(rows)
    if not any(covers(r, EVERYDAY) for r in tested):
        return False, "no_everyday_ai", len(rows)
    return True, None, len(rows)


def sync_provider(db: Session, provider: ModelProvider) -> None:
    """A provider was (re)tested or edited: the API connections on it follow. A test of the
    connection's
    model passes or fails it; an edit (``last_test`` cleared) asks for a new test. No commit."""
    rows = list(
        db.scalars(
            select(AiConnection).where(
                AiConnection.company_id == provider.company_id,
                AiConnection.model_provider_id == provider.id,
            )
        )
    )
    if not rows:
        return
    last = provider.last_test
    for row in rows:
        if last is None:
            row.last_test = None
        elif last.get("model") == row.model:
            ok = last.get("ok") is True
            row.last_test = {
                "ok": ok,
                "detail": "Passed the provider test"
                if ok
                else str(last.get("message") or "The provider test did not pass")[:300],
                "tested_at": format_ts(utcnow()),
                "model": row.model,
            }
    from app.services import ai_setup

    ai_setup.release_gate(db, provider.company_id)


# ---------------------------------------------------------------- bootstrap from the legacy setting


def ensure_bootstrapped(db: Session, company_id: str) -> list[AiConnection]:
    """The first time a company's stack is read it is built from the legacy settings (management
    provider, work model, hybrid CLI, B9 Claude plan) in one audited transaction (system principal).
    Idempotent; never runs again once the flag is set or any connection exists, even after deletes.
    Returns the connections it created (empty when it did nothing)."""
    limits = policy.load_policy(db, company_id)
    if limits.ai_stack_bootstrapped:
        return []
    if db.scalar(select(func.count(AiConnection.id)).where(AiConnection.company_id == company_id)):
        limits.ai_stack_bootstrapped = True
        db.commit()
        return []
    created: list[AiConnection] = []
    skipped: list[str] = []

    def add(**fields: Any) -> AiConnection:
        row = AiConnection(company_id=company_id, position=len(created), enabled=True, **fields)
        db.add(row)
        created.append(row)
        return row

    def provider_row(provider_id: str | None) -> ModelProvider | None:
        if not provider_id:
            return None
        found = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == provider_id, ModelProvider.company_id == company_id
            )
        )
        return found if found is not None and found.enabled else None

    def api_test(prov: ModelProvider, model: str) -> dict[str, Any] | None:
        last = prov.last_test
        if not last or last.get("model") != model or "ok" not in last:
            return None
        ok = last.get("ok") is True
        return {
            "ok": ok,
            "detail": "Passed the provider test"
            if ok
            else str(last.get("message") or "The provider test did not pass")[:300],
            "tested_at": last.get("checked_at") or format_ts(utcnow()),
            "model": model,
        }

    if limits.ai_engine == "claude_plan":
        state = store.load(db, "claude_code")
        passed = state.get("auth_mode") == "own_login" and (state.get("last_test") or {}).get("ok")
        add(
            label="Claude plan",
            engine="cli",
            cli_id="claude_code",
            auth_mode="plan",
            use_for=EVERYTHING,
            share=2,
            home_dir=CLAUDE_HOME,
            last_test=state.get("last_test") if passed else None,
        )
    else:
        mgmt = provider_row(limits.default_model_provider_id)
        mgmt_model = limits.default_model_name or (mgmt.default_model if mgmt else None)
        work = provider_row(limits.work_model_provider_id)
        work_model = limits.work_model_name
        separate_work = bool(
            work and work_model and not (mgmt and work.id == mgmt.id and work_model == mgmt_model)
        )
        cli_row: dict[str, Any] | None = None
        if limits.work_mode == "hybrid" and (limits.work_runtime or "").startswith("cli:"):
            cli_row = _legacy_cli_connection(db, company_id, limits.work_runtime, skipped)
        heavy = separate_work or cli_row is not None
        if mgmt is not None and mgmt_model:
            add(
                label=mgmt.name,
                engine="api",
                model_provider_id=mgmt.id,
                model=mgmt_model,
                use_for=HEAVY if heavy else EVERYTHING,
                share=2,
                last_test=api_test(mgmt, mgmt_model),
            )
        if separate_work and work is not None and work_model:
            add(
                label=f"{work.name} (everyday work)",
                engine="api",
                model_provider_id=work.id,
                model=work_model,
                use_for=EVERYDAY,
                share=2,
                last_test=api_test(work, work_model),
            )
        if cli_row is not None:
            add(**cli_row)
    limits.ai_stack_bootstrapped = True
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=None,
        action="ai_stack.bootstrapped",
        target_type="ai_connection",
        target_id=None,
        summary=f"Built the AI stack from the previous settings ({len(created)} connection(s))",
        data={
            "created": [
                {
                    "id": r.id,
                    "option": option_of(
                        r,
                        db.get(ModelProvider, r.model_provider_id) if r.model_provider_id else None,
                    ),
                }
                for r in created
            ],
            "skipped": skipped,
        },
    )
    _emit(db, company_id, None)
    db.commit()
    return created


def _legacy_cli_connection(
    db: Session, company_id: str, runtime: str, skipped: list[str]
) -> dict[str, Any] | None:
    """Fields of the connection for a legacy hybrid ``work_runtime`` with its installation-level
    sign-in."""
    cli_id = runtime.removeprefix("cli:")
    state = store.load(db, cli_id)
    mode = state.get("auth_mode")
    last = state.get("last_test") if isinstance(state.get("last_test"), dict) else None
    base: dict[str, Any] = {
        "engine": "cli",
        "cli_id": cli_id,
        "use_for": EVERYDAY,
        "share": 2,
        "model": state.get("model"),
        "last_test": last,
    }
    spec = CLIS.get(cli_id)
    label = spec.display_name if spec else cli_id
    if mode == "own_login" and cli_id == "claude_code":
        return {**base, "label": "Claude plan", "auth_mode": "plan", "home_dir": CLAUDE_HOME}
    if mode == "api_key" and cli_id == "opencode" and state.get("model_provider_id"):
        prov = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == state["model_provider_id"],
                ModelProvider.company_id == company_id,
            )
        )
        if prov is not None:
            return {
                **base,
                "label": label,
                "auth_mode": "provider",
                "cli_provider_id": prov.id,
            }
    if mode == "api_key" and cli_id != "claude_code" and state.get("credential_id"):
        cred = db.scalar(
            select(Credential).where(
                Credential.id == state["credential_id"],
                Credential.company_id == company_id,
                Credential.revoked_at.is_(None),
            )
        )
        if cred is not None:
            return {**base, "label": label, "auth_mode": "api_key", "credential_id": cred.id}
    skipped.append(f"{runtime} ({mode or 'not configured'})")
    return None


# ---------------------------------------------------------------- views


def _status(
    row: AiConnection,
    provider: ModelProvider | None,
    cli_env: CliEnvironment | None,
    now: datetime,
) -> dict[str, Any]:
    last = row.last_test if isinstance(row.last_test, dict) else None
    out: dict[str, Any] = {
        "state": "ready",
        "message": None,
        "last_test": _last_test_view(last),
        "signed_in": None,
        "account": None,
        "paused_until": row.paused_until if _paused(row, now) else None,
    }

    def done(state: str, message: str | None = None) -> dict[str, Any]:
        out.update(state=state, message=message)
        return out

    installed = True
    if row.engine == "cli" and cli_env is not None and row.cli_id in CLIS:
        installed = cli_env.find_binary(CLIS[row.cli_id]) is not None
    signed_in: bool | None = None
    if is_plan(row) and installed and cli_env is not None:
        home = _home(row, cli_env.data_dir)
        if home is not None:
            login = plan_login.status_cached(cli_env, row.cli_id or "", home)
            signed_in = bool(login["signed_in"])
            out["signed_in"] = signed_in
            out["account"] = login.get("email")
    if not row.enabled:
        return done("disabled", "Turned off")
    if row.engine == "api" and (provider is None or not provider.enabled):
        return done("test_failed", "The model provider was removed or is turned off")
    if row.engine == "cli" and not installed:
        spec = CLIS.get(row.cli_id or "")
        return done(
            "not_installed", f"{spec.display_name if spec else row.cli_id} is not installed"
        )
    if is_plan(row) and (signed_in is False or row.pause_reason == LOGIN_EXPIRED):
        return done("needs_sign_in", _sign_in_message(row))
    if _paused(row, utcnow()):
        return done("paused", row.pause_reason)
    if last is None:
        return done("needs_test")
    if last.get("ok") is not True:
        return done("test_failed", str(last.get("detail") or "")[:300] or None)
    return out


def _sign_in_message(row: AiConnection) -> str:
    return "Sign in again" if row.pause_reason == LOGIN_EXPIRED else "Not signed in"


def _last_test_view(last: dict[str, Any] | None) -> dict[str, Any] | None:
    if not last or "tested_at" not in last:
        return None
    return {
        "ok": bool(last.get("ok")),
        "detail": str(last.get("detail") or "")[:300],
        "tested_at": last["tested_at"],
        "model": last.get("model"),
    }


def _runs_24h(db: Session, company_id: str) -> dict[str, int]:
    since = utcnow() - timedelta(hours=24)
    rows = db.execute(
        select(TaskRun.ai_connection_id, func.count(TaskRun.id))
        .where(
            TaskRun.company_id == company_id,
            TaskRun.ai_connection_id.is_not(None),
            TaskRun.started_at >= since,
        )
        .group_by(TaskRun.ai_connection_id)
    )
    return {cid: int(n) for cid, n in rows}


def serialize(
    db: Session,
    company_id: str,
    rows: list[AiConnection],
    cli_env: CliEnvironment | None,
) -> list[dict[str, Any]]:
    now = utcnow()
    providers = {
        p.id: p
        for p in db.scalars(select(ModelProvider).where(ModelProvider.company_id == company_id))
    }
    runs = _runs_24h(db, company_id)
    out = []
    for index, row in enumerate(rows):
        provider = providers.get(row.model_provider_id or "")
        out.append(
            {
                "id": row.id,
                "position": row.position,
                "rank_label": rank_label(index),
                "label": row.label,
                "engine": row.engine,
                "option": option_of(row, provider),
                "provider_kind": provider.kind if provider else None,
                "model_provider_id": row.model_provider_id,
                "model": row.model,
                "cli_id": row.cli_id,
                "auth_mode": row.auth_mode,
                "credential_id": row.credential_id,
                "cli_provider_id": row.cli_provider_id,
                "use_for": row.use_for,
                "share": row.share,
                "enabled": row.enabled,
                "status": _status(row, provider, cli_env, now),
                "runs_24h": runs.get(row.id, 0),
                "max_concurrency": max_concurrency(row),
                "home_dir": row.home_dir if is_plan(row) else None,
                "created_at": row.created_at,
            }
        )
    return out


def _percentages(weights: list[tuple[str, int]]) -> dict[str, int]:
    total = sum(w for _, w in weights)
    if total <= 0:
        return {}
    raw = [(cid, w * 100 / total) for cid, w in weights]
    floors = {cid: int(v) for cid, v in raw}
    left = 100 - sum(floors.values())
    order = sorted(raw, key=lambda t: (-(t[1] - int(t[1])), [c for c, _ in raw].index(t[0])))
    for cid, _ in order[:left]:
        floors[cid] += 1
    return floors


def summarize(views: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Share-weighted split among enabled, ready, non-backup connections per class; backups listed
    with 0."""
    out: dict[str, list[dict[str, Any]]] = {HEAVY: [], EVERYDAY: []}
    for klass in (HEAVY, EVERYDAY):
        cover = [v for v in views if v["enabled"] and v["use_for"] in (EVERYTHING, klass)]
        live = [v for v in cover if v["status"]["state"] == "ready" and v["share"] > 0]
        pct = _percentages([(v["id"], v["share"]) for v in live])
        for v in live:
            out[klass].append(
                {"connection_id": v["id"], "label": v["label"], "percent": pct.get(v["id"], 0)}
            )
        for v in cover:
            if v["share"] == 0:
                out[klass].append(
                    {"connection_id": v["id"], "label": v["label"], "percent": 0, "backup": True}
                )
    return out


def options(db: Session, ctx: CompanyContext, cli_env: CliEnvironment) -> list[dict[str, Any]]:
    from app.services import cli_runtimes

    rows = _rows(db, ctx.company_id)
    plans = {r.cli_id for r in rows if is_plan(r)}
    has_ollama = (
        db.scalar(
            select(ModelProvider.id).where(
                ModelProvider.company_id == ctx.company_id, ModelProvider.kind == "ollama"
            )
        )
        is not None
    )
    installs = {cid: cli_runtimes.status_of(db, cli_env, cid) for cid in CLIS}
    out = []
    for spec in OPTIONS.values():
        item: dict[str, Any] = {
            "option": spec.key,
            "label": spec.label,
            "group": spec.group,
            "engine": spec.engine,
            "cli_id": spec.cli_id,
            "provider_kind": spec.provider_kind,
            "available": True,
            "reason": None,
            "needs_install": False,
            "install": None,
            "warning": spec.warning,
        }
        if spec.cli_id:
            info = installs[spec.cli_id]
            if not info["installed"]:
                item["needs_install"] = True
                item["install"] = {
                    "available": bool(info["install"]["available"]),
                    "command": info["install"]["command"] or info["install"]["manual"],
                }
        if spec.auth_mode == "plan" and spec.cli_id in plans:
            item.update(available=False, reason="plan_already_connected")
        if spec.key == "ollama" and not has_ollama:
            item.update(available=False, reason="ollama_not_detected")
        out.append(item)
    return out


def list_stack(db: Session, ctx: CompanyContext, cli_env: CliEnvironment) -> dict[str, Any]:
    _require_human(ctx)
    ready, reason, _ = readiness(db, ctx.company_id)
    rows = _rows(db, ctx.company_id)
    views = serialize(db, ctx.company_id, rows, cli_env)
    return {
        "items": views,
        "ready": ready,
        "blocking_reason": reason,
        "summary": summarize(views),
        "options": options(db, ctx, cli_env),
    }


def get_view(
    db: Session, ctx: CompanyContext, connection_id: str, cli_env: CliEnvironment
) -> dict[str, Any]:
    _require_human(ctx)
    rows = _rows(db, ctx.company_id)
    index = next((i for i, r in enumerate(rows) if r.id == connection_id), None)
    if index is None:
        raise NotFound("AI connection not found")
    return serialize(db, ctx.company_id, rows, cli_env)[index]


# ---------------------------------------------------------------- writes


def _validate_new(
    db: Session, ctx: CompanyContext, spec: OptionSpec, data: AiConnectionCreate
) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []

    def bad(field: str, msg: str) -> None:
        problems.append({"loc": ["body", field], "msg": msg})

    fields: dict[str, Any] = {}
    if spec.engine == "api":
        if data.credential_id or data.cli_provider_id:
            bad("credential_id", "API options use a model provider, not a key")
        if not data.model_provider_id:
            bad("model_provider_id", "required for API options")
        if problems:
            raise ValidationFailed("Invalid AI connection", details=problems)
        provider = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == data.model_provider_id,
                ModelProvider.company_id == ctx.company_id,
            )
        )
        if provider is None:
            raise NotFound("Model provider not found")
        if provider.kind != spec.provider_kind:
            raise ValidationFailed(
                f"That provider is a {provider.kind} provider, not {spec.label}",
                details=[{"loc": ["body", "model_provider_id"], "msg": "wrong provider kind"}],
            )
        if not provider.enabled:
            raise ValidationFailed(
                "That provider is turned off",
                details=[{"loc": ["body", "model_provider_id"], "msg": "provider disabled"}],
            )
        fields.update(
            model_provider_id=provider.id,
            model=data.model or provider.default_model,
            label=data.label or provider.name,
        )
        return fields
    # CLI options
    if data.model_provider_id:
        bad("model_provider_id", "CLI options do not use a model provider")
    fields.update(cli_id=spec.cli_id, label=data.label or CLIS[spec.cli_id or ""].display_name)
    if spec.auth_mode == "plan":
        if data.credential_id or data.cli_provider_id:
            bad("credential_id", "a plan sign-in uses no key")
        fields.update(auth_mode="plan", model=data.model)
    elif spec.key == "opencode":
        if data.cli_provider_id:
            fields.update(auth_mode="provider", model=data.model)
            if data.credential_id:
                bad("credential_id", "use either a provider or a key")
        elif data.credential_id:
            if not (data.model and "/" in data.model):
                bad("model", "OpenCode with a key needs a 'provider/model' name")
            fields.update(auth_mode="api_key", model=data.model)
        else:
            bad("credential_id", "OpenCode needs cli_provider_id or credential_id")
    else:
        if data.cli_provider_id:
            bad("cli_provider_id", "only OpenCode can use a model provider")
        if not data.credential_id:
            bad("credential_id", "required: this option signs in with an API key")
        fields.update(auth_mode="api_key", model=data.model)
    if problems:
        raise ValidationFailed("Invalid AI connection", details=problems)
    if data.credential_id:
        credentials.require_usable(
            db, ctx.company_id, data.credential_id
        )  # 404 for another company
        fields["credential_id"] = data.credential_id
    if data.cli_provider_id:
        prov = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == data.cli_provider_id,
                ModelProvider.company_id == ctx.company_id,
            )
        )
        if prov is None:
            raise NotFound("Model provider not found")
        if not prov.enabled:
            raise ValidationFailed("That provider is turned off")
        fields["cli_provider_id"] = prov.id
    return fields


def _adopt_match(created: list[AiConnection], fields: dict[str, Any]) -> AiConnection | None:
    """A connection the bootstrap just built that is the same thing as the one being added."""
    for row in created:
        same = (
            fields.get("model_provider_id")
            and fields["model_provider_id"] == row.model_provider_id
            and fields.get("model") == row.model
        ) or (
            fields.get("cli_id")
            and fields["cli_id"] == row.cli_id
            and fields.get("auth_mode") == row.auth_mode
        )
        if same:
            return row
    return None


def add(
    db: Session,
    ctx: CompanyContext,
    data: AiConnectionCreate,
    cli_env: CliEnvironment,
) -> dict[str, Any]:
    _write(ctx, "change the AI stack")
    spec = OPTIONS[data.option]
    fields = _validate_new(db, ctx, spec, data)
    created = ensure_bootstrapped(db, ctx.company_id)
    existing = _rows(db, ctx.company_id)
    if spec.auth_mode == "plan" and any(is_plan(r) and r.cli_id == spec.cli_id for r in existing):
        adopted = _adopt_match(created, fields)
        if adopted is None:
            raise PlanAlreadyConnected(
                f"{CLIS[spec.cli_id or ''].display_name} is already signed in with a plan in this "
                "company. Remove that connection first; rotating several personal plans is not "
                "supported."
            )
        row = adopted
        row.label, row.use_for, row.share = fields["label"], data.use_for, data.share
        _audit(db, ctx, ctx.company_id, "ai_connection.updated", row, f"Set up AI: {row.label}")
        _commit(db, ctx.company_id)
        return get_view(db, ctx, row.id, cli_env)
    if len(existing) >= MAX_CONNECTIONS:
        raise ValidationFailed(f"At most {MAX_CONNECTIONS} AI connections")
    adopted = _adopt_match(created, fields)
    if adopted is not None and spec.auth_mode != "plan":
        adopted.label, adopted.use_for, adopted.share = fields["label"], data.use_for, data.share
        _audit(
            db, ctx, ctx.company_id, "ai_connection.updated", adopted, f"Set up AI: {adopted.label}"
        )
        _commit(db, ctx.company_id)
        return get_view(db, ctx, adopted.id, cli_env)
    row = AiConnection(
        company_id=ctx.company_id,
        position=(max((r.position for r in existing), default=-1) + 1),
        engine=spec.engine,
        use_for=data.use_for,
        share=data.share,
        enabled=True,
        **fields,
    )
    if spec.engine == "api":
        provider = db.get(ModelProvider, row.model_provider_id)
        last = provider.last_test if provider else None
        if last and last.get("ok") is True and last.get("model") == row.model:
            row.last_test = {
                "ok": True,
                "detail": "Passed the provider test",
                "tested_at": last.get("checked_at") or format_ts(utcnow()),
                "model": row.model,
            }
    db.add(row)
    db.flush()
    if spec.auth_mode == "plan":
        row.home_dir = f"{plan_login.PLAN_CLIS[spec.cli_id or ''].subdir}/{row.id}"
        if cli_env.data_dir is not None:
            plan_login.make_home(cli_env.data_dir, row.home_dir)
    _audit(
        db,
        ctx,
        ctx.company_id,
        "ai_connection.added",
        row,
        f"Added AI connection {row.label}",
        use_for=row.use_for,
        share=row.share,
        model=row.model,
        has_credential=bool(row.credential_id),
        position=row.position,
    )
    _commit(db, ctx.company_id)
    return get_view(db, ctx, row.id, cli_env)


def patch(
    db: Session,
    ctx: CompanyContext,
    connection_id: str,
    data: AiConnectionPatch,
    cli_env: CliEnvironment,
) -> dict[str, Any]:
    _write(ctx, "change the AI stack")
    row = _load(db, ctx.company_id, connection_id)
    given = data.model_fields_set
    changed: list[str] = []
    for field in ("label", "use_for", "share", "enabled"):
        if field in given:
            value = getattr(data, field)
            if value is None:
                raise ValidationFailed(f"{field} cannot be null")
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(field)
    if "model" in given:
        if data.model is None:
            raise ValidationFailed("model cannot be null")
        if row.engine == "cli" and row.auth_mode == "provider":
            raise ValidationFailed("The model of a provider-backed CLI comes from its provider")
        if row.model != data.model:
            row.model = data.model
            row.last_test = None  # the old test described another model
            changed.append("model")
    if changed:
        row.updated_at = utcnow()
        _audit(
            db,
            ctx,
            ctx.company_id,
            "ai_connection.updated",
            row,
            f"Changed AI connection {row.label}: {', '.join(changed)}",
            changed=changed,
        )
    _commit(db, ctx.company_id)
    return get_view(db, ctx, row.id, cli_env)


def reorder(
    db: Session, ctx: CompanyContext, ids: list[str], cli_env: CliEnvironment
) -> dict[str, Any]:
    _write(ctx, "change the AI stack")
    ensure_bootstrapped(db, ctx.company_id)
    rows = {r.id: r for r in _rows(db, ctx.company_id)}
    if len(set(ids)) != len(ids) or set(ids) != set(rows):
        raise ValidationFailed(
            "ids must be exactly the company's AI connection ids, each once",
            details=[{"loc": ["body", "ids"], "msg": "must list every connection exactly once"}],
        )
    for position, cid in enumerate(ids):
        rows[cid].position = position
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="ai_connection.reordered",
        target_type="ai_connection",
        target_id=None,
        summary="Reordered the AI stack",
        data={"order": ids},
    )
    _emit(db, ctx.company_id, None)
    _commit(db, ctx.company_id)
    return list_stack(db, ctx, cli_env)


def remove(db: Session, ctx: CompanyContext, connection_id: str, cli_env: CliEnvironment) -> None:
    """Delete a connection. A plan connection signs out first and loses its home. A model provider
    or
    credential is never deleted (it may be used elsewhere)."""
    _write(ctx, "change the AI stack")
    row = _load(db, ctx.company_id, connection_id)
    logged_out: bool | None = None
    removed_home = False
    if is_plan(row):
        data_dir = _data_dir(cli_env)
        home = _home(row, data_dir)
        if home is not None and home.is_dir():
            if plan_login.is_running(home):
                raise plan_login.LoginInProgress("A sign-in is running; wait for it to end first")
            try:
                logged_out = plan_login.logout(cli_env, row.cli_id or "", home)
            except plan_login.CliNotInstalled:
                logged_out = None
            plan_login.invalidate(home)
        if data_dir is not None:
            removed_home = plan_login.remove_home(data_dir, row.home_dir)
    _audit(
        db,
        ctx,
        ctx.company_id,
        "ai_connection.removed",
        row,
        f"Removed AI connection {row.label}",
        logged_out=logged_out,
        removed_home=removed_home,
    )
    db.delete(row)
    db.flush()
    for position, other in enumerate(_rows(db, ctx.company_id)):
        other.position = position
    _commit(db, ctx.company_id)


# ---------------------------------------------------------------- test


def _record_test(
    db: Session, ctx: CompanyContext, row: AiConnection, ok: bool, detail: str, model: str | None
) -> None:
    row.last_test = {
        "ok": ok,
        "detail": detail[:300],
        "tested_at": format_ts(utcnow()),
        "model": model,
    }
    if ok and row.pause_reason == LOGIN_EXPIRED:
        row.pause_reason = None
    row.updated_at = utcnow()
    _audit(
        db,
        ctx,
        ctx.company_id,
        "ai_connection.tested",
        row,
        f"Tested AI connection {row.label}: {'ok' if ok else 'failed'}",
        ok=ok,
    )
    _commit(db, ctx.company_id)


def test(
    db: Session,
    ctx: CompanyContext,
    connection_id: str,
    *,
    cipher: CredentialCipher,
    cli_env: CliEnvironment,
    adapters: Any,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """API: the provider's live test for this connection's model. CLI: the adapter's tiny headless
    prompt
    using THIS connection's auth (its key, OpenCode provider or plan home)."""
    _write(ctx, "test AI connections")
    row = _load(db, ctx.company_id, connection_id)
    if row.engine == "api":
        provider = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == row.model_provider_id,
                ModelProvider.company_id == ctx.company_id,
            )
        )
        if provider is None:
            _record_test(db, ctx, row, False, "The model provider was removed", row.model)
            return get_view(db, ctx, row.id, cli_env)
        tested = model_providers.test_provider(
            db, ctx, provider.id, cipher, model=row.model, transport=transport
        )
        last = tested.last_test or {}
        ok = last.get("ok") is True and last.get("model") == row.model
        failed = [c for c in (last.get("checks") or []) if not c.get("ok")]
        detail = (
            "Passed the provider test"
            if ok
            else (f"{failed[0].get('name')}: {failed[0].get('detail')}" if failed else None)
            or str(last.get("message") or "The provider test did not pass")
        )
        _record_test(db, ctx, row, ok, detail, row.model)
        return get_view(db, ctx, row.id, cli_env)
    spec = CLIS[row.cli_id or ""]
    adapter = adapters.get(f"cli:{row.cli_id}") if adapters is not None else None
    if adapter is None:
        raise NotFound("Unknown CLI runtime")
    if cli_env.find_binary(spec) is None:
        raise Conflict(f"{spec.display_name} is not installed")
    auth = run_auth(db, row, data_dir=cli_env.data_dir)
    ok, detail = adapter.run_test(db, auth.as_state(), ctx.company_id)
    home = _home(row, cli_env.data_dir)
    if home is not None:
        plan_login.invalidate(home)  # the test also proves the sign-in works
    _record_test(db, ctx, row, ok, detail, auth.model)
    return get_view(db, ctx, row.id, cli_env)


# ---------------------------------------------------------------- what the routing step uses


@dataclass(frozen=True)
class RunAuth:
    """How a CLI run / test signs in, for one connection. Holds ids and a path, never a secret: the
    adapter
    decrypts ``credential_id`` itself at spawn time and passes the key only to the child's
    environment."""

    connection_id: str
    cli_id: str
    auth_mode: str  # own_login (plan) | api_key (key or OpenCode provider)
    credential_id: str | None
    provider_id: str | None
    model: str | None
    plan_home: str | None  # absolute path of a plan connection's private home
    max_concurrency: int | None

    def as_state(self) -> dict[str, Any]:
        """The ``state`` dict the CLI adapters consume (same keys as the installation-level
        setting)."""
        return {
            "auth_mode": self.auth_mode,
            "credential_id": self.credential_id,
            "model_provider_id": self.provider_id,
            "model": self.model,
            "plan_home": self.plan_home,
            "connection_id": self.connection_id,
        }


def run_auth(db: Session, connection: AiConnection, *, data_dir: Path | None = None) -> RunAuth:
    """Auth of a CLI connection for a run or test. ``data_dir`` defaults to the app's data
    directory."""
    if connection.engine != "cli" or not connection.cli_id:
        raise ValueError("run_auth is for CLI connections")
    if data_dir is None:
        from app.services import cli_runtimes

        env = cli_runtimes.bound_environment()
        data_dir = env.data_dir if env is not None else None
    home = _home(connection, data_dir)
    if is_plan(connection) and home is not None:
        home.mkdir(parents=True, exist_ok=True)
    return RunAuth(
        connection_id=connection.id,
        cli_id=connection.cli_id,
        auth_mode="own_login" if is_plan(connection) else "api_key",
        credential_id=connection.credential_id,
        provider_id=connection.cli_provider_id,
        model=connection.model,
        plan_home=str(home) if home is not None else None,
        max_concurrency=max_concurrency(connection),
    )


def usable(
    connection: AiConnection,
    *,
    db: Session | None = None,
    cli_env: CliEnvironment | None = None,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Can this connection take a run right now? (usable, reason when not). Checks the stored state
    (enabled,
    passing test, not paused, login not expired); with ``cli_env`` also that the CLI is installed,
    with ``db``
    that an API connection's provider still exists and is enabled. Runs no subprocess."""
    now = now or utcnow()
    if not connection.enabled:
        return False, f"{connection.label} is turned off"
    if not _last_test_ok(connection):
        return False, f"{connection.label} has not passed its test"
    if connection.pause_reason == LOGIN_EXPIRED:
        return False, f"{connection.label} needs to be signed in again"
    if connection.paused_until and connection.paused_until > now:
        until = format_ts(connection.paused_until)
        return False, f"{connection.label} is paused until {until}"
    if connection.engine == "cli" and cli_env is not None:
        spec = CLIS.get(connection.cli_id or "")
        if spec is None or cli_env.find_binary(spec) is None:
            return False, f"{spec.display_name if spec else connection.cli_id} is not installed"
    if connection.engine == "api" and db is not None:
        provider = db.get(ModelProvider, connection.model_provider_id)
        if provider is None or not provider.enabled:
            return False, f"The model provider of {connection.label} is not available"
    return True, None


def record_plan_event(
    db: Session,
    connection_id: str,
    kind: str,
    *,
    until: datetime | None = None,
    message: str | None = None,
) -> None:
    """A plan run hit its usage limit (``usage_limit``: paused until ``until``, else one hour) or
    the login
    expired (``login_expired``: needs sign-in). Audited (system principal), no commit."""
    row = db.get(AiConnection, connection_id)
    if row is None:
        return
    if kind == USAGE_LIMIT:
        row.paused_until = until or (utcnow() + timedelta(hours=1))
        row.pause_reason = (message or "Plan usage limit reached")[:200]
        summary = f"{row.label} paused: plan usage limit"
    else:
        row.paused_until = None
        row.pause_reason = LOGIN_EXPIRED
        summary = f"{row.label} needs to be signed in again"
        home = _home(row, _data_dir(_bound_env()))
        if home is not None:
            plan_login.invalidate(home)
    _audit(db, None, row.company_id, "ai_connection.paused", row, summary, kind=kind)


def clear_plan_event(db: Session, connection_id: str) -> None:
    """A run succeeded: clear a stale pause / login-expired mark. No commit."""
    row = db.get(AiConnection, connection_id)
    if row is None or not (row.paused_until or row.pause_reason):
        return
    row.paused_until = None
    row.pause_reason = None
    _audit(
        db, None, row.company_id, "ai_connection.resumed", row, f"{row.label} is available again"
    )


def _bound_env() -> CliEnvironment | None:
    from app.services import cli_runtimes

    return cli_runtimes.bound_environment()


# ---------------------------------------------------------------- plan sign-in


def _plan_connection(
    db: Session, ctx: CompanyContext, connection_id: str, cli_env: CliEnvironment
) -> tuple[AiConnection, Path]:
    row = _load(db, ctx.company_id, connection_id)
    if not is_plan(row) or not plan_login.supported(row.cli_id):
        raise NotFound("Only plan connections have a sign-in")
    data_dir = _data_dir(cli_env)
    if data_dir is None or not row.home_dir:
        raise Conflict("The data directory is not known")
    return row, plan_login.make_home(data_dir, row.home_dir)


def login_status(
    db: Session,
    ctx: CompanyContext,
    connection_id: str,
    cli_env: CliEnvironment,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    _require_human(ctx)
    row, home = _plan_connection(db, ctx, connection_id, cli_env)
    return plan_login.status(cli_env, row.cli_id or "", home, refresh=refresh)


def start_login(
    db: Session,
    ctx: CompanyContext,
    connection_id: str,
    cli_env: CliEnvironment,
    session_factory: sessionmaker[Session],
    *,
    method: str = "claudeai",
) -> dict[str, Any]:
    _write(ctx, "sign AI connections in")
    row, home = _plan_connection(db, ctx, connection_id, cli_env)
    if method not in ("claudeai", "console"):
        raise ValidationFailed(
            "method must be claudeai or console",
            details=[{"loc": ["body", "method"], "msg": "claudeai or console"}],
        )
    company_id, cid, label = ctx.company_id, row.id, row.label
    on_finish = _finish_callback(session_factory, company_id, cid, label)
    result = plan_login.start(cli_env, row.cli_id or "", home, method=method, on_finish=on_finish)
    row.last_test = None  # a new sign-in may be another account: test again
    _audit(
        db,
        ctx,
        company_id,
        "ai_connection.login_started",
        row,
        f"Started the sign-in of {label}",
        method=method if row.cli_id == "claude_code" else None,
    )
    db.commit()
    return result


def _finish_callback(
    session_factory: sessionmaker[Session], company_id: str, connection_id: str, label: str
) -> Callable[[bool], None]:
    def finish(ok: bool) -> None:
        with session_factory() as db:
            row = db.scalar(
                select(AiConnection).where(
                    AiConnection.id == connection_id, AiConnection.company_id == company_id
                )
            )
            if row is None:
                return
            row.last_test = None
            if ok and row.pause_reason == LOGIN_EXPIRED:
                row.pause_reason = None
            _audit(
                db,
                None,
                company_id,
                "ai_connection.login_finished",
                row,
                f"The sign-in of {label} finished"
                if ok
                else f"The sign-in of {label} did not complete",
                ok=ok,
            )
            db.commit()

    return finish


def logout(
    db: Session, ctx: CompanyContext, connection_id: str, cli_env: CliEnvironment
) -> dict[str, Any]:
    _write(ctx, "sign AI connections out")
    row, home = _plan_connection(db, ctx, connection_id, cli_env)
    ok = plan_login.logout(cli_env, row.cli_id or "", home)
    row.last_test = None
    _audit(
        db,
        ctx,
        ctx.company_id,
        "ai_connection.logout",
        row,
        f"Signed out of {row.label}"
        if ok
        else f"Tried to sign out of {row.label} (it reported a problem)",
        ok=ok,
    )
    db.commit()
    return plan_login.status(cli_env, row.cli_id or "", home, refresh=True)
