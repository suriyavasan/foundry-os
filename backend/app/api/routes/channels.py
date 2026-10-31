"""M19: chat channels (Telegram, Discord, WhatsApp). Owners/admins manage the apps; a person manages
their own link. Secrets are write-only. The public WhatsApp webhook lives in ``webhooks.py``."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.api.deps import CompanyCtx, DbSession
from app.schemas.channels import (
    ChannelCreate,
    ChannelList,
    ChannelOut,
    ChannelPatch,
    LinkCodeOut,
    LinkOut,
    LinkPatch,
)
from app.services.channels import service

router = APIRouter(prefix="/companies/{company_id}", tags=["channels"])


def _hub(request: Request):
    return request.app.state.channels


@router.get("/channels", response_model=ChannelList)
def list_channels(request: Request, ctx: CompanyCtx, db: DbSession):
    ic = request.app.state.integrations
    rows = service.list_channels(db, ctx)
    return {
        "items": [service.serialize_channel(db, row, ic=ic) for row in rows],
        "available": service.availability(ic),
        "next_cursor": None,
    }


@router.post("/channels", response_model=ChannelOut, status_code=201)
def connect_channel(body: ChannelCreate, request: Request, ctx: CompanyCtx, db: DbSession):
    """Validates the credentials against the platform, stores them (write-only) and creates the
    channel. For WhatsApp the response carries the webhook URL and the verify token ONCE."""
    ic = request.app.state.integrations
    channel, verify_token = service.connect(db, ctx, _hub(request), ic, body)
    return service.serialize_channel(db, channel, ic=ic, verify_token=verify_token)


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
def patch_channel(
    channel_id: str, body: ChannelPatch, request: Request, ctx: CompanyCtx, db: DbSession
):
    channel = service.update_channel(db, ctx, _hub(request), channel_id, body)
    return service.serialize_channel(db, channel, ic=request.app.state.integrations)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, ctx: CompanyCtx, db: DbSession):
    service.remove_channel(db, ctx, channel_id)
    return Response(status_code=204)


@router.post("/channels/{channel_id}/link-code", response_model=LinkCodeOut)
def link_code(channel_id: str, request: Request, ctx: CompanyCtx, db: DbSession):
    """A one-time code (10 minutes) for the calling person to send to the bot. Shown once."""
    return service.issue_link_code(db, ctx, _hub(request), channel_id)


@router.patch("/channel-links/{link_id}", response_model=LinkOut)
def patch_link(link_id: str, body: LinkPatch, ctx: CompanyCtx, db: DbSession):
    link = service.update_link_notify(db, ctx, link_id, body.notify)
    return service.link_out(db, link)


@router.delete("/channel-links/{link_id}", status_code=204)
def delete_link(link_id: str, ctx: CompanyCtx, db: DbSession):
    service.delete_link(db, ctx, link_id)
    return Response(status_code=204)
