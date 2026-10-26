"""Passkeys (WebAuthn): real registration and login ceremonies against a software authenticator."""

from __future__ import annotations

from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import create_engine, func, inspect, select, update

from alembic import command
from app.core.config import Settings
from app.core.time import utcnow
from app.main import create_app
from app.models.accounts import AuthSession, LoginThrottle, Passkey, PasskeyChallenge
from app.models.audit import AuditEvent
from app.models.user import User
from app.runtimes.registry import RuntimeRegistry
from app.services import passkeys as passkey_service
from app.services import reachability
from tests.accounts_helpers import PASSWORD, browser, create_company
from tests.conftest import alembic_config
from tests.passkey_helpers import (
    SoftwareAuthenticator,
    login_with,
    loopback_login,
    loopback_setup,
    register,
)

OPTIONS = "/api/auth/passkeys/register/options"
VERIFY = "/api/auth/passkeys/register/verify"
LOGIN_OPTIONS = "/api/auth/passkey/login/options"
LOGIN_VERIFY = "/api/auth/passkey/login/verify"


@pytest.fixture
def alice(app):
    """The signed-in owner on localhost, with a company (so account events reach an activity feed)."""
    b = loopback_setup(app)
    create_company(b)
    return b


def events(app, action: str) -> list[AuditEvent]:
    with app.state.session_factory() as db:
        return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def stored(app, credential_id: str) -> Passkey:
    with app.state.session_factory() as db:
        row = db.scalar(select(Passkey).where(Passkey.credential_id == credential_id))
        assert row is not None
        db.expunge(row)
        return row


# ------------------------------------------------------------------ registration


def test_registration_options_follow_the_contract(app, alice):
    r = alice.post(OPTIONS, json={"name": "MacBook"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"attempt_id", "public_key"}
    pk = body["public_key"]
    assert pk["rp"] == {"name": "Foundry AI OS", "id": "localhost"}
    assert pk["user"]["name"] == "alice" and pk["user"]["displayName"] == "Alice"
    assert pk["authenticatorSelection"]["residentKey"] == "preferred"
    assert pk["authenticatorSelection"]["userVerification"] == "preferred"
    assert pk["excludeCredentials"] == []
    assert pk["attestation"] == "none"
    assert len(pk["challenge"]) >= 43  # 32+ random bytes, base64url
    assert {p["alg"] for p in pk["pubKeyCredParams"]} >= {-7, -257}
    assert r.headers["cache-control"] == "no-store"
    # the user handle is opaque: not the user id, not the username
    with app.state.session_factory() as db:
        user_id = db.scalar(select(User.id).where(User.username == "alice"))
    assert pk["user"]["id"] != user_id and "alice" not in pk["user"]["id"]


def test_register_then_list_rename_and_audit(app, alice):
    auth = SoftwareAuthenticator(backed_up=True)
    info = register(alice, auth, name="  MacBook   Touch ID ")
    assert set(info) == {"id", "name", "created_at", "last_used_at", "transports", "backed_up"}
    assert info["name"] == "MacBook Touch ID"
    assert info["last_used_at"] is None
    assert info["transports"] == ["hybrid", "internal"] and info["backed_up"] is True
    row = stored(app, auth.id)
    assert row.sign_count == 0 and row.aaguid and row.public_key and row.user_id
    # stored data is public material only
    assert alice.get("/api/auth/passkeys").json() == {"items": [info], "next_cursor": None}
    assert alice.get("/api/auth/session").json()["passkeys_count"] == 1

    r = alice.patch(f"/api/auth/passkeys/{info['id']}", json={"name": "Work laptop"})
    assert r.status_code == 200 and r.json()["name"] == "Work laptop"
    assert [e.summary for e in events(app, "auth.passkey_added")] == [
        "Passkey 'MacBook Touch ID' added"
    ]
    assert len(events(app, "auth.passkey_renamed")) == 1
    # an audit record never contains key material
    blob = " ".join(str(e.data) + e.summary for e in events(app, "auth.passkey_added"))
    assert auth.id not in blob


def test_a_registered_credential_is_excluded_from_the_next_registration(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    pk = alice.post(OPTIONS, json={}).json()["public_key"]
    assert [c["id"] for c in pk["excludeCredentials"]] == [auth.id]
    assert pk["excludeCredentials"][0]["transports"] == ["hybrid", "internal"]


def test_registering_the_same_credential_twice_conflicts(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    opts = alice.post(OPTIONS, json={}).json()
    r = alice.post(
        VERIFY,
        json={"attempt_id": opts["attempt_id"], "credential": auth.create(opts["public_key"])},
    )
    assert r.status_code == 409


def test_registration_needs_the_csrf_header(app, alice):
    alice.auto_csrf = False
    r = alice.post(OPTIONS, json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    alice.auto_csrf = True
    opts = alice.post(OPTIONS, json={}).json()
    auth = SoftwareAuthenticator()
    alice.auto_csrf = False
    r = alice.post(
        VERIFY,
        json={"attempt_id": opts["attempt_id"], "credential": auth.create(opts["public_key"])},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    alice.auto_csrf = True
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Passkey.id))) == 0


def test_registration_challenge_is_single_use_even_after_a_failure(app, alice):
    opts = alice.post(OPTIONS, json={}).json()
    auth = SoftwareAuthenticator()
    good = auth.create(opts["public_key"])
    bad = SoftwareAuthenticator().create(opts["public_key"], origin="http://evil.example")
    r = alice.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": bad})
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "verification_failed"
    # the failed attempt burnt the challenge: the genuine response cannot be replayed into it
    r = alice.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": good})
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "challenge_used"
    # and a success cannot be replayed either
    opts = alice.post(OPTIONS, json={}).json()
    good = auth.create(opts["public_key"])
    assert (
        alice.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": good}).status_code
        == 201
    )
    r = alice.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": good})
    assert r.status_code == 422


def test_registration_challenge_expires_after_five_minutes(app, alice):
    opts = alice.post(OPTIONS, json={}).json()
    with app.state.session_factory() as db:
        row = db.get(PasskeyChallenge, opts["attempt_id"])
        assert row.expires_at - row.created_at == timedelta(minutes=5)
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    auth = SoftwareAuthenticator()
    r = alice.post(
        VERIFY,
        json={"attempt_id": opts["attempt_id"], "credential": auth.create(opts["public_key"])},
    )
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "challenge_expired"


def test_a_challenge_the_client_made_up_is_refused(app, alice):
    opts = alice.post(OPTIONS, json={}).json()
    auth = SoftwareAuthenticator()
    forged = auth.create(
        opts["public_key"], challenge="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    r = alice.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": forged})
    assert r.status_code == 422
    r = alice.post(VERIFY, json={"attempt_id": "no-such-attempt", "credential": forged})
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "challenge_unknown"


def test_a_registration_challenge_is_bound_to_the_user_who_asked(app, alice):
    # a second person with a session on the same machine
    with app.state.session_factory() as db:
        db.add(
            User(
                username="bob",
                display_name="Bob",
                password_hash=app.state.auth.hash_password(PASSWORD),
                password_changed_at=utcnow(),
            )
        )
        db.commit()
    bob = loopback_login(app, "bob")
    opts = alice.post(OPTIONS, json={}).json()
    auth = SoftwareAuthenticator()
    r = bob.post(
        VERIFY,
        json={"attempt_id": opts["attempt_id"], "credential": auth.create(opts["public_key"])},
    )
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "challenge_unknown"
    # and Bob can neither see nor change Alice's passkeys
    info = register(alice, SoftwareAuthenticator())
    assert bob.get("/api/auth/passkeys").json()["items"] == []
    assert bob.patch(f"/api/auth/passkeys/{info['id']}", json={"name": "x"}).status_code == 404
    assert (
        bob.request(
            "DELETE", f"/api/auth/passkeys/{info['id']}", json={"password": PASSWORD}
        ).status_code
        == 404
    )


def test_wrong_origin_and_wrong_rp_id_are_rejected_at_registration(app, alice):
    for kwargs in (
        {"origin": "http://localhost:9999"},
        {"origin": "https://localhost"},
        {"rp_id": "example.com"},
    ):
        opts = alice.post(OPTIONS, json={}).json()
        cred = SoftwareAuthenticator().create(opts["public_key"], **kwargs)
        r = alice.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": cred})
        assert r.status_code == 422, kwargs
    assert alice.get("/api/auth/passkeys").json()["items"] == []


def test_the_registration_options_are_bounded_per_person(app, alice, monkeypatch):
    monkeypatch.setattr(passkey_service, "MAX_PASSKEYS", 2)
    register(alice, SoftwareAuthenticator())
    register(alice, SoftwareAuthenticator())
    assert alice.post(OPTIONS, json={}).status_code == 409


# ------------------------------------------------------------------ login


def test_full_login_ceremony_creates_a_session_like_the_password_login(app, alice):
    auth = SoftwareAuthenticator()
    info = register(alice, auth, name="Phone")
    anon = browser(app, loopback=True)
    r = login_with(anon, auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "authenticated" and body["user"]["username"] == "alice"
    assert body["two_factor_required"] is False and body["expires_at"]
    assert body["passkeys_count"] == 1 and body["passkey_supported_origin"] is True
    assert "cos_session" in anon.cookies and "cos_csrf" in anon.cookies
    assert "HttpOnly" in r.headers["set-cookie"] and r.headers["cache-control"] == "no-store"
    # the cookies work: a state-changing call with CSRF and a read both succeed
    assert anon.get("/api/me").status_code == 200
    assert anon.get("/api/auth/passkeys").json()["items"][0]["id"] == info["id"]
    # counter and last-used were recorded
    row = stored(app, auth.id)
    assert row.sign_count == 1 and row.last_used_at is not None
    assert anon.get("/api/auth/passkeys").json()["items"][0]["last_used_at"] is not None
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(AuthSession.id))) == 2


def test_login_options_without_a_username_are_discoverable(app, alice):
    register(alice, SoftwareAuthenticator())
    r = browser(app, loopback=True).post(LOGIN_OPTIONS, json={})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"attempt_id", "public_key"}
    pk = body["public_key"]
    assert pk["rpId"] == "localhost" and pk["userVerification"] == "preferred"
    assert pk["allowCredentials"] == []
    assert len(pk["challenge"]) >= 43


def test_login_options_with_a_username_list_that_users_passkeys(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    pk = (
        browser(app, loopback=True)
        .post(LOGIN_OPTIONS, json={"username": "Alice"})
        .json()["public_key"]
    )
    assert [c["id"] for c in pk["allowCredentials"]] == [auth.id]
    r = login_with(browser(app, loopback=True), auth, username="alice")
    assert r.status_code == 200


def test_login_options_do_not_reveal_whether_a_username_exists(app, alice):
    real = SoftwareAuthenticator()
    register(alice, real)
    with app.state.session_factory() as db:  # a real account that has no passkey
        db.add(User(username="bob", display_name="Bob"))
        db.commit()
    anon = browser(app, loopback=True)

    def options(username):
        r = anon.post(LOGIN_OPTIONS, json={"username": username})
        assert r.status_code == 200, r.text
        return r.json()

    known, no_passkey, unknown = options("alice"), options("bob"), options("nobody-here")

    def shape(o):
        return (
            set(o),
            set(o["public_key"]),
            {tuple(sorted(c)) for c in o["public_key"]["allowCredentials"]},
        )

    assert shape(known) == shape(no_passkey) == shape(unknown)
    for o in (no_passkey, unknown):
        assert len(o["public_key"]["allowCredentials"]) >= 1  # a decoy list, never an empty one
        assert real.id not in {c["id"] for c in o["public_key"]["allowCredentials"]}
    # decoys are stable (an attacker cannot tell them from real ids by asking twice) and per name
    assert [c["id"] for c in options("nobody-here")["public_key"]["allowCredentials"]] == [
        c["id"] for c in unknown["public_key"]["allowCredentials"]
    ]
    assert [c["id"] for c in options("someone-else")["public_key"]["allowCredentials"]] != [
        c["id"] for c in unknown["public_key"]["allowCredentials"]
    ]
    # nothing about the response differs in status or headers either
    assert anon.post(LOGIN_OPTIONS, json={"username": "bob"}).status_code == 200


def test_login_failures_are_generic(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    stranger = SoftwareAuthenticator()
    stranger.user_handle = auth.user_handle
    anon = browser(app, loopback=True)
    bodies = []
    # unknown credential, wrong signature (another key under the real credential id), wrong origin
    forged = SoftwareAuthenticator()
    forged.credential_id = auth.credential_id
    for authenticator, kwargs in (
        (stranger, {}),
        (forged, {}),
        (auth, {"origin": "http://evil.example"}),
        (auth, {"rp_id": "example.com"}),
    ):
        r = login_with(anon, authenticator, **kwargs)
        assert r.status_code == 401, (authenticator, kwargs, r.text)
        bodies.append(r.json())
    assert all(b == bodies[0] for b in bodies)
    assert bodies[0]["error"]["code"] == "invalid_credentials"
    assert "cos_session" not in anon.cookies
    # the genuine authenticator still signs in afterwards
    assert login_with(browser(app, loopback=True), auth).status_code == 200


def test_login_challenge_is_single_use_and_expires(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    anon = browser(app, loopback=True)
    opts = anon.post(LOGIN_OPTIONS, json={}).json()
    first = auth.get(opts["public_key"])
    body = {"attempt_id": opts["attempt_id"], "credential": first}
    assert anon.post(LOGIN_VERIFY, json=body).status_code == 200
    # replay of the very same (valid) assertion
    assert browser(app, loopback=True).post(LOGIN_VERIFY, json=body).status_code == 401
    # expiry
    opts = anon.post(LOGIN_OPTIONS, json={}).json()
    with app.state.session_factory() as db:
        db.execute(
            update(PasskeyChallenge)
            .where(PasskeyChallenge.id == opts["attempt_id"])
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        db.commit()
    r = browser(app, loopback=True).post(
        LOGIN_VERIFY,
        json={"attempt_id": opts["attempt_id"], "credential": auth.get(opts["public_key"])},
    )
    assert r.status_code == 401
    # a challenge issued for registration cannot be used to log in
    reg = alice.post(OPTIONS, json={}).json()
    cred = auth.get({"challenge": reg["public_key"]["challenge"], "rpId": "localhost"})
    r = browser(app, loopback=True).post(
        LOGIN_VERIFY, json={"attempt_id": reg["attempt_id"], "credential": cred}
    )
    assert r.status_code == 401


def test_a_failed_login_burns_the_challenge(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    anon = browser(app, loopback=True)
    opts = anon.post(LOGIN_OPTIONS, json={}).json()
    bad = auth.get(opts["public_key"], origin="http://evil.example")
    assert (
        anon.post(
            LOGIN_VERIFY, json={"attempt_id": opts["attempt_id"], "credential": bad}
        ).status_code
        == 401
    )
    good = auth.get(opts["public_key"])
    assert (
        anon.post(
            LOGIN_VERIFY, json={"attempt_id": opts["attempt_id"], "credential": good}
        ).status_code
        == 401
    )


def test_clone_detection_refuses_a_stale_counter(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    assert login_with(browser(app, loopback=True), auth).status_code == 200  # counter 1
    assert login_with(browser(app, loopback=True), auth).status_code == 200  # counter 2
    assert stored(app, auth.id).sign_count == 2
    for stale in (1, 2, 0):  # lower, equal, and a counter that went back to zero
        r = login_with(browser(app, loopback=True), auth, counter=stale)
        assert r.status_code == 401, stale
        assert r.json()["error"]["code"] == "invalid_credentials"
    assert stored(app, auth.id).sign_count == 2  # untouched
    assert len(events(app, "auth.passkey_clone_suspected")) == 3
    assert login_with(browser(app, loopback=True), auth, counter=3).status_code == 200
    assert stored(app, auth.id).sign_count == 3


def test_an_authenticator_without_a_counter_keeps_working(app, alice):
    auth = SoftwareAuthenticator()  # many platform passkeys always report 0
    register(alice, auth)
    for _ in range(3):
        assert login_with(browser(app, loopback=True), auth, counter=0).status_code == 200
    assert stored(app, auth.id).sign_count == 0


def test_a_user_handle_that_is_not_the_users_is_refused(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    auth.user_handle = b"someone else entirely"
    assert login_with(browser(app, loopback=True), auth).status_code == 401


def test_a_passkey_login_satisfies_two_factor(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    enroll = alice.post("/api/auth/2fa/enroll").json()
    assert (
        alice.post(
            "/api/auth/2fa/verify", json={"code": pyotp.TOTP(enroll["secret"]).now()}
        ).status_code
        == 200
    )
    # the password alone now asks for a code ...
    anon = browser(app, loopback=True)
    r = anon.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.json()["two_factor_required"] is True
    # ... a passkey does not
    r = login_with(browser(app, loopback=True), auth)
    assert r.status_code == 200 and r.json()["state"] == "authenticated"
    assert r.json()["user"]["two_factor_enabled"] is True


def test_passkey_login_is_rate_limited_per_credential_like_the_password_login(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    anon = browser(app, loopback=True)
    for _ in range(4):  # 3 free failures for the credential, the 4th starts the backoff
        assert login_with(anon, auth, origin="http://evil.example").status_code == 401
    r = login_with(anon, auth)  # even the genuine assertion is refused while locked
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert r.json()["error"]["details"]["retry_after_seconds"] >= 1
    assert "retry-after" in r.headers
    with app.state.session_factory() as db:
        keys = set(db.scalars(select(LoginThrottle.key)))
    assert any(k.startswith("pk:") for k in keys) and any(k.startswith("ip:") for k in keys)
    assert not any(auth.id in k for k in keys)  # the credential id itself is not stored in the key


def test_passkey_login_is_rate_limited_per_ip(app, make_settings, scripted):
    limited = create_app(
        make_settings(login_free_attempts=1), runtime_registry=RuntimeRegistry([scripted])
    )
    loopback_setup(limited)
    anon = browser(limited, loopback=True)
    for _ in range(4):  # a new unknown credential each time: only the IP bucket fills (3 free)
        assert login_with(anon, SoftwareAuthenticator()).status_code == 401
    assert login_with(anon, SoftwareAuthenticator()).status_code == 429
    # password logins from the same address are throttled by the same IP bucket
    r = anon.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 429


def test_agents_cannot_use_passkey_login_with_a_run_token(app, alice):
    from tests.helpers import new_agent  # noqa: PLC0415
    from tests.test_agent_api import start_run  # noqa: PLC0415

    cid = alice.get("/api/me").json()["memberships"][0]["company_id"]
    agent = new_agent(alice, cid, name="Nova", script={})
    token = start_run(app, alice, cid, agent["id"]).token
    anon = browser(app, loopback=True)
    r = anon.post(LOGIN_OPTIONS, json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    r = anon.post(
        LOGIN_VERIFY,
        json={"attempt_id": "x", "credential": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


# ------------------------------------------------------------------ managing passkeys


def age_session(app, minutes: int) -> None:
    with app.state.session_factory() as db:
        for s in db.scalars(select(AuthSession)):
            s.created_at = utcnow() - timedelta(minutes=minutes)
        db.commit()


def test_removing_a_passkey_needs_a_fresh_session_or_the_password(app, alice):
    info = register(alice, SoftwareAuthenticator())
    other = register(alice, SoftwareAuthenticator())
    url = f"/api/auth/passkeys/{info['id']}"
    age_session(app, 11)
    r = alice.delete(url)
    assert (
        r.status_code == 403
        and r.json()["error"]["details"]["reason"] == "reauthentication_required"
    )
    r = alice.request("DELETE", url, json={"password": "not the password at all"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert len(alice.get("/api/auth/passkeys").json()["items"]) == 2
    r = alice.request("DELETE", url, json={"password": PASSWORD})
    assert r.status_code == 204
    assert [p["id"] for p in alice.get("/api/auth/passkeys").json()["items"]] == [other["id"]]
    assert len(events(app, "auth.passkey_removed")) == 1
    # a session younger than ten minutes needs neither
    age_session(app, 9)
    assert register(alice, SoftwareAuthenticator())
    assert alice.delete(f"/api/auth/passkeys/{other['id']}").status_code == 204
    assert alice.delete(f"/api/auth/passkeys/{other['id']}").status_code == 404


def test_the_password_check_for_removal_is_throttled(app, alice):
    info = register(alice, SoftwareAuthenticator())
    register(alice, SoftwareAuthenticator())
    age_session(app, 30)
    url = f"/api/auth/passkeys/{info['id']}"
    for _ in range(4):
        assert (
            alice.request("DELETE", url, json={"password": "wrong wrong wrong"}).status_code == 401
        )
    assert alice.request("DELETE", url, json={"password": PASSWORD}).status_code == 429


def test_the_last_sign_in_method_cannot_be_removed(app, alice):
    first = register(alice, SoftwareAuthenticator())
    second = register(alice, SoftwareAuthenticator())
    # with a password, even the last passkey can go
    assert alice.delete(f"/api/auth/passkeys/{first['id']}").status_code == 204
    # a person with no password has the passkeys as their only way in
    with app.state.session_factory() as db:
        db.execute(update(User).where(User.username == "alice").values(password_hash=None))
        db.commit()
    r = alice.delete(f"/api/auth/passkeys/{second['id']}")
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "last_sign_in_method"
    other = register(alice, SoftwareAuthenticator())
    assert (
        alice.delete(f"/api/auth/passkeys/{second['id']}").status_code == 204
    )  # not the last any more
    assert alice.delete(f"/api/auth/passkeys/{other['id']}").status_code == 409
    assert len(alice.get("/api/auth/passkeys").json()["items"]) == 1


def test_rename_validates_the_name(app, alice):
    info = register(alice, SoftwareAuthenticator())
    assert alice.patch(f"/api/auth/passkeys/{info['id']}", json={"name": ""}).status_code == 422
    assert (
        alice.patch(f"/api/auth/passkeys/{info['id']}", json={"name": "x" * 81}).status_code == 422
    )
    assert (
        alice.patch(f"/api/auth/passkeys/{info['id']}", json={"name": "  Key  "}).json()["name"]
        == "Key"
    )
    assert alice.patch("/api/auth/passkeys/missing", json={"name": "x"}).status_code == 404


# ------------------------------------------------------------------ who may call what


PASSKEY_ROUTES = [
    ("GET", "/api/auth/passkeys", None),
    ("POST", OPTIONS, {}),
    ("POST", VERIFY, {"attempt_id": "x", "credential": {}}),
    ("PATCH", "/api/auth/passkeys/abc", {"name": "x"}),
    ("DELETE", "/api/auth/passkeys/abc", None),
]


def test_passkey_management_is_closed_to_anonymous_callers(app):
    anon = browser(app, loopback=True)
    for method, path, body in PASSKEY_ROUTES:
        r = anon.request(method, path, json=body)
        assert r.status_code == 401, (method, path)


def test_api_tokens_and_the_owner_token_cannot_manage_passkeys(app, alice):
    token = alice.post("/api/auth/tokens", json={"name": "ci", "scope": "admin"}).json()["token"]
    for credential, expected in (
        (token, "token_cannot_manage_account"),
        (app.state.owner_token.get(), "passkeys_need_session"),
    ):
        c = browser(app, loopback=True)
        for method, path, body in PASSKEY_ROUTES:
            r = c.request(
                method, path, json=body, headers={"Authorization": f"Bearer {credential}"}
            )
            assert r.status_code == 403, (method, path, r.text)
            assert r.json()["error"]["details"]["reason"] == expected, (method, path)


def test_agent_run_tokens_are_denied_on_every_passkey_route(app, alice):
    from app.models.guardrails import GuardrailEvent  # noqa: PLC0415
    from tests.helpers import new_agent  # noqa: PLC0415
    from tests.test_agent_api import start_run  # noqa: PLC0415

    cid = alice.get("/api/me").json()["memberships"][0]["company_id"]
    agent = new_agent(alice, cid, name="Nova", script={})
    token = start_run(app, alice, cid, agent["id"]).token
    info = register(alice, SoftwareAuthenticator())
    c = browser(app, loopback=True)
    for method, path, body in PASSKEY_ROUTES:
        r = c.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["code"] == "guardrail_denied", (method, path)
    assert len(alice.get("/api/auth/passkeys").json()["items"]) == 1 and info
    with app.state.session_factory() as db:
        denied = set(
            db.scalars(select(GuardrailEvent.action).where(GuardrailEvent.decision == "deny"))
        )
    assert "passkey.manage" in denied


def test_every_passkey_route_sits_behind_a_human_gate(app):
    from app.api.routes import auth  # noqa: PLC0415
    from tests.test_accounts_work import _gates  # noqa: PLC0415

    seen = set()
    for route in auth.router.routes:
        if "passkey" not in route.path:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            names = " ".join(_gates(route.dependant))
            assert any(g in names for g in ("account_user", "reject_agent_credentials")), (
                method,
                route.path,
            )
            seen.add((method, route.path))
    assert len(seen) == 7


# ------------------------------------------------------------------ RP id and origins


def test_a_lan_http_origin_gets_no_webauthn(app, alice):
    lan = browser(app)  # http://testserver, a peer on the LAN
    assert lan.get("/api/auth/session").json()["passkey_supported_origin"] is False
    r = lan.post(LOGIN_OPTIONS, json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_origin_unsupported"
    for host in ("192.168.1.20:8700", "my-mac.local:8700", "[::1]:8700"):
        c = browser(app, base_url=f"http://{host}")
        assert c.post(LOGIN_OPTIONS, json={}).status_code == 400, host
        assert c.get("/api/auth/session").json()["passkey_supported_origin"] is False
    # signed in over the LAN: no registration either
    r = browser(app).post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200
    # localhost and 127.0.0.1 both use the RP id "localhost"
    for host in ("localhost:8700", "127.0.0.1:8700", "localhost:5173"):
        c = browser(app, base_url=f"http://{host}", client=("127.0.0.1", 5000))
        assert c.get("/api/auth/session").json()["passkey_supported_origin"] is True, host
        assert c.post(LOGIN_OPTIONS, json={}).json()["public_key"]["rpId"] == "localhost"


def test_the_vite_dev_origin_is_accepted_on_localhost(app, alice):
    auth = SoftwareAuthenticator()
    dev = browser(app, base_url="http://localhost:5173", client=("127.0.0.1", 5001))
    assert (
        dev.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code
        == 200
    )
    register(dev, auth, origin="http://localhost:5173")
    assert (
        login_with(
            browser(app, base_url="http://localhost:5173", client=("127.0.0.1", 1)),
            auth,
            origin="http://localhost:5173",
        ).status_code
        == 200
    )


def test_a_passkey_made_for_another_rp_id_does_not_sign_in_here(app, alice):
    auth = SoftwareAuthenticator()
    register(alice, auth)
    r = login_with(browser(app, loopback=True), auth, rp_id="evil.example")
    assert r.status_code == 401


@pytest.fixture
def https_app(make_settings, scripted):
    return create_app(
        make_settings(passkey_origins=["https://os.example.com"]),
        runtime_registry=RuntimeRegistry([scripted]),
    )


def https_browser(app, host="os.example.com"):
    return browser(app, base_url=f"https://{host}", client=("203.0.113.9", 4000))


def test_a_configured_https_origin_works_end_to_end(https_app):
    app = https_app
    loopback_setup(app)
    auth = SoftwareAuthenticator()
    origin = "https://os.example.com"
    web = https_browser(app)
    assert web.get("/api/auth/session").json()["passkey_supported_origin"] is True
    assert (
        web.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code
        == 200
    )
    opts = web.post(OPTIONS, json={}).json()
    assert opts["public_key"]["rp"]["id"] == "os.example.com"
    cred = auth.create(opts["public_key"], origin=origin)
    assert (
        web.post(VERIFY, json={"attempt_id": opts["attempt_id"], "credential": cred}).status_code
        == 201
    )
    r = login_with(https_browser(app), auth, origin=origin)
    assert r.status_code == 200 and r.json()["passkey_supported_origin"] is True
    # a passkey bound to os.example.com is not usable from localhost (different RP id) ...
    assert login_with(browser(app, loopback=True), auth, origin=origin).status_code == 401
    # ... nor from an origin that merely claims to be it
    spoof = login_with(https_browser(app), auth, origin="https://evil.example")
    assert spoof.status_code == 401
    assert login_with(https_browser(app), auth, origin="http://os.example.com").status_code == 401
    # another host name on the same server is not an origin of ours
    other = https_browser(app, "other.example.com")
    assert other.get("/api/auth/session").json()["passkey_supported_origin"] is False
    assert other.post(LOGIN_OPTIONS, json={}).status_code == 400


def test_a_running_named_tunnel_url_becomes_an_allowed_origin(app, monkeypatch):
    loopback_setup(app)
    tunnel = browser(app, base_url="https://os.acme.example", client=("127.0.0.1", 9))
    assert tunnel.post(LOGIN_OPTIONS, json={}).status_code == 400  # no tunnel yet
    monkeypatch.setattr(reachability, "public_url", lambda ic: "https://os.acme.example")
    r = tunnel.post(LOGIN_OPTIONS, json={})
    assert r.status_code == 200 and r.json()["public_key"]["rpId"] == "os.acme.example"
    s = tunnel.get("/api/auth/session").json()
    assert s["passkey_supported_origin"] is True and s["passkey_unsupported_reason"] is None


def test_a_quick_tunnel_address_gets_no_passkeys(app, monkeypatch):
    """A trycloudflare.com name changes on every restart: a passkey bound to it would break."""
    loopback_setup(app)
    url = "https://abc-def.trycloudflare.com"
    tunnel = browser(app, base_url=url, client=("127.0.0.1", 9))
    for running in (False, True):  # the host alone decides, running tunnel or not
        if running:
            monkeypatch.setattr(reachability, "public_url", lambda ic: url)
            monkeypatch.setattr(reachability, "tunnel_is_ephemeral", lambda ic: True)
        s = tunnel.get("/api/auth/session").json()
        assert s["passkey_supported_origin"] is False
        assert s["passkey_unsupported_reason"] == "ephemeral_origin"
        r = tunnel.post(LOGIN_OPTIONS, json={})
        assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_origin_unsupported"
    # even an operator-configured origin cannot make a quick-tunnel host a relying party
    app.state.settings.passkey_origins = [url]
    assert tunnel.post(LOGIN_OPTIONS, json={}).status_code == 400


def test_a_passkey_cannot_be_added_through_a_quick_tunnel(app, alice):
    host = {"Host": "abc-def.trycloudflare.com"}
    r = alice.post(OPTIONS, json={"name": "Laptop"}, headers=host)
    assert r.status_code == 400 and r.json()["error"]["code"] == "passkey_origin_unsupported"
    assert alice.get("/api/auth/passkeys").json()["items"] == []
    # on localhost it still works, and the session says why/where passkeys are fine
    assert alice.get("/api/auth/session").json()["passkey_unsupported_reason"] is None
    assert alice.post(OPTIONS, json={"name": "Laptop"}).status_code == 200


def test_the_session_reports_the_reason_only_for_quick_tunnel_hosts(app, alice):
    s = alice.get("/api/auth/session", headers={"Host": "abc.trycloudflare.com"}).json()
    assert s["passkey_supported_origin"] is False and s["passkey_unsupported_reason"] == (
        "ephemeral_origin"
    )
    s = alice.get("/api/auth/session", headers={"Host": "192.168.1.20:8700"}).json()
    assert s["passkey_supported_origin"] is False and s["passkey_unsupported_reason"] is None


def test_a_custom_domain_becomes_an_allowed_origin(app):
    from app.models.reachability import CompanyDomain  # noqa: PLC0415

    b = loopback_setup(app)
    cid = create_company(b)["id"]
    with app.state.session_factory() as db:
        db.add(CompanyDomain(company_id=cid, domain="os.acme.example"))
        db.commit()
    web = https_browser(app, "os.acme.example")
    assert web.post(LOGIN_OPTIONS, json={}).json()["public_key"]["rpId"] == "os.acme.example"


def test_passkey_origins_setting_accepts_a_comma_list_or_json(monkeypatch):
    monkeypatch.setenv("COMPANY_OS_PASSKEY_ORIGINS", "https://a.example.com, https://b.example.com")
    assert Settings(_env_file=None).passkey_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]
    monkeypatch.setenv("COMPANY_OS_PASSKEY_ORIGINS", '["https://c.example.com"]')
    assert Settings(_env_file=None).passkey_origins == ["https://c.example.com"]
    monkeypatch.delenv("COMPANY_OS_PASSKEY_ORIGINS")
    assert Settings(_env_file=None).passkey_origins == []


def test_origin_normalisation():
    n = passkey_service.normalize_origin
    assert n("https://Os.Example.com:443/path?x=1") == "https://os.example.com"
    assert n("https://os.example.com:8443") == "https://os.example.com:8443"
    assert n("ftp://x.example.com") is None and n("not a url") is None and n("") is None


# ------------------------------------------------------------------ exposure gate


def test_a_passkey_counts_as_strong_authentication_for_the_exposure_gate(app, alice):
    with app.state.session_factory() as db:  # a password that was not set under the policy
        db.execute(update(User).where(User.username == "alice").values(password_changed_at=None))
        db.commit()
    status = alice.get("/api/reachability").json()
    assert status["public_exposure_blocked_code"] == "weak_password"
    assert status["public_exposure_advice"] is None
    register(alice, SoftwareAuthenticator())
    status = alice.get("/api/reachability").json()
    assert status["public_exposure_blocked_code"] is None
    assert status["public_exposure_advice"] is None  # nothing more to recommend
    # removing it again restores the block
    pid = alice.get("/api/auth/passkeys").json()["items"][0]["id"]
    assert alice.delete(f"/api/auth/passkeys/{pid}").status_code == 204
    assert alice.get("/api/reachability").json()["public_exposure_blocked_code"] == "weak_password"


def test_a_passkey_from_a_quick_tunnel_origin_is_not_strong_authentication(app, alice):
    """Passkeys added there (before registration was refused) break when the URL changes."""
    with app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.username == "alice"))
        owner.password_changed_at = None
        db.add(
            Passkey(
                id="pk-ephemeral",
                user_id=owner.id,
                credential_id="cred-ephemeral",
                public_key=b"x",
                name="Quick",
            )
        )
        db.commit()
        company_id = db.scalar(select(AuditEvent.company_id).limit(1))
        from app.services import audit  # noqa: PLC0415

        audit.record(
            db,
            company_id=company_id,
            principal=None,
            action="auth.passkey_added",
            target_type="user",
            target_id=owner.id,
            summary="Passkey 'Quick' added",
            data={"passkey_id": "pk-ephemeral", "rp_id": "abc-def.trycloudflare.com"},
        )
        db.commit()
    status = alice.get("/api/reachability").json()
    assert status["public_exposure_blocked_code"] == "weak_password"  # it does not count
    register(alice, SoftwareAuthenticator())  # a localhost passkey does
    assert alice.get("/api/reachability").json()["public_exposure_blocked_code"] is None
    added = [
        e.data for e in events(app, "auth.passkey_added") if e.data.get("rp_id") == "localhost"
    ]
    assert len(added) == 1  # the relying party is recorded with every new passkey


# ------------------------------------------------------------------ migration


def test_migration_0010_creates_and_drops_the_passkey_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'pk.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0009")

    def names():
        engine = create_engine(url)
        try:
            return set(inspect(engine).get_table_names()), inspect(engine)
        finally:
            engine.dispose()

    assert not {"passkeys", "passkey_challenges"} & names()[0]
    command.upgrade(cfg, "head")
    tables, _ = names()
    assert {"passkeys", "passkey_challenges"} <= tables
    engine = create_engine(url)
    insp = inspect(engine)
    assert {c["name"] for c in insp.get_columns("passkeys")} == {
        "id", "user_id", "credential_id", "public_key", "sign_count", "transports",
        "aaguid", "name", "backed_up", "created_at", "last_used_at",
    }  # fmt: skip
    assert any(
        u["column_names"] == ["credential_id"] for u in insp.get_unique_constraints("passkeys")
    )
    engine.dispose()
    command.downgrade(cfg, "0009")
    assert not {"passkeys", "passkey_challenges"} & names()[0]
    assert "users" in names()[0]  # the rest is intact
    command.upgrade(cfg, "head")
    assert {"passkeys", "passkey_challenges"} <= names()[0]


def test_expired_challenges_are_swept_and_anonymous_ones_are_capped(app, alice, monkeypatch):
    anon = browser(app, loopback=True)
    opts = anon.post(LOGIN_OPTIONS, json={}).json()
    with app.state.session_factory() as db:
        db.execute(
            update(PasskeyChallenge)
            .where(PasskeyChallenge.id == opts["attempt_id"])
            .values(expires_at=utcnow() - timedelta(hours=2))
        )
        db.commit()
    anon.post(LOGIN_OPTIONS, json={})
    with app.state.session_factory() as db:
        assert db.get(PasskeyChallenge, opts["attempt_id"]) is None
    monkeypatch.setattr(passkey_service, "MAX_OUTSTANDING_LOGIN_CHALLENGES", 2)
    assert anon.post(LOGIN_OPTIONS, json={}).status_code == 200  # 1 already outstanding
    r = anon.post(LOGIN_OPTIONS, json={})
    assert r.status_code == 429
