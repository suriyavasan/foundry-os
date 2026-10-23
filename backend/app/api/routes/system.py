from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, DbSession
from app.core.config import APP_VERSION
from app.core.errors import Forbidden
from app.schemas.system import Health, Me, MeMembership, MeUser
from app.services import users

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
        user=MeUser(id=user.id, email=user.email, display_name=user.display_name),
        memberships=[
            MeMembership(company_id=c.id, company_name=c.name, role=m.role)
            for m, c in users.list_memberships(db, user.id)
        ],
    )
