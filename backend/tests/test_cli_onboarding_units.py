"""`foundryos setup`: the pure rules (app/cli/onboarding.py) and the small contracts the wizard relies on."""

from __future__ import annotations

import httpx
import pytest

from app.cli import onboarding
from app.cli.client import ApiClient, CliError
from app.runtimes.cli.catalog import CLIS, assert_safe_argv


def test_secrets_stdin_splits_on_the_first_equals_and_rejects_the_unknown():
    text = "# a comment\n\npassword=pa=ss word=with=equals\napi_key=sk-abc\r\nsetup_token=tok\ncli_api_key=k2\n"
    assert onboarding.parse_secrets(text) == {
        "password": "pa=ss word=with=equals",
        "api_key": "sk-abc",
        "setup_token": "tok",
        "cli_api_key": "k2",
    }
    for bad in ("passwd=x", "password", "password=", "password=a\npassword=b"):
        with pytest.raises(CliError) as exc:
            onboarding.parse_secrets(bad)
        assert exc.value.exit_code == 2  # a usage error
    with pytest.raises(CliError) as exc:
        onboarding.parse_secrets("tokenn=hunter2-secret-value")
    assert "hunter2" not in exc.value.message  # the message names the key, never a value


def test_redact_hides_secrets_but_not_short_noise():
    assert (
        onboarding.redact("bad key sk-live-1234 here", "sk-live-1234") == "bad key [redacted] here"
    )
    assert onboarding.redact("abc", "ab") == "abc"  # too short to be a meaningful secret
    assert onboarding.redact("x", None) == "x"


def test_idea_rule_matches_the_web():
    onboarding.idea_error("")  # optional
    onboarding.idea_error("x" * 280)
    with pytest.raises(CliError):
        onboarding.idea_error("x" * 281)
    with pytest.raises(CliError):
        onboarding.idea_error("two\nlines")


def test_timezone_detection_order(tmp_path):
    tzfile = tmp_path / "timezone"
    link = tmp_path / "localtime"
    none = {"environ": {}, "timezone_file": tzfile, "localtime_link": link}
    assert onboarding.detect_timezone(**none) == "UTC"
    link.symlink_to("/usr/share/zoneinfo/Europe/Berlin")
    assert onboarding.detect_timezone(**none) == "Europe/Berlin"
    tzfile.write_text("Asia/Tokyo\n")
    assert onboarding.detect_timezone(**none) == "Asia/Tokyo"
    assert (
        onboarding.detect_timezone(**{**none, "environ": {"TZ": ":America/New_York"}})
        == "America/New_York"
    )
    # a TZ that is not an IANA name is ignored, not trusted
    assert (
        onboarding.detect_timezone(**{**none, "environ": {"TZ": "EST5EDT garbage"}}) == "Asia/Tokyo"
    )
    assert onboarding.is_valid_timezone("UTC") and not onboarding.is_valid_timezone("Mars/Base")
    with pytest.raises(CliError):
        onboarding.timezone_error("Mars/Base")


def test_provider_presets_mirror_the_web_cards():
    assert [p.flag for p in onboarding.ALL_PRESETS] == [
        "openrouter", "anthropic", "openai", "openai-compatible", "ollama",
    ]  # fmt: skip
    pre = onboarding.PRESET_BY_FLAG
    assert pre["openrouter"].needs_key and not pre["openrouter"].needs_base_url
    assert pre["openai-compatible"].needs_base_url and not pre["openai-compatible"].needs_key
    assert pre["openai-compatible"].takes_key and not pre["ollama"].takes_key
    assert onboarding.credential_name(pre["openai-compatible"]) == "openai_compatible-key"
    assert pre["openrouter"].model_suggestions == ("anthropic/claude-sonnet-4.5", "openai/gpt-4.1")


def test_provider_body_references_the_credential_and_never_holds_a_secret():
    pre = onboarding.PRESET_BY_FLAG
    body = onboarding.provider_body(
        pre["openrouter"], base_url="https://ignored", model=" m ", credential_id="c1"
    )
    assert body == {
        "name": "OpenRouter", "kind": "openrouter", "default_model": "m",
        "is_local": False, "enabled": True, "credential_id": "c1",
    }  # fmt: skip
    local = onboarding.provider_body(
        pre["openai-compatible"], base_url="http://127.0.0.1:8080/v1", model="m", credential_id=None
    )
    assert local["is_local"] is True and local["base_url"] == "http://127.0.0.1:8080/v1"
    remote = onboarding.provider_body(
        pre["openai-compatible"],
        base_url="http://192.168.1.5/v1",
        model="m",
        credential_id=None,
        allow_private_network=True,
    )
    assert remote["is_local"] is False and remote["allow_private_network"] is True


def test_url_and_key_validation():
    assert onboarding.base_url_error("https://host.example/v1") is None
    assert onboarding.base_url_error("ftp://host") and onboarding.base_url_error("not a url")
    assert "credentials" in onboarding.base_url_error("https://u:p@host/v1")
    assert onboarding.api_key_error("sk-ok") is None
    assert onboarding.api_key_error("sk bad") and onboarding.api_key_error("  ")
    assert onboarding.is_loopback_url("http://127.0.0.1:8700") and onboarding.is_loopback_url(
        "http://localhost"
    )
    assert not onboarding.is_loopback_url("http://192.168.1.5:8700")
    assert onboarding.url_port("http://h:9001") == 9001 and onboarding.url_port("http://h") == 8700


def _test(ok_names, bad=None, detail="nope"):
    checks = [
        {"name": n, "ok": n not in (bad or ()), "detail": detail if n in (bad or ()) else "fine"}
        for n in ok_names
    ]
    return {"ok": not bad, "checks": checks}


def test_check_lines_show_the_five_checks_and_the_first_failure():
    names = list(onboarding.CHECK_ORDER)
    good = _test(names)
    assert onboarding.provider_test_passed(good)
    lines = onboarding.check_lines(good)
    assert [ln.split(":")[0] for ln in lines] == [
        "[ok] Reachable", "[ok] Authenticated", "[ok] Tool calling", "[ok] Context window", "[ok] Model available",
    ]  # fmt: skip
    bad = _test(names, bad={"auth", "context"}, detail="key rejected")
    assert not onboarding.provider_test_passed(bad)
    assert onboarding.first_failure(bad)["name"] == "auth"
    assert onboarding.failure_summary(bad) == "Authenticated failed: key rejected"
    assert "[FAIL] Authenticated: key rejected" in onboarding.check_lines(bad)
    partial = onboarding.check_lines(_test(["reachable", "auth"]))
    assert partial[-1] == "[--] Model available (not checked)"
    # a test that says ok but reports a failing check is not trusted; an empty/None test never passes
    assert not onboarding.provider_test_passed(
        {"ok": True, "checks": [{"name": "auth", "ok": False}]}
    )
    assert not onboarding.provider_test_passed(None) and not onboarding.provider_test_passed({})


def test_model_list_normalising_and_filtering():
    raw = {
        "items": [
            {"id": "gpt-4.1", "context_length": 128000},
            {"name": "gpt-4o"},
            "claude-x",
            {"id": "gpt-4.1"},
            7,
        ]
    }
    models = onboarding.normalize_models(raw)
    assert [m.id for m in models] == ["gpt-4.1", "gpt-4o", "claude-x"]
    assert models[0].context_length == 128000 and models[1].context_length is None
    assert [m.id for m in onboarding.filter_models(models, "GPT")] == ["gpt-4.1", "gpt-4o"]
    assert [m.id for m in onboarding.filter_models(models, "x")] == ["claude-x"]
    # prefix matches come before substring matches
    more = onboarding.normalize_models(["o-gpt", "gpt-z"])
    assert [m.id for m in onboarding.filter_models(more, "gpt")] == ["gpt-z", "o-gpt"]
    assert len(onboarding.filter_models(models, "", limit=2)) == 2


def test_options_are_validated_before_anything_happens():
    ok = onboarding.WizardOptions
    onboarding.validate_options(ok())
    for bad in (
        ok(only="nope"), ok(only="work"), ok(public_url="lan"), ok(provider="gemini"),
        ok(public_url="named"), ok(idea="x" * 300), ok(timezone="Mars/Base"),
        ok(base_url="ftp://x"), ok(provider="claude-plan", model="opus"),
        ok(provider="cursor-plan", base_url="https://x.example"),
    ):  # fmt: skip
        with pytest.raises(CliError) as exc:
            onboarding.validate_options(bad)
        assert exc.value.exit_code == 2, bad
    onboarding.validate_options(ok(public_url="named", hostname="foundry.example.com"))
    onboarding.validate_options(ok(provider="claude-plan"))
    onboarding.validate_options(ok(provider="cursor-plan"))


def test_qr_text_draws_or_returns_none():
    qr = onboarding.qr_text("otpauth://totp/Foundry:alice?secret=ABC234&issuer=Foundry")
    assert qr and "\n" in qr


def test_headless_login_argv_is_defined_for_the_own_login_clis_and_is_safe():
    assert CLIS["claude_code"].headless_login_argv == ("claude", "auth", "login")
    assert CLIS["codex"].headless_login_argv == ("codex", "login", "--device-auth")
    assert CLIS["opencode"].headless_login_argv == ("opencode", "auth", "login")
    assert CLIS["antigravity"].headless_login_argv is None  # api_key only
    assert CLIS["cursor"].headless_login_argv is None  # api_key only
    for spec in CLIS.values():
        if spec.headless_login_argv:
            assert_safe_argv(list(spec.headless_login_argv))
        assert (spec.headless_login_argv is None) == ("own_login" not in spec.auth_modes)


def test_api_client_per_request_timeout_reaches_httpx():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.extensions["timeout"]
        return httpx.Response(200, json={})

    http = httpx.Client(
        base_url="http://x",
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(30.0, connect=5.0),
    )
    api = ApiClient(http, None)
    api.get("/a")
    api.post("/b", {}, timeout=180)
    api.put("/c", {}, timeout=120)
    assert seen["/api/a"]["read"] == 30.0  # the default is untouched
    assert seen["/api/b"]["read"] == 180.0 and seen["/api/b"]["connect"] == 5.0
    assert seen["/api/c"]["read"] == 120.0
