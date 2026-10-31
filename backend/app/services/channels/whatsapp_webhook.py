"""Meta's WhatsApp Cloud API webhook for one channel (M19): the subscription handshake and signed
deliveries. No session auth: the verify token (handshake) and the HMAC of the raw body under the app
secret (deliveries) are the only credentials, both compared in constant time. Every failure is the
same 403, so an unknown channel, a missing secret and a bad signature look alike."""

from __future__ import annotations

import hmac
import json

from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.models.chat import ChannelStatus, ChatChannel
from app.services.channels.adapters import WhatsAppAdapter
from app.services.channels.common import Inbound
from app.services.channels.hub import ChannelHub, SecretsUnavailable

REJECTED = "Webhook rejected"


def _channel_and_secrets(db: Session, hub: ChannelHub, channel_id: str):
    channel = db.get(ChatChannel, channel_id)
    if (
        channel is None
        or channel.kind != "whatsapp"
        or channel.status
        not in (ChannelStatus.ACTIVE, ChannelStatus.CONNECTING, ChannelStatus.ERROR)
    ):
        raise Forbidden(REJECTED)
    try:
        return channel, hub.secrets_for(db, channel)
    except SecretsUnavailable:
        raise Forbidden(REJECTED) from None


def handshake(db: Session, hub: ChannelHub, channel_id: str, params: dict[str, str]) -> str:
    """``GET``: echo ``hub.challenge`` when the mode is ``subscribe`` and the verify token matches."""
    _, secrets = _channel_and_secrets(db, hub, channel_id)
    given = params.get("hub.verify_token", "")
    expected = secrets.get("verify_token", "")
    if (
        params.get("hub.mode") != "subscribe"
        or not expected
        or not hmac.compare_digest(given.encode(), expected.encode())
        or not params.get("hub.challenge")
    ):
        raise Forbidden(REJECTED)
    return params["hub.challenge"]


def receive(
    db: Session, hub: ChannelHub, channel_id: str, headers: dict[str, str], body: bytes
) -> list[Inbound]:
    """``POST``: verify ``X-Hub-Signature-256`` over the RAW body before parsing anything; returns
    the normalised inbound events (empty for status receipts and foreign numbers)."""
    channel, secrets = _channel_and_secrets(db, hub, channel_id)
    lowered = {k.lower(): v for k, v in headers.items()}
    if not WhatsAppAdapter.verify_signature(
        secrets.get("app_secret", ""), body, lowered.get("x-hub-signature-256", "")
    ):
        raise Forbidden(REJECTED)
    try:
        payload = json.loads(body)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    return WhatsAppAdapter.parse_webhook(channel.id, dict(channel.config or {}), payload)
