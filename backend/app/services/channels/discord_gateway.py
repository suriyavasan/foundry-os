"""Discord's Gateway (official bot connection) for inbound DMs and button presses, via ``discord.py``.

Intents: ``dm_messages`` only. The privileged ``message_content`` intent is NOT requested (direct
messages to a bot carry their content without it), so the bot needs no approval from Discord.
Everything we SEND goes over REST (``adapters.DiscordAdapter``); this client only listens, turns
events into ``Inbound`` and hands them to the router. One client per channel runs in its own thread
with its own event loop. ``discord`` is imported lazily: nothing loads it unless a Discord channel
is actually started.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from app.services.channels.common import Inbound

logger = logging.getLogger("company_os.channels")


def inbound_from_message(channel_id: str, message: Any) -> Inbound | None:
    """A ``discord.Message``-like object -> ``Inbound`` (DMs only; bots and servers are ignored)."""
    author = getattr(message, "author", None)
    if author is None or getattr(author, "bot", False):
        return None
    if getattr(message, "guild", None) is not None:
        return None  # servers are ignored in this milestone
    content = getattr(message, "content", "") or ""
    ref = getattr(message, "reference", None)
    return Inbound(
        channel_id=channel_id,
        external_user_id=str(author.id),
        external_chat_id=str(message.channel.id),
        external_message_id=str(message.id),
        kind="text" if content.strip() else "unsupported",
        text=content,
        reply_to_message_id=str(ref.message_id) if ref is not None and ref.message_id else None,
        display_name=str(getattr(author, "display_name", "") or author)[:120],
        is_private=True,
    )


def inbound_from_interaction(channel_id: str, interaction: Any) -> Inbound | None:
    """A button press in a DM -> ``Inbound`` (``callback_id`` = ``<interaction id>:<token>`` so the
    REST adapter can acknowledge it)."""
    data = getattr(interaction, "data", None) or {}
    custom_id = data.get("custom_id") if isinstance(data, dict) else None
    user = getattr(interaction, "user", None)
    if not custom_id or user is None or getattr(interaction, "guild_id", None) is not None:
        return None
    message = getattr(interaction, "message", None)
    channel = getattr(interaction, "channel_id", None) or getattr(
        getattr(interaction, "channel", None), "id", None
    )
    if channel is None:
        return None
    return Inbound(
        channel_id=channel_id,
        external_user_id=str(user.id),
        external_chat_id=str(channel),
        external_message_id=f"cb:{interaction.id}",
        kind="callback",
        callback_data=str(custom_id),
        callback_id=f"{interaction.id}:{interaction.token}",
        callback_message_id=str(message.id) if message is not None else None,
        display_name=str(getattr(user, "display_name", "") or user)[:120],
    )


class DiscordGateway:
    """``DiscordGateway(token=..., channel_id=..., on_inbound=..., on_ready=..., on_error=...)``;
    ``start()`` returns at once, ``stop()`` closes the connection."""

    def __init__(
        self,
        *,
        token: str,
        channel_id: str,
        on_inbound: Callable[[Inbound], None],
        on_ready: Callable[[], None],
        on_error: Callable[[str, bool], None],
    ) -> None:
        self._token = token
        self._channel_id = channel_id
        self._on_inbound = on_inbound
        self._on_ready = on_ready
        self._on_error = on_error  # (message, is_auth_failure)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="discord-gateway", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        loop, client = self._loop, self._client
        if loop is not None and client is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=10)
            except Exception:
                logger.info("discord: gateway did not close cleanly")
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        import discord

        intents = discord.Intents.none()
        intents.dm_messages = True
        client = discord.Client(intents=intents)
        self._client = client
        loop = asyncio.new_event_loop()
        self._loop = loop

        @client.event
        async def on_ready() -> None:
            self._on_ready()

        @client.event
        async def on_message(message: Any) -> None:
            inbound = inbound_from_message(self._channel_id, message)
            if inbound is not None:
                await loop.run_in_executor(None, self._on_inbound, inbound)

        @client.event
        async def on_interaction(interaction: Any) -> None:
            if getattr(interaction.type, "name", "") != "component":
                return
            inbound = inbound_from_interaction(self._channel_id, interaction)
            if inbound is not None:
                await loop.run_in_executor(None, self._on_inbound, inbound)

        async def main() -> None:
            try:
                await client.start(self._token, reconnect=True)
            except discord.LoginFailure:
                self._on_error("Discord refused the bot token", True)
            except Exception as exc:
                self._on_error(f"Discord connection stopped ({type(exc).__name__})", False)

        try:
            loop.run_until_complete(main())
        finally:
            loop.close()
