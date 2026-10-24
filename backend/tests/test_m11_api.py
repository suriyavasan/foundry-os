"""M11: GET /api/system/update (owner only) and the switch for the daily check."""

from __future__ import annotations

from app.models.audit import AuditEvent
from app.models.guardrails import GuardrailEvent
from app.ops import updatecheck
from tests import accounts_helpers as ah
from tests.helpers import new_agent
from tests.m11_helpers import fake_github, make_release
from tests.test_agent_api import start_run


def online(monkeypatch, tmp_path, latest="9.0.0", calls=None):
    monkeypatch.setattr(
        updatecheck,
        "http_client_factory",
        fake_github({latest: make_release(tmp_path / "dl", latest)}, latest=latest, calls=calls),
    )


def test_the_owner_sees_the_update_status_with_the_command_for_this_install(
    client, monkeypatch, tmp_path
):
    seen: list[str] = []
    online(monkeypatch, tmp_path, calls=seen)
    r = client.get("/api/system/update")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["update_available"] is True and body["latest_version"] == "9.0.0"
    assert body["enabled"] is True and body["install_kind"] == "source"
    assert "git pull" in body["update_command"]
    assert body["release_url"].endswith("/releases/tag/v9.0.0")
    client.get("/api/system/update")
    assert len(seen) == 1  # at most one GitHub request per day


def test_an_offline_server_still_answers(client):
    r = client.get("/api/system/update")
    assert r.status_code == 200
    assert r.json()["update_available"] is False and r.json()["error"]


def test_switching_the_check_off_is_persistent_audited_and_stops_requests(
    app, client, company, monkeypatch, tmp_path
):
    seen: list[str] = []
    online(monkeypatch, tmp_path, calls=seen)
    r = client.put("/api/system/update/settings", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    off = client.get("/api/system/update").json()
    assert off["enabled"] is False and off["update_available"] is False and not seen
    with app.state.session_factory() as db:
        actions = [e.action for e in db.query(AuditEvent).filter_by(company_id=company["id"])]
    assert "update_check.disabled" in actions
    assert (
        client.put("/api/system/update/settings", json={"enabled": True}).json()["enabled"] is True
    )
    assert client.get("/api/system/update").json()["update_available"] is True


def test_only_the_installation_owner_can_see_or_change_it(app, client, monkeypatch, tmp_path):
    online(monkeypatch, tmp_path)
    owner = ah.do_setup(app)
    cid = ah.create_company(owner)["id"]
    assert owner.get("/api/system/update").status_code == 200
    created = ah.invite(owner, cid, "bob@example.com", "admin")
    bob = ah.join(app, created, "bob")
    assert bob.get("/api/system/update").status_code == 403
    assert bob.put("/api/system/update/settings", json={"enabled": False}).status_code == 403
    assert ah.browser(app).get("/api/system/update").status_code == 401
    assert owner.get("/api/system/update").json()["enabled"] is True  # bob changed nothing


def test_agents_cannot_read_or_change_it(app, client, company, monkeypatch, tmp_path):
    online(monkeypatch, tmp_path)
    agent = new_agent(client, company["id"], name="Ops")
    session = start_run(app, client, company["id"], agent["id"])
    assert client.get("/api/system/update", headers=session.headers).status_code == 403
    r = client.put("/api/system/update/settings", headers=session.headers, json={"enabled": False})
    assert r.status_code == 403 and r.json()["error"]["code"] == "guardrail_denied"
    with app.state.session_factory() as db:
        denied = [e.action for e in db.query(GuardrailEvent).filter_by(decision="deny")]
    assert "system.update" in denied
    assert client.get("/api/system/update").json()["enabled"] is True


def test_cli_status_mentions_an_available_update(app, client, company, monkeypatch, tmp_path):
    # drives the real CLI against the in-process app, like tests/test_m9_cli.py
    from typer.testing import CliRunner

    from app.cli import context
    from app.cli.main import app as cli_app

    online(monkeypatch, tmp_path)
    token = client.headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("COMPANYOS_TOKEN", token)
    monkeypatch.setattr(context, "http_factory", lambda url: client)
    monkeypatch.setenv("COLUMNS", "220")
    r = CliRunner().invoke(cli_app, ["status"])
    assert r.exit_code == 0, r.output
    assert "Update available: Foundry AI OS 9.0.0" in r.stdout
    data = CliRunner().invoke(cli_app, ["status", "--json"])
    import json

    assert json.loads(data.stdout)["update"]["latest_version"] == "9.0.0"
