"""Public webhooks (no session: authenticated by signature only): inbound e-mail, GitHub (M14) and
card-provider events (M15; both only trigger a sync)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import DbSession
from app.core.errors import PayloadTooLarge
from app.schemas.repository import WebhookAccepted
from app.services import card_reconcile, email_identity, repositories
from app.services.channels import whatsapp_webhook

router = APIRouter(tags=["webhooks"])


async def _read_capped(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLarge("The webhook body is too large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLarge("The webhook body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/email/{company_id}/{source}", status_code=202)
async def inbound_email(company_id: str, source: str, request: Request, db: DbSession):
    """Inbound mail from AgentMail / Resend (Svix-signed) or the Cloudflare Email Worker
    (``X-Company-OS-Signature``). Unknown company, no configured secret and a bad signature all
    answer the same 401. The mail is stored as untrusted data and routed to the mailbox owner."""
    settings = request.app.state.settings
    body = await _read_capped(request, settings.webhook_max_bytes)
    return await run_in_threadpool(
        email_identity.receive,
        db,
        request.app.state.integrations,
        company_id,
        source,
        dict(request.headers),
        body,
    )


@router.post("/webhooks/github/{repository_id}", status_code=202, response_model=WebhookAccepted)
async def github_webhook(repository_id: str, request: Request, db: DbSession):
    """An optional GitHub webhook (M14). It only pokes a sync: the payload is never trusted or
    used. An unknown repository, no secret and a bad ``X-Hub-Signature-256`` answer the same 401."""
    settings = request.app.state.settings
    body = await _read_capped(request, settings.webhook_max_bytes)
    return await run_in_threadpool(
        repositories.receive_webhook,
        db,
        request.app.state.integrations,
        repository_id,
        dict(request.headers),
        body,
    )


@router.post("/webhooks/cards/{company_id}/{provider}", status_code=202)
async def card_events(company_id: str, provider: str, request: Request, db: DbSession):
    """A card provider event (Stripe Issuing). The signature is verified against the company's
    stored webhook secret and the event only triggers a sync: nothing in the body is trusted.
    Unknown company, no secret, no webhooks for that provider and a bad signature all answer the
    same 401."""
    settings = request.app.state.settings
    body = await _read_capped(request, settings.webhook_max_bytes)
    return await run_in_threadpool(
        card_reconcile.receive_webhook,
        db,
        request.app.state.integrations,
        company_id,
        provider,
        dict(request.headers),
        body,
    )


@router.get("/webhooks/whatsapp/{channel_id}", response_class=PlainTextResponse)
async def whatsapp_handshake(channel_id: str, request: Request, db: DbSession):
    """Meta's subscription handshake (M19): echoes ``hub.challenge`` as plain text when the mode is
    ``subscribe`` and ``hub.verify_token`` matches the channel's token (constant-time). Else 403."""
    challenge = await run_in_threadpool(
        whatsapp_webhook.handshake,
        db,
        request.app.state.channels,
        channel_id,
        dict(request.query_params),
    )
    return PlainTextResponse(challenge)


@router.post("/webhooks/whatsapp/{channel_id}")
async def whatsapp_events(
    channel_id: str, request: Request, background: BackgroundTasks, db: DbSession
):
    """WhatsApp Cloud API deliveries (M19). ``X-Hub-Signature-256`` is checked over the raw body
    with the channel's app secret before anything is parsed (403 otherwise). Answers 200 at once;
    the messages are handled after the response."""
    settings = request.app.state.settings
    body = await _read_capped(request, settings.webhook_max_bytes)
    inbound = await run_in_threadpool(
        whatsapp_webhook.receive,
        db,
        request.app.state.channels,
        channel_id,
        dict(request.headers),
        body,
    )
    router_ = request.app.state.channel_router
    for event in inbound:
        background.add_task(router_.process, event)
    return {"accepted": True}
