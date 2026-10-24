"""Public webhooks (no session: authenticated by signature only). Today: inbound e-mail."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import DbSession
from app.core.errors import PayloadTooLarge
from app.services import email_identity

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
