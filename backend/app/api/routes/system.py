from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, DbSession, human_only
from app.core.config import APP_VERSION
from app.core.errors import Forbidden
from app.schemas.guardrails import EmergencyStopRequest, EmergencyStopStatus, SystemStatus
from app.schemas.system import (
    Health,
    Me,
    MeMembership,
    MeUser,
    UpdateSettingsRequest,
    UpdateStatus,
)
from app.services import emergency, system_update, users

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Health)
def health(request: Request, db: DbSession):
    body = {
        "status": "ok",
        "database": "ok",
        "version": APP_VERSION,
        "auth_mode": request.app.state.settings.auth_mode,
    }
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        body.update(status="degraded", database="error")
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/me", response_model=Me)
def me(principal: CurrentPrincipal, db: DbSession):
    if not principal.is_human:
        raise Forbidden("Only human users have an account profile")
    user = users.get_user(db, principal.id)
    return Me(
        user=MeUser(
            id=user.id,
            email=user.email or "",
            display_name=user.display_name,
            username=user.username or "",
        ),
        memberships=[
            MeMembership(company_id=c.id, company_name=c.name, role=m.role, member_id=m.id)
            for m, c in users.list_memberships(db, user.id)
        ],
    )


# ------------------------------------------------------------ Milestone 6: emergency stop


@router.get("/system/status", response_model=SystemStatus)
def system_status(principal: CurrentPrincipal, db: DbSession):
    """Instance-wide status (today: the emergency stop). Humans only."""
    if not principal.is_human:
        raise Forbidden("Only human users can read the system status")
    return SystemStatus(emergency_stop=emergency.status(db), version=APP_VERSION)


@router.post("/system/emergency-stop", response_model=EmergencyStopStatus)
def emergency_stop(
    principal: CurrentPrincipal, db: DbSession, body: EmergencyStopRequest | None = None
):
    """Owner only. Cancels every live run in EVERY company and disables dispatch until resumed."""
    return emergency.stop(db, principal, body.reason if body else None)


@router.post("/system/resume", response_model=EmergencyStopStatus)
def resume(principal: CurrentPrincipal, db: DbSession):
    """Owner only. Lifts the emergency stop. Agents never can (403, run tokens included)."""
    return emergency.resume(db, principal)


# ------------------------------------------------------------ Milestone 11: updates

UpdateGate = Annotated[object, Depends(human_only("system.update"))]


@router.get("/system/update", response_model=UpdateStatus)
def update_status(request: Request, principal: CurrentPrincipal, db: DbSession, _: UpdateGate):
    """Installation owner only. Is a newer release known? Asks GitHub at most once a day."""
    return system_update.get_status(db, request.app.state.settings, principal)


@router.put("/system/update/settings", response_model=UpdateStatus)
def update_settings(
    body: UpdateSettingsRequest,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    _: UpdateGate,
):
    """Installation owner only. Switch the daily update check on or off (audited)."""
    return system_update.set_enabled(db, request.app.state.settings, principal, body.enabled)
