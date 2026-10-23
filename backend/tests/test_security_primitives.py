from __future__ import annotations

import json
import os
import stat

import pytest

from app.core.config import Settings
from app.core.keys import KEY_FILENAME, SecretKeyProvider
from app.core.tokens import TOKEN_PREFIX, RunTokenClaims, mint_run_token, verify_run_token
from app.runtimes.registry import build_default_registry
from app.runtimes.scripted import ScriptedRuntime
from app.services.redaction import REDACTED, redact_payload
from tests.helpers import api, login

KEY = b"k" * 32
CLAIMS = RunTokenClaims("run-1", "agent-1", "company-1", 2_000_000_000)


def test_token_roundtrip_and_claims():
    token = mint_run_token(KEY, CLAIMS)
    assert token.startswith(TOKEN_PREFIX)
    assert verify_run_token(KEY, token, now_epoch=1_999_999_999) == CLAIMS
    assert verify_run_token(KEY, token, now_epoch=2_000_000_000) is None  # exp is exclusive
    assert verify_run_token(KEY, token, now_epoch=2_000_000_001) is None


def test_token_tamper_resistance():
    token = mint_run_token(KEY, CLAIMS)
    body, sig = token[len(TOKEN_PREFIX) :].split(".")
    forged_claims = mint_run_token(
        KEY, RunTokenClaims("run-1", "agent-1", "company-2", 2_000_000_000)
    )
    forged_body = forged_claims[len(TOKEN_PREFIX) :].split(".")[0]
    for bad in (
        token + "x",
        TOKEN_PREFIX + body + "." + "A" * len(sig),
        TOKEN_PREFIX + forged_body + "." + sig,  # claims swapped, signature reused
        TOKEN_PREFIX + body,
        TOKEN_PREFIX,
        "",
        "Bearer " + token,
        token.replace(TOKEN_PREFIX, "other."),
        TOKEN_PREFIX + "!!!." + sig,
    ):
        assert verify_run_token(KEY, bad, now_epoch=1) is None, bad
    assert verify_run_token(b"x" * 32, token, now_epoch=1) is None  # other key


def test_token_payload_contains_no_secret_material():
    token = mint_run_token(KEY, CLAIMS)
    import base64

    body = token[len(TOKEN_PREFIX) :].split(".")[0]
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert set(data) == {"run_id", "agent_id", "company_id", "exp"}


def test_secret_key_comes_from_env_setting_first(tmp_path):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'a.db'}", secret_key="from-env-1234"
    )
    provider = SecretKeyProvider(settings)
    assert provider.get() == b"from-env-1234"
    assert not (tmp_path / KEY_FILENAME).exists()
    assert "from-env" not in repr(provider) and "from-env" not in repr(settings)


def test_secret_key_file_is_generated_once_with_private_permissions(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'a.db'}")
    first = SecretKeyProvider(settings).get()
    path = tmp_path / KEY_FILENAME
    assert path.exists() and len(first) >= 32
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert SecretKeyProvider(settings).get() == first  # another process reuses it
    assert (
        SecretKeyProvider(settings).get()
        != SecretKeyProvider(
            Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'sub' / 'b.db'}")
        ).get()
    )  # per data dir


def test_corrupt_key_file_is_refused(tmp_path):
    (tmp_path / KEY_FILENAME).write_text("short")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'a.db'}")
    with pytest.raises(RuntimeError, match="not a valid secret key"):
        SecretKeyProvider(settings).get()


def test_secret_key_never_appears_in_openapi_or_api_responses(client, company):
    key = client.app.state.keys.get().decode()
    for path in (
        "/api/openapi.json",
        api(company["id"], "/policy"),
        "/api/runtimes",
        api(company["id"], "/overview"),
    ):
        assert key not in client.get(path).text


def test_data_dir_defaults_next_to_the_sqlite_file(tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'x' / 'db.sqlite'}")
    assert s.resolved_data_dir == tmp_path / "x"
    assert (
        s.artifacts_dir == tmp_path / "x" / "artifacts"
        and s.workspaces_dir == tmp_path / "x" / "workspaces"
    )
    assert Settings(_env_file=None, data_dir=tmp_path / "d").resolved_data_dir == tmp_path / "d"


# ------------------------------------------------------------------ redaction


def test_redaction_masks_secret_keys_at_any_depth_without_mutating_input():
    payload = {
        "token": "t",
        "ok": 1,
        "nested": {"Authorization": "Bearer x", "list": [{"client_secret": "s", "n": 2}]},
        "monkey": "keyboard",  # benign words are not flagged
    }
    out = redact_payload(payload)
    assert out == {
        "token": REDACTED,
        "ok": 1,
        "nested": {"Authorization": REDACTED, "list": [{"client_secret": REDACTED, "n": 2}]},
        "monkey": "keyboard",
    }
    assert payload["token"] == "t"


def test_redaction_masks_known_secret_values_inside_strings():
    out = redact_payload({"log": "calling with cos_run.abc.def now"}, ["cos_run.abc.def"])
    assert out == {"log": f"calling with {REDACTED} now"}


def test_redaction_bounds_size():
    out = redact_payload({"big": "x" * 50_000})
    assert len(out["big"]) < 11_000 and out["big"].endswith("...[truncated]")
    wide = redact_payload({"k": ["y" * 5000] * 200})
    assert wide.get("truncated") is True or len(json.dumps(wide)) <= 64_000
    assert redact_payload("plain string") == {"value": "plain string"}
    deep: dict = {}
    cur = deep
    for _ in range(30):
        cur["n"] = {}
        cur = cur["n"]
    assert "[truncated]" in json.dumps(redact_payload(deep))


# ------------------------------------------------------------------ test runtime isolation


def test_scripted_runtime_is_off_by_default_and_labelled_when_on(make_settings, client):
    default = build_default_registry(make_settings())
    assert default.get("scripted") is None and [a.id for a in default.all()] == ["hermes"]
    enabled = build_default_registry(make_settings(enable_test_runtimes=True))
    assert isinstance(enabled.get("scripted"), ScriptedRuntime)
    assert enabled.get("scripted").kind == "test_fixture"
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(
        create_app(make_settings(enable_test_runtimes=True), runtime_registry=enabled)
    ) as c:
        items = {i["id"]: i for i in login(c).get("/api/runtimes").json()["items"]}
    assert (
        items["scripted"]["kind"] == "test_fixture"
        and items["scripted"]["health"]["status"] == "ok"
    )
    assert items["hermes"]["kind"] == "agent_runtime"
    # the default app of this suite (no flag) reports only what it was given
    assert Settings(_env_file=None).enable_test_runtimes is False


def test_enable_test_runtimes_env_var(monkeypatch):
    monkeypatch.setenv("COMPANY_OS_ENABLE_TEST_RUNTIMES", "1")
    assert Settings(_env_file=None).enable_test_runtimes is True
    monkeypatch.setenv("COMPANY_OS_EMBEDDED_WORKER", "1")
    assert Settings(_env_file=None).embedded_worker is True
