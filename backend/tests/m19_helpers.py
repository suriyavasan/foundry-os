"""M19 test world: fake Telegram / Discord / WhatsApp Cloud API servers behind one
``httpx.MockTransport`` (nothing here, or in the code under test, touches the network)."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from types import SimpleNamespace
from typing import Any

import httpx

from app.services.channels.common import Inbound
from app.services.channels.router import ChannelRouter
from app.services.channels.supervisor import ChannelSupervisor
from tests.helpers import FakeClock, api

TG_TOKEN = "123456:TELEGRAM-TEST-TOKEN-abcdef"
DISCORD_TOKEN = "discord.fake.TOKEN-aaaaaaaaaaaaaaaaaaaa"
WA_TOKEN = "EAAFAKEWHATSAPPACCESSTOKEN123456"
WA_APP_SECRET = "wa-app-secret-0123456789"
WA_PHONE_ID = "109876543210"
WA_NUMBER = "+1 555 010 0000"
ALL_SECRETS = (TG_TOKEN, DISCORD_TOKEN, WA_TOKEN, WA_APP_SECRET)


class FakePlatforms:
    """Records every call; answers like the real APIs for the cases the tests need."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tg_queue: list[dict[str, Any]] = []  # updates the next getUpdates returns
        self.tg_offsets: list[int | None] = []
        self.tg_sent: list[dict[str, Any]] = []
        self.dc_sent: list[dict[str, Any]] = []
        self.wa_sent: list[dict[str, Any]] = []
        self.fail_sends: list[tuple[int, dict[str, Any]]] = []  # consumed by the next send(s)
        self._mid = 100
        self.transport = httpx.MockTransport(self._handle)

    # ---------------------------------------------------------------- helpers for tests
    def next_id(self) -> int:
        self._mid += 1
        return self._mid

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return {"telegram": self.tg_sent, "discord": self.dc_sent, "whatsapp": self.wa_sent}[kind]

    def texts(self, kind: str) -> list[str]:
        out = []
        for p in self.sent(kind):
            if kind == "telegram":
                out.append(p["text"])
            elif kind == "discord":
                out.append(p["content"])
            else:
                out.append(
                    p["text"]["body"] if p["type"] == "text" else p["interactive"]["body"]["text"]
                )
        return out

    # ---------------------------------------------------------------- server
    def _handle(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        body: dict[str, Any] = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}
        self.calls.append(
            {
                "host": host,
                "path": path,
                "method": request.method,
                "body": body,
                "headers": dict(request.headers),
                "params": dict(request.url.params),
            }
        )
        if host == "api.telegram.org":
            return self._telegram(path, body)
        if host == "discord.com":
            return self._discord(request, path, body)
        if host == "graph.facebook.com":
            return self._graph(request, path, body)
        return httpx.Response(404, json={})

    def _maybe_fail(self) -> httpx.Response | None:
        if self.fail_sends:
            status, payload = self.fail_sends.pop(0)
            return httpx.Response(status, json=payload)
        return None

    def _telegram(self, path: str, body: dict[str, Any]) -> httpx.Response:
        m = re.match(r"/bot([^/]+)/(\w+)$", path)
        if not m:
            return httpx.Response(404, json={"ok": False})
        token, method = m.groups()
        if token != TG_TOKEN:
            return httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
            )
        if method == "getMe":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "id": 999,
                        "is_bot": True,
                        "first_name": "Foundry",
                        "username": "foundry_test_bot",
                    },
                },
            )
        if method == "getUpdates":
            self.tg_offsets.append(body.get("offset"))
            offset = body.get("offset")
            out = [u for u in self.tg_queue if offset is None or u["update_id"] >= offset]
            self.tg_queue = []
            return httpx.Response(200, json={"ok": True, "result": out})
        if method == "sendMessage":
            failed = self._maybe_fail()
            if failed is not None:
                return failed
            self.tg_sent.append(body)
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": self.next_id(), "chat": {"id": 1}}}
            )
        return httpx.Response(200, json={"ok": True, "result": True})

    def _discord(self, request: httpx.Request, path: str, body: dict[str, Any]) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if path.endswith("/users/@me"):
            if auth != f"Bot {DISCORD_TOKEN}":
                return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
            return httpx.Response(200, json={"id": "777000", "username": "FoundryBot", "bot": True})
        if re.search(r"/channels/[^/]+/messages$", path) and request.method == "POST":
            if auth != f"Bot {DISCORD_TOKEN}":
                return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
            failed = self._maybe_fail()
            if failed is not None:
                return failed
            self.dc_sent.append(body)
            return httpx.Response(200, json={"id": str(self.next_id())})
        return httpx.Response(204)

    def _graph(self, request: httpx.Request, path: str, body: dict[str, Any]) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {WA_TOKEN}":
            return httpx.Response(
                401, json={"error": {"message": "Invalid OAuth access token.", "code": 190}}
            )
        if request.method == "GET" and path.endswith(f"/{WA_PHONE_ID}"):
            return httpx.Response(
                200,
                json={
                    "id": WA_PHONE_ID,
                    "display_phone_number": WA_NUMBER,
                    "verified_name": "Acme Labs",
                },
            )
        if request.method == "POST" and path.endswith("/messages"):
            failed = self._maybe_fail()
            if failed is not None:
                return failed
            self.wa_sent.append(body)
            return httpx.Response(200, json={"messages": [{"id": f"wamid.{self.next_id()}"}]})
        return httpx.Response(404, json={"error": {"message": "unknown", "code": 100}})


class FakeGateway:
    """Stands in for ``DiscordGateway``: records start/stop, runs nothing."""

    instances: list[FakeGateway] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        FakeGateway.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------- the world


def make_world(app, client, tmp_clock: FakeClock | None = None) -> SimpleNamespace:
    """Fake platforms wired into the app's hub, a router and a supervisor on a fake clock, and a
    company owned by the test client's user."""
    platforms = FakePlatforms()
    hub = app.state.channels
    hub.http.transport = platforms.transport
    FakeGateway.instances = []
    hub.gateway_factory = FakeGateway
    clock = tmp_clock or FakeClock()
    router = ChannelRouter(app.state.session_factory, hub, clock=clock)
    app.state.channel_router.clock = clock  # the webhook's router runs on the same fake clock
    started: list[str] = []

    def starter(channel, secrets):
        started.append(channel.id)
        return SimpleNamespace(stop=lambda: started.remove(channel.id))

    supervisor = ChannelSupervisor(
        app.state.session_factory, hub, owner_id="sup-1", clock=clock, inbound_starter=starter
    )
    company = client.post("/api/companies", json={"name": "Acme Labs", "mission": "Ship"}).json()
    me = client.get("/api/me").json()
    return SimpleNamespace(
        app=app,
        client=client,
        platforms=platforms,
        hub=hub,
        clock=clock,
        router=router,
        supervisor=supervisor,
        started=started,
        cid=company["id"],
        company=company,
        me=me,
        owner_id=me["user"]["id"],
        factory=app.state.session_factory,
    )


def url(w, path: str = "") -> str:
    return api(w.cid, path)


def connect(w, kind: str, **overrides) -> dict:
    """Connect a channel through the API with the fake platform's good credentials."""
    body: dict[str, Any]
    if kind == "telegram":
        body = {"kind": "telegram", "token": TG_TOKEN}
    elif kind == "discord":
        body = {"kind": "discord", "token": DISCORD_TOKEN}
    else:
        body = {
            "kind": "whatsapp",
            "whatsapp": {
                "phone_number_id": WA_PHONE_ID,
                "access_token": WA_TOKEN,
                "app_secret": WA_APP_SECRET,
            },
        }
    body.update(overrides)
    r = w.client.post(url(w, "/channels"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def link_code(w, channel_id: str, client=None) -> dict:
    r = (client or w.client).post(url(w, f"/channels/{channel_id}/link-code"))
    assert r.status_code == 200, r.text
    return r.json()


_n = {"i": 0}


def tg_text(
    channel_id: str, text: str, *, user: str = "4242", reply_to: str | None = None
) -> Inbound:
    _n["i"] += 1
    return Inbound(
        channel_id=channel_id,
        external_user_id=user,
        external_chat_id=user,
        external_message_id=f"{user}:{1000 + _n['i']}",
        text=text,
        reply_to_message_id=reply_to,
        display_name="Pat",
    )


def tg_press(channel_id: str, data: str, *, user: str = "4242") -> Inbound:
    _n["i"] += 1
    return Inbound(
        channel_id=channel_id,
        external_user_id=user,
        external_chat_id=user,
        external_message_id=f"cb:{_n['i']}",
        kind="callback",
        callback_data=data,
        callback_id=f"cbq{_n['i']}",
        callback_message_id=f"{user}:55",
        display_name="Pat",
    )


def link_owner(w, kind: str = "telegram", user: str = "4242") -> dict:
    """Connect (if needed), issue a code as the owner and link ``user`` to them."""
    chan = connect(w, kind) if not hasattr(w, f"chan_{kind}") else getattr(w, f"chan_{kind}")
    setattr(w, f"chan_{kind}", chan)
    code = link_code(w, chan["id"])["code"]
    text = f"/start {code}" if kind == "telegram" else f"link {code}"
    w.router.process(tg_text(chan["id"], text, user=user))
    return chan


def sign(body: bytes, secret: str = WA_APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def wa_payload(
    from_: str,
    *,
    text: str | None = None,
    mid: str = "wamid.IN1",
    button: str | None = None,
    mtype: str | None = None,
    context: str | None = None,
    phone_id: str = WA_PHONE_ID,
) -> dict[str, Any]:
    msg: dict[str, Any] = {"from": from_, "id": mid, "timestamp": "1760000000"}
    if button is not None:
        msg["type"] = "interactive"
        msg["interactive"] = {"type": "button_reply", "button_reply": {"id": button, "title": "x"}}
    elif text is not None:
        msg["type"] = "text"
        msg["text"] = {"body": text}
    else:
        msg["type"] = mtype or "image"
    if context:
        msg["context"] = {"id": context}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550100000",
                                "phone_number_id": phone_id,
                            },
                            "contacts": [{"profile": {"name": "Pat"}, "wa_id": from_}],
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }


def post_wa(w, channel_id: str, payload: dict, *, secret: str | None = WA_APP_SECRET):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Hub-Signature-256"] = sign(body, secret)
    # public route: no owner credential at all
    return w.anon.post(f"/api/webhooks/whatsapp/{channel_id}", content=body, headers=headers)
