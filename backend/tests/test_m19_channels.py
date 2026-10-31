"""M19: chat channels. Fake Telegram / Discord / WhatsApp servers behind a MockTransport; no test
here (or in the code under test) reaches a network."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.models.audit import AuditEvent
from app.models.autonomy import FounderNote
from app.models.chat import ChatChannel, ChatLink, ChatLinkCode, ChatMessage
from app.models.commands import CommandExchange
from app.models.company import CompanyMember
from app.models.governance import Approval
from app.models.questions import FounderQuestion
from app.models.run import CompanyEvent
from app.services import notifications
from app.services.channels import questions_seam
from app.services.channels.common import DENIED_TEXT, UNSUPPORTED_TEXT, Inbound
from tests.conftest import human
from tests.helpers import api, new_agent
from tests.m19_helpers import (
    ALL_SECRETS,
    DISCORD_TOKEN,
    TG_TOKEN,
    WA_APP_SECRET,
    WA_TOKEN,
    connect,
    link_code,
    link_owner,
    make_world,
    tg_press,
    tg_text,
    url,
)
from tests.test_agent_api import make_approval, start_run


@pytest.fixture
def w(app, client, anon_client, monkeypatch):
    world = make_world(app, client)
    world.anon = anon_client
    # one clock for the chat services (code expiry, link times) and the router
    monkeypatch.setattr("app.services.channels.service.utcnow", world.clock)
    return world


def rows(w, model, **where):
    with w.factory() as db:
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return list(db.scalars(stmt))


def drain(w, channel_id: str) -> None:
    w.supervisor.tick()
    assert channel_id in w.supervisor.held()


def outbound(w, **where):
    return rows(w, ChatMessage, direction="out", **where)


# ================================================================== connect / secrets


def test_connect_validates_the_token_and_never_returns_a_secret(w):
    bad = w.client.post(url(w, "/channels"), json={"kind": "telegram", "token": "nope:BAD-TOKEN"})
    assert bad.status_code == 422
    assert "nope:BAD-TOKEN" not in bad.text and bad.json()["error"]["message"]
    assert rows(w, ChatChannel) == []

    chan = connect(w, "telegram")
    assert chan["kind"] == "telegram" and chan["status"] == "active"
    assert (
        chan["name"] == "@foundry_test_bot" and chan["config"]["bot_username"] == "foundry_test_bot"
    )
    # long polling and a webhook are exclusive: deleteWebhook was called on connect
    assert any(c["path"].endswith("/deleteWebhook") for c in w.platforms.calls)
    # secrets: not in the API, the audit trail, the events, or the channel row
    listing = w.client.get(url(w, "/channels"))
    assert listing.status_code == 200
    assert TG_TOKEN not in listing.text and TG_TOKEN not in str(chan)
    with w.factory() as db:
        for a in db.scalars(select(AuditEvent)):
            assert TG_TOKEN not in f"{a.summary} {a.data}"
        for e in db.scalars(select(CompanyEvent)):
            assert TG_TOKEN not in str(e.payload)
        row = db.scalar(select(ChatChannel))
        assert TG_TOKEN not in str(row.config) and row.credential_id
        from app.models.provider import Credential

        cred = db.get(Credential, row.credential_id)
        assert TG_TOKEN not in cred.encrypted_value  # sealed
    kinds = {a["kind"]: a for a in listing.json()["available"]}
    assert kinds["telegram"]["available"] and kinds["slack"]["available"] is False
    assert kinds["whatsapp"]["needs_public_url"]
    # one channel per kind and company
    assert (
        w.client.post(url(w, "/channels"), json={"kind": "telegram", "token": TG_TOKEN}).status_code
        == 409
    )
    audit = [a.action for a in rows(w, AuditEvent)]
    assert "chat.channel_connected" in audit


def test_one_bot_serves_one_workspace_only(w, client):
    connect(w, "telegram")
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    r = client.post(api(other, "/channels"), json={"kind": "telegram", "token": TG_TOKEN})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "channel_in_use"


def test_whatsapp_needs_a_public_url_and_shows_the_verify_token_once(w, monkeypatch):
    body = {
        "kind": "whatsapp",
        "whatsapp": {
            "phone_number_id": "109876543210",
            "access_token": WA_TOKEN,
            "app_secret": WA_APP_SECRET,
        },
    }
    no_url = w.client.post(url(w, "/channels"), json=body)
    assert (
        no_url.status_code == 409
        and no_url.json()["error"]["details"]["reason"] == "public_url_required"
    )
    monkeypatch.setattr(w.app.state.settings, "public_url", "https://foundry.example.com")
    chan = connect(w, "whatsapp")
    assert chan["webhook_url"] == f"https://foundry.example.com/api/webhooks/whatsapp/{chan['id']}"
    assert chan["verify_token"] and len(chan["verify_token"]) >= 20
    assert chan["config"]["display_phone"]
    again = w.client.get(url(w, "/channels")).json()["items"][0]
    assert again["verify_token"] is None
    assert WA_TOKEN not in str(again) and WA_APP_SECRET not in str(again)
    wrong = w.client.post(
        url(w, "/channels"),
        json={"kind": "whatsapp", "whatsapp": {**body["whatsapp"], "access_token": "EAAbad"}},
    )
    assert wrong.status_code in (409, 422)  # already connected / invalid token, never a 500


def test_remove_deletes_links_messages_and_secret(w):
    chan = link_owner(w)
    assert len(rows(w, ChatLink)) == 1
    r = w.client.delete(url(w, f"/channels/{chan['id']}"))
    assert r.status_code == 204
    assert rows(w, ChatChannel) == [] and rows(w, ChatLink) == [] and rows(w, ChatMessage) == []
    with w.factory() as db:
        from app.models.provider import Credential

        creds = list(db.scalars(select(Credential)))
        assert all(
            c.revoked_at is not None and c.encrypted_value == ""
            for c in creds
            if "Chat channel" in c.name
        )
    assert "chat.channel_removed" in [a.action for a in rows(w, AuditEvent)]
    # the poller is gone: nothing to hold
    w.supervisor.tick()
    assert w.supervisor.held() == set()


def test_disable_and_enable(w):
    chan = connect(w, "telegram")
    off = w.client.patch(url(w, f"/channels/{chan['id']}"), json={"enabled": False})
    assert off.json()["status"] == "disabled"
    w.supervisor.tick()
    assert w.supervisor.held() == set()
    assert w.client.post(url(w, f"/channels/{chan['id']}/link-code")).status_code == 409
    on = w.client.patch(url(w, f"/channels/{chan['id']}"), json={"enabled": True, "name": "Main"})
    assert on.json()["status"] == "active" and on.json()["name"] == "Main"


# ================================================================== roles & tenancy


def test_only_admins_manage_channels_and_non_members_get_404(w, make_user, add_member, client_as):
    chan = connect(w, "telegram")
    member = make_user("mia@example.com")
    add_member(w.cid, member.id, "member")
    viewer = make_user("vic@example.com")
    add_member(w.cid, viewer.id, "viewer")
    stranger = make_user("sam@example.com")
    for user, expected in ((member, 403), (viewer, 403), (stranger, 404)):
        c = client_as(human(user))
        assert c.get(url(w, "/channels")).status_code == expected
        assert (
            c.post(url(w, "/channels"), json={"kind": "telegram", "token": TG_TOKEN}).status_code
            == expected
        )
        assert c.post(url(w, f"/channels/{chan['id']}/link-code")).status_code == expected
        assert c.delete(url(w, f"/channels/{chan['id']}")).status_code == expected


def test_channels_codes_and_links_never_cross_companies(w, client):
    chan = link_owner(w)
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    assert client.get(api(other, "/channels")).json()["items"] == []
    assert (
        client.patch(api(other, f"/channels/{chan['id']}"), json={"name": "x"}).status_code == 404
    )
    assert client.delete(api(other, f"/channels/{chan['id']}")).status_code == 404
    assert client.post(api(other, f"/channels/{chan['id']}/link-code")).status_code == 404
    link = rows(w, ChatLink)[0]
    assert client.delete(api(other, f"/channel-links/{link.id}")).status_code == 404
    assert (
        client.patch(
            api(other, f"/channel-links/{link.id}"), json={"notify": {"alerts": False}}
        ).status_code
        == 404
    )
    # a code issued for company A cannot link a chat of company B's channel
    code_a = link_code(w, chan["id"])["code"]
    assert rows(w, ChatLink)[0].revoked_at is None
    with w.factory() as db:
        from app.services.channels import service as svc

        assert (
            db.scalar(
                select(ChatLinkCode).where(ChatLinkCode.code_hash == svc.hash_code(code_a))
            ).company_id
            == w.cid
        )


# ================================================================== linking


def test_link_flow_unlinked_sender_reply_and_code_rules(w):
    chan = connect(w, "telegram")
    cid = chan["id"]
    # 1) an unlinked sender gets the fixed reply, once per hour, and nothing is processed
    w.router.process(tg_text(cid, "hello?", user="777"))
    w.router.process(tg_text(cid, "anyone?", user="777"))
    assert w.platforms.texts("telegram") == [DENIED_TEXT]
    assert rows(w, FounderNote) == [] and rows(w, CommandExchange) == []
    assert rows(w, ChatLink) == []
    w.clock.advance(3601)
    w.router.process(tg_text(cid, "again", user="777"))
    assert w.platforms.texts("telegram") == [DENIED_TEXT, DENIED_TEXT]
    # 2) a wrong code is refused; 3) a right code links; 4) it cannot be reused
    w.router.process(tg_text(cid, "/start ZZZZZZZZ", user="42"))
    assert "not valid" in w.platforms.texts("telegram")[-1]
    issued = link_code(w, cid)
    assert issued["deep_link"] == f"https://t.me/foundry_test_bot?start={issued['code']}"
    w.router.process(tg_text(cid, f"/start {issued['code']}", user="42"))
    assert w.platforms.texts("telegram")[-1].startswith("Linked to Acme Labs as ")
    links = rows(w, ChatLink)
    assert len(links) == 1 and links[0].user_id == w.owner_id and links[0].external_user_id == "42"
    w.router.process(tg_text(cid, f"/start {issued['code']}", user="99"))
    assert "not valid" in w.platforms.texts("telegram")[-1]
    assert len(rows(w, ChatLink)) == 1
    actions = [a.action for a in rows(w, AuditEvent)]
    assert "chat.linked" in actions and issued["code"] not in str(
        [a.data for a in rows(w, AuditEvent)]
    )
    # the code is stored hashed
    with w.factory() as db:
        assert issued["code"] not in str([c.code_hash for c in db.scalars(select(ChatLinkCode))])


def test_link_codes_expire_and_are_bound_to_their_channel(w, monkeypatch):
    tg = connect(w, "telegram")
    dc = connect(w, "discord")
    old = link_code(w, tg["id"])["code"]
    w.clock.advance(601)  # 10 minutes + 1 s
    w.router.process(tg_text(tg["id"], f"/start {old}", user="42"))
    assert "not valid or has expired" in w.platforms.texts("telegram")[-1]
    assert rows(w, ChatLink) == []
    # a Telegram code typed to the Discord bot is wrong-channel
    tg_code = link_code(w, tg["id"])["code"]
    w.router.process(tg_text(dc["id"], f"link {tg_code}", user="900"))
    assert "not valid" in w.platforms.texts("discord")[-1]
    assert rows(w, ChatLink) == []
    # and the failed attempts are limited per sender
    for _ in range(6):
        w.router.process(tg_text(tg["id"], "/start AAAAAAAA", user="31337"))
    assert len(rows(w, ChatLink)) == 0
    good = link_code(w, tg["id"])["code"]
    w.router.process(tg_text(tg["id"], f"/start {good}", user="31337"))
    assert rows(w, ChatLink) == []  # locked out for the hour even with a valid code


def test_a_code_for_a_demoted_issuer_does_not_link(w, make_user, add_member, client_as):
    chan = connect(w, "telegram")
    u = make_user("ann@example.com")
    add_member(w.cid, u.id, "admin")
    code = link_code(w, chan["id"], client_as(human(u)))["code"]
    with w.factory() as db:
        db.execute(update(CompanyMember).where(CompanyMember.user_id == u.id).values(role="member"))
        db.commit()
    w.router.process(tg_text(chan["id"], f"/start {code}", user="55"))
    assert rows(w, ChatLink) == []


def test_unlink_from_chat_and_from_web(w):
    chan = link_owner(w)
    w.router.process(tg_text(chan["id"], "/unlink"))
    assert rows(w, ChatLink)[0].revoked_at is not None
    w.router.process(tg_text(chan["id"], "are you there"))
    assert w.platforms.texts("telegram")[-1] == DENIED_TEXT
    # web: own link
    link_owner_again = link_code(w, chan["id"])["code"]
    w.router.process(tg_text(chan["id"], f"/start {link_owner_again}"))
    live = [link for link in rows(w, ChatLink) if link.revoked_at is None]
    assert len(live) == 1
    r = w.client.delete(url(w, f"/channel-links/{live[0].id}"))
    assert r.status_code == 204
    assert all(link.revoked_at for link in rows(w, ChatLink))
    assert "chat.unlinked" in [a.action for a in rows(w, AuditEvent)]


def test_removing_a_member_revokes_their_links(w, make_user, add_member, client_as):
    chan = connect(w, "telegram")
    u = make_user("ann@example.com")
    add_member(w.cid, u.id, "admin")
    code = link_code(w, chan["id"], client_as(human(u)))["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}", user="55"))
    assert len(rows(w, ChatLink)) == 1
    member_id = next(m.id for m in rows(w, CompanyMember, user_id=u.id))
    assert w.client.delete(url(w, f"/members/{member_id}")).status_code == 204
    assert rows(w, ChatLink)[0].revoked_at is not None


def test_a_removed_member_with_a_stale_link_gets_nothing_done(w, make_user, add_member, client_as):
    chan = connect(w, "telegram")
    u = make_user("ann@example.com")
    add_member(w.cid, u.id, "admin")
    code = link_code(w, chan["id"], client_as(human(u)))["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}", user="55"))
    with w.factory() as db:  # access removed behind the link's back
        db.execute(
            update(CompanyMember).where(CompanyMember.user_id == u.id).values(status="removed")
        )
        db.commit()
    w.router.process(tg_text(chan["id"], "do something", user="55"))
    assert w.platforms.texts("telegram")[-1] == DENIED_TEXT
    assert rows(w, FounderNote) == []
    assert rows(w, ChatLink)[0].revoked_at is not None


def test_notification_preferences_are_the_owners_own(w, make_user, add_member, client_as):
    chan = link_owner(w)
    link = rows(w, ChatLink)[0]
    r = w.client.patch(
        url(w, f"/channel-links/{link.id}"), json={"notify": {"alerts": False, "digest": True}}
    )
    assert (
        r.status_code == 200
        and r.json()["notify"]["alerts"] is False
        and r.json()["notify"]["digest"] is True
    )
    other = make_user("ann@example.com")
    add_member(w.cid, other.id, "admin")
    oc = client_as(human(other))
    assert (
        oc.patch(url(w, f"/channel-links/{link.id}"), json={"notify": {"alerts": True}}).status_code
        == 403
    )
    assert chan  # an admin may remove someone's link, not tune it
    assert oc.delete(url(w, f"/channel-links/{link.id}")).status_code == 204


def test_groups_are_ignored(w):
    chan = connect(w, "telegram")
    group = Inbound(
        channel_id=chan["id"],
        external_user_id="5",
        external_chat_id="-100",
        external_message_id="-100:1",
        text="hi all",
        is_private=False,
    )
    w.router.process(group)
    assert w.platforms.tg_sent == [] and rows(w, ChatMessage) == []


# ================================================================== inbound -> note -> reply


def test_a_message_becomes_a_note_and_the_reply_comes_back_to_that_chat(w, app, client):
    chan = link_owner(w)
    cid = chan["id"]
    w.platforms.tg_sent.clear()
    w.router.process(tg_text(cid, "Build a landing page for the launch"))
    notes = rows(w, FounderNote)
    assert len(notes) == 1 and notes[0].body == "Build a landing page for the launch"
    assert notes[0].author_user_id == w.owner_id
    assert "Passed to the AI Co-founder" in w.platforms.texts("telegram")[-1]
    inbound = [m for m in rows(w, ChatMessage, direction="in") if m.ref_type == "note"]
    assert len(inbound) == 1 and inbound[0].ref_id == notes[0].id and inbound[0].link_id
    # a note written in the WEB is not delivered to the chat
    web = client.post(api(w.cid, "/notes"), json={"body": "from the web"}).json()
    # the Co-founder replies to both
    founder = next(
        a for a in client.get(api(w.cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    sess = start_run(app, client, w.cid, founder["id"], task_title="plan", kind="planning")
    for note_id, text in ((notes[0].id, "On it - the page is planned."), (web["id"], "Web reply")):
        assert sess.post(f"/notes/{note_id}/replies", json={"body": text}).status_code == 201
    pending = outbound(w, status="pending")
    assert len(pending) == 1 and pending[0].ref_type == "reply"
    w.supervisor.tick()
    texts = w.platforms.texts("telegram")
    assert texts[-1].endswith("On it - the page is planned.") and "(AI)" in texts[-1]
    assert not any("Web reply" in t for t in texts)
    sent = outbound(w, ref_type="reply")[0]
    assert sent.status == "sent" and sent.external_message_id
    # the text itself is not stored in the log
    assert "planned" not in str([(m.error, m.ref_id) for m in rows(w, ChatMessage)])
    # delivering twice is impossible: the row is done
    w.supervisor.tick()
    assert len([t for t in w.platforms.texts("telegram") if "planned" in t]) == 1


def test_secrets_in_agent_text_are_scrubbed_before_sending(w, app, client):
    chan = link_owner(w)
    w.router.process(tg_text(chan["id"], "status please?"))
    note = rows(w, FounderNote)[0]
    founder = next(
        a for a in client.get(api(w.cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    sess = start_run(app, client, w.cid, founder["id"], task_title="plan", kind="planning")
    sess.post(
        f"/notes/{note.id}/replies",
        json={"body": "key is sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA ok"},
    )
    w.supervisor.tick()
    assert "sk-ant" not in w.platforms.texts("telegram")[-1]


def test_slash_commands_work_and_a_member_cannot_write_notes(w, make_user, add_member, client_as):
    chan = connect(w, "telegram")
    u = make_user("ann@example.com")
    add_member(w.cid, u.id, "admin")
    code = link_code(w, chan["id"], client_as(human(u)))["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}", user="55"))
    w.router.process(tg_text(chan["id"], "/status", user="55"))
    assert "Company: Acme Labs" in w.platforms.texts("telegram")[-1]
    assert rows(w, FounderNote) == []
    w.router.process(tg_text(chan["id"], "/help", user="55"))
    assert "/questions" in w.platforms.texts("telegram")[-1]
    with w.factory() as db:  # demoted afterwards: the real role is read on every message
        db.execute(update(CompanyMember).where(CompanyMember.user_id == u.id).values(role="member"))
        db.commit()
    w.router.process(tg_text(chan["id"], "please buy a domain", user="55"))
    assert "Only company owners and admins can write notes" in w.platforms.texts("telegram")[-1]
    assert rows(w, FounderNote) == []
    w.router.process(tg_text(chan["id"], "/pause", user="55"))
    reply = w.platforms.texts("telegram")[-1]
    assert "pause" in reply.lower() or "owner" in reply.lower() or "admin" in reply.lower()


def test_unsupported_content_is_answered_not_dropped(w):
    chan = link_owner(w)
    w.router.process(
        Inbound(
            channel_id=chan["id"],
            external_user_id="4242",
            external_chat_id="4242",
            external_message_id="4242:9001",
            kind="unsupported",
        )
    )
    assert w.platforms.texts("telegram")[-1] == UNSUPPORTED_TEXT


def test_too_long_messages_and_rate_limit(w):
    chan = link_owner(w)
    w.router.process(tg_text(chan["id"], "x" * 4001))
    assert "too long" in w.platforms.texts("telegram")[-1]
    assert rows(w, FounderNote) == []
    for _ in range(22):
        w.router.process(tg_text(chan["id"], "/help"))
    assert "Slow down" in w.platforms.texts("telegram")[-1]
    w.clock.advance(301)
    w.router.process(tg_text(chan["id"], "/help"))
    assert "Slow down" not in w.platforms.texts("telegram")[-1]


def test_duplicate_deliveries_are_handled_once(w):
    chan = link_owner(w)
    msg = tg_text(chan["id"], "do the thing")
    w.router.process(msg)
    w.router.process(msg)
    assert len(rows(w, FounderNote)) == 1


# ================================================================== telegram polling


def tg_update(uid: int, text: str, *, user: int = 4242, mid: int | None = None) -> dict:
    return {
        "update_id": uid,
        "message": {
            "message_id": mid or uid,
            "from": {"id": user, "is_bot": False, "first_name": "Pat"},
            "chat": {"id": user, "type": "private"},
            "text": text,
        },
    }


def test_telegram_polling_persists_the_cursor_and_skips_repeats(w):
    from app.services.channels.supervisor import TelegramPoller

    chan = link_owner(w)
    poller = TelegramPoller(w.factory, w.hub, w.router, chan["id"])
    w.platforms.tg_queue = [tg_update(500, "/help"), tg_update(501, "/status")]
    assert poller.poll_once() == 2
    assert rows(w, ChatChannel)[0].poll_cursor == "501"
    # next call asks for offset 502; a re-delivered old update is ignored
    w.platforms.tg_queue = [tg_update(501, "/status"), tg_update(502, "/goals")]
    w.platforms.tg_offsets.clear()
    assert poller.poll_once() == 1
    assert w.platforms.tg_offsets == [502]
    assert rows(w, ChatChannel)[0].poll_cursor == "502"
    # a new poller (a restart) resumes from the stored cursor
    w.platforms.tg_offsets.clear()
    TelegramPoller(w.factory, w.hub, w.router, chan["id"]).poll_once()
    assert w.platforms.tg_offsets == [503]
    getupdates = [c for c in w.platforms.calls if c["path"].endswith("/getUpdates")]
    assert getupdates[-1]["body"]["allowed_updates"] == ["message", "callback_query"]


def test_a_refused_bot_token_puts_the_channel_in_error_and_tells_the_admins(w):
    from app.services.channels.supervisor import TelegramPoller

    chan = connect(w, "telegram")
    with w.factory() as db:  # the founder revoked the bot in BotFather: swap in a bad token
        row = db.scalar(select(ChatChannel))
        from app.models.provider import Credential

        cred = db.get(Credential, row.credential_id)
        cred.encrypted_value = w.hub.seal({"token": "123:revoked"})
        db.commit()
    assert TelegramPoller(w.factory, w.hub, w.router, chan["id"]).poll_once() is None
    row = rows(w, ChatChannel)[0]
    assert row.status == "error" and "refused the bot token" in row.last_error
    assert "123:revoked" not in row.last_error
    from app.models.accounts import Notification

    with w.factory() as db:
        notes = list(db.scalars(select(Notification).where(Notification.kind == "alert")))
    assert any("needs attention" in n.title for n in notes)
    w.supervisor.tick()
    assert w.supervisor.held() == set()  # an errored channel is not served


# ================================================================== lease


def test_two_supervisors_one_poller(w):
    from types import SimpleNamespace

    from tests.m19_helpers import ChannelSupervisor

    chan = connect(w, "telegram")
    started_b: list[str] = []
    other = ChannelSupervisor(
        w.factory,
        w.hub,
        owner_id="sup-2",
        clock=w.clock,
        inbound_starter=lambda c, s: (started_b.append(c.id), SimpleNamespace(stop=lambda: None))[
            1
        ],
    )
    report_a = w.supervisor.tick()
    report_b = other.tick()
    assert report_a["held"] == [chan["id"]] and w.started == [chan["id"]]
    assert report_b["held"] == [] and started_b == []
    assert rows(w, ChatChannel)[0].lease_owner == "sup-1"
    # A keeps renewing, B still waits
    w.clock.advance(20)
    w.supervisor.tick()
    w.clock.advance(20)
    assert other.tick()["held"] == []
    # A dies without releasing: after the lease (60 s) B takes over
    w.clock.advance(61)
    assert other.tick()["held"] == [chan["id"]] and started_b == [chan["id"]]
    assert rows(w, ChatChannel)[0].lease_owner == "sup-2"
    # A comes back, notices it lost the channel and stops its poller
    w.clock.advance(16)
    rep = w.supervisor.tick()
    assert chan["id"] not in rep["held"] and w.started == []
    # a clean stop releases the lease
    other.stop()
    assert rows(w, ChatChannel)[0].lease_owner is None


def test_discord_gateway_is_started_with_the_stored_token_only_by_the_leaseholder(
    app, client, anon_client
):
    from tests.m19_helpers import FakeGateway, make_world

    w = make_world(app, client)
    sup = __import__("app.services.channels.supervisor", fromlist=["x"]).ChannelSupervisor(
        w.factory, w.hub, owner_id="s", clock=w.clock
    )
    chan = connect(w, "discord")
    assert chan["status"] == "connecting" and chan["invite_url"].startswith(
        "https://discord.com/oauth2/authorize"
    )
    sup.tick()
    gw = FakeGateway.instances[-1]
    assert (
        gw.started and gw.kwargs["token"] == DISCORD_TOKEN and gw.kwargs["channel_id"] == chan["id"]
    )
    gw.kwargs["on_ready"]()
    assert rows(w, ChatChannel)[0].status == "active"
    gw.kwargs["on_error"]("Discord refused the bot token", True)
    assert rows(w, ChatChannel)[0].status == "error"
    sup.tick()
    assert gw.stopped
    sup.stop()


# ================================================================== approvals


@pytest.fixture
def approval_world(w, app, client):
    chan = link_owner(w)
    worker = new_agent(client, w.cid, name="Worker")
    approval_id = make_approval(client, w.cid, worker["id"], "external_purchase")
    return chan, worker, approval_id


def press(w, chan, data: str, **kw):
    w.router.process(tg_press(chan["id"], data, **kw))


def calls(w, suffix: str):
    return [c for c in w.platforms.calls if c["path"].endswith(suffix)]


def test_a_pending_approval_arrives_with_buttons_and_approve_decides_it(w, approval_world):
    chan, _, approval_id = approval_world
    w.supervisor.tick()
    msg = w.platforms.tg_sent[-1]
    assert "Approval needed" in msg["text"]
    labels = [b["text"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
    data = [b["callback_data"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["Approve", "Decline"] and data == [
        f"a:{approval_id}:approve",
        f"a:{approval_id}:decline",
    ]
    press(w, chan, f"a:{approval_id}:approve")
    with w.factory() as db:
        a = db.get(Approval, approval_id)
        assert (
            a.status == "approved"
            and a.decided_by_id == w.owner_id
            and "Telegram" in a.decision_note
        )
    acts = [x for x in rows(w, AuditEvent) if x.action == "chat.approval_decided"]
    assert acts and acts[0].data["via"] == "telegram"
    assert calls(w, "/answerCallbackQuery") and calls(w, "/editMessageReplyMarkup")
    # a second press is stale: already decided, no change
    press(w, chan, f"a:{approval_id}:decline")
    with w.factory() as db:
        assert db.get(Approval, approval_id).status == "approved"
    assert "already approved" in w.platforms.texts("telegram")[-1]


def test_decline_rejects(w, approval_world):
    chan, _, approval_id = approval_world
    w.supervisor.tick()
    press(w, chan, f"a:{approval_id}:decline")
    with w.factory() as db:
        assert db.get(Approval, approval_id).status == "rejected"


def test_button_payloads_are_revalidated_never_trusted(w, approval_world, client):
    chan, worker, approval_id = approval_world
    # never sent to this chat -> not valid, even though the id is real
    other_id = make_approval(client, w.cid, worker["id"], "external_purchase")
    press(w, chan, f"a:{other_id}:approve")
    with w.factory() as db:
        assert db.get(Approval, other_id).status == "pending"
    # garbage payloads
    for bad in ("a:nope:approve", "a", "x:1:2", f"a:{approval_id}:maybe", "q::0"):
        press(w, chan, bad)
    # a foreign company's approval, even if a message row existed for it
    other_company = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    foreign_agent = new_agent(client, other_company, name="Fx")
    foreign = make_approval(client, other_company, foreign_agent["id"], "external_purchase")
    with w.factory() as db:
        link = db.scalar(select(ChatLink))
        db.add(
            ChatMessage(
                company_id=w.cid,
                channel_id=chan["id"],
                link_id=link.id,
                direction="out",
                ref_type="approval",
                ref_id=foreign,
                status="sent",
            )
        )
        db.commit()
    press(w, chan, f"a:{foreign}:approve")
    with w.factory() as db:
        assert db.get(Approval, foreign).status == "pending"
    assert "no longer exists" in w.platforms.texts("telegram")[-1] or calls(
        w, "/answerCallbackQuery"
    )


def test_a_non_approver_cannot_decide_from_chat(
    w, approval_world, make_user, add_member, client_as
):
    chan, _, approval_id = approval_world
    w.supervisor.tick()
    u = make_user("ann@example.com")
    add_member(w.cid, u.id, "admin")
    code = link_code(w, chan["id"], client_as(human(u)))["code"]
    w.router.process(tg_text(chan["id"], f"/start {code}", user="55"))
    with w.factory() as db:
        ann_link = db.scalar(select(ChatLink).where(ChatLink.external_user_id == "55"))
        db.add(
            ChatMessage(
                company_id=w.cid,
                channel_id=chan["id"],
                link_id=ann_link.id,
                direction="out",
                ref_type="approval",
                ref_id=approval_id,
                status="sent",
            )
        )
        db.execute(update(CompanyMember).where(CompanyMember.user_id == u.id).values(role="member"))
        db.commit()
    press(w, chan, f"a:{approval_id}:approve", user="55")
    with w.factory() as db:
        assert db.get(Approval, approval_id).status == "pending"
    assert "Only company owners and admins" in w.platforms.texts("telegram")[-1]


def test_purchase_checkout_stays_in_the_web(w, app, client):
    chan = link_owner(w)
    worker = new_agent(client, w.cid, name="Worker")
    approval_id = make_approval(client, w.cid, worker["id"], "external_purchase")
    with w.factory() as db:
        db.execute(
            update(Approval)
            .where(Approval.id == approval_id)
            .values(subject_type="purchase_request")
        )
        db.commit()
    w.supervisor.tick()
    msg = w.platforms.tg_sent[-1]
    assert "reply_markup" not in msg and "Open Foundry" in msg["text"]
    assert chan


def test_slash_approvals_lists_with_buttons(w, approval_world):
    chan, _, approval_id = approval_world
    w.router.process(tg_text(chan["id"], "/approvals"))
    msg = w.platforms.tg_sent[-1]
    assert (
        approval_id
        and msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        == f"a:{approval_id}:approve"
    )
    # the listed message is logged so its buttons are valid
    press(w, chan, f"a:{approval_id}:approve")
    with w.factory() as db:
        assert db.get(Approval, approval_id).status == "approved"


def test_an_approval_decided_elsewhere_is_not_sent_late(w, approval_world, client):
    chan, _, approval_id = approval_world
    assert client.post(api(w.cid, f"/approvals/{approval_id}/approve")).status_code == 200
    w.supervisor.tick()
    assert w.platforms.tg_sent == [] or "Approval needed" not in w.platforms.tg_sent[-1]["text"]
    assert outbound(w, ref_type="approval")[0].error == "no_longer_pending"


# ================================================================== founder questions


@pytest.fixture
def fake_questions():
    """The real M18 service answers; this name just keeps the tests' intent readable."""
    return None


_agents = {"n": 0}


def make_question(w, client, *, options=("A", "B"), default="A", name=None):
    """An agent asks through the real service (which notifies the admins)."""
    from app.core.principal import CompanyContext, Principal, PrincipalType, Role
    from app.schemas.questions import QuestionAsk
    from app.services import questions

    _agents["n"] += 1
    agent = new_agent(client, w.cid, name=name or f"Nova{_agents['n']}")
    ctx = CompanyContext(
        w.cid, Principal(PrincipalType.AGENT, agent["id"], agent["name"], w.cid), Role.AGENT
    )
    with w.factory() as db:
        q = questions.ask(
            db,
            ctx,
            None,
            QuestionAsk(question="Which plan?", options=list(options), default_answer=default),
        )
        return q.id


def test_a_question_arrives_with_buttons_and_an_option_answers_it(w, client, fake_questions):
    chan = link_owner(w)
    qid = make_question(w, client, name="Nova")
    w.supervisor.tick()
    msg = w.platforms.tg_sent[-1]
    assert (
        "Nova asks (AI)" in msg["text"] and "Which plan?" in msg["text"] and "use: A" in msg["text"]
    )
    buttons = [b for row in msg["reply_markup"]["inline_keyboard"] for b in row]
    assert [b["text"] for b in buttons] == ["A", "B", "Use default"]
    assert [b["callback_data"] for b in buttons] == [f"q:{qid}:0", f"q:{qid}:1", f"q:{qid}:d"]
    press(w, chan, f"q:{qid}:1")
    with w.factory() as db:
        q = db.get(FounderQuestion, qid)
        assert (q.status, q.answer, q.answered_via) == ("answered", "B", "telegram")
        assert q.answered_by_user_id == w.owner_id
    # pressing again: closed, nothing changes
    press(w, chan, f"q:{qid}:0")
    with w.factory() as db:
        assert db.get(FounderQuestion, qid).answer == "B"


def test_use_default_and_free_text_reply(w, client, fake_questions):
    chan = link_owner(w)
    q1 = make_question(w, client, default="B")
    w.supervisor.tick()
    press(w, chan, f"q:{q1}:d")
    with w.factory() as db:
        assert db.get(FounderQuestion, q1).answer == "B"
    # free text: reply to the question message
    q2 = make_question(w, client)
    w.supervisor.tick()
    sent = [m for m in outbound(w, ref_type="question") if m.ref_id == q2][0]
    assert sent.external_message_id
    w.router.process(
        tg_text(chan["id"], "Neither - use the free tier", reply_to=sent.external_message_id)
    )
    with w.factory() as db:
        q = db.get(FounderQuestion, q2)
        assert q.answer == "Neither - use the free tier" and q.answered_via == "telegram"
    # an over-long answer is refused before the service sees it
    q3 = make_question(w, client)
    w.supervisor.tick()
    sent3 = [m for m in outbound(w, ref_type="question") if m.ref_id == q3][0]
    w.router.process(tg_text(chan["id"], "x" * 501, reply_to=sent3.external_message_id))
    assert "between 1 and 500" in w.platforms.texts("telegram")[-1]
    with w.factory() as db:
        assert db.get(FounderQuestion, q3).status == "open"
    # a plain message without a reply is NOT an answer: it goes to the Command Center
    w.router.process(tg_text(chan["id"], "B"))
    assert rows(w, FounderNote)[-1].body == "B"


def test_questions_command_and_other_company_question_buttons(w, client, fake_questions):
    chan = link_owner(w)
    w.router.process(tg_text(chan["id"], "/questions"))
    assert "No open questions" in w.platforms.texts("telegram")[-1]
    qid = make_question(w, client)
    w.platforms.tg_sent.clear()
    w.router.process(tg_text(chan["id"], "/questions"))
    assert w.platforms.tg_sent[-1]["reply_markup"]
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    other_agent = new_agent(client, other, name="Zed")
    with w.factory() as db:
        from app.core.time import utcnow

        foreign = FounderQuestion(
            company_id=other,
            agent_id=other_agent["id"],
            question="Secret?",
            options=["x"],
            default_answer="x",
            deadline=utcnow() + timedelta(hours=1),
        )
        db.add(foreign)
        db.commit()
        fid = foreign.id
        link = db.scalar(select(ChatLink))
        db.add(
            ChatMessage(
                company_id=w.cid,
                channel_id=chan["id"],
                link_id=link.id,
                direction="out",
                ref_type="question",
                ref_id=fid,
                status="sent",
            )
        )
        db.commit()
    press(w, chan, f"q:{fid}:0")
    with w.factory() as db:
        assert db.get(FounderQuestion, fid).status == "open"
    assert qid


def test_questions_without_the_service_say_so(w, monkeypatch):
    chan = link_owner(w)
    monkeypatch.setattr(questions_seam, "_service", lambda: None)
    w.router.process(tg_text(chan["id"], "/questions"))
    assert "not available" in w.platforms.texts("telegram")[-1]


# ================================================================== alerts & delivery failures


def test_alerts_follow_the_notify_preference(w):
    chan = link_owner(w)
    with w.factory() as db:
        notifications.notify_admins(
            db,
            w.cid,
            "alert",
            "Budget exceeded",
            body="Spend passed the cap.",
            related_type="alert",
            related_id="a1",
        )
        db.commit()
    w.supervisor.tick()
    assert w.platforms.texts("telegram")[-1] == "Alert: Budget exceeded\nSpend passed the cap."
    link = rows(w, ChatLink)[0]
    assert (
        w.client.patch(
            url(w, f"/channel-links/{link.id}"), json={"notify": {"alerts": False}}
        ).status_code
        == 200
    )
    n = len(w.platforms.tg_sent)
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Again", related_type="alert", related_id="a2"
        )
        db.commit()
    w.supervisor.tick()
    assert len(w.platforms.tg_sent) == n and chan


def test_failed_sends_retry_with_backoff_then_give_up(w):
    link_owner(w)
    w.platforms.tg_sent.clear()
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Heads up", related_type="alert", related_id="a1"
        )
        db.commit()
    w.platforms.fail_sends = [(500, {"ok": False, "error_code": 500, "description": "boom"})] * 3
    w.supervisor.tick()
    row = outbound(w, ref_type="alert")[0]
    assert row.status == "pending" and row.attempts == 1 and row.next_attempt_at
    w.supervisor.tick()  # backoff not over: nothing happens
    assert outbound(w, ref_type="alert")[0].attempts == 1
    w.clock.advance(6)
    w.supervisor.tick()
    assert outbound(w, ref_type="alert")[0].attempts == 2
    w.clock.advance(31)
    w.supervisor.tick()
    row = outbound(w, ref_type="alert")[0]
    assert row.status == "failed" and "boom" in row.error and row.attempts == 3


def test_a_send_that_works_after_a_failure_is_sent(w):
    link_owner(w)
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Heads up", related_type="alert", related_id="a1"
        )
        db.commit()
    w.platforms.fail_sends = [(500, {"ok": False, "error_code": 500, "description": "boom"})]
    w.supervisor.tick()
    w.clock.advance(6)
    w.supervisor.tick()
    assert outbound(w, ref_type="alert")[0].status == "sent"


def test_a_401_on_send_marks_the_channel_error(w):
    chan = link_owner(w)
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Heads up", related_type="alert", related_id="a1"
        )
        db.commit()
    w.platforms.fail_sends = [
        (401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    ]
    w.supervisor.tick()
    row = rows(w, ChatChannel)[0]
    assert row.status == "error" and row.last_error and chan
    assert outbound(w, ref_type="alert")[0].status == "failed"
    # an errored channel is not queued to any more
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "More", related_type="alert", related_id="a2"
        )
        db.commit()
    assert len(outbound(w, ref_type="alert")) == 1


def test_nothing_is_queued_when_the_transaction_rolls_back(w):
    link_owner(w)
    with w.factory() as db:
        notifications.notify_admins(
            db, w.cid, "alert", "Never happened", related_type="alert", related_id="x"
        )
        db.rollback()
    assert outbound(w, ref_type="alert") == []


def test_secrets_never_reach_logs_audit_or_events(w, caplog):
    caplog.set_level(logging.DEBUG)
    chan = link_owner(w)
    w.router.process(tg_text(chan["id"], "hello"))
    w.supervisor.tick()
    w.client.delete(url(w, f"/channels/{chan['id']}"))
    blob = caplog.text
    with w.factory() as db:
        blob += " ".join(f"{a.summary} {a.data}" for a in db.scalars(select(AuditEvent)))
        blob += " ".join(str(e.payload) for e in db.scalars(select(CompanyEvent)))
    for secret in ALL_SECRETS:
        assert secret not in blob
