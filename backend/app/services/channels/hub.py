"""The ``ChannelHub``: adapters, the cipher and the injectable network pieces (one per process).

The API process and the engine process each build one. Tests replace ``hub.http.transport`` with a
``httpx.MockTransport`` and ``hub.gateway_factory`` with a fake Discord gateway.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.integrations.base import IntegrationHttp
from app.models.chat import ChatChannel
from app.models.provider import Credential
from app.services.channels.adapters import (
    ChannelAdapter,
    DiscordAdapter,
    TelegramAdapter,
    WhatsAppAdapter,
)
from app.services.channels.common import Inbound

logger = logging.getLogger("company_os.channels")

#: ``gateway_factory(token, on_inbound, on_ready, on_error)`` -> object with ``start()``/``stop()``.
GatewayFactory = Callable[..., Any]


class SecretsUnavailable(Exception):
    """The channel's secrets cannot be read (revoked, or the server key changed)."""


class ChannelHub:
    def __init__(
        self,
        cipher: CredentialCipher,
        *,
        http: IntegrationHttp | None = None,
        gateway_factory: GatewayFactory | None = None,
    ) -> None:
        self.cipher = cipher
        self.http = http or IntegrationHttp()
        self._gateway_factory = gateway_factory
        self._adapters: dict[str, ChannelAdapter] = {
            "telegram": TelegramAdapter(self.http),
            "discord": DiscordAdapter(self.http),
            "whatsapp": WhatsAppAdapter(self.http),
        }

    def adapter(self, kind: str) -> ChannelAdapter:
        return self._adapters[kind]

    @property
    def gateway_factory(self) -> GatewayFactory:
        if self._gateway_factory is not None:
            return self._gateway_factory
        from app.services.channels.discord_gateway import DiscordGateway

        return DiscordGateway

    @gateway_factory.setter
    def gateway_factory(self, value: GatewayFactory | None) -> None:
        self._gateway_factory = value

    # -- the secret bundle
    def seal(self, secrets: dict[str, str]) -> str:
        return self.cipher.encrypt(json.dumps(secrets, separators=(",", ":")))

    def secrets_for(self, db: Session, channel: ChatChannel) -> dict[str, str]:
        """Decrypted secrets. For the channel code only: never returned, logged or audited."""
        if not channel.credential_id:
            raise SecretsUnavailable("no credential")
        row = db.get(Credential, channel.credential_id)
        if row is None or row.revoked_at is not None or row.company_id != channel.company_id:
            raise SecretsUnavailable("credential revoked")
        try:
            data = json.loads(self.cipher.decrypt(row.encrypted_value))
        except (CredentialDecryptError, ValueError) as exc:
            raise SecretsUnavailable("credential unreadable") from exc
        if not isinstance(data, dict):
            raise SecretsUnavailable("credential unreadable")
        return {str(k): str(v) for k, v in data.items()}


__all__ = ["ChannelHub", "GatewayFactory", "Inbound", "SecretsUnavailable"]
