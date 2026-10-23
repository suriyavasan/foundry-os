"""FastAPI dependencies: DB session, principal, company context.

Tests override ``get_principal`` via ``app.dependency_overrides`` to act as another user.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFound, Unauthenticated
from app.core.keys import SecretKeyProvider
from app.core.principal import CompanyContext, Principal, Role
from app.core.tokens import TOKEN_PREFIX
from app.models.agent import Agent
from app.models.company import CompanyMember
from app.runtimes.registry import RuntimeRegistry
from app.services import agent_auth, users
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


def get_principal(request: Request, db: DbSession) -> Principal:
    """A valid agent run token makes the caller that agent (bound to its company), so the
    owner API's authorization rules apply to it. Otherwise, in local auth mode every request
    is the single local owner (localhost only!)."""
    settings: Settings = request.app.state.settings
    token = bearer_token(request)
    if token and token.startswith(TOKEN_PREFIX):
        return agent_auth.authenticate(db, request.app.state.keys, token).ctx.principal
    if settings.auth_mode == "local":
        owner = users.get_local_owner(db)
        if owner is None:
            raise Unauthenticated("Local owner account is missing; restart the server")
        return users.principal_for_user(owner)
    raise Unauthenticated()  # pragma: no cover - other modes arrive in M8


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_company_context(
    company_id: str, principal: CurrentPrincipal, db: DbSession
) -> CompanyContext:
    """Resolve membership. Non-members and unknown companies are indistinguishable (404)."""
    if principal.is_human:
        member = db.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id, CompanyMember.user_id == principal.id
            )
        )
        if member is None:
            raise NotFound("Company not found")
        return CompanyContext(company_id, principal, Role(member.role))

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
