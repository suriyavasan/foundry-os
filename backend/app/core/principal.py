"""Principals (who is calling) and per-company context (what they may do here)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import Forbidden


class PrincipalType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"  # M7: a human colleague; reads, and works on what is assigned to them
    VIEWER = "viewer"
    AGENT = "agent"  # role of an agent principal inside its own company


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Human principals have ``id`` = user id; agent principals
    have ``id`` = agent id and are bound to exactly one company."""

    type: PrincipalType
    id: str
    label: str
    company_id: str | None = None  # agent principals only

    @property
    def is_human(self) -> bool:
        return self.type == PrincipalType.HUMAN

    @property
    def is_agent(self) -> bool:
        return self.type == PrincipalType.AGENT


@dataclass(frozen=True)
class CompanyContext:
    company_id: str
    principal: Principal
    role: Role
    # M7: the caller's CompanyMember id (humans only). ``member:<member_id>`` is the ref other
    # records (assignee, manager, approver) use for this person.
    member_id: str | None = None

    @property
    def member_ref(self) -> str | None:
        return f"member:{self.member_id}" if self.member_id else None

    @property
    def is_human_admin(self) -> bool:
        return self.principal.is_human and self.role in (Role.OWNER, Role.ADMIN)


def require_human_admin(ctx: CompanyContext, action: str = "do this") -> None:
    """Owner/admin humans only. Viewers and agents get 403."""
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    if not ctx.is_human_admin:
        raise Forbidden(f"Only company owners and admins can {action}")
