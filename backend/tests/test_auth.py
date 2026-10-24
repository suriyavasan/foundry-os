"""M3b-sec: the local-mode owner bypass is closed. No owner credential and no run token = 401."""

from __future__ import annotations

import logging
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from app.core.owner_token import OWNER_COOKIE, OWNER_TOKEN_FILENAME, OwnerTokenProvider
from app.main import create_app
from app.runtimes.registry import RuntimeRegistry
from tests.helpers import api

PROTECTED = [
    ("GET", "/api/me"),
    ("GET", "/api/companies"),
    ("POST", "/api/companies"),
    ("GET", "/api/runtimes"),
    ("GET", "/api/agent/v1/context"),
    ("POST", "/api/agent/v1/tasks"),
    ("POST", "/api/agent/v1/hires"),
]


def owner_token(app) -> str:
    return app.state.owner_token.get()


@pytest.mark.parametrize("method, path", PROTECTED)
def test_no_credential_is_401_not_the_owner(anon_client, method, path):
    r = anon_client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code == 401, (path, r.text)
    err = r.json()["error"]
    assert err["code"] == "unauthenticated"


def test_401_tells_a_human_how_to_log_in(make_settings):
    """The owner-token gate message belongs to the dev-only ``local`` mode; password mode asks the
    person to sign in (see test_accounts_auth)."""
    app = create_app(make_settings(auth_mode="local"), runtime_registry=RuntimeRegistry())
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1)) as c:
        err = c.get("/api/me").json()["error"]
    assert "login link" in err["message"].lower()
    assert err["details"]["reason"] == "owner_credential_required"


def test_health_and_docs_stay_open(anon_client):
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.get("/api/openapi.json").status_code == 200


def test_loopback_is_not_trusted(anon_client):
    r = anon_client.get("/api/me", headers={"X-Forwarded-For": "127.0.0.1", "Host": "localhost"})
    assert r.status_code == 401


def test_bearer_owner_token_authenticates_the_owner(app, anon_client):
    r = anon_client.get("/api/me", headers={"Authorization": f"Bearer {owner_token(app)}"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "owner@localhost"


def test_cookie_authenticates_the_owner(app, anon_client):
    anon_client.cookies.set(OWNER_COOKIE, owner_token(app))
    assert anon_client.get("/api/me").status_code == 200


@pytest.mark.parametrize("bad", ["", "nope", "x" * 64, "Bearer", "cos_run.not.valid"])
def test_wrong_credentials_are_401(anon_client, bad):
    assert anon_client.get("/api/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    anon_client.cookies.set(OWNER_COOKIE, bad or "empty")
    assert anon_client.get("/api/me").status_code == 401


def test_a_prefix_or_extension_of_the_token_is_not_the_token(app, anon_client):
    token = owner_token(app)
    for wrong in (
        token[:-1],
        token + "x",
        token.upper() if token.upper() != token else token + "A",
    ):
        r = anon_client.get("/api/me", headers={"Authorization": f"Bearer {wrong}"})
        assert r.status_code == 401


def test_comparison_is_constant_time(app, monkeypatch):
    import hmac

    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    assert not app.state.owner_token.verify("guess")
    assert app.state.owner_token.verify(owner_token(app))
    assert len(calls) == 2


# ------------------------------------------------------------------ login endpoint


def test_login_sets_an_httponly_samesite_strict_cookie_and_redirects(app, anon_client):
    r = anon_client.get(f"/api/auth/local?token={owner_token(app)}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{OWNER_COOKIE}=")
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=strict" in lowered and "path=/" in lowered
    assert (
        r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    )
    # the browser now has the session
    assert anon_client.get("/api/me").status_code == 200


@pytest.mark.parametrize("token", ["", "wrong", "x" * 43])
def test_login_with_a_wrong_token_is_401_and_sets_nothing(anon_client, token):
    r = anon_client.get(f"/api/auth/local?token={token}", follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert r.json()["error"]["code"] == "unauthenticated"


def test_login_has_no_token_in_the_response_body(app, anon_client):
    r = anon_client.get(f"/api/auth/local?token={owner_token(app)}", follow_redirects=False)
    assert owner_token(app) not in r.text


# ------------------------------------------------------------------ run tokens are still agents


def test_the_owner_token_is_not_a_run_token(app, anon_client):
    r = anon_client.get(
        "/api/agent/v1/context", headers={"Authorization": f"Bearer {owner_token(app)}"}
    )
    assert r.status_code == 401  # the agent API takes run tokens only


def test_run_token_is_an_agent_on_both_apis_and_never_the_owner(app, client, anon_client, company):
    from tests.conftest import new_agent
    from tests.test_agent_api import AgentSession, finish_run, start_run

    cid = company["id"]
    agent = new_agent(client, cid, name="Worker")
    minted = start_run(app, client, cid, agent["id"])
    sess = AgentSession(
        anon_client,
        minted.token,
        run_id=minted.run_id,
        task_id=minted.task_id,
        agent_id=agent["id"],
    )
    assert sess.get("/context").status_code == 200
    assert anon_client.get(api(cid), headers=sess.headers).status_code == 200  # reads its company
    # human-only mutations stay human-only, even though the request "comes from localhost"
    r = anon_client.patch(api(cid, "/policy"), json={"max_headcount": 99}, headers=sess.headers)
    assert r.status_code == 403
    assert anon_client.patch(api(cid, "/policy"), json={"max_headcount": 99}).status_code == 401
    # a finished run's token is dead, and does not fall back to the owner either
    finish_run(app, sess)
    assert sess.get("/context").status_code == 401
    assert anon_client.get(api(cid), headers=sess.headers).status_code == 401


def test_owner_cookie_does_not_open_the_agent_api(client):
    assert client.get("/api/agent/v1/context").status_code == 401


# ------------------------------------------------------------------ the token file


def test_token_file_is_created_0600_stable_and_random(make_settings, tmp_path):
    settings = make_settings()
    provider = OwnerTokenProvider(settings)
    token = provider.ensure()
    path = settings.resolved_data_dir / OWNER_TOKEN_FILENAME
    assert path.read_text().strip() == token and len(token) >= 40
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # a second process (new provider) reads the same token instead of rotating it
    assert OwnerTokenProvider(settings).ensure() == token


def test_loose_permissions_are_tightened_and_short_tokens_refused(make_settings):
    settings = make_settings()
    provider = OwnerTokenProvider(settings)
    token = provider.ensure()
    path = settings.resolved_data_dir / OWNER_TOKEN_FILENAME
    path.chmod(0o644)
    assert OwnerTokenProvider(settings).ensure() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("short")
    with pytest.raises(RuntimeError):
        OwnerTokenProvider(settings).ensure()


def test_startup_creates_the_token_and_the_app_refuses_nothing_else(make_settings):
    settings = make_settings()
    with TestClient(create_app(settings, runtime_registry=RuntimeRegistry())):
        pass
    assert (settings.resolved_data_dir / OWNER_TOKEN_FILENAME).is_file()


def test_repr_never_contains_the_token(app):
    assert owner_token(app) not in repr(app.state.owner_token)


# ------------------------------------------------------------------ announcing the login link


def test_login_url_goes_to_a_terminal_only(make_settings, monkeypatch, capsys, caplog):
    settings = make_settings(public_url="http://localhost:8700")
    provider = OwnerTokenProvider(settings)
    token = provider.ensure()

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    with caplog.at_level(logging.DEBUG):
        provider.announce()
    out = capsys.readouterr().err
    assert f"http://localhost:8700/api/auth/local?token={token}" in out
    assert "http://localhost:5173/api/auth/local?token=" in out  # the Vite dev server
    assert token not in caplog.text

    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        provider.announce()
    captured = capsys.readouterr()
    assert token not in captured.err and token not in captured.out and token not in caplog.text
    assert "owner.token" in caplog.text  # points at the file instead


def test_print_login_url_can_be_forced(make_settings, monkeypatch, capsys):
    settings = make_settings(print_login_url=True)
    provider = OwnerTokenProvider(settings)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    provider.announce()
    assert provider.ensure() in capsys.readouterr().err


# ------------------------------------------------------------------ tests keep the old escape hatch


def test_dependency_override_still_works_for_tests(app, make_user, client_as, company):
    from tests.conftest import human

    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(company["id"])).status_code == 404  # authenticated, but not a member


# ------------------------------------------------------------------ the token never reaches access logs


def test_access_log_masks_the_login_token():
    import logging

    from app.core.owner_token import RedactLoginToken

    token = "T0kenValue-0123456789abcdefghijklmnopqrstu"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "x",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/api/auth/local?token={token}&x=1", "1.1", 303),
        None,
    )
    assert RedactLoginToken().filter(record)
    rendered = record.getMessage()
    assert token not in rendered and "token=[redacted]" in rendered and "x=1" in rendered
    plain = logging.LogRecord(
        "u", logging.INFO, "x", 1, f"GET /api/auth/local?token={token}", (), None
    )
    RedactLoginToken().filter(plain)
    assert token not in plain.getMessage()


def test_the_filter_is_installed_on_uvicorns_access_logger(app):
    import logging

    from app.core.owner_token import RedactLoginToken

    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, RedactLoginToken) for f in filters) == 1  # not duplicated per app


def test_access_log_masks_invitation_tokens():
    import logging

    from app.core.owner_token import RedactLoginToken

    token = "InviteTok-0123456789abcdefghijklmnopqrstu"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "x",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "POST", f"/api/invitations/{token}/accept", "1.1", 200),
        None,
    )
    assert RedactLoginToken().filter(record)
    assert (
        token not in record.getMessage() and "/invitations/[redacted]/accept" in record.getMessage()
    )
