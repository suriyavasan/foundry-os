"""Device-level reachability (M8): installation-wide, installation owner only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentPrincipal, DbSession, human_only
from app.schemas import reachability as schemas
from app.services import reachability

router = APIRouter(tags=["reachability"])
Gate = Annotated[object, Depends(human_only("reachability.manage"))]


def _ic(request: Request):
    return request.app.state.integrations


@router.get("/reachability", response_model=schemas.ReachabilityStatus)
def get_status(request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate):
    """LAN, tunnel and the public-exposure gate in one poll (the UI refreshes it every 15 s)."""
    return reachability.status(db, _ic(request), principal)


@router.patch("/reachability/lan", response_model=schemas.ReachabilityStatus)
def set_lan(
    body: schemas.LanRequest, request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate
):
    """Persist the bind preference. The listener is rebound by the launcher: the response reports
    ``lan.restart_required`` while the running process still differs."""
    reachability.set_lan(db, _ic(request), principal, body.enabled)
    return reachability.status(db, _ic(request), principal)


def _tunnel(request: Request, principal, db):
    return reachability.status(db, _ic(request), principal)["tunnel"]


@router.post("/reachability/tunnel/start", response_model=schemas.TunnelStatus)
def start_tunnel(
    body: schemas.TunnelConfig,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    _: Gate,
):
    reachability.start_tunnel(db, _ic(request), principal, body.provider, body.hostname)
    return _tunnel(request, principal, db)


@router.post("/reachability/tunnel/stop", response_model=schemas.TunnelStatus)
def stop_tunnel(request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate):
    reachability.stop_tunnel(db, _ic(request), principal)
    return _tunnel(request, principal, db)


@router.get("/reachability/tunnel/install-plan", response_model=schemas.TunnelInstallPlan)
def install_plan(request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate):
    """What a confirmed install would download and the checksum it must match. Metadata only."""
    return reachability.install_plan(db, _ic(request), principal)


@router.post("/reachability/tunnel/install", response_model=schemas.TunnelStatus)
def install_tunnel_binary(
    body: schemas.TunnelInstallRequest,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    _: Gate,
):
    """Download cloudflared from the official release, verify its SHA-256 against the published
    checksum and install it into data/bin. Needs ``confirm: true``."""
    reachability.install_binary(
        db, _ic(request), principal, confirm=body.confirm, sha256=body.sha256
    )
    return _tunnel(request, principal, db)


@router.get("/reachability/tunnel/logs", response_model=schemas.TunnelLogs)
def tunnel_logs(request: Request, principal: CurrentPrincipal, db: DbSession, _: Gate):
    return {"lines": reachability.tunnel_logs(db, _ic(request), principal)}
