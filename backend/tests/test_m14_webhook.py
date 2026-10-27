"""M14 WP-C: the optional GitHub webhook. It exists only on a stable public URL, it is signed with a
secret that lives encrypted in the credential store, and a delivery does nothing but poke a sync:
the payload is never read."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.models.provider import Credential
from app.services import reachability
from tests.helpers import api
from tests.m14_helpers import LOGIN, github_world  # noqa: F401  (a fixture)
from tests.m14_service_helpers import (
    audit_actions,
    connect_body,
    github_account,
    m14,  # noqa: F401  (a fixture)
    post_repo,
    repo_row,
    set_repo,
)

PUBLIC = "https://foundry.example.test"
HOOK_TOKEN = (
    "github_pat_11HOOKTOKEN0000_"
    + "k1L2m3N4b5V6c7X8z9A0s1D2f3G4h5J6q7W8e9R0t1Y2u3I4o5P6a7S8d9F0g1H2"
)


@pytest.fixture
def stable_url(monkeypatch):
    monkeypatch.setattr(reachability, "public_url", lambda ic: PUBLIC)
    monkeypatch.setattr(reachability, "tunnel_is_ephemeral", lambda ic: False)


def sign(secret: str, body: bytes) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "X-GitHub-Event": "push"}


def active_repo(client, cid, m14, app, *, token=None, name="hooked"):
    account = github_account(client, cid, m14, token=token or HOOK_TOKEN, label=f"acc-{name}")
    m14.add_repo(f"{LOGIN}/{name}")
    resp = post_repo(client, cid, connect_body(account, f"{LOGIN}/{name}", project_name=name))
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    set_repo(app, rid, status="active")
    return rid


@pytest.fixture
def hook_world(m14):
    m14.github.add_token(HOOK_TOKEN, LOGIN, permissions={"webhooks": "write"})
    return m14


def enable(client, cid, rid, enabled=True):
    return client.put(api(cid, f"/repositories/{rid}/webhook"), json={"enabled": enabled})


def stored_secret(app, rid: str) -> str:
    row = repo_row(app, rid)
    with app.state.session_factory() as db:
        credential = db.get(Credential, row.webhook_secret_credential_id)
        assert PUBLIC not in credential.encrypted_value
        return app.state.integrations.cipher.decrypt(credential.encrypted_value)


# ------------------------------------------------------------------ enabling


def test_enabling_needs_a_stable_public_url(client, company, hook_world, app, monkeypatch):
    cid = company["id"]
    rid = active_repo(client, cid, hook_world, app)
    # no tunnel at all
    monkeypatch.setattr(reachability, "public_url", lambda ic: None)
    resp = enable(client, cid, rid)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "public_url_ephemeral"
    # a quick tunnel (the address changes on every start)
    monkeypatch.setattr(reachability, "public_url", lambda ic: "https://quick.trycloudflare.com")
    monkeypatch.setattr(reachability, "tunnel_is_ephemeral", lambda ic: True)
    resp = enable(client, cid, rid)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "public_url_ephemeral"
    assert hook_world.github.repo(f"{LOGIN}/hooked").hooks == {}
    assert repo_row(app, rid).webhook_id is None


def test_enabling_creates_the_hook_with_the_right_events_and_an_encrypted_secret(
    client, company, hook_world, app, stable_url
):
    cid = company["id"]
    rid = active_repo(client, cid, hook_world, app)
    resp = enable(client, cid, rid)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["webhook"]["enabled"] is True and body["webhook"]["stale"] is False
    assert body["webhook"]["url"] == f"{PUBLIC}/api/webhooks/github/{rid}"
    assert body["webhook_id"]

    hooks = hook_world.github.repo(f"{LOGIN}/hooked").hooks
    assert len(hooks) == 1
    hook = next(iter(hooks.values()))
    assert hook["config"]["url"] == body["webhook"]["url"]
    assert hook["config"]["content_type"] == "json" and hook["config"]["insecure_ssl"] == "0"
    assert set(hook["events"]) == {
        "push", "pull_request", "pull_request_review", "pull_request_review_comment", "issues",
        "issue_comment", "check_suite", "check_run", "status", "workflow_run", "dependabot_alert",
    }  # fmt: skip
    # the secret GitHub got is the one in the vault, encrypted, 32 random bytes' worth
    secret = stored_secret(app, rid)
    assert hook["config"]["secret"] == secret and len(secret) >= 40

    # the secret never leaves the vault: not in responses, not in the audit trail, not in the feed
    feed = client.get(api(cid, f"/repositories/{rid}/activity")).text
    audit = json.dumps([e.data for e in audit_actions(app, cid, prefix="")])
    assert secret not in resp.text + feed + audit
    assert "repository.webhook_enabled" in [e.action for e in audit_actions(app, cid, rid)]
    # enabling twice with the same URL is a no-op (one hook, one secret)
    assert enable(client, cid, rid).status_code == 200
    assert len(hook_world.github.repo(f"{LOGIN}/hooked").hooks) == 1
    assert stored_secret(app, rid) == secret


def test_a_token_without_the_webhooks_permission_gets_a_clear_refusal(
    client, company, m14, app, stable_url
):
    cid = company["id"]
    account = github_account(client, cid, m14, label="plain")  # no Webhooks permission
    m14.add_repo(f"{LOGIN}/nohook")
    rid = post_repo(client, cid, connect_body(account, f"{LOGIN}/nohook")).json()["id"]
    set_repo(app, rid, status="active")
    resp = enable(client, cid, rid)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["reason"] == "webhook_permission_missing"
    assert repo_row(app, rid).webhook_id is None


def test_only_owners_and_admins_change_the_webhook_and_it_needs_a_working_repository(
    client, client_as, make_user, add_member, company, hook_world, app, stable_url
):
    from tests.conftest import human

    cid = company["id"]
    rid = active_repo(client, cid, hook_world, app)
    viewer = make_user("viewer-hook@x.test")
    add_member(cid, viewer.id, "viewer")
    assert enable(client_as(human(viewer)), cid, rid).status_code == 403
    set_repo(app, rid, status="cloning")
    assert enable(client, cid, rid).status_code == 409


# ------------------------------------------------------------------ deliveries


def deliver(anon_client, rid, body: bytes, headers: dict[str, str]):
    return anon_client.post(f"/api/webhooks/github/{rid}", content=body, headers=headers)


@pytest.fixture
def live_hook(client, company, hook_world, app, stable_url):
    cid = company["id"]
    rid = active_repo(client, cid, hook_world, app)
    assert enable(client, cid, rid).status_code == 200
    set_repo(app, rid, next_sync_at=None)
    return rid, stored_secret(app, rid)


def test_a_valid_delivery_pokes_a_sync_and_is_accepted(anon_client, live_hook, app):
    rid, secret = live_hook
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    resp = deliver(anon_client, rid, body, sign(secret, body))
    assert resp.status_code == 202 and resp.json() == {"accepted": True}
    assert repo_row(app, rid).next_sync_at is not None


def test_the_payload_is_ignored_entirely(anon_client, live_hook, app):
    rid, secret = live_hook
    before = repo_row(app, rid)
    # not even JSON: signed garbage is accepted, and nothing but the sync poke happens
    junk = b"\x00\xff not json at all {{{"
    assert deliver(anon_client, rid, junk, sign(secret, junk)).status_code == 202
    # a payload that claims to be about another repository or tells us what to do changes nothing
    lie = json.dumps(
        {"repository": {"full_name": "evil/other"}, "action": "merge", "ref": "refs/heads/x",
         "pull_request": {"merged": True, "number": 7}}
    ).encode()  # fmt: skip
    assert deliver(anon_client, rid, lie, sign(secret, lie)).status_code == 202
    after = repo_row(app, rid)
    for field in ("full_name", "default_branch", "work_branch", "status", "policy", "sync_state"):
        assert getattr(after, field) == getattr(before, field), field


def test_a_ping_is_accepted_without_a_sync(anon_client, live_hook, app):
    rid, secret = live_hook
    body = b'{"zen": "Keep it logically awesome."}'
    headers = {**sign(secret, body), "X-GitHub-Event": "ping"}
    assert deliver(anon_client, rid, body, headers).status_code == 202
    assert repo_row(app, rid).next_sync_at is None


def test_a_paused_repository_is_not_synced_by_a_delivery(
    client, company, anon_client, live_hook, app
):
    rid, secret = live_hook
    set_repo(app, rid, status="paused")
    body = b"{}"
    assert deliver(anon_client, rid, body, sign(secret, body)).status_code == 202
    assert repo_row(app, rid).next_sync_at is None


def test_every_way_of_being_rejected_looks_the_same(
    anon_client, client, company, live_hook, app, m14
):
    rid, secret = live_hook
    cid = company["id"]
    body = b'{"a": 1}'
    good = sign(secret, body)
    wrong_secret = sign("not-the-secret", body)
    tampered = {**good, "X-Hub-Signature-256": good["X-Hub-Signature-256"][:-1] + "0"}
    no_prefix = {**good, "X-Hub-Signature-256": good["X-Hub-Signature-256"][len("sha256=") :]}
    sha1 = {"X-Hub-Signature": "sha1=abc"}
    cases = [
        (rid, body, wrong_secret),
        (rid, body, tampered),
        (rid, body, no_prefix),
        (rid, body, sha1),
        (rid, body, {}),
        (rid, b'{"a": 2}', good),  # the signature of another body
        ("00000000-0000-0000-0000-000000000001", body, good),  # unknown repository
    ]
    # a repository without a webhook (no secret at all)
    plain = active_repo(client, cid, m14, app, token=None, name="nohook2")
    cases.append((plain, body, good))
    answers = {
        (r.status_code, json.dumps(r.json(), sort_keys=True))
        for r in (deliver(anon_client, r_id, b, h) for r_id, b, h in cases)
    }
    assert len(answers) == 1
    status, text = answers.pop()
    assert status == 401 and json.loads(text)["error"]["code"] == "webhook_rejected"
    assert repo_row(app, rid).next_sync_at is None  # nothing was poked


def test_the_body_is_capped(anon_client, live_hook, app):
    rid, secret = live_hook
    limit = app.state.settings.webhook_max_bytes
    body = b"x" * (limit + 1)
    resp = deliver(anon_client, rid, body, sign(secret, body))
    assert resp.status_code == 413
    assert repo_row(app, rid).next_sync_at is None
    # the cap is checked before the signature, so it cannot be used to probe for a secret
    unsigned = deliver(anon_client, rid, body, {})
    assert unsigned.status_code == 413


# ------------------------------------------------------------------ disabling and URL changes


def test_disabling_deletes_the_hook_and_revokes_the_secret(
    client, company, anon_client, live_hook, hook_world, app
):
    rid, secret = live_hook
    cid = company["id"]
    row = repo_row(app, rid)
    resp = enable(client, cid, rid, False)
    assert resp.status_code == 200
    assert resp.json()["webhook"] == {"enabled": False, "url": None, "stale": False}
    assert hook_world.github.repo(f"{LOGIN}/hooked").hooks == {}
    with app.state.session_factory() as db:
        assert db.get(Credential, row.webhook_secret_credential_id).revoked_at is not None
    assert repo_row(app, rid).webhook_secret_credential_id is None
    body = b"{}"
    assert deliver(anon_client, rid, body, sign(secret, body)).status_code == 401
    event = [e for e in audit_actions(app, cid, rid) if e.action == "repository.webhook_disabled"][
        0
    ]
    assert event.data["hook_deleted_on_github"] is True
    assert secret not in json.dumps(event.data)
    # disabling again changes nothing
    assert enable(client, cid, rid, False).status_code == 200
    assert [e.action for e in audit_actions(app, cid, rid)].count(
        "repository.webhook_disabled"
    ) == 1


def test_a_changed_public_url_shows_the_hook_as_stale_and_reenabling_replaces_it(
    client, company, live_hook, hook_world, app, monkeypatch
):
    rid, old_secret = live_hook
    cid = company["id"]
    with app.state.session_factory() as db:
        reachability._save_url_state(db, {"last_url": "https://new.example.test"})
        db.commit()
    shown = client.get(api(cid, f"/repositories/{rid}")).json()
    assert shown["webhook"]["enabled"] is True and shown["webhook"]["stale"] is True
    monkeypatch.setattr(reachability, "public_url", lambda ic: "https://new.example.test")
    resp = enable(client, cid, rid)
    assert resp.status_code == 200
    assert resp.json()["webhook"]["url"] == f"https://new.example.test/api/webhooks/github/{rid}"
    assert resp.json()["webhook"]["stale"] is False
    hooks = hook_world.github.repo(f"{LOGIN}/hooked").hooks
    assert len(hooks) == 1  # the old hook was removed
    new_secret = stored_secret(app, rid)
    assert new_secret != old_secret
    with app.state.session_factory() as db:
        revoked = [c for c in db.scalars(select(Credential)) if c.revoked_at is not None]
        assert any(c.name.startswith("GitHub webhook secret") for c in revoked)


def test_disconnecting_stops_the_webhook_from_working(client, company, anon_client, live_hook, app):
    rid, secret = live_hook
    cid = company["id"]
    assert (
        client.post(api(cid, f"/repositories/{rid}/disconnect"), json={"confirm": True}).status_code
        == 200
    )
    body = b"{}"
    assert deliver(anon_client, rid, body, sign(secret, body)).status_code == 401
    event = [e for e in audit_actions(app, cid, rid) if e.action == "repository.disconnected"][0]
    assert event.data["webhook_left_on_github"] is True  # reported: the hook stays on GitHub
