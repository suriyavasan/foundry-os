"""M7 accounts: setup, passwords, sessions, CSRF, login throttling, API tokens, 2FA, local mode."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pyotp
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.time import utcnow
from app.main import create_app
from app.models.accounts import ApiToken, AuthSession, LoginThrottle
from app.models.user import User
from app.runtimes.registry import RuntimeRegistry
from tests.accounts_helpers import (
    PASSWORD,
    PASSWORD2,
    browser,
    create_company,
    do_setup,
    login,
    setup_body,
)


def with_db(app):
    return app.state.session_factory()


def user_row(app, username="alice") -> User:
    with with_db(app) as db:
        u = db.scalar(select(User).where(User.username == username))
        assert u is not None
        db.expunge(u)
        return u


# ------------------------------------------------------------------ first-run setup


def test_fresh_install_reports_setup_required_and_who_needs_the_token(app):
    lan = browser(app).get("/api/auth/session").json()
    assert lan == {
        "state": "setup_required",
        "user": None,
        "setup_token_required": True,
        "two_factor_required": False,
        "expires_at": None,
        "passkeys_count": 0,
        "passkey_supported_origin": False,  # http://testserver is neither localhost nor https
    }
    here = browser(app, loopback=True).get("/api/auth/session").json()
    assert here["state"] == "setup_required" and here["setup_token_required"] is False


def test_setup_needs_the_setup_token_unless_loopback(app):
    token = app.state.owner_token.get()
    lan = browser(app)
    body = setup_body(app)
    no_token = {k: v for k, v in body.items() if k != "setup_token"}
    r = lan.post("/api/auth/setup", json=no_token)
    assert r.status_code == 403 and r.json()["error"]["code"] == "setup_token_required"
    r = lan.post("/api/auth/setup", json={**body, "setup_token": "x" * 43})
    assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_setup_token"
    assert lan.get("/api/auth/session").json()["state"] == "setup_required"  # nothing was created
    r = lan.post("/api/auth/setup", json={**body, "setup_token": token})
    assert r.status_code == 200 and r.json()["state"] == "authenticated"


def test_setup_from_loopback_needs_no_token(app):
    here = browser(app, loopback=True)
    body = {k: v for k, v in setup_body(app).items() if k != "setup_token"}
    r = here.post("/api/auth/setup", json=body)
    assert r.status_code == 200, r.text


def test_a_proxied_request_is_not_loopback(app):
    """A tunnel connects from 127.0.0.1 but forwards the public host: the exemption must not apply."""
    here = browser(app, loopback=True)
    body = {k: v for k, v in setup_body(app).items() if k != "setup_token"}
    r = here.post("/api/auth/setup", json=body, headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "setup_token_required"
    r = here.post("/api/auth/setup", json=body, headers={"Host": "abc.trycloudflare.com"})
    assert r.status_code == 403


def test_setup_with_the_owner_credential_needs_no_extra_token(app, client):
    body = {k: v for k, v in setup_body(app).items() if k != "setup_token"}
    r = browser(app).post(
        "/api/auth/setup", json=body, headers=client.headers
    )  # Bearer owner token from a LAN peer
    assert r.status_code == 200, r.text


def test_setup_sets_httponly_session_and_readable_csrf_cookies(app):
    b = browser(app)
    r = b.post("/api/auth/setup", json=setup_body(app))
    cookies = {c.split("=", 1)[0]: c.lower() for c in r.headers.get_list("set-cookie")}
    assert "httponly" in cookies["cos_session"] and "samesite=lax" in cookies["cos_session"]
    assert "path=/" in cookies["cos_session"] and "secure" not in cookies["cos_session"]
    assert "httponly" not in cookies["cos_csrf"] and "samesite=lax" in cookies["cos_csrf"]
    me = b.get("/api/me").json()
    assert me["user"]["username"] == "alice"


def test_session_cookie_is_secure_over_https(app):
    b = browser(app, base_url="https://testserver")
    r = b.post("/api/auth/setup", json=setup_body(app))
    flags = {c.split("=", 1)[0]: c.lower() for c in r.headers.get_list("set-cookie")}
    assert "secure" in flags["cos_session"] and "secure" in flags["cos_csrf"]


def test_setup_runs_once(app):
    do_setup(app)
    r = browser(app).post("/api/auth/setup", json=setup_body(app, username="mallory"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "setup_complete"
    assert browser(app).get("/api/auth/session").json()["state"] == "unauthenticated"


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"username": "Al"}, "username"),
        ({"username": "has space"}, "username"),
        ({"username": "x" * 33}, "username"),
        ({"password": "short1!"}, "password"),
        ({"password": "passwordpassword"}, "password"),
        ({"password": "Password123456!"}, "password"),
        ({"password": "qwertyuiop1234"}, "password"),
        ({"password": "aaaaaaaaaaaaaaaa"}, "password"),
        ({"password": "alice-is-the-founder"}, "password"),
        ({"email": "not-an-email"}, "email"),
        ({"display_name": "   "}, "display_name"),
    ],
)
def test_setup_validates_username_and_password_policy(app, overrides, field):
    r = browser(app).post("/api/auth/setup", json=setup_body(app, **overrides))
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert any(field in d["loc"] for d in err["details"]), err
    assert overrides.get("password", "\0") not in r.text  # the password is never echoed back


def test_password_strength_accepts_a_long_passphrase_and_a_random_one():
    from app.core.passwords import check_password_strength as check

    assert check("correct horse battery staple").ok
    assert check("xK9#mPq2$vLw8Zr").ok
    assert not check("hunter2hunter2").ok
    assert not check("a" * 300).ok


def test_setup_stores_email_and_never_returns_the_password_hash(app):
    b = do_setup(app, email="Alice@Example.COM")
    s = b.get("/api/auth/session").json()
    assert s["user"]["email"] == "alice@example.com" and s["user"]["two_factor_enabled"] is False
    assert "password" not in str(s).lower() and "hash" not in str(s).lower()


# ------------------------------------------------------------------ Argon2id


def test_passwords_are_argon2id_with_per_user_salt(app):
    do_setup(app)
    first = user_row(app).password_hash
    assert first.startswith("$argon2id$")
    # a second account with the same password gets a different salt/hash
    with with_db(app) as db:
        u2 = User(
            username="bob",
            display_name="Bob",
            password_hash=app.state.auth.hash_password(PASSWORD),
        )
        db.add(u2)
        db.commit()
    assert user_row(app, "bob").password_hash != first


def test_default_argon2_parameters_follow_owasp_guidance():
    s = Settings(_env_file=None)
    assert (s.argon2_time_cost, s.argon2_memory_kib, s.argon2_parallelism) == (3, 65536, 4)


def test_weak_hash_is_upgraded_at_the_next_login(app):
    do_setup(app)
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    with with_db(app) as db:
        db.execute(
            User.__table__.update().where(User.username == "alice").values(password_hash=weak)
        )
        db.commit()
    assert app.state.auth.hasher.check_needs_rehash(weak)
    login(app, "alice")
    new = user_row(app).password_hash
    assert new != weak and not app.state.auth.hasher.check_needs_rehash(new)
    login(app, "alice")  # and the upgraded hash still verifies


def test_wrong_password_and_unknown_user_look_identical(app):
    do_setup(app)
    a = browser(app).post(
        "/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"}
    )
    b = browser(app).post(
        "/api/auth/login", json={"username": "nobody", "password": "nope-nope-nope"}
    )
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert a.json()["error"]["code"] == "invalid_credentials"


# ------------------------------------------------------------------ sessions


def test_session_secret_is_stored_only_as_a_hash(app):
    b = do_setup(app)
    raw = b.cookies.get("cos_session")
    with with_db(app) as db:
        row = db.scalar(select(AuthSession))
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in {str(v) for v in row.__dict__.values()}
        assert row.expires_at - row.created_at == timedelta(days=14)
        assert row.absolute_expires_at - row.created_at == timedelta(days=30)


def test_session_expiry_slides_but_never_passes_the_absolute_cap(app):
    b = do_setup(app)
    now = utcnow()
    with with_db(app) as db:
        row = db.scalar(select(AuthSession))
        row.last_seen_at = now - timedelta(minutes=5)
        row.expires_at = now + timedelta(days=1)
        db.commit()
    assert b.get("/api/me").status_code == 200
    with with_db(app) as db:
        row = db.scalar(select(AuthSession))
        assert row.expires_at > now + timedelta(days=13)  # slid out to ~14 days
        # near the absolute cap the window is clamped to it
        row.last_seen_at = now - timedelta(minutes=5)
        row.absolute_expires_at = now + timedelta(days=2)
        db.commit()
    assert b.get("/api/me").status_code == 200
    with with_db(app) as db:
        row = db.scalar(select(AuthSession))
        assert row.expires_at == row.absolute_expires_at


def test_idle_and_absolute_expiry_end_the_session(app):
    b = do_setup(app)
    with with_db(app) as db:
        row = db.scalar(select(AuthSession))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert b.get("/api/me").status_code == 401
    assert b.get("/api/auth/session").json()["state"] == "unauthenticated"

    b2 = login(app, "alice")
    with with_db(app) as db:
        row = db.scalars(select(AuthSession).order_by(AuthSession.created_at.desc())).first()
        row.absolute_expires_at = utcnow() - timedelta(seconds=1)
        row.expires_at = utcnow() + timedelta(days=5)
        db.commit()
    assert b2.get("/api/me").status_code == 401


def test_sessions_can_be_listed_and_revoked(app):
    b1 = do_setup(app)
    b2 = login(app, "alice")
    items = b1.get("/api/auth/sessions").json()["items"]
    assert len(items) == 2 and sum(1 for s in items if s["current"]) == 1
    other = next(s for s in items if not s["current"])
    assert b1.delete(f"/api/auth/sessions/{other['id']}").status_code == 204
    assert b2.get("/api/me").status_code == 401 and b1.get("/api/me").status_code == 200
    assert b1.delete(f"/api/auth/sessions/{other['id']}").status_code == 404
    assert b1.delete("/api/auth/sessions/does-not-exist").status_code == 404


def test_sessions_of_other_users_are_not_visible_or_revocable(app):
    b1 = do_setup(app)
    inv_company = create_company(b1)
    from tests.accounts_helpers import invite, join

    bob = join(app, invite(b1, inv_company["id"], "bob@example.com"), "bob")
    alice_session = b1.get("/api/auth/sessions").json()["items"][0]["id"]
    assert bob.delete(f"/api/auth/sessions/{alice_session}").status_code == 404
    assert b1.get("/api/me").status_code == 200


def test_log_out_everywhere_ends_every_session_and_clears_cookies(app):
    b1 = do_setup(app)
    b2 = login(app, "alice")
    r = b1.delete("/api/auth/sessions")
    assert r.status_code == 204
    assert b1.get("/api/me").status_code == 401 and b2.get("/api/me").status_code == 401


def test_logout_revokes_the_session_and_needs_csrf(app):
    b = do_setup(app)
    b.auto_csrf = False
    assert b.post("/api/auth/logout").status_code == 403  # a cross-site form could not do this
    assert b.get("/api/me").status_code == 200
    b.auto_csrf = True
    r = b.post("/api/auth/logout")
    assert r.status_code == 204
    assert b.get("/api/me").status_code == 401
    assert b.post("/api/auth/logout").status_code == 204  # idempotent


# ------------------------------------------------------------------ CSRF


def test_cookie_auth_needs_a_matching_signed_csrf_token(app):
    b = do_setup(app)
    good = b.cookies.get("cos_csrf")
    b.auto_csrf = False
    url = "/api/companies"
    body = {"name": "Acme"}
    r = b.post(url, json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert b.post(url, json=body, headers={"X-CSRF-Token": "x" * 64}).status_code == 403
    # an attacker who can plant a cookie (sibling origin) still cannot invent a valid pair
    b.cookies.set("cos_csrf", "f" * 64)
    assert b.post(url, json=body, headers={"X-CSRF-Token": "f" * 64}).status_code == 403
    b.cookies.set("cos_csrf", good)
    assert b.post(url, json=body, headers={"X-CSRF-Token": good}).status_code == 201
    # reads never need it
    assert b.get(url).status_code == 200


def test_csrf_token_of_one_session_is_useless_in_another(app):
    b1 = do_setup(app)
    b2 = login(app, "alice")
    b2.auto_csrf = False
    r = b2.post(
        "/api/companies", json={"name": "X"}, headers={"X-CSRF-Token": b1.cookies.get("cos_csrf")}
    )
    assert r.status_code == 403


def test_reading_the_session_reissues_the_csrf_cookie(app):
    b = do_setup(app)
    token = b.cookies.get("cos_csrf")
    b.cookies.delete("cos_csrf")
    assert b.post("/api/companies", json={"name": "A"}, headers={}).status_code == 403
    b.get("/api/auth/session")
    assert b.cookies.get("cos_csrf") == token
    assert b.post("/api/companies", json={"name": "A"}).status_code == 201


def test_bearer_requests_need_no_csrf(app, client):
    r = client.post("/api/companies", json={"name": "Acme"})  # Bearer owner token
    assert r.status_code == 201
    b = do_setup(app)
    token = b.post("/api/auth/tokens", json={"name": "cli", "scope": "write"}).json()["token"]
    bare = TestClient(app)
    r = bare.post(
        "/api/companies", json={"name": "B"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 201


# ------------------------------------------------------------------ owner credential after setup


def test_owner_cookie_is_bootstrap_only_in_password_mode(app):
    token = app.state.owner_token.get()
    c = browser(app)
    c.cookies.set("cos_owner", token)
    assert c.get("/api/me").status_code == 200  # install not claimed yet: the bootstrap works
    assert c.get("/api/auth/session").json()["state"] == "setup_required"
    do_setup(app)
    assert c.get("/api/me").status_code == 401  # claimed: the cookie is dead
    bearer = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    assert bearer.get("/api/me").json()["user"]["username"] == "alice"  # CLI fallback stays


def test_owner_token_resolves_to_the_claimed_account(app, client):
    b = do_setup(app)
    company = create_company(b)
    r = client.get("/api/companies")
    assert [c["id"] for c in r.json()["items"]] == [company["id"]]


# ------------------------------------------------------------------ login throttling


def fail(app, username="alice", *, n=1, ip="192.168.1.50"):
    last = None
    for _ in range(n):
        c = browser(app, client=(ip, 4000))
        last = c.post(
            "/api/auth/login", json={"username": username, "password": "wrong-wrong-wrong"}
        )
    return last


def test_login_backs_off_per_username_with_a_generic_error(app):
    do_setup(app)
    for _ in range(3):  # the free attempts
        assert fail(app).status_code == 401
    r = fail(app)  # the 4th wrong attempt arms the lock
    assert r.status_code == 401
    locked = fail(app)
    assert locked.status_code == 429
    err = locked.json()["error"]
    assert err["code"] == "rate_limited" and err["details"]["retry_after_seconds"] >= 1
    assert locked.headers["retry-after"] == str(err["details"]["retry_after_seconds"])
    assert "alice" not in locked.text
    # even the right password waits
    ok = browser(app).post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert ok.status_code == 429


def test_unknown_usernames_are_throttled_exactly_like_real_ones(app):
    do_setup(app)
    for _ in range(4):
        fail(app, "ghost")
    r = fail(app, "ghost")
    assert r.status_code == 429
    real = fail(app, "alice")  # a different key: not locked by the ghost
    assert real.status_code == 401


def test_login_is_throttled_per_ip_across_usernames(app):
    do_setup(app)
    statuses = [fail(app, f"user{i}", ip="198.51.100.7").status_code for i in range(12)]
    assert 429 in statuses and statuses[0] == 401
    assert fail(app, "alice", ip="198.51.100.8").status_code == 401  # another address is fine


def test_lock_expires_and_success_resets_the_counter(app):
    do_setup(app)
    for _ in range(5):
        fail(app)
    with with_db(app) as db:
        for row in db.scalars(select(LoginThrottle)):
            if row.locked_until:
                row.locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    ok = browser(app).post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert ok.status_code == 200
    with with_db(app) as db:
        assert db.get(LoginThrottle, "user:alice") is None


def test_lockout_is_exponential_and_capped(app):
    do_setup(app)
    waits = []
    for _ in range(9):
        fail(app)
        with with_db(app) as db:
            row = db.get(LoginThrottle, "user:alice")
            if row.locked_until:
                waits.append((row.locked_until - row.last_failure_at).total_seconds())
                row.locked_until = None  # let the next attempt through
                db.commit()
    assert waits == sorted(waits) and waits[0] == 2 and waits[1] == 4
    assert max(waits) <= app.state.settings.login_max_lockout_seconds


# ------------------------------------------------------------------ API tokens


def make_token(b: TestClient, scope: str, name: str = "cli") -> dict:
    r = b.post("/api/auth/tokens", json={"name": name, "scope": scope})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(app, token: str) -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def test_api_token_is_shown_once_and_stored_hashed(app):
    b = do_setup(app)
    created = make_token(b, "read")
    assert created["token"].startswith("cos_pat_") and created["prefix"] == created["token"][:12]
    listed = b.get("/api/auth/tokens").json()["items"]
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0] and created["token"] not in str(listed)
    with with_db(app) as db:
        row = db.scalar(select(ApiToken))
        assert row.token_hash == hashlib.sha256(created["token"].encode()).hexdigest()
        assert created["token"] not in {str(v) for v in row.__dict__.values()}
    assert bearer(app, created["token"]).get("/api/me").json()["user"]["username"] == "alice"


def test_token_scopes_read_write_admin(app):
    b = do_setup(app)
    company = create_company(b)
    cid = company["id"]
    read = bearer(app, make_token(b, "read", "r")["token"])
    write = bearer(app, make_token(b, "write", "w")["token"])
    admin = bearer(app, make_token(b, "admin", "a")["token"])

    assert read.get(f"/api/companies/{cid}/tasks").status_code == 200
    r = read.post(f"/api/companies/{cid}/tasks", json={"title": "t"})
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "token_scope"

    assert write.post(f"/api/companies/{cid}/tasks", json={"title": "t"}).status_code == 201
    inv = {"email": "x@example.com", "role": "member"}
    r = write.post(f"/api/companies/{cid}/invitations", json=inv)
    assert r.status_code == 403 and r.json()["error"]["details"]["scope"] == "write"
    assert write.patch(f"/api/companies/{cid}/policy", json={}).status_code == 403
    assert write.patch(f"/api/companies/{cid}", json={"name": "Renamed"}).status_code == 403

    assert admin.post(f"/api/companies/{cid}/invitations", json=inv).status_code == 201
    assert admin.patch(f"/api/companies/{cid}", json={"name": "Renamed"}).status_code == 200


def test_api_tokens_cannot_manage_the_account(app):
    b = do_setup(app)
    admin = bearer(app, make_token(b, "admin")["token"])
    for method, path, body in [
        ("POST", "/api/auth/tokens", {"name": "evil", "scope": "admin"}),
        ("GET", "/api/auth/tokens", None),
        ("GET", "/api/auth/sessions", None),
        ("DELETE", "/api/auth/sessions", None),
        ("POST", "/api/auth/password", {"current_password": PASSWORD, "new_password": PASSWORD2}),
        ("POST", "/api/auth/2fa/enroll", {}),
    ]:
        r = admin.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["details"]["reason"] == "token_cannot_manage_account"


def test_revoked_and_unknown_tokens_are_401(app):
    b = do_setup(app)
    t = make_token(b, "read")
    c = bearer(app, t["token"])
    assert c.get("/api/me").status_code == 200
    assert b.delete(f"/api/auth/tokens/{t['id']}").status_code == 204
    assert c.get("/api/me").status_code == 401
    assert b.delete(f"/api/auth/tokens/{t['id']}").status_code == 404
    assert bearer(app, "cos_pat_" + "x" * 43).get("/api/me").status_code == 401
    assert b.get("/api/auth/tokens").json()["items"] == []


def test_token_validation(app):
    b = do_setup(app)
    assert b.post("/api/auth/tokens", json={"name": "x", "scope": "root"}).status_code == 422
    assert b.post("/api/auth/tokens", json={"name": "  ", "scope": "read"}).status_code == 422


# ------------------------------------------------------------------ password change


def test_password_change_rotates_credentials_and_signs_other_devices_out(app):
    b1 = do_setup(app)
    b2 = login(app, "alice")
    r = b1.post(
        "/api/auth/password",
        json={"current_password": "wrong-password-1", "new_password": PASSWORD2},
    )
    assert r.status_code == 401
    r = b1.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 422
    r = b1.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD})
    assert r.status_code == 422  # must actually change
    r = b1.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD2}
    )
    assert r.status_code == 204
    assert b1.get("/api/me").status_code == 200 and b2.get("/api/me").status_code == 401
    assert (
        browser(app)
        .post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        .status_code
        == 401
    )
    login(app, "alice", PASSWORD2)


# ------------------------------------------------------------------ 2FA


def enable_2fa(app, b: TestClient) -> tuple[str, list[str]]:
    secret = b.post("/api/auth/2fa/enroll", json={}).json()["secret"]
    code = pyotp.TOTP(secret).now()
    r = b.post("/api/auth/2fa/verify", json={"code": code})
    assert r.status_code == 200 and r.json()["enabled"] is True
    return secret, r.json()["recovery_codes"]


def test_2fa_enrolment_and_login_flow(app):
    b = do_setup(app)
    enroll = b.post("/api/auth/2fa/enroll", json={}).json()
    assert (
        enroll["otpauth_uri"].startswith("otpauth://totp/")
        and "Foundry%20AI%20OS" in enroll["otpauth_uri"]
    )
    assert b.get("/api/auth/session").json()["user"]["two_factor_enabled"] is False  # not confirmed
    assert b.post("/api/auth/2fa/verify", json={"code": "000000"}).status_code == 401
    secret = enroll["secret"]
    res = b.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()}).json()
    assert res["enabled"] is True and len(res["recovery_codes"]) == 10
    assert b.get("/api/auth/session").json()["user"]["two_factor_enabled"] is True
    with with_db(app) as db:
        u = db.scalar(select(User))
        assert secret not in (u.totp_secret_enc or "")  # encrypted at rest
        assert all(c not in str(u.recovery_code_hashes) for c in res["recovery_codes"])

    # password alone is not enough
    c = browser(app)
    r = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["state"] == "unauthenticated" and r.json()["two_factor_required"] is True
    assert "cos_session" not in c.cookies and c.get("/api/me").status_code == 401
    bad = c.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD, "totp_code": "123456"}
    )
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    # the enrolment code spent this step; the next step's code (clock skew window) works once
    totp = pyotp.TOTP(secret)
    assert (
        c.post(
            "/api/auth/login",
            json={"username": "alice", "password": PASSWORD, "totp_code": totp.now()},
        ).status_code
        == 401
    )  # replay of the code that confirmed the enrolment
    code = totp.at(utcnow() + timedelta(seconds=30))
    ok = c.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD, "totp_code": code}
    )
    assert ok.status_code == 200 and ok.json()["state"] == "authenticated"
    again = browser(app).post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD, "totp_code": code}
    )
    assert again.status_code == 401  # replay


def test_recovery_codes_work_once(app):
    b = do_setup(app)
    _, codes = enable_2fa(app, b)
    body = {"username": "alice", "password": PASSWORD, "totp_code": codes[0]}
    assert browser(app).post("/api/auth/login", json=body).status_code == 200
    assert browser(app).post("/api/auth/login", json=body).status_code == 401
    assert (
        browser(app)
        .post("/api/auth/login", json={**body, "totp_code": codes[1].upper()})
        .status_code
        == 200
    )  # case and dash tolerant


def test_disabling_2fa_needs_password_and_a_code(app):
    b = do_setup(app)
    secret, codes = enable_2fa(app, b)
    assert (
        b.post("/api/auth/2fa/disable", json={"code": codes[0], "password": "nope"}).status_code
        == 401
    )
    assert (
        b.post("/api/auth/2fa/disable", json={"code": "000000", "password": PASSWORD}).status_code
        == 401
    )
    r = b.post("/api/auth/2fa/disable", json={"code": codes[0], "password": PASSWORD})
    assert r.status_code == 204
    assert b.get("/api/auth/session").json()["user"]["two_factor_enabled"] is False
    login(app, "alice")  # no code needed any more


def test_2fa_code_guessing_is_throttled(app):
    b = do_setup(app)
    enable_2fa(app, b)
    statuses = []
    for _ in range(8):
        c = browser(app)
        statuses.append(
            c.post(
                "/api/auth/login",
                json={"username": "alice", "password": PASSWORD, "totp_code": "000000"},
            ).status_code
        )
    assert 429 in statuses


# ------------------------------------------------------------------ local (dev-only) mode


def local_app(make_settings, **kw):
    return create_app(make_settings(auth_mode="local", **kw), runtime_registry=RuntimeRegistry())


def test_local_mode_only_answers_this_machine(make_settings):
    app = local_app(make_settings)
    token = app.state.owner_token
    with TestClient(app, base_url="http://localhost:8700", client=("127.0.0.1", 1)) as here:
        here.cookies.set("cos_owner", token.get())
        assert here.get("/api/me").status_code == 200
        assert here.get("/api/health").json()["auth_mode"] == "local"
    with TestClient(app, client=("192.168.1.9", 1)) as lan:
        r = lan.get("/api/health")
        assert (
            r.status_code == 403
            and r.json()["error"]["details"]["reason"] == "local_mode_loopback_only"
        )
        lan.cookies.set("cos_owner", token.get())
        assert lan.get("/api/me").status_code == 403


def test_local_mode_refuses_a_non_loopback_bind(make_settings):
    app = local_app(make_settings, bind_host="0.0.0.0")
    with pytest.raises(RuntimeError, match="refuses to run bound to"), TestClient(app):
        pass
    ok = local_app(make_settings, bind_host="127.0.0.1")
    with TestClient(ok, base_url="http://localhost", client=("127.0.0.1", 1)) as c:
        assert c.get("/api/health").status_code == 200


def test_local_mode_keeps_the_owner_cookie_after_an_account_exists(make_settings):
    app = local_app(make_settings)
    here = TestClient(app, base_url="http://localhost:8700", client=("127.0.0.1", 1))
    with here:
        body = {k: v for k, v in setup_body(app).items() if k != "setup_token"}
        assert here.post("/api/auth/setup", json=body).status_code == 200
        c2 = TestClient(app, base_url="http://localhost:8700", client=("127.0.0.1", 1))
        c2.cookies.set("cos_owner", app.state.owner_token.get())
        assert c2.get("/api/me").status_code == 200
        assert c2.get("/api/auth/session").json()["state"] == "authenticated"


def test_unauthenticated_message_depends_on_the_mode(app, make_settings):
    pw = browser(app).get("/api/me").json()["error"]
    assert pw["details"]["reason"] == "authentication_required"
    legacy = local_app(make_settings)
    with TestClient(legacy, base_url="http://localhost", client=("127.0.0.1", 1)) as c:
        err = c.get("/api/me").json()["error"]
        assert "login link" in err["message"].lower()
        assert err["details"]["reason"] == "owner_credential_required"


def test_health_and_docs_stay_open_without_a_session(app):
    c = browser(app)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/openapi.json").status_code == 200
    assert c.get("/api/auth/session").status_code == 200


def test_session_endpoint_answers_for_api_token_callers_too(app):
    b = do_setup(app)
    token = make_token(b, "read")["token"]
    s = bearer(app, token).get("/api/auth/session").json()
    assert s["state"] == "authenticated" and s["user"]["username"] == "alice"


def test_api_tokens_are_caught_by_the_outbound_dlp(app):
    from app.guardrails import dlp

    b = do_setup(app)
    token = make_token(b, "read")["token"]
    with app.state.session_factory() as db:
        found = dlp.scan_outbound(
            db, "any-company", {"body": f"my token is {token}"}, own_secrets=(), allowed_domains=[]
        )
    assert "own_token" in {f.kind for f in found}


def test_old_throttle_rows_are_pruned(app):
    do_setup(app)
    fail(app, "ghost")
    with with_db(app) as db:
        row = db.get(LoginThrottle, "user:ghost")
        row.last_failure_at = utcnow() - timedelta(days=3)
        db.commit()
    fail(app, "other")
    with with_db(app) as db:
        assert db.get(LoginThrottle, "user:ghost") is None
