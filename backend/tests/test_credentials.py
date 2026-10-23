"""Credential store: Fernet at rest, metadata-only API, no value in logs/audit/events, tenancy."""

from __future__ import annotations

import base64
import json
import logging

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.core.keys import SecretKeyProvider
from app.models.audit import AuditEvent
from app.models.provider import Credential
from app.models.run import CompanyEvent
from app.schemas.providers import CredentialCreate
from tests.conftest import human
from tests.helpers import api

SECRET = "sk-live-0123456789abcdefghijklmnopqrstuv"
META_KEYS = {
    "id",
    "company_id",
    "name",
    "kind",
    "created_by_type",
    "created_by_id",
    "revoked_at",
    "created_at",
    "updated_at",
}


def create(client, cid, name="openrouter", value=SECRET, kind="api_key"):
    return client.post(api(cid, "/credentials"), json={"name": name, "kind": kind, "value": value})


def everything_stored(app) -> str:
    """Every audit row, outbox event and raw DB byte, as one searchable blob."""
    with app.state.session_factory() as db:
        audits = [json.dumps([a.summary, a.data, a.action]) for a in db.scalars(select(AuditEvent))]
        events = [json.dumps(e.payload) for e in db.scalars(select(CompanyEvent))]
    return "\n".join(audits + events)


# ------------------------------------------------------------------ cryptography


@pytest.fixture
def cipher(make_settings) -> CredentialCipher:
    return CredentialCipher(SecretKeyProvider(make_settings()))


def test_round_trip_and_ciphertext_is_opaque(cipher):
    token = cipher.encrypt(SECRET)
    assert SECRET not in token and token != cipher.encrypt(SECRET)  # random IV per encryption
    assert cipher.decrypt(token) == SECRET
    assert cipher.decrypt(cipher.encrypt("ünïcödé ✓")) == "ünïcödé ✓"


def test_tampered_or_foreign_ciphertext_does_not_decrypt(cipher, make_settings):
    token = cipher.encrypt(SECRET)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt(tampered)
    other = CredentialCipher(
        SecretKeyProvider(make_settings(secret_key="a-different-secret-key-0123456789"))
    )
    with pytest.raises(CredentialDecryptError):
        other.decrypt(token)
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt("not even base64 ###")
    # the failure never echoes the secret or the token
    try:
        other.decrypt(token)
    except CredentialDecryptError as exc:
        assert SECRET not in str(exc) and token not in str(exc)


def test_key_is_derived_not_the_raw_secret_key(make_settings):
    settings = make_settings(secret_key="a-secret-key-that-is-long-enough-0123456789")
    token = CredentialCipher(SecretKeyProvider(settings)).encrypt("x")
    raw = Fernet(base64.urlsafe_b64encode(b"a-secret-key-that-is-long-enough-0123456789"[:32]))
    with pytest.raises(InvalidToken):
        raw.decrypt(token.encode())


def test_secret_key_file_is_the_default_key_source(make_settings):
    settings = make_settings()
    CredentialCipher(SecretKeyProvider(settings)).encrypt("x")
    key_file = settings.resolved_data_dir / "secret.key"
    assert key_file.is_file() and oct(key_file.stat().st_mode & 0o777) == "0o600"


def test_repr_and_models_never_show_the_value(cipher):
    body = CredentialCreate(name="n", value=SECRET)
    assert SECRET not in repr(body) and SECRET not in str(body)
    assert SECRET not in repr(cipher)


# ------------------------------------------------------------------ API


def test_create_returns_metadata_only(client, company):
    r = create(client, company["id"])
    assert r.status_code == 201
    body = r.json()
    assert set(body) == META_KEYS
    assert body["name"] == "openrouter" and body["kind"] == "api_key" and body["revoked_at"] is None
    assert SECRET not in r.text


def test_list_and_get_never_return_the_value(client, company):
    cid = company["id"]
    cred = create(client, cid).json()
    for r in (
        client.get(api(cid, "/credentials")),
        client.get(api(cid, f"/credentials/{cred['id']}")),
    ):
        assert r.status_code == 200 and SECRET not in r.text and "encrypted" not in r.text
    items = client.get(api(cid, "/credentials")).json()["items"]
    assert [set(i) for i in items] == [META_KEYS]


def test_the_database_holds_ciphertext_only(app, client, company):
    cred = create(client, company["id"]).json()
    with app.state.session_factory() as db:
        row = db.get(Credential, cred["id"])
        assert row.encrypted_value and SECRET not in row.encrypted_value
        assert row.encrypted_value.startswith("gAAAA")  # a Fernet token
    db_path = app.state.settings.resolved_database_url.removeprefix("sqlite:///")
    from pathlib import Path

    raw = b"".join(p.read_bytes() for p in (Path(db_path), Path(db_path + "-wal")) if p.exists())
    assert SECRET.encode() not in raw


def test_value_is_not_in_audit_events_or_logs(app, client, company, caplog):
    cid = company["id"]
    with caplog.at_level(logging.DEBUG):
        cred = create(client, cid).json()
        client.get(api(cid, "/credentials"))
        client.delete(api(cid, f"/credentials/{cred['id']}"))
    assert SECRET not in caplog.text
    stored = everything_stored(app)
    assert SECRET not in stored
    assert "credential.created" in stored and "credential.revoked" in stored  # but they ARE audited
    activity = client.get(api(cid, "/activity")).json()
    assert SECRET not in json.dumps(activity)


def test_validation_errors_do_not_echo_the_value(client, company):
    too_long = "k" * 9000
    r = create(client, company["id"], value=too_long)
    assert r.status_code == 422 and too_long[:50] not in r.text
    r = client.post(
        api(company["id"], "/credentials"), json={"name": "x", "value": SECRET, "oops": 1}
    )
    assert r.status_code == 422 and SECRET not in r.text


def test_names_are_unique_among_live_credentials(client, company):
    cid = company["id"]
    first = create(client, cid).json()
    assert create(client, cid).status_code == 409
    assert client.delete(api(cid, f"/credentials/{first['id']}")).status_code == 200
    assert create(client, cid).status_code == 201  # a revoked credential frees its name


def test_revoke_wipes_the_ciphertext_and_is_idempotent(app, client, company):
    cid = company["id"]
    cred = create(client, cid).json()
    r = client.delete(api(cid, f"/credentials/{cred['id']}"))
    assert r.status_code == 200 and r.json()["revoked_at"] is not None
    assert client.delete(api(cid, f"/credentials/{cred['id']}")).status_code == 200
    with app.state.session_factory() as db:
        assert db.get(Credential, cred["id"]).encrypted_value == ""


def test_a_credential_in_use_by_an_enabled_provider_cannot_be_revoked(client, company):
    cid = company["id"]
    cred = create(client, cid).json()
    provider = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "OR",
            "kind": "openrouter",
            "credential_id": cred["id"],
            "default_model": "m",
        },
    ).json()
    assert client.delete(api(cid, f"/credentials/{cred['id']}")).status_code == 409
    client.patch(api(cid, f"/model-providers/{provider['id']}"), json={"enabled": False})
    assert client.delete(api(cid, f"/credentials/{cred['id']}")).status_code == 200


def test_a_revoked_credential_cannot_be_attached(client, company):
    cid = company["id"]
    cred = create(client, cid).json()
    client.delete(api(cid, f"/credentials/{cred['id']}"))
    r = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "OR",
            "kind": "openrouter",
            "credential_id": cred["id"],
            "default_model": "m",
        },
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ tenancy + authorization


def test_credentials_are_tenant_scoped(client, company):
    other = client.post("/api/companies", json={"name": "Other Co"}).json()
    mine = create(client, company["id"]).json()
    assert client.get(api(other["id"], f"/credentials/{mine['id']}")).status_code == 404
    assert client.delete(api(other["id"], f"/credentials/{mine['id']}")).status_code == 404
    assert client.get(api(other["id"], "/credentials")).json()["items"] == []
    r = client.post(
        api(other["id"], "/model-providers"),
        json={
            "name": "OR",
            "kind": "openrouter",
            "credential_id": mine["id"],
            "default_model": "m",
        },
    )
    assert r.status_code == 404  # cannot attach another company's secret


def test_non_members_get_404(client, client_as, make_user, company):
    cred = create(client, company["id"]).json()
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(company["id"], "/credentials")).status_code == 404
    assert stranger.get(api(company["id"], f"/credentials/{cred['id']}")).status_code == 404
    assert create(stranger, company["id"], name="x").status_code == 404


def test_viewers_can_list_but_not_write(client, client_as, make_user, add_member, company):
    cid = company["id"]
    cred = create(client, cid).json()
    viewer_user = make_user("viewer@example.com")
    add_member(cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))
    listed = viewer.get(api(cid, "/credentials"))
    assert listed.status_code == 200 and SECRET not in listed.text
    assert create(viewer, cid, name="v").status_code == 403
    assert viewer.delete(api(cid, f"/credentials/{cred['id']}")).status_code == 403


def test_agents_cannot_touch_credentials_or_providers(app, client, anon_client, company):
    from tests.conftest import new_agent
    from tests.test_agent_api import start_run

    cid = company["id"]
    agent = new_agent(client, cid, name="Worker")
    cred = create(client, cid).json()
    token = start_run(app, client, cid, agent["id"]).token
    h = {"Authorization": f"Bearer {token}"}
    assert anon_client.get(api(cid, "/credentials"), headers=h).status_code == 403
    assert anon_client.get(api(cid, f"/credentials/{cred['id']}"), headers=h).status_code == 403
    assert (
        anon_client.post(
            api(cid, "/credentials"), json={"name": "evil", "value": SECRET}, headers=h
        ).status_code
        == 403
    )
    assert anon_client.delete(api(cid, f"/credentials/{cred['id']}"), headers=h).status_code == 403
    assert anon_client.get(api(cid, "/model-providers"), headers=h).status_code == 403
    assert (
        anon_client.post(
            api(cid, "/model-providers"),
            json={"name": "p", "kind": "ollama", "default_model": "m"},
            headers=h,
        ).status_code
        == 403
    )


def test_reveal_is_tenant_scoped_and_refuses_revoked(app, client, company):
    from app.core.errors import Conflict, NotFound
    from app.services import credentials as service

    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other Co"}).json()
    cred = create(client, cid).json()
    cipher = app.state.cipher
    with app.state.session_factory() as db:
        assert service.reveal(db, cipher, cid, cred["id"]) == SECRET
        with pytest.raises(NotFound):
            service.reveal(db, cipher, other["id"], cred["id"])
    client.delete(api(cid, f"/credentials/{cred['id']}"))
    with app.state.session_factory() as db, pytest.raises(Conflict):
        service.reveal(db, cipher, cid, cred["id"])


def test_a_replaced_secret_key_makes_credentials_unreadable_not_leaky(
    app, client, company, make_settings
):
    from app.services import credentials as service

    cred = create(client, company["id"]).json()
    wrong = CredentialCipher(
        SecretKeyProvider(make_settings(secret_key="rotated-key-0123456789abcdef"))
    )
    with app.state.session_factory() as db, pytest.raises(CredentialDecryptError):
        service.reveal(db, wrong, company["id"], cred["id"])


def test_company_responses_carry_no_secret_material(client, company):
    assert "secret" not in json.dumps(client.get(api(company["id"])).json()).lower()
