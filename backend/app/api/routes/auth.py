"""Authentication (M7): first-run setup, login, sessions, CSRF, tokens, 2FA, password.

``GET /api/auth/local?token=`` (M3b-sec) stays as the owner-token bootstrap link.
"""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import (
    SAFE_METHODS,
    AuthSvc,
    CurrentAuth,
    DbSession,
    account_user,
    authenticate,
    bearer_token,
    get_owner_token,
    owner_cookie_valid,
    reject_agent_credentials,
)
from app.core.errors import CsrfFailed, Unauthenticated
from app.core.netinfo import client_ip, is_loopback_request
from app.core.owner_token import COOKIE_MAX_AGE_SECONDS, OWNER_COOKIE, OwnerTokenProvider
from app.core.time import utcnow
from app.models.user import User
from app.schemas import auth as schemas
from app.schemas.common import Page
from app.services.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE

router = APIRouter(prefix="/auth", tags=["auth"])


# ------------------------------------------------------------------ cookies


def _secure(request: Request) -> bool:
    configured = request.app.state.settings.cookie_secure
    return configured if configured is not None else request.url.scheme == "https"


def _set_session_cookies(
    response: Response, request: Request, auth: AuthSvc, session, raw: str | None
) -> None:
    max_age = request.app.state.settings.session_absolute_days * 86400
    secure = _secure(request)
    if raw is not None:
        response.set_cookie(
            SESSION_COOKIE,
            raw,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            path="/",
            secure=secure,
        )
    # Readable by the page's script on purpose (double submit): it is derived from, and only
    # valid with, this session.
    response.set_cookie(
        CSRF_COOKIE,
        auth.csrf_token(session),
        max_age=max_age,
        httponly=False,
        samesite="lax",
        path="/",
        secure=secure,
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"


def _auth_user(user: User) -> schemas.AuthUser:
    return schemas.AuthUser(
        id=user.id,
        username=user.username or "",
        display_name=user.display_name,
        email=user.email,
        two_factor_enabled=user.totp_enabled,
    )


# ------------------------------------------------------------------ owner-token bootstrap


@router.get("/local", include_in_schema=True, status_code=303)
def local_login(
    request: Request, token: str = "", owner: OwnerTokenProvider = Depends(get_owner_token)
):
    """Exchange the owner token (from the terminal login link) for an HttpOnly, SameSite=Strict
    cookie, then redirect to ``/``. A wrong token is 401 and sets nothing. After the first-run
    setup this cookie no longer authenticates in ``password`` mode (see ``owner_cookie_valid``)."""
    if not owner.verify(token):
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


# ------------------------------------------------------------------ session state


@router.get("/session", response_model=schemas.AuthSession)
def get_session(request: Request, response: Response, db: DbSession, auth: AuthSvc):
    """Public. Who is this, or what has to happen first? Also (re)issues the CSRF cookie for a
    valid browser session, which is how the web app recovers from a stale token."""
    response.headers["Cache-Control"] = "no-store"
    principal = info = None
    with contextlib.suppress(Unauthenticated, CsrfFailed):
        principal, info = authenticate(request, db, enforce_csrf=False, enforce_scope=False)
    user = None
    if principal is not None and principal.is_human:
        user = db.get(User, principal.id)
    if user is not None and user.username:
        if info is not None and info.session is not None:
            _set_session_cookies(response, request, auth, info.session, None)
        return schemas.AuthSession(
            state="authenticated",
            user=_auth_user(user),
            expires_at=info.session.expires_at if info and info.session else None,
        )
    if auth.setup_pending(db):
        holds_owner_credential = info is not None and info.kind == "owner"
        return schemas.AuthSession(
            state="setup_required",
            user=None,
            setup_token_required=not (is_loopback_request(request) or holds_owner_credential),
        )
    return schemas.AuthSession(state="unauthenticated", user=None)


@router.post(
    "/setup",
    response_model=schemas.AuthSession,
    dependencies=[Depends(reject_agent_credentials("user.create"))],
)
def setup(
    body: schemas.SetupRequest, request: Request, response: Response, db: DbSession, auth: AuthSvc
):
    """First run only: claim the install. Needs the setup token printed in the server terminal
    (``data/owner.token``) unless the request comes from loopback or already carries the owner
    credential. On an existing install this claims the placeholder owner, keeping its company."""
    token = bearer_token(request)
    owner_credential = owner_cookie_valid(request, db) or (
        bool(token) and request.app.state.owner_token.verify(token)
    )
    user, session, raw = auth.setup(
        db,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        email=body.email,
        setup_token=body.setup_token,
        loopback=is_loopback_request(request),
        owner_credential=owner_credential,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookies(response, request, auth, session, raw)
    response.delete_cookie(OWNER_COOKIE, path="/")  # the bootstrap cookie has done its job
    return schemas.AuthSession(
        state="authenticated", user=_auth_user(user), expires_at=session.expires_at
    )


@router.post(
    "/login",
    response_model=schemas.AuthSession,
    dependencies=[Depends(reject_agent_credentials("session.create"))],
)
def login(
    body: schemas.LoginRequest, request: Request, response: Response, db: DbSession, auth: AuthSvc
):
    outcome = auth.login(
        db,
        username=body.username,
        password=body.password,
        totp_code=body.totp_code,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if outcome.two_factor_required:
        response.headers["Cache-Control"] = "no-store"
        return schemas.AuthSession(state="unauthenticated", user=None, two_factor_required=True)
    assert outcome.session is not None
    _set_session_cookies(response, request, auth, outcome.session, outcome.session_token)
    return schemas.AuthSession(
        state="authenticated",
        user=_auth_user(outcome.user),
        expires_at=outcome.session.expires_at,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, db: DbSession, auth: AuthSvc):
    """End this browser session (idempotent). A valid session still needs the CSRF token."""
    out = Response(status_code=204)
    resolved = auth.resolve_session(db, request.cookies.get(SESSION_COOKIE), utcnow(), touch=False)
    if resolved is not None:
        session, _user = resolved
        if request.method.upper() not in SAFE_METHODS and not auth.csrf_ok(
            session, request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE)
        ):
            raise CsrfFailed(details={"reason": "csrf"})
        auth.revoke_session(db, session, utcnow())
        db.commit()
    _clear_cookies(out)
    return out


# ------------------------------------------------------------------ password


@router.post("/password", status_code=204)
def change_password(
    body: schemas.PasswordChange,
    user: Annotated[User, Depends(account_user("password.change"))],
    info: CurrentAuth,
    db: DbSession,
    auth: AuthSvc,
):
    auth.change_password(
        db,
        user,
        current_password=body.current_password,
        new_password=body.new_password,
        keep_session_id=info.session.id if info.session else None,
    )
    return Response(status_code=204)


# ------------------------------------------------------------------ sessions


@router.get("/sessions", response_model=Page[schemas.SessionInfo])
def list_sessions(
    user: Annotated[User, Depends(account_user("session.revoke"))],
    info: CurrentAuth,
    db: DbSession,
    auth: AuthSvc,
):
    current = info.session.id if info.session else None
    items = [
        schemas.SessionInfo(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            ip=s.ip,
            user_agent=s.user_agent,
            current=s.id == current,
        )
        for s in auth.list_sessions(db, user.id, utcnow())
    ]
    return {"items": items, "next_cursor": None}


@router.delete("/sessions", status_code=204)
def revoke_all_sessions(
    user: Annotated[User, Depends(account_user("session.revoke"))], db: DbSession, auth: AuthSvc
):
    """Log out everywhere, this browser included."""
    auth.revoke_everywhere(db, user, utcnow())
    out = Response(status_code=204)
    _clear_cookies(out)
    return out


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str,
    user: Annotated[User, Depends(account_user("session.revoke"))],
    info: CurrentAuth,
    db: DbSession,
    auth: AuthSvc,
):
    auth.revoke_one(db, user, session_id, utcnow())
    out = Response(status_code=204)
    if info.session is not None and info.session.id == session_id:
        _clear_cookies(out)
    return out


# ------------------------------------------------------------------ API tokens


@router.get("/tokens", response_model=Page[schemas.ApiToken])
def list_tokens(
    user: Annotated[User, Depends(account_user("token.create"))], db: DbSession, auth: AuthSvc
):
    return {"items": auth.list_tokens(db, user.id), "next_cursor": None}


@router.post("/tokens", response_model=schemas.ApiTokenCreated, status_code=201)
def create_token(
    body: schemas.ApiTokenCreate,
    response: Response,
    user: Annotated[User, Depends(account_user("token.create"))],
    db: DbSession,
    auth: AuthSvc,
):
    """The secret is in this response and nowhere else: only its hash is stored."""
    token, raw = auth.create_token(db, user, name=body.name, scope=body.scope)
    response.headers["Cache-Control"] = "no-store"
    return schemas.ApiTokenCreated(
        id=token.id,
        name=token.name,
        scope=token.scope,  # type: ignore[arg-type]
        prefix=token.prefix,
        created_at=token.created_at,
        last_used_at=None,
        expires_at=token.expires_at,
        revoked_at=None,
        token=raw,
    )


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(
    token_id: str,
    user: Annotated[User, Depends(account_user("token.revoke"))],
    db: DbSession,
    auth: AuthSvc,
):
    auth.revoke_token(db, user, token_id, utcnow())
    return Response(status_code=204)


# ------------------------------------------------------------------ two-factor


@router.post("/2fa/enroll", response_model=schemas.TwoFactorEnroll)
def twofa_enroll(
    response: Response,
    user: Annotated[User, Depends(account_user("twofa.manage"))],
    db: DbSession,
    auth: AuthSvc,
):
    secret, uri = auth.twofa_enroll(db, user)
    response.headers["Cache-Control"] = "no-store"
    return schemas.TwoFactorEnroll(secret=secret, otpauth_uri=uri)


@router.post("/2fa/verify", response_model=schemas.TwoFactorVerifyResult)
def twofa_verify(
    body: schemas.TwoFactorVerify,
    response: Response,
    user: Annotated[User, Depends(account_user("twofa.manage"))],
    db: DbSession,
    auth: AuthSvc,
):
    codes = auth.twofa_verify(db, user, body.code)
    response.headers["Cache-Control"] = "no-store"
    return schemas.TwoFactorVerifyResult(enabled=True, recovery_codes=codes)


@router.post("/2fa/disable", status_code=204)
def twofa_disable(
    body: schemas.TwoFactorDisable,
    user: Annotated[User, Depends(account_user("twofa.manage"))],
    db: DbSession,
    auth: AuthSvc,
):
    auth.twofa_disable(db, user, code=body.code, password=body.password)
    return Response(status_code=204)
