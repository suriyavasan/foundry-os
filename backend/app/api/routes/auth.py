"""Local-mode owner login (M3b-sec): ``GET /api/auth/local?token=<owner token>``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.api.deps import get_owner_token
from app.core.errors import Unauthenticated
from app.core.owner_token import COOKIE_MAX_AGE_SECONDS, OWNER_COOKIE, OwnerTokenProvider

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/local", include_in_schema=True, status_code=303)
def local_login(
    request: Request, token: str = "", owner: OwnerTokenProvider = Depends(get_owner_token)
):
    """Exchange the owner token (from the terminal login link) for an HttpOnly, SameSite=Strict
    session cookie, then redirect to ``/``. A wrong token is 401 and sets nothing."""
    if request.app.state.settings.auth_mode != "local" or not owner.verify(token):
        raise Unauthenticated("Invalid login token", details={"reason": "invalid_login_token"})
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        OWNER_COOKIE,
        token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        path="/",
        secure=request.url.scheme == "https",
    )
    # The URL carries the token: keep it out of caches and Referer headers.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
