"""M7 team schemas: members, invitations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel, RequestModel, Timestamp

MemberRoleLiteral = Literal["owner", "admin", "member", "viewer"]
MemberStatusLiteral = Literal["active", "disabled", "removed"]
InvitationStatusLiteral = Literal["pending", "accepted", "expired", "revoked"]
InvitationDeliveryLiteral = Literal["email", "link"]


class Member(ApiModel):
    id: str
    company_id: str
    user_id: str
    username: str
    display_name: str
    email: str | None  # visible to owners and admins only
    role: MemberRoleLiteral
    title: str | None
    manager_ref: str | None  # agent:<id> | member:<id> | null (reports to the Founder)
    status: MemberStatusLiteral
    created_at: Timestamp


class MemberUpdate(RequestModel):
    role: MemberRoleLiteral | None = None
    title: str | None = Field(default=None, max_length=120)
    manager_ref: str | None = Field(default=None, max_length=48)


class Invitation(ApiModel):
    id: str
    company_id: str
    email: str
    role: MemberRoleLiteral
    title: str | None
    manager_ref: str | None
    status: InvitationStatusLiteral
    expires_at: Timestamp
    created_at: Timestamp
    accepted_at: Timestamp | None = None


class InvitationCreate(RequestModel):
    email: str = Field(min_length=3, max_length=320)
    role: MemberRoleLiteral
    title: str | None = Field(default=None, max_length=120)
    manager_ref: str | None = Field(default=None, max_length=48)


class InvitationCreated(Invitation):
    """Returned once, at creation: the token is hashed at rest and cannot be shown again."""

    delivery: InvitationDeliveryLiteral
    invite_url: str | None = None
    token: str | None = None


class InvitationAccept(RequestModel):
    """Empty when the caller is signed in; otherwise the new account's credentials."""

    username: str | None = Field(default=None, max_length=64)
    display_name: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=256)


class InvitationAccepted(ApiModel):
    company_id: str
    company_name: str | None = None
    role: MemberRoleLiteral | None = None
    member_id: str | None = None
