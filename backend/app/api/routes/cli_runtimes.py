"""Coding-CLI runtimes (M10): installation-level, installation owner only (agents get 403)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentPrincipal, DbSession, human_only
from app.schemas.ai_setup import (
    CliAuthUpdate,
    CliInstallLog,
    CliInstallRequest,
    CliRuntimeList,
    CliRuntimeStatus,
)
from app.services import cli_runtimes

router = APIRouter(prefix="/cli-runtimes", tags=["cli-runtimes"])
Gate = Annotated[object, Depends(human_only("cli_runtime.manage"))]


@router.get("", response_model=CliRuntimeList)
def list_clis(request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate):
    cli_runtimes.require_owner(db, principal)
    return {"items": cli_runtimes.list_status(db, request.app.state.cli_env)}


@router.get("/{cli_id}", response_model=CliRuntimeStatus)
def get_cli(cli_id: str, request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate):
    cli_runtimes.require_owner(db, principal)
    return cli_runtimes.status_of(db, request.app.state.cli_env, cli_id)


@router.post("/{cli_id}/install", response_model=CliInstallLog, status_code=202)
def install(
    cli_id: str,
    body: CliInstallRequest,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    _: Gate,
):
    """Run the install command shown in ``install.command`` (npm global install). Never without
    ``confirm: true``, never with sudo. Poll ``GET .../install-log``."""
    state = request.app.state
    return cli_runtimes.start_install(
        db,
        principal,
        state.cli_env,
        state.session_factory,
        state.settings.logs_dir,
        cli_id,
        confirm=body.confirm,
    )


@router.get("/{cli_id}/install-log", response_model=CliInstallLog)
def get_install_log(
    cli_id: str, request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate
):
    return cli_runtimes.install_log(db, principal, request.app.state.settings.logs_dir, cli_id)


@router.put("/{cli_id}/auth", response_model=CliRuntimeStatus)
def put_auth(
    cli_id: str,
    body: CliAuthUpdate,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    _: Gate,
):
    """How the CLI signs in: its own login (you ran its login in a terminal) or an API key from the
    credential store. The key is never returned."""
    return cli_runtimes.set_auth(db, principal, request.app.state.cli_env, cli_id, body)


@router.post("/{cli_id}/test", response_model=CliRuntimeStatus)
async def test_cli(
    cli_id: str, request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate
):
    """Run a tiny headless prompt ("Reply with OK") in a temp dir with a 60 s limit and a $0.05 cap."""
    adapter = request.app.state.runtimes.get(f"cli:{cli_id}")
    return await run_in_threadpool(
        cli_runtimes.run_test, db, principal, request.app.state.cli_env, adapter, cli_id
    )
