"""Coding-CLI runtimes: installation-level status, install, sign-in mode and test
(docs/specs/m10 section 3, docs/cli-runtimes.md).

Everything here is the installation owner's (the account that claimed the install; agents get 403).
Foundry AI OS never reads, copies or logs a CLI's own credential files: *own login* means the CLI finds
its login itself; an *API key* lives in the encrypted credential store and is injected into one child
process's environment.

The install endpoint runs exactly the command it shows (``npm install -g <package>``), only with
``confirm: true``, never through a shell and never with sudo, as a supervised child whose redacted
output is kept for ``GET .../install-log``. A CLI without an npm package (Antigravity, Cursor: the vendor's
``curl | bash`` installer) is never installed by this endpoint: the status carries a ``manual`` hint."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import Principal
from app.core.time import format_ts, utcnow
from app.models.company import CompanyMember
from app.models.enums import MemberRole, MemberStatus
from app.models.provider import Credential, ModelProvider
from app.runtimes.cli import store
from app.runtimes.cli.catalog import CLIS, CliSpec, sandbox_for
from app.runtimes.cli.env import CliEnvironment, base_env, claude_config_dir
from app.schemas.ai_setup import CliAuthUpdate
from app.services import audit, events, plan_login, reachability

logger = logging.getLogger("company_os.cli_runtimes")

INSTALL_LOG_LINES = 400
INSTALL_TIMEOUT_SECONDS = 900
OWN_LOGIN_WARNING = (
    "Own-login mode uses your real home directory so the CLI can find its login: isolation is weaker "
    "(an API key gives each agent its own home). Foundry AI OS never reads or copies the CLI's "
    "credential files. Check the tool's terms of service before using a personal subscription for "
    "automated, unattended work."
)
_running: dict[str, threading.Thread] = {}
_lock = threading.Lock()

CLAUDE_ID = "claude_code"
CliNotInstalled = plan_login.CliNotInstalled
LoginInProgress = plan_login.LoginInProgress


def _spec(cli_id: str) -> CliSpec:
    spec = CLIS.get(cli_id)
    if spec is None:
        raise NotFound("Unknown CLI runtime")
    return spec


def require_owner(db: Session, principal: Principal) -> None:
    """Installation owner only (agents and other members get 403)."""
    if not principal.is_human:
        raise Forbidden("Only the installation owner manages the coding CLIs")
    reachability.require_installation_owner(db, principal)


# ---------------------------------------------------------------- status


def _version_cached(cli_env: CliEnvironment, binary: str) -> str | None:
    cache: dict[str, tuple[float, str | None]] = cli_env.__dict__.setdefault("_versions", {})
    try:
        mtime = os.stat(binary).st_mtime
    except OSError:
        return None
    hit = cache.get(binary)
    if hit and hit[0] == mtime:
        return hit[1]
    version = cli_env.version(binary)
    cache[binary] = (mtime, version)
    return version


def _auth_block(state: dict[str, Any], spec: CliSpec) -> dict[str, Any]:
    mode = state.get("auth_mode")
    configured = bool(
        mode == "own_login"
        or (mode == "api_key" and (state.get("credential_id") or state.get("model_provider_id")))
    )
    last = state.get("last_test")
    return {
        "mode": mode,
        "configured": configured,
        "credential_id": state.get("credential_id"),
        "last_test": last if isinstance(last, dict) else None,
        "model_provider_id": state.get("model_provider_id"),
        "model": state.get("model"),
    }


def status_of(db: Session, cli_env: CliEnvironment, cli_id: str) -> dict[str, Any]:
    spec = _spec(cli_id)
    binary = cli_env.find_binary(spec)
    state = store.load(db, cli_id)
    npm = cli_env.find_tool("npm")
    install_state = state.get("install") or {}
    auth = _auth_block(state, spec)
    warnings: list[str] = []
    if auth["mode"] == "own_login":
        warnings.append(OWN_LOGIN_WARNING)
    if cli_id == "codex":
        warnings.append(
            "Codex support is coded from the official documentation and tested with a fake binary; it "
            "has not been run against the real CLI."
        )
    if cli_id == "antigravity":
        warnings.append(
            "Antigravity CLI support is coded from Google's documentation and `agy --help`, and tested "
            "with a fake binary; it was never started with a prompt. It runs only with a Gemini API key "
            "(no own login: that would mean writing into your own ~/.gemini)."
        )
        if binary is None:
            warnings.append(
                "Foundry AI OS cannot install it (Google ships a shell installer, which is never run "
                f"from here). In a terminal, run: {spec.manual_install}"
            )
    if cli_id == "cursor":
        warnings.append(
            "Cursor CLI support is coded from Cursor's documentation and tested with a fake binary; "
            "it is not installed on the development machine and was never run. It runs only with a "
            "Cursor API key (no own login: that would mean writing into your own ~/.cursor)."
        )
        if binary is None:
            warnings.append(
                "Foundry AI OS cannot install it (Cursor ships a shell installer, which is never run "
                f"from here). In a terminal, run: {spec.manual_install}"
            )
    if cli_id == "claude_code":
        warnings.append(
            "Claude Code's own sandbox is not verified by Foundry AI OS, so its shell (Bash) tool is never "
            "enabled: it works with files in its workspace and the company tools."
        )
    login: dict[str, Any] | None = None  # removed in M17 (per-connection: /ai-stack/{id}/login)
    return {
        "id": spec.id,
        "display_name": spec.display_name,
        "installed": binary is not None,
        "binary_path": binary,
        "version": _version_cached(cli_env, binary) if binary else None,
        "auth_modes": list(spec.auth_modes),
        "auth": auth,
        "sandbox": sandbox_for(spec.id, binary is not None),
        "install": {
            "available": spec.install_command is not None and npm is not None and binary is None,
            "command": " ".join(spec.install_command) if spec.install_command else None,
            "requires": ["npm"] if spec.install_command else [],
            "status": install_state.get("status", "none"),
            "manual": spec.manual_install,
        },
        "login_hint": spec.login_hint,
        "login": login,
        "warnings": warnings,
    }


def list_status(db: Session, cli_env: CliEnvironment) -> list[dict[str, Any]]:
    return [status_of(db, cli_env, cli_id) for cli_id in CLIS]


def runs_free(db: Session, runtime_id: str) -> bool:
    """A CLI run costs nothing when it is pointed at a local model provider (nothing to reserve)."""
    state = store.load(db, runtime_id.removeprefix("cli:"))
    provider_id = state.get("model_provider_id")
    if not provider_id:
        return False
    row = db.get(ModelProvider, provider_id)
    return bool(row is not None and row.is_local)


# ---------------------------------------------------------------- sign-in mode


def _is_admin_of(db: Session, principal: Principal, company_id: str) -> bool:
    return (
        db.scalar(
            select(CompanyMember.id).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == principal.id,
                CompanyMember.status == MemberStatus.ACTIVE,
                CompanyMember.role.in_([MemberRole.OWNER.value, MemberRole.ADMIN.value]),
            )
        )
        is not None
    )


def _audit(
    db: Session, principal: Principal, action: str, summary: str, data: dict[str, Any]
) -> None:
    for company_id in reachability.owner_company_ids(db, principal.id):
        audit.record(
            db,
            company_id=company_id,
            principal=principal,
            action=action,
            target_type="cli_runtime",
            target_id=data.get("id"),
            summary=summary,
            data=data,
        )
        events.emit(db, company_id, "cli_runtime.updated", {"id": data.get("id")})


def set_auth(
    db: Session, principal: Principal, cli_env: CliEnvironment, cli_id: str, data: CliAuthUpdate
) -> dict[str, Any]:
    require_owner(db, principal)
    spec = _spec(cli_id)
    problems: list[dict[str, Any]] = []
    if data.mode not in spec.auth_modes:
        problems.append(
            {
                "loc": ["body", "mode"],
                "msg": f"{spec.display_name} supports only: {', '.join(spec.auth_modes)}",
            }
        )
    if data.mode == "own_login" and data.credential_id:
        problems.append({"loc": ["body", "credential_id"], "msg": "own_login uses no stored key"})
    if data.model_provider_id and spec.id != "opencode":
        problems.append(
            {
                "loc": ["body", "model_provider_id"],
                "msg": "only OpenCode can use a company model provider",
            }
        )
    if data.mode == "api_key" and not (data.credential_id or data.model_provider_id):
        problems.append(
            {
                "loc": ["body", "credential_id"],
                "msg": "api_key mode needs credential_id"
                + (" or model_provider_id" if spec.id == "opencode" else ""),
            }
        )
    if (
        spec.id == "opencode"
        and not data.model_provider_id
        and not (data.model and "/" in data.model)
    ):
        problems.append(
            {
                "loc": ["body", "model"],
                "msg": "OpenCode needs model_provider_id, or a 'provider/model' name",
            }
        )
    if problems:
        raise ValidationFailed("Invalid sign-in settings", details=problems)
    if data.credential_id:
        cred = db.get(Credential, data.credential_id)
        if (
            cred is None
            or cred.revoked_at is not None
            or not _is_admin_of(db, principal, cred.company_id)
        ):
            raise NotFound("Credential not found")
    if data.model_provider_id:
        prov = db.get(ModelProvider, data.model_provider_id)
        if prov is None or not prov.enabled or not _is_admin_of(db, principal, prov.company_id):
            raise NotFound("Model provider not found")
    store.save(
        db,
        spec.id,
        auth_mode=data.mode,
        credential_id=data.credential_id,
        model_provider_id=data.model_provider_id,
        model=data.model,
        last_test=None,  # the old test described another configuration
    )
    _audit(
        db,
        principal,
        "cli_runtime.auth_updated",
        f"{spec.display_name} signs in with {'its own login' if data.mode == 'own_login' else 'an API key'}",
        {
            "id": spec.id,
            "mode": data.mode,
            "has_credential": bool(data.credential_id),
            "model_provider_id": data.model_provider_id,
        },  # ids only, never a key
    )
    db.commit()
    return status_of(db, cli_env, spec.id)


# ---------------------------------------------------------------- test


def run_test(
    db: Session, principal: Principal, cli_env: CliEnvironment, adapter: Any, cli_id: str
) -> dict[str, Any]:
    require_owner(db, principal)
    spec = _spec(cli_id)
    if adapter is None:
        raise NotFound("Unknown CLI runtime")
    state = store.load(db, spec.id)
    if cli_env.find_binary(spec) is None:
        raise Conflict(f"{spec.display_name} is not installed")
    if not state.get("auth_mode"):
        raise Conflict(f"Choose how {spec.display_name} signs in first")
    ok, detail = adapter.run_test(db, state)
    if spec.id == CLAUDE_ID and cli_env.data_dir is not None:
        # the connectivity test also proves the sign-in works: refresh the cached status
        invalidate_claude_login(cli_env, cli_env.data_dir)
    store.save(
        db, spec.id, last_test={"ok": ok, "detail": detail[:300], "tested_at": format_ts(utcnow())}
    )
    _audit(
        db,
        principal,
        "cli_runtime.tested",
        f"Tested {spec.display_name}: {'ok' if ok else 'failed'}",
        {"id": spec.id, "ok": ok},
    )
    db.commit()
    return status_of(db, cli_env, spec.id)


# ---------------------------------------------------------------- install


def _log_path(logs_dir: Path, cli_id: str) -> Path:
    return logs_dir / f"cli-install-{cli_id}.log"


def install_log(db: Session, principal: Principal, logs_dir: Path, cli_id: str) -> dict[str, Any]:
    require_owner(db, principal)
    spec = _spec(cli_id)
    state = (store.load(db, spec.id).get("install")) or {}
    lines: list[str] = []
    path = _log_path(logs_dir, spec.id)
    if path.is_file():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-INSTALL_LOG_LINES:]
    return {
        "id": spec.id,
        "status": state.get("status", "none"),
        "command": state.get("command"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "exit_code": state.get("exit_code"),
        "lines": lines,
    }


def start_install(
    db: Session,
    principal: Principal,
    cli_env: CliEnvironment,
    session_factory: sessionmaker[Session],
    logs_dir: Path,
    cli_id: str,
    *,
    confirm: bool,
) -> dict[str, Any]:
    """Run ``npm install -g <package>`` as a supervised child. Needs ``confirm: true``."""
    require_owner(db, principal)
    spec = _spec(cli_id)
    install_command = spec.install_command
    if install_command is None:
        raise Conflict(
            f"{spec.display_name} cannot be installed from here (it is not an npm package). "
            f"Run the vendor's installer yourself in a terminal: {spec.manual_install}"
        )
    if confirm is not True:
        raise ValidationFailed(
            "Installing runs a command on this machine; send confirm: true to approve "
            f"`{' '.join(install_command)}`",
            details=[{"loc": ["body", "confirm"], "msg": "must be true"}],
        )
    if cli_env.find_binary(spec) is not None:
        raise Conflict(f"{spec.display_name} is already installed")
    npm = cli_env.find_tool("npm")
    if npm is None:
        raise Conflict("npm is required to install it and was not found on this machine")
    with _lock:
        existing = _running.get(spec.id)
        if existing is not None and existing.is_alive():
            raise Conflict(f"{spec.display_name} is already being installed")
        argv = [npm, *install_command[1:]]
        assert argv[0] != "sudo" and "sudo" not in argv  # never elevated
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = _log_path(logs_dir, spec.id)
        path.write_text("", encoding="utf-8")
        started = format_ts(utcnow())
        command = " ".join(["npm", *install_command[1:]])
        store.save(
            db,
            spec.id,
            install={"status": "running", "command": command, "started_at": started},
        )
        _audit(
            db,
            principal,
            "cli_runtime.install_started",
            f"Installing {spec.display_name}: {command}",
            {"id": spec.id, "command": command},
        )
        db.commit()
        thread = threading.Thread(
            target=_run_install,
            args=(argv, cli_env, session_factory, path, spec, command, started),
            name=f"cli-install-{spec.id}",
            daemon=True,
        )
        _running[spec.id] = thread
        thread.start()
    return install_log(db, principal, logs_dir, spec.id)


def _run_install(
    argv: list[str],
    cli_env: CliEnvironment,
    session_factory: sessionmaker[Session],
    path: Path,
    spec: CliSpec,
    command: str,
    started: str,
) -> None:
    env = {
        **base_env(),
        "PATH": cli_env.search_path(spec),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "npm_config_update_notifier": "false",
        "npm_config_fund": "false",
        "npm_config_audit": "false",
    }
    code: int | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            errors="replace",
        )
        count = 0
        assert proc.stdout is not None
        watchdog = threading.Timer(INSTALL_TIMEOUT_SECONDS, proc.kill)  # a hung npm must end
        watchdog.start()
        try:
            with path.open("a", encoding="utf-8") as log:
                for line in proc.stdout:
                    count += 1
                    if count <= INSTALL_LOG_LINES * 5:
                        log.write(_redact_line(line.rstrip())[:500] + "\n")
                        log.flush()
            code = proc.wait(timeout=30)
        finally:
            watchdog.cancel()
    except Exception as exc:  # the install must always end in a recorded state
        with path.open("a", encoding="utf-8") as log:
            log.write(f"Install could not run ({type(exc).__name__})\n")
        code = -1
    finally:
        with session_factory() as db:
            store.save(
                db,
                spec.id,
                install={
                    "status": "succeeded" if code == 0 else "failed",
                    "command": command,
                    "started_at": started,
                    "finished_at": format_ts(utcnow()),
                    "exit_code": code,
                },
            )
            for company_id in {
                cid
                for cid in db.scalars(
                    select(CompanyMember.company_id).where(
                        CompanyMember.role == MemberRole.OWNER.value,
                        CompanyMember.status == MemberStatus.ACTIVE,
                    )
                )
            }:
                events.emit(db, company_id, "cli_runtime.updated", {"id": spec.id})
            db.commit()


def _redact_line(line: str) -> str:
    from app.services import secrets_scan

    return secrets_scan.scrub(line) or ""


# ---------------------------------------------------------------- environment binding

_bound_env: CliEnvironment | None = None


def bind_environment(cli_env: CliEnvironment) -> None:
    """The app's CLI environment, for checks that run without a request."""
    global _bound_env
    _bound_env = cli_env


def bound_environment() -> CliEnvironment | None:
    return _bound_env


def claude_login_cached(
    cli_env: CliEnvironment, data_dir: Path, *, refresh: bool = False
) -> dict[str, Any]:
    """Sign-in state of the installation-level Claude home ``<data>/claude-code`` (the B9 location, reused
    by a bootstrapped plan connection). Per-connection state: ``plan_login.status_cached``."""
    return plan_login.status_cached(
        cli_env, CLAUDE_ID, claude_config_dir(data_dir), refresh=refresh
    )


def invalidate_claude_login(cli_env: CliEnvironment, data_dir: Path) -> None:
    plan_login.invalidate(claude_config_dir(data_dir))
