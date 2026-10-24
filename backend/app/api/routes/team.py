"""Members, invitations and the invitation-accept flow (M7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select

from app.api.deps import (
    AuthSvc,
    CompanyCtx,
    DbSession,
    authenticate,
    human_only,
    reject_agent_credentials,
)
from app.api.routes.auth import _set_session_cookies
from app.core.errors import Unauthenticated
from app.core.netinfo import client_ip
from app.models.accounts import AuthSession
from app.models.user import User
from app.schemas import team as schemas
from app.schemas.common import Page
from app.services import team
from app.services.auth import digest
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(tags=["team"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


# ---------------------------------------------------------------- members


@router.get("/companies/{company_id}/members", response_model=Page[schemas.Member])
def list_members(
    ctx: CompanyCtx,
    db: DbSession,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
    include_removed: bool = False,
):
    items, nxt = team.list_members(
        db, ctx, limit=limit, cursor=cursor, include_removed=include_removed
    )
    return {"items": items, "next_cursor": nxt}


@router.get("/companies/{company_id}/members/{member_id}", response_model=schemas.Member)
def get_member(member_id: str, ctx: CompanyCtx, db: DbSession):
    return team.get_member_view(db, ctx, member_id)


@router.patch("/companies/{company_id}/members/{member_id}", response_model=schemas.Member)
def update_member(
    member_id: str,
    body: schemas.MemberUpdate,
    ctx: CompanyCtx,
    db: DbSession,
    _: Annotated[object, Depends(human_only("member.update"))],
):
    return team.update_member(db, ctx, member_id, body)


@router.delete("/companies/{company_id}/members/{member_id}", status_code=204)
def remove_member(
    member_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    auth: AuthSvc,
    _: Annotated[object, Depends(human_only("member.remove"))],
):
    team.remove_member(db, ctx, member_id, auth)
    return Response(status_code=204)


# ---------------------------------------------------------------- invitations


@router.get("/companies/{company_id}/invitations", response_model=Page[schemas.Invitation])
def list_invitations(
    ctx: CompanyCtx, db: DbSession, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None
):
    items, nxt = team.list_invitations(db, ctx, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/companies/{company_id}/invitations",
    response_model=schemas.InvitationCreated,
    status_code=201,
)
def create_invitation(
    body: schemas.InvitationCreate,
    request: Request,
    response: Response,
    ctx: CompanyCtx,
    db: DbSession,
    _: Annotated[object, Depends(human_only("invitation.create"))],
):
    """Delivery is ``link``: no email provider is connected until M8, so nothing is sent. The link
    (and the token inside it) is in this response once; only its hash is stored."""
    created = team.create_invitation(db, ctx, body, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"
    return created


@router.delete("/companies/{company_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    invitation_id: str,
    ctx: CompanyCtx,
    db: DbSession,
    _: Annotated[object, Depends(human_only("invitation.revoke"))],
):
    team.revoke_invitation(db, ctx, invitation_id)
    return Response(status_code=204)


@router.post(
    "/invitations/{token}/accept",
    response_model=schemas.InvitationAccepted,
    dependencies=[Depends(reject_agent_credentials("invitation.accept"))],
)
def accept_invitation(
    token: str,
    request: Request,
    response: Response,
    db: DbSession,
    auth: AuthSvc,
    body: schemas.InvitationAccept | None = None,
):
    """Join a company with an invitation link. Signed in: the invitation's email must be the
    account's. Otherwise send ``{username, display_name, password}`` to create the account (you are
    then signed in). Any invalid token (unknown, used, expired, revoked) is the same 404."""
    caller: User | None = None
    try:
        principal, _info = authenticate(request, db)  # CSRF applies when a browser session is used
        if principal.is_human:
            caller = db.get(User, principal.id)
    except Unauthenticated:
        caller = None
    if caller is not None and not caller.username:
        caller = None  # the unclaimed placeholder owner is not an account
    accepted, new_session_secret = team.accept_invitation(
        db,
        auth,
        token,
        body or schemas.InvitationAccept(),
        caller=caller,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if new_session_secret is not None:
        created = db.scalar(
            select(AuthSession).where(AuthSession.token_hash == digest(new_session_secret))
        )
        assert created is not None
        _set_session_cookies(response, request, auth, created, new_session_secret)
    return accepted
