"""FastAPI dependencies: DB session, principal, company context.

Tests override ``get_principal`` via ``app.dependency_overrides`` to act as another user.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import CredentialCipher
from app.core.errors import CsrfFailed, Forbidden, NotFound, Unauthenticated
from app.core.keys import SecretKeyProvider
from app.core.owner_token import OWNER_COOKIE, OwnerTokenProvider
from app.core.principal import CompanyContext, Principal, Role
from app.core.time import utcnow
from app.core.tokens import TOKEN_PREFIX
from app.guardrails import humans
from app.models.accounts import ApiToken, AuthSession
from app.models.agent import Agent
from app.models.company import CompanyMember
from app.models.enums import MemberStatus, TokenScope
from app.models.user import User
from app.runtimes.registry import RuntimeRegistry
from app.services import agent_auth, users
from app.services.auth import (
    API_TOKEN_PREFIX,
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthService,
)
from app.skills.env import SkillsEnv
from app.storage.base import ArtifactStorage


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_runtime_registry(request: Request) -> RuntimeRegistry:
    return request.app.state.runtimes


def get_keys(request: Request) -> SecretKeyProvider:
    return request.app.state.keys


def get_storage(request: Request) -> ArtifactStorage:
    return request.app.state.storage


def get_skills_env(request: Request) -> SkillsEnv:
    return request.app.state.skills


def get_cipher(request: Request) -> CredentialCipher:
    return request.app.state.cipher


def get_owner_token(request: Request) -> OwnerTokenProvider:
    return request.app.state.owner_token


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
Skills = Annotated[SkillsEnv, Depends(get_skills_env)]


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth


AuthSvc = Annotated[AuthService, Depends(get_auth_service)]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Routes a ``write``-scoped API token may not call: team, policy, secrets, guardrails, system.
_ADMIN_ONLY = re.compile(
    r"^/(companies/[^/]+/(members|invitations|policy|credentials|guardrails|model-providers)"
    r"(/|$)|system(/|$))"
)
_COMPANY_ADMIN = re.compile(r"^/companies/[^/]+(/(pause|resume))?/?$")


def _api_path(request: Request) -> str:
    path = request.url.path
    return path[4:] if path.startswith("/api/") else path


@dataclass(frozen=True)
class AuthInfo:
    """How the caller authenticated (``request.state.auth``). Routes that manage the account
    itself (password, sessions, tokens, 2FA) require ``kind`` in {session, owner}."""

    kind: str  # "session" | "api_token" | "owner" | "run"
    session: AuthSession | None = None
    token: ApiToken | None = None

    @property
    def scope(self) -> str | None:
        return self.token.scope if self.token else None


def _enforce_token_scope(token: ApiToken, request: Request) -> None:
    path = _api_path(request)
    unsafe = request.method.upper() not in SAFE_METHODS
    if path.startswith("/auth/"):
        raise Forbidden(
            "API tokens cannot manage the account; sign in with a browser session",
            details={"reason": "token_cannot_manage_account"},
        )
    if not unsafe:
        return
    if token.scope == TokenScope.READ:
        raise Forbidden(
            "This token is read-only", details={"reason": "token_scope", "scope": "read"}
        )
    if token.scope == TokenScope.WRITE and (
        _ADMIN_ONLY.match(path)
        or (request.method.upper() in ("PATCH", "POST") and _COMPANY_ADMIN.match(path))
    ):
        raise Forbidden(
            "This route needs an admin-scoped token",
            details={"reason": "token_scope", "scope": "write"},
        )


def _unauthenticated(settings: Settings) -> Unauthenticated:
    if settings.auth_mode == "local":
        return Unauthenticated(
            "Owner credential required. Open Company OS from the login link printed in the "
            "terminal where the backend runs (see backend/README.md).",
            details={"reason": "owner_credential_required", "login_path": "/api/auth/local"},
        )
    return Unauthenticated("Sign in to continue", details={"reason": "authentication_required"})


def owner_cookie_valid(request: Request, db: Session) -> bool:
    """The M3b-sec ``cos_owner`` cookie: always in ``local`` mode, otherwise only to bootstrap an
    install that has no account yet (once setup is done it is dead; the Bearer owner token stays
    as the CLI fallback)."""
    cookie = request.cookies.get(OWNER_COOKIE)
    if not cookie:
        return False
    owner_token: OwnerTokenProvider = request.app.state.owner_token
    if not owner_token.verify(cookie):
        return False
    settings: Settings = request.app.state.settings
    return settings.auth_mode == "local" or request.app.state.auth.setup_pending(db)


def authenticate(
    request: Request, db: Session, *, enforce_csrf: bool = True, enforce_scope: bool = True
) -> tuple[Principal, AuthInfo]:
    """Resolve the caller or raise 401.

    * ``Authorization: Bearer cos_run.…`` -> that agent (bound to its company);
    * ``Bearer cos_pat_…`` -> a personal API token (scope-limited, no CSRF, no account management);
    * ``Bearer <owner token>`` -> the local owner (bootstrap / CLI fallback, no CSRF);
    * the ``cos_session`` cookie -> that account, with the double-submit CSRF check on every
      state-changing method;
    * the ``cos_owner`` cookie -> the local owner while bootstrapping (see ``owner_cookie_valid``).
    A request that carries a Bearer header is judged on it alone. Loopback is NOT trusted.
    """
    settings: Settings = request.app.state.settings
    auth: AuthService = request.app.state.auth
    now = utcnow()
    token = bearer_token(request)
    if token:
        if token.startswith(TOKEN_PREFIX):
            rc = agent_auth.authenticate(db, request.app.state.keys, token)
            return rc.ctx.principal, AuthInfo("run")
        if token.startswith(API_TOKEN_PREFIX):
            found = auth.resolve_token(db, token, now)
            if found is None:
                raise Unauthenticated("Invalid or revoked API token")
            api_token, user = found
            if enforce_scope:
                _enforce_token_scope(api_token, request)
            return auth.principal_for(user), AuthInfo("api_token", token=api_token)
        if request.app.state.owner_token.verify(token):
            owner = users.get_local_owner(db)
            if owner is None:
                raise Unauthenticated("Local owner account is missing; restart the server")
            return users.principal_for_user(owner), AuthInfo("owner")
        raise _unauthenticated(settings)

    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        resolved = auth.resolve_session(db, raw, now)
        if resolved is not None:
            session, user = resolved
            if (
                enforce_csrf
                and request.method.upper() not in SAFE_METHODS
                and not auth.csrf_ok(
                    session, request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE)
                )
            ):
                raise CsrfFailed(details={"reason": "csrf"})
            return auth.principal_for(user), AuthInfo("session", session=session)
    if owner_cookie_valid(request, db):
        owner = users.get_local_owner(db)
        if owner is not None:
            return users.principal_for_user(owner), AuthInfo("owner")
    raise _unauthenticated(settings)


def get_principal(request: Request, db: DbSession) -> Principal:
    """Who is calling? See ``authenticate``."""
    principal, info = authenticate(request, db)
    request.state.auth = info
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_auth_info(request: Request, _principal: CurrentPrincipal) -> AuthInfo:
    info = getattr(request.state, "auth", None)
    # Tests override get_principal; such a caller is treated like the owner of the process.
    return info if isinstance(info, AuthInfo) else AuthInfo("owner")


CurrentAuth = Annotated[AuthInfo, Depends(get_auth_info)]


def human_only(action: str):
    """Dependency factory: a human account, else 403 ``guardrail_denied`` (and a guardrail event).
    An agent's run token is accepted by ``get_principal``, so this is what keeps agents out of the
    account and team routes. ``action`` is a name from ``guardrails.registry.HUMAN_ONLY``."""

    def dependency(principal: CurrentPrincipal, db: DbSession) -> Principal:
        humans.deny_agent(db, principal, action)
        return principal

    return dependency


def reject_agent_credentials(action: str):
    """For the public routes (setup, login, invitation accept): a request that presents an agent run
    token is refused outright -- even though these routes need no credential."""

    def dependency(request: Request, db: DbSession) -> None:
        token = bearer_token(request)
        if token and token.startswith(TOKEN_PREFIX):
            principal = None
            with contextlib.suppress(Unauthenticated):
                principal = agent_auth.authenticate(db, request.app.state.keys, token).ctx.principal
            if principal is not None:
                humans.deny_agent(db, principal, action)
            raise Forbidden(
                "Agents cannot manage accounts", details={"reason": "human_only", "action": action}
            )

    return dependency


def account_user(action: str = "account.manage"):
    """Dependency factory: the caller's own account for account-management routes. Needs a
    browser session or the owner token (a personal API token can never mint tokens, change the
    password or read sessions) and a human (an agent's run token gets 403 + a guardrail event)."""

    def dependency(principal: CurrentPrincipal, info: CurrentAuth, db: DbSession) -> User:
        humans.deny_agent(db, principal, action)
        if info.kind not in ("session", "owner"):
            raise Forbidden(
                "Account management needs a browser session",
                details={"reason": "token_cannot_manage_account"},
            )
        return users.get_user(db, principal.id)

    return dependency


def get_company_context(
    company_id: str, principal: CurrentPrincipal, db: DbSession
) -> CompanyContext:
    """Resolve membership. Non-members, removed members and unknown companies are
    indistinguishable (404)."""
    if principal.is_human:
        member = db.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == principal.id,
                CompanyMember.status == MemberStatus.ACTIVE,
            )
        )
        if member is None:
            raise NotFound("Company not found")
        return CompanyContext(company_id, principal, Role(member.role), member_id=member.id)

    # Agent principal: bound to exactly one company.
    agent = db.scalar(
        select(Agent.id).where(Agent.id == principal.id, Agent.company_id == company_id)
    )
    if principal.company_id != company_id or agent is None:
        raise NotFound("Company not found")
    return CompanyContext(company_id, principal, Role.AGENT)


CompanyCtx = Annotated[CompanyContext, Depends(get_company_context)]


def get_agent_run_context(request: Request, db: DbSession) -> agent_auth.AgentRunContext:
    """Agent API auth: ``Authorization: Bearer <run token>``; valid only for a live run."""
    token = bearer_token(request)
    if not token:
        raise Unauthenticated("Run token required")
    return agent_auth.authenticate(db, request.app.state.keys, token)


RunCtx = Annotated[agent_auth.AgentRunContext, Depends(get_agent_run_context)]
