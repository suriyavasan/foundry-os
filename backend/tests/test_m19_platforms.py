"""M19: WhatsApp (webhook, signature, 24 h window), Discord and platform details."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.autonomy import FounderNote
from app.models.chat import ChatLink, ChatMessage
from app.models.governance import Approval
from app.models.questions import FounderQuestion
from app.services import notifications
from app.services.channels.common import Inbound, chunk_text, escape_discord
from app.services.channels.discord_gateway import inbound_from_interaction, inbound_from_message
from tests.helpers import api, new_agent
from tests.m19_helpers import (
    DISCORD_TOKEN,
    WA_PHONE_ID,
    WA_TOKEN,
    connect,
    link_code,
    make_world,
    post_wa,
    sign,
    tg_text,
    url,
    wa_payload,
)
from tests.test_agent_api import make_approval
from tests.test_m19_channels import make_question, outbound, rows


@pytest.fixture
def w(app, client, anon_client, monkeypatch):
    world = make_world(app, client)
    world.anon = anon_client
    monkeypatch.setattr(world.app.state.settings, "public_url", "https://foundry.example.com")
    monkeypatch.setattr("app.services.channels.service.utcnow", world.clock)
    return world


def wa_link(w, number="15551234567"):
    chan = connect(w, "whatsapp")
    code = link_code(w, chan["id"])["code"]
    assert (
        post_wa(w, chan["id"], wa_payload(number, text=f"link {code}", mid="wamid.L1")).status_code
        == 200
    )
    return chan, number


# ================================================================== WhatsApp webhook


def test_verification_handshake_and_signature(w):
    chan = connect(w, "whatsapp")
    cid = chan["id"]
    token = chan["verify_token"]
    ok = w.anon.get(
        f"/api/webhooks/whatsapp/{cid}",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "12345"},
    )
    assert (
        ok.status_code == 200
        and ok.text == "12345"
        and ok.headers["content-type"].startswith("text/plain")
    )
    for params in (
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": token, "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": token},
    ):
        assert w.anon.get(f"/api/webhooks/whatsapp/{cid}", params=params).status_code == 403
    assert (
        w.anon.get(
            "/api/webhooks/whatsapp/nope",
            params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1"},
        ).status_code
        == 403
    )
    # deliveries: signature over the RAW body
    payload = wa_payload("15551234567", text="hello")
    assert post_wa(w, cid, payload, secret=None).status_code == 403  # no signature
    assert post_wa(w, cid, payload, secret="wrong-secret").status_code == 403  # bad signature
    assert post_wa(w, "nope", payload).status_code == 403  # unknown channel: same answer
    body = json.dumps(payload).encode()
    tampered = w.anon.post(
        f"/api/webhooks/whatsapp/{cid}",
        content=body + b" ",
        headers={"X-Hub-Signature-256": sign(body)},
    )
    assert tampered.status_code == 403
    assert w.platforms.wa_sent == [] and rows(w, ChatMessage) == []  # nothing was processed
    # a good one is accepted: the sender is unlinked, so they get the fixed reply and nothing else
    assert post_wa(w, cid, payload).status_code == 200
    assert "private Foundry bot" in w.platforms.texts("whatsapp")[-1]
    assert rows(w, FounderNote) == []


def test_another_companys_secret_cannot_sign_for_this_channel(w, client):
    a = connect(w, "whatsapp")
    other_cid = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    r = client.post(
        api(other_cid, "/channels"),
        json={
            "kind": "whatsapp",
            "whatsapp": {
                "phone_number_id": WA_PHONE_ID,
                "access_token": WA_TOKEN,
                "app_secret": "other-secret",
            },
        },
    )
    assert r.status_code == 409  # the same number cannot be served twice
    assert a["id"]


def test_link_and_message_over_whatsapp_and_reply_in_the_window(w, app, client):
    from tests.test_agent_api import start_run

    chan, number = wa_link(w)
    assert "Linked to Acme Labs" in w.platforms.texts("whatsapp")[-1]
    link = rows(w, ChatLink)[0]
    assert link.external_user_id == number and link.display_name == "Pat"
    # the message becomes a note, a duplicate delivery (Meta retries) is ignored
    assert (
        post_wa(
            w, chan["id"], wa_payload(number, text="Plan the launch", mid="wamid.M1")
        ).status_code
        == 200
    )
    assert (
        post_wa(
            w, chan["id"], wa_payload(number, text="Plan the launch", mid="wamid.M1")
        ).status_code
        == 200
    )
    notes = rows(w, FounderNote)
    assert len(notes) == 1
    # non-text is answered
    assert (
        post_wa(w, chan["id"], wa_payload(number, mtype="image", mid="wamid.M2")).status_code == 200
    )
    assert "Only text" in w.platforms.texts("whatsapp")[-1]
    # status receipts / a foreign number's messages are ignored
    assert (
        post_wa(
            w, chan["id"], wa_payload(number, text="x", mid="wamid.M3", phone_id="999")
        ).status_code
        == 200
    )
    assert len(rows(w, FounderNote)) == 1
    # the Co-founder's reply is delivered inside the 24 h window
    founder = next(
        a for a in client.get(api(w.cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    sess = start_run(app, client, w.cid, founder["id"], task_title="plan", kind="planning")
    sess.post(f"/notes/{notes[0].id}/replies", json={"body": "Launch plan drafted."})
    w.supervisor.tick()
    assert w.platforms.texts("whatsapp")[-1].endswith("Launch plan drafted.")
    sent = w.platforms.wa_sent[-1]
    assert sent["to"] == number and sent["messaging_product"] == "whatsapp"
    assert w.platforms.calls[-1]["headers"]["authorization"] == f"Bearer {WA_TOKEN}"


def test_nothing_is_sent_outside_the_24_hour_window(w, client):
    chan, number = wa_link(w)
    n = len(w.platforms.wa_sent)
    w.clock.advance(24 * 3600 + 5)
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Budget exceeded", related_type="alert", related_id="a1"
        )
        db.commit()
    w.supervisor.tick()
    assert len(w.platforms.wa_sent) == n
    row = outbound(w, ref_type="alert")[0]
    assert row.status == "skipped" and row.error == "outside_24h_window"
    out = w.client.get(url(w, "/channels")).json()["items"][0]
    assert out["links"][0]["window_open"] is False
    # the person writes again: the window re-opens and later messages go out
    post_wa(w, chan["id"], wa_payload(number, text="hi", mid="wamid.W2"))
    out = w.client.get(url(w, "/channels")).json()["items"][0]
    assert out["links"][0]["window_open"] is True
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Again", related_type="alert", related_id="a2"
        )
        db.commit()
    w.supervisor.tick()
    assert w.platforms.texts("whatsapp")[-1].startswith("Alert: Again")


def test_whatsapp_approval_buttons_and_the_decision(w, client):
    chan, number = wa_link(w)
    worker = new_agent(client, w.cid, name="Worker")
    approval_id = make_approval(client, w.cid, worker["id"], "external_purchase")
    w.supervisor.tick()
    msg = w.platforms.wa_sent[-1]
    assert msg["type"] == "interactive"
    buttons = msg["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in buttons] == ["Approve", "Decline"]
    assert buttons[0]["reply"]["id"] == f"a:{approval_id}:approve"
    post_wa(w, chan["id"], wa_payload(number, button=f"a:{approval_id}:approve", mid="wamid.B1"))
    with w.factory() as db:
        assert db.get(Approval, approval_id).status == "approved"
    assert "Approved:" in w.platforms.texts("whatsapp")[-1]


def test_whatsapp_questions_use_buttons_up_to_three_then_a_numbered_list(w, client):
    chan, number = wa_link(w)
    few = make_question(w, client, options=("Yes",), default="Yes")
    many = make_question(w, client, options=("A", "B", "C", "D"), default="B")
    w.supervisor.tick()
    by_ref = {m.ref_id: m for m in outbound(w, ref_type="question")}
    assert set(by_ref) == {few, many}
    texts = w.platforms.wa_sent
    btn = next(m for m in texts if m["type"] == "interactive")
    assert [b["reply"]["title"] for b in btn["interactive"]["action"]["buttons"]] == [
        "Yes",
        "Use default",
    ]
    listed = next(
        m for m in texts if m["type"] == "text" and "Reply with a number" in m["text"]["body"]
    )
    assert "1. A" in listed["text"]["body"] and "0. Use the default" in listed["text"]["body"]
    # answer the numbered one with "3"
    post_wa(w, chan["id"], wa_payload(number, text="3", mid="wamid.N1"))
    with w.factory() as db:
        q = db.get(FounderQuestion, many)
        assert (q.status, q.answer, q.answered_via) == ("answered", "C", "whatsapp")
        assert db.get(FounderQuestion, few).status == "open"
    # the quick-reply button answers the other
    post_wa(w, chan["id"], wa_payload(number, button=f"q:{few}:d", mid="wamid.N2"))
    with w.factory() as db:
        assert db.get(FounderQuestion, few).answer == "Yes"


def test_wa_free_text_reply_to_a_question_uses_the_context_id(w, client):
    chan, number = wa_link(w)
    qid = make_question(w, client, options=("A", "B", "C", "D"), default="B")
    w.supervisor.tick()
    ext = outbound(w, ref_type="question")[0].external_message_id
    post_wa(w, chan["id"], wa_payload(number, text="Actually do E", mid="wamid.R1", context=ext))
    with w.factory() as db:
        assert db.get(FounderQuestion, qid).answer == "Actually do E"


# ================================================================== Discord


def test_discord_send_escapes_text_blocks_pings_and_uses_buttons(w, client):
    chan = connect(w, "discord")
    issued = link_code(w, chan["id"])
    code = issued["code"]
    assert f"link {code}" in issued["instructions"]
    w.router.process(tg_text(chan["id"], f"link {code}", user="880001"))
    assert "Linked to Acme Labs" in w.platforms.texts("discord")[-1]
    qid = make_question(
        w, client, options=("Use *bold* @everyone",), default="use *bold* @everyone"
    )
    w.supervisor.tick()
    msg = w.platforms.dc_sent[-1]
    assert "@everyone" not in msg["content"] and "\\*bold\\*" in msg["content"]
    assert "@\u200beveryone" in msg["content"]
    assert msg["allowed_mentions"] == {"parse": []}
    row = msg["components"][0]["components"]
    assert row[0]["custom_id"] == f"q:{qid}:0" and row[-1]["label"] == "Use default"
    assert row[0]["label"] == "Use *bold* @everyone"  # button labels are plain text
    auth = [
        c for c in w.platforms.calls if c["host"] == "discord.com" and "/channels/" in c["path"]
    ][-1]
    assert auth["headers"]["authorization"] == f"Bot {DISCORD_TOKEN}"
    # a button press is acknowledged over REST (type 6) before it is processed
    pressed = Inbound(
        channel_id=chan["id"],
        external_user_id="880001",
        external_chat_id="880001",
        external_message_id="cb:i1",
        kind="callback",
        callback_data=f"q:{qid}:0",
        callback_id="i1:tok",
        callback_message_id="m1",
    )
    w.router.process(pressed)
    cb = [c for c in w.platforms.calls if "/interactions/i1/tok/callback" in c["path"]]
    assert cb and cb[0]["body"] == {"type": 6}
    with w.factory() as db:
        assert db.get(FounderQuestion, qid).answered_via == "discord"


def test_discord_events_become_inbounds_dms_only():
    author = SimpleNamespace(id=5, bot=False, display_name="Pat")
    dm = SimpleNamespace(
        author=author,
        guild=None,
        content="hello",
        channel=SimpleNamespace(id=77),
        id=900,
        reference=SimpleNamespace(message_id=800),
    )
    got = inbound_from_message("c1", dm)
    assert (got.external_user_id, got.external_chat_id, got.text, got.reply_to_message_id) == (
        "5",
        "77",
        "hello",
        "800",
    )
    assert inbound_from_message("c1", SimpleNamespace(**{**dm.__dict__, "guild": object()})) is None
    assert (
        inbound_from_message(
            "c1", SimpleNamespace(**{**dm.__dict__, "author": SimpleNamespace(id=6, bot=True)})
        )
        is None
    )
    assert (
        inbound_from_message("c1", SimpleNamespace(**{**dm.__dict__, "content": ""})).kind
        == "unsupported"
    )
    it = SimpleNamespace(
        data={"custom_id": "q:1:0"},
        user=author,
        guild_id=None,
        channel_id=77,
        id=1,
        token="t",
        message=SimpleNamespace(id=9),
    )
    got = inbound_from_interaction("c1", it)
    assert got.kind == "callback" and got.callback_id == "1:t" and got.callback_data == "q:1:0"
    assert inbound_from_interaction("c1", SimpleNamespace(**{**it.__dict__, "guild_id": 3})) is None


def test_discord_gateway_class_requests_no_privileged_intent():
    import discord

    from app.services.channels.discord_gateway import DiscordGateway

    gw = DiscordGateway(
        token="t",
        channel_id="c",
        on_inbound=lambda i: None,
        on_ready=lambda: None,
        on_error=lambda m, a: None,
    )
    assert gw._client is None  # nothing connects on construction
    intents = discord.Intents.none()
    intents.dm_messages = True
    assert not intents.message_content and not intents.members and not intents.presences


# ================================================================== misc


def test_chunking_and_escaping():
    text = ("paragraph " * 30 + "\n\n") * 5
    parts = chunk_text(text, 400)
    assert all(len(p) <= 400 for p in parts) and "".join(parts).replace("\n", "").replace(
        " ", ""
    ) == text.replace("\n", "").replace(" ", "")
    assert chunk_text("short", 4096) == ["short"]
    assert chunk_text("x" * 5000, 2000) == ["x" * 2000, "x" * 2000, "x" * 1000]
    assert escape_discord("@everyone `code` *x*") == "@​everyone \\`code\\` \\*x\\*"


def test_long_replies_are_chunked_per_platform(w, app, client):
    from tests.test_agent_api import start_run

    chan = connect(w, "telegram")
    code = link_code(w, chan["id"])["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}"))
    w.router.process(tg_text(chan["id"], "tell me everything"))
    note = rows(w, FounderNote)[0]
    founder = next(
        a for a in client.get(api(w.cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    sess = start_run(app, client, w.cid, founder["id"], task_title="plan", kind="planning")
    sess.post(f"/notes/{note.id}/replies", json={"body": ("word " * 1200).strip()})
    w.supervisor.tick()
    parts = [t for t in w.platforms.texts("telegram") if "word word" in t]
    assert len(parts) == 2 and all(len(t) <= 4096 for t in parts)


def test_digest_goes_only_to_links_that_opted_in_once_a_day(w):
    from datetime import UTC, datetime

    chan = connect(w, "telegram")
    code = link_code(w, chan["id"])["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}"))
    link = rows(w, ChatLink)[0]
    morning = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
    w.clock.set(morning)
    w.supervisor.tick()
    assert outbound(w, ref_type="digest") == []
    assert (
        w.client.patch(
            url(w, f"/channel-links/{link.id}"), json={"notify": {"digest": True}}
        ).status_code
        == 200
    )
    w.clock.advance(400)
    w.supervisor.tick()
    w.clock.advance(400)
    w.supervisor.tick()
    digest = outbound(w, ref_type="digest")
    assert len(digest) == 1 and digest[0].status == "sent"
    assert w.platforms.texts("telegram")[-1].startswith("Daily summary")


def test_old_log_rows_are_pruned_but_pending_ones_are_kept(w):
    from app.services.channels import outbox

    chan = connect(w, "telegram")
    with w.factory() as db:
        old = w.clock() - timedelta(days=40)
        db.add(
            ChatMessage(
                company_id=w.cid,
                channel_id=chan["id"],
                direction="in",
                external_message_id="a",
                status="received",
                created_at=old,
            )
        )
        db.add(
            ChatMessage(
                company_id=w.cid,
                channel_id=chan["id"],
                direction="out",
                status="pending",
                created_at=old,
            )
        )
        db.add(
            ChatMessage(
                company_id=w.cid,
                channel_id=chan["id"],
                direction="in",
                external_message_id="b",
                status="received",
                created_at=w.clock(),
            )
        )
        db.flush()
        assert outbox.prune(db, w.clock()) == 1
        db.commit()
    assert sorted(m.direction + m.status for m in rows(w, ChatMessage)) == [
        "inreceived",
        "outpending",
    ]


def test_a_failed_note_is_reported_to_the_chat_that_sent_it(w):
    from app.models.commands import CommandExchange
    from app.services import command_center

    chan = connect(w, "telegram")
    code = link_code(w, chan["id"])["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}"))
    w.router.process(tg_text(chan["id"], "do something hard"))
    with w.factory() as db:
        ex = db.scalar(select(CommandExchange))
        ex.attempts = command_center.MAX_ATTEMPTS
        command_center._fail_attempt(db, ex, "its run failed", w.clock())
        db.commit()
    w.supervisor.tick()
    assert "could not answer" in w.platforms.texts("telegram")[-1]
    assert outbound(w, ref_type="command")[0].status == "sent"


def test_the_engine_service_runs_the_supervisor_only_when_enabled(engine, make_settings):
    from app.engine.service import EngineService

    assert EngineService(engine).channels is not None
    engine.settings = make_settings(channels_enabled=False)
    assert EngineService(engine).channels is None


def test_telegram_updates_are_normalised():
    from app.services.channels.adapters import TelegramAdapter

    parse = TelegramAdapter.parse_update
    base = {
        "message_id": 7,
        "from": {"id": 5, "first_name": "Pat", "last_name": "Q"},
        "chat": {"id": 5, "type": "private"},
    }
    text = parse(
        "c1",
        {"update_id": 1, "message": {**base, "text": "hi", "reply_to_message": {"message_id": 3}}},
    )
    assert (text.kind, text.text, text.external_message_id, text.reply_to_message_id) == (
        "text",
        "hi",
        "5:7",
        "5:3",
    )
    assert text.display_name == "Pat Q" and text.is_private
    assert parse("c1", {"update_id": 2, "message": {**base, "sticker": {}}}).kind == "unsupported"
    group = parse(
        "c1",
        {
            "update_id": 3,
            "message": {**base, "chat": {"id": -1, "type": "supergroup"}, "text": "x"},
        },
    )
    assert group.is_private is False
    assert (
        parse(
            "c1",
            {"update_id": 4, "message": {**base, "from": {"id": 9, "is_bot": True}, "text": "x"}},
        )
        is None
    )
    cb = parse(
        "c1",
        {
            "update_id": 5,
            "callback_query": {
                "id": "q9",
                "from": {"id": 5},
                "data": "a:1:approve",
                "message": {"message_id": 8, "chat": {"id": 5, "type": "private"}},
            },
        },
    )
    assert (cb.kind, cb.callback_data, cb.callback_id, cb.callback_message_id) == (
        "callback",
        "a:1:approve",
        "q9",
        "5:8",
    )
    assert parse("c1", {"update_id": 6, "edited_message": {}}) is None


def test_agents_cannot_use_the_channel_api(w, app, client):
    from tests.test_agent_api import start_run

    connect(w, "telegram")
    worker = new_agent(client, w.cid, name="Worker")
    sess = start_run(app, client, w.cid, worker["id"])
    anon = w.anon
    assert anon.get(api(w.cid, "/channels"), headers=sess.headers).status_code == 403
    assert (
        anon.post(
            api(w.cid, "/channels"), json={"kind": "telegram", "token": "x"}, headers=sess.headers
        ).status_code
        == 403
    )
    assert (
        anon.get(api(w.cid, "/channels")).status_code == 401
    )  # and no credential at all is no access
