"""The platform adapters: thin, synchronous clients over each platform's OFFICIAL API.

* Telegram: Bot API over httpx (long polling with ``getUpdates``).
* Discord: REST over httpx for everything we send; the Gateway (``discord.py``) only receives
  (see ``discord_gateway.py``).
* WhatsApp: Business Cloud API (Meta Graph) over httpx; inbound arrives on our webhook.

Nothing here talks to an unofficial client. All network access goes through
``IntegrationHttp.client`` so tests hand the hub a ``httpx.MockTransport``.
Errors never contain the secret: URLs with a token are not logged and exception text is not echoed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.base import IntegrationHttp
from app.services.channels.common import (
    Button,
    ChannelAuthError,
    Inbound,
    OutMessage,
    SendError,
    chunk_text,
    escape_discord,
)

# httpx logs every request URL at INFO, and Telegram's carries the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("company_os.channels")


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _clip(text: str, n: int = 240) -> str:
    return " ".join((text or "").split())[:n]


class ChannelAdapter:
    kind = ""
    max_text = 4000
    needs_public_url = False
    #: secret field names stored in the bundle
    secret_fields: tuple[str, ...] = ("token",)

    def __init__(self, http: IntegrationHttp) -> None:
        self.http = http

    # -- connect
    def validate(self, secrets: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
        """Check the credentials against the platform; return the non-secret config.
        Raises ``ChannelAuthError`` with the platform's message."""
        raise NotImplementedError

    def on_connect(self, secrets: dict[str, str], config: dict[str, Any]) -> None:
        """Platform-side setup after the credentials were accepted."""

    def link_hint(self, config: dict[str, Any], code: str) -> tuple[str | None, str]:
        raise NotImplementedError

    # -- send
    def send(
        self, secrets: dict[str, str], config: dict[str, Any], chat_id: str, msg: OutMessage
    ) -> str | None:
        """Send one logical message (chunked as needed). Returns the platform id of the last chunk
        (the one carrying the buttons)."""
        raise NotImplementedError

    # -- button presses
    def ack_early(self, secrets: dict[str, str], config: dict[str, Any], inbound: Inbound) -> None:
        """Acknowledge a button press at once (Discord needs it within 3 s)."""

    def ack_late(
        self, secrets: dict[str, str], config: dict[str, Any], inbound: Inbound, text: str | None
    ) -> None:
        """Show the outcome of a button press (Telegram toast, Discord ephemeral follow-up)."""

    def clear_buttons(
        self, secrets: dict[str, str], config: dict[str, Any], inbound: Inbound
    ) -> None:
        """Remove the buttons of the message that was pressed (it was decided)."""


# ============================================================================ Telegram


class TelegramAdapter(ChannelAdapter):
    kind = "telegram"
    max_text = 4096
    base = "https://api.telegram.org"

    def _call(
        self,
        token: str,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        url = f"{self.base}/bot{token}/{method}"
        client = self.http.client(timeout=timeout)
        try:
            resp = client.post(url, json=payload or {})
        except httpx.HTTPError as exc:
            raise SendError(f"Telegram is not reachable ({type(exc).__name__})") from None
        finally:
            client.close()
        body = _json(resp)
        if resp.status_code == 200 and body.get("ok"):
            return body
        description = _clip(str(body.get("description") or f"HTTP {resp.status_code}"))
        code = int(body.get("error_code") or resp.status_code)
        if code == 401:
            raise SendError(f"Telegram refused the bot token: {description}", auth=True)
        if code == 429:
            raise SendError(f"Telegram rate limit: {description}", retryable=True)
        if code in (
            400,
            403,
            404,
        ):  # a bad request / a user who blocked the bot: retrying is useless
            raise SendError(f"Telegram: {description}", retryable=False)
        raise SendError(f"Telegram error {code}: {description}", retryable=code >= 500)

    def validate(self, secrets: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
        try:
            body = self._call(secrets["token"], "getMe", timeout=15.0)
        except SendError as exc:
            raise ChannelAuthError(str(exc)) from None
        me = body.get("result") or {}
        return {
            "bot_id": str(me.get("id") or ""),
            "bot_username": me.get("username") or "",
            "bot_name": me.get("first_name") or "",
        }

    def on_connect(self, secrets: dict[str, str], config: dict[str, Any]) -> None:
        # long polling and a webhook are mutually exclusive
        try:
            self._call(secrets["token"], "deleteWebhook", {"drop_pending_updates": False})
        except SendError as exc:
            raise ChannelAuthError(str(exc)) from None

    def link_hint(self, config: dict[str, Any], code: str) -> tuple[str | None, str]:
        bot = config.get("bot_username") or ""
        link = f"https://t.me/{bot}?start={code}" if bot else None
        return link, f"Open the link, or send /start {code} to @{bot} in Telegram."

    # -- inbound
    def get_updates(
        self, token: str, offset: int | None, *, timeout: int = 25
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        body = self._call(token, "getUpdates", payload, timeout=timeout + 10.0)
        result = body.get("result")
        return result if isinstance(result, list) else []

    @staticmethod
    def parse_update(channel_id: str, update: dict[str, Any]) -> Inbound | None:
        def name_of(user: dict[str, Any]) -> str:
            full = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
            return (full or user.get("username") or "")[:120]

        cb = update.get("callback_query")
        if isinstance(cb, dict):
            msg = cb.get("message") or {}
            chat = msg.get("chat") or {}
            sender = cb.get("from") or {}
            if not sender.get("id") or not chat.get("id") or not cb.get("id"):
                return None
            return Inbound(
                channel_id=channel_id,
                external_user_id=str(sender["id"]),
                external_chat_id=str(chat["id"]),
                external_message_id=f"cb:{cb['id']}",
                kind="callback",
                callback_data=str(cb.get("data") or ""),
                callback_id=str(cb["id"]),
                callback_message_id=f"{chat['id']}:{msg.get('message_id')}",
                display_name=name_of(sender),
                is_private=chat.get("type") == "private",
            )
        msg = update.get("message")
        if not isinstance(msg, dict):
            return None
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if not sender.get("id") or not chat.get("id") or sender.get("is_bot"):
            return None
        reply = msg.get("reply_to_message") or {}
        text = msg.get("text")
        return Inbound(
            channel_id=channel_id,
            external_user_id=str(sender["id"]),
            external_chat_id=str(chat["id"]),
            external_message_id=f"{chat['id']}:{msg.get('message_id')}",
            kind="text" if isinstance(text, str) and text else "unsupported",
            text=text if isinstance(text, str) else "",
            reply_to_message_id=(
                f"{chat['id']}:{reply['message_id']}" if reply.get("message_id") else None
            ),
            display_name=name_of(sender),
            is_private=chat.get("type") == "private",
        )

    # -- outbound
    def send(self, secrets, config, chat_id, msg: OutMessage) -> str | None:
        token = secrets["token"]
        chunks = chunk_text(msg.text, self.max_text)
        last_id: str | None = None
        for i, part in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": part or "(empty)",
                "disable_web_page_preview": True,
            }
            if i == 0 and msg.reply_to:
                payload["reply_parameters"] = {
                    "message_id": int(msg.reply_to.rsplit(":", 1)[-1]),
                    "allow_sending_without_reply": True,
                }
            if i == len(chunks) - 1 and msg.buttons:
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [{"text": b.label[:60], "callback_data": b.data}] for b in msg.buttons
                    ]
                }
            body = self._call(token, "sendMessage", payload)
            mid = (body.get("result") or {}).get("message_id")
            last_id = f"{chat_id}:{mid}" if mid is not None else last_id
        return last_id

    def ack_late(self, secrets, config, inbound, text) -> None:
        if inbound.callback_id:
            payload: dict[str, Any] = {"callback_query_id": inbound.callback_id}
            if text:
                payload["text"] = text[:190]
            try:
                self._call(secrets["token"], "answerCallbackQuery", payload)
            except SendError:
                logger.info("telegram: could not answer a callback query")

    def clear_buttons(self, secrets, config, inbound) -> None:
        if not inbound.callback_message_id:
            return
        chat, _, mid = inbound.callback_message_id.rpartition(":")
        try:
            self._call(
                secrets["token"],
                "editMessageReplyMarkup",
                {"chat_id": chat, "message_id": int(mid), "reply_markup": {"inline_keyboard": []}},
            )
        except (SendError, ValueError):
            logger.info("telegram: could not clear the buttons of a message")


# ============================================================================ Discord


class DiscordAdapter(ChannelAdapter):
    kind = "discord"
    max_text = 2000
    base = "https://discord.com/api/v10"

    def _call(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bot {token}"} if auth else {}
        client = self.http.client(timeout=20.0)
        try:
            resp = client.request(method, self.base + path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise SendError(f"Discord is not reachable ({type(exc).__name__})") from None
        finally:
            client.close()
        if resp.status_code in (200, 201, 204):
            return _json(resp)
        body = _json(resp)
        description = _clip(str(body.get("message") or f"HTTP {resp.status_code}"))
        if resp.status_code == 401:
            raise SendError(f"Discord refused the bot token: {description}", auth=True)
        if resp.status_code == 429:
            raise SendError("Discord rate limit", retryable=True)
        if resp.status_code in (400, 403, 404):
            raise SendError(f"Discord: {description}", retryable=False)
        raise SendError(f"Discord error {resp.status_code}: {description}")

    def validate(self, secrets, params) -> dict[str, Any]:
        try:
            me = self._call(secrets["token"], "GET", "/users/@me")
        except SendError as exc:
            raise ChannelAuthError(str(exc)) from None
        if not me.get("bot"):
            raise ChannelAuthError("That token does not belong to a bot")
        client_id = str(me.get("id") or "")
        return {
            "bot_id": client_id,
            "bot_username": me.get("username") or "",
            "invite_url": (
                f"https://discord.com/oauth2/authorize?client_id={client_id}"
                "&scope=bot%20applications.commands&permissions=0"
            ),
        }

    def link_hint(self, config, code):
        bot_id = config.get("bot_id") or ""
        link = f"https://discord.com/users/{bot_id}" if bot_id else None
        return link, f"Open a direct message with the bot on Discord and send: link {code}"

    def send(self, secrets, config, chat_id, msg: OutMessage) -> str | None:
        chunks = chunk_text(escape_discord(msg.text), self.max_text)
        last_id: str | None = None
        for i, part in enumerate(chunks):
            payload: dict[str, Any] = {
                "content": part or "(empty)",
                "allowed_mentions": {"parse": []},
                "flags": 4,  # no embeds from links in agent-written text
            }
            if i == 0 and msg.reply_to:
                payload["message_reference"] = {
                    "message_id": msg.reply_to,
                    "fail_if_not_exists": False,
                }
            if i == len(chunks) - 1 and msg.buttons:
                payload["components"] = [
                    {
                        "type": 1,
                        "components": [
                            {"type": 2, "style": 1, "label": b.label[:80], "custom_id": b.data}
                            for b in msg.buttons[:5]
                        ],
                    }
                ]
            body = self._call(secrets["token"], "POST", f"/channels/{chat_id}/messages", payload)
            last_id = str(body.get("id")) if body.get("id") else last_id
        return last_id

    def ack_early(self, secrets, config, inbound) -> None:
        if inbound.callback_id and ":" in inbound.callback_id:
            iid, _, itoken = inbound.callback_id.partition(":")
            try:  # type 6: acknowledge, keep the message as it is
                self._call(
                    secrets["token"],
                    "POST",
                    f"/interactions/{iid}/{itoken}/callback",
                    {"type": 6},
                    auth=False,
                )
            except SendError:
                logger.info("discord: could not acknowledge an interaction")

    def ack_late(self, secrets, config, inbound, text) -> None:
        if text and inbound.callback_id and ":" in inbound.callback_id:
            _, _, itoken = inbound.callback_id.partition(":")
            app_id = config.get("bot_id") or ""
            try:
                self._call(
                    secrets["token"],
                    "POST",
                    f"/webhooks/{app_id}/{itoken}",
                    {"content": escape_discord(text)[:300], "flags": 64},
                    auth=False,
                )
            except SendError:
                logger.info("discord: could not send an interaction follow-up")

    def clear_buttons(self, secrets, config, inbound) -> None:
        if inbound.callback_message_id:
            try:
                self._call(
                    secrets["token"],
                    "PATCH",
                    f"/channels/{inbound.external_chat_id}/messages/{inbound.callback_message_id}",
                    {"components": []},
                )
            except SendError:
                logger.info("discord: could not clear the buttons of a message")


# ============================================================================ WhatsApp


class WhatsAppAdapter(ChannelAdapter):
    kind = "whatsapp"
    max_text = 4096
    needs_public_url = True
    secret_fields = ("access_token", "app_secret", "verify_token")
    graph = "https://graph.facebook.com/v21.0"
    BUTTON_BODY_MAX = 1024

    def _call(
        self,
        access_token: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self.http.client(timeout=20.0)
        try:
            resp = client.request(
                method,
                self.graph + path,
                json=payload,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise SendError(f"WhatsApp is not reachable ({type(exc).__name__})") from None
        finally:
            client.close()
        body = _json(resp)
        if resp.status_code == 200:
            return body
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        description = _clip(str(err.get("message") or f"HTTP {resp.status_code}"))
        code = err.get("code")
        if resp.status_code in (401, 403) or code == 190:
            raise SendError(f"WhatsApp refused the access token: {description}", auth=True)
        if resp.status_code == 429 or code in (4, 80007, 130429):
            raise SendError("WhatsApp rate limit", retryable=True)
        if resp.status_code >= 500:
            raise SendError(f"WhatsApp error {resp.status_code}: {description}")
        raise SendError(f"WhatsApp: {description}", retryable=False)

    def validate(self, secrets, params) -> dict[str, Any]:
        phone_number_id = str(params.get("phone_number_id") or "").strip()
        if not phone_number_id.isdigit():
            raise ChannelAuthError("The phone number id must be numeric")
        try:
            body = self._call(
                secrets["access_token"],
                "GET",
                f"/{phone_number_id}",
                params={"fields": "display_phone_number,verified_name,quality_rating"},
            )
        except SendError as exc:
            raise ChannelAuthError(str(exc)) from None
        display = str(body.get("display_phone_number") or "")
        return {
            "phone_number_id": phone_number_id,
            "display_phone": display,
            "verified_name": body.get("verified_name") or "",
            "waba_id": str(params.get("waba_id") or "") or None,
        }

    def link_hint(self, config, code):
        digits = "".join(c for c in str(config.get("display_phone") or "") if c.isdigit())
        link = f"https://wa.me/{digits}?text={quote('link ' + code)}" if digits else None
        return (
            link,
            f"Send the message “link {code}” to the WhatsApp number {config.get('display_phone') or ''}.",
        )

    # -- inbound
    @staticmethod
    def verify_signature(app_secret: str, body: bytes, header: str) -> bool:
        if not (app_secret and header and header.startswith("sha256=")):
            return False
        expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.encode(), header[7:].strip().lower().encode())

    @staticmethod
    def parse_webhook(
        channel_id: str, config: dict[str, Any], payload: dict[str, Any]
    ) -> list[Inbound]:
        out: list[Inbound] = []
        if payload.get("object") != "whatsapp_business_account":
            return out
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes") or []:
                if not isinstance(change, dict) or change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                meta = value.get("metadata") or {}
                if str(meta.get("phone_number_id") or "") != str(config.get("phone_number_id")):
                    continue  # another number of the same Meta app
                names = {
                    str(c.get("wa_id")): str((c.get("profile") or {}).get("name") or "")
                    for c in value.get("contacts") or []
                    if isinstance(c, dict)
                }
                for m in value.get("messages") or []:
                    if not isinstance(m, dict) or not m.get("from") or not m.get("id"):
                        continue
                    sender = str(m["from"])
                    base = {
                        "channel_id": channel_id,
                        "external_user_id": sender,
                        "external_chat_id": sender,
                        "external_message_id": str(m["id"]),
                        "display_name": names.get(sender) or None,
                        "reply_to_message_id": (m.get("context") or {}).get("id"),
                    }
                    mtype = m.get("type")
                    if mtype == "text":
                        out.append(
                            Inbound(
                                kind="text",
                                text=str((m.get("text") or {}).get("body") or ""),
                                **base,
                            )
                        )
                    elif mtype == "interactive":
                        inter = m.get("interactive") or {}
                        reply = inter.get("button_reply") or inter.get("list_reply") or {}
                        if reply.get("id"):
                            base["external_message_id"] = f"cb:{m['id']}"
                            out.append(
                                Inbound(
                                    kind="callback",
                                    callback_data=str(reply["id"]),
                                    callback_message_id=(m.get("context") or {}).get("id"),
                                    **base,
                                )
                            )
                        else:
                            out.append(Inbound(kind="unsupported", **base))
                    else:
                        out.append(Inbound(kind="unsupported", **base))
        return out

    # -- outbound
    def _post_message(self, secrets, config, payload: dict[str, Any]) -> str | None:
        body = self._call(
            secrets["access_token"], "POST", f"/{config['phone_number_id']}/messages", payload
        )
        msgs = body.get("messages") or []
        return str(msgs[0].get("id")) if msgs and isinstance(msgs[0], dict) else None

    def send(self, secrets, config, chat_id, msg: OutMessage) -> str | None:
        buttons = msg.buttons[:3] if 1 <= len(msg.buttons) <= 3 else []
        limit = self.BUTTON_BODY_MAX if buttons else self.max_text
        chunks = chunk_text(msg.text, self.max_text)
        if buttons and len(chunks[-1]) > limit:
            # the buttoned message has a smaller body limit: re-split the tail
            tail = chunk_text(chunks.pop(), limit)
            chunks.extend(tail)
        last_id: str | None = None
        for i, part in enumerate(chunks):
            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
            }
            if i == 0 and msg.reply_to:
                payload["context"] = {"message_id": msg.reply_to}
            if buttons and i == len(chunks) - 1:
                payload["type"] = "interactive"
                payload["interactive"] = {
                    "type": "button",
                    "body": {"text": part or "(empty)"},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": b.data, "title": b.label[:20]}}
                            for b in buttons
                        ]
                    },
                }
            else:
                payload["type"] = "text"
                payload["text"] = {"preview_url": False, "body": part or "(empty)"}
            last_id = self._post_message(secrets, config, payload) or last_id
        return last_id


__all__ = [
    "Button",
    "ChannelAdapter",
    "DiscordAdapter",
    "TelegramAdapter",
    "WhatsAppAdapter",
]
