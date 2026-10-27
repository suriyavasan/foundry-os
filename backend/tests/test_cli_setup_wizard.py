"""`foundryos setup` against an in-process app (Typer CliRunner + TestClient as the HTTP client).

Providers are faked with ``app.state.provider_transport`` (httpx.MockTransport); coding CLIs are fake
binaries; the pieces written by other packages (service, headless, tunnel) are monkeypatched, so nothing
here starts a process, calls a real provider, runs a real CLI or opens a tunnel."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pyotp
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from app.cli import config, context
from app.cli.commands import onboard
from app.cli.main import app as cli_app
from app.models.audit import AuditEvent
from app.models.user import User
from tests.accounts_helpers import PASSWORD, browser, do_setup
from tests.helpers import api
from tests.m10_helpers import (
    CODEX_OK,
    install_fake_cli,
    make_ready,
    register_cli_adapters,
    write_script,
)
from tests.test_model_providers import TOOL_CALL_REPLY

pytestmark = pytest.mark.ai_gate  # the readiness gate is enforced exactly as in production

KEY = "sk-or-test-key-0123456789abcdef"
BAD_KEY = "sk-BAD-key-0123456789abcdef"
IDEA = "A newsletter that explains one research paper a day"
CLI_KEY = "sk-ant-cli-key-0123456789abcdef"
MODEL = "anthropic/claude-sonnet-4.5"


@dataclass
class Calls:
    service: list[dict] = field(default_factory=list)
    tunnel: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)
    logins: list[list[str]] = field(default_factory=list)


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self):
        return json.loads(self.out)


@pytest.fixture
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "cfg"
    monkeypatch.setenv("COMPANYOS_CONFIG_DIR", str(d))
    for name in ("COMPANYOS_TOKEN", "COMPANYOS_API_URL", "COMPANYOS_COMPANY", "TZ"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    return d


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Calls:
    """The contracts other packages provide, replaced by recorders."""
    from app.cli import tunnel_flow
    from app.ops import headless, lifecycle

    rec = Calls()

    def ensure_running(settings, say, *, service, linger):
        rec.service.append({"service": service, "linger": linger})
        say("(fake) ensure_running")

    def enable_public_url(st, *, provider, hostname, confirm, say, timeout=60):
        rec.tunnel.append({"provider": provider, "hostname": hostname})
        plan = {
            "version": "2026.1.0",
            "url": "https://example.test/cloudflared",
            "sha256": "ab" * 32,
        }
        rec.plans.append(plan)
        if not confirm(plan):
            return None
        say("(fake) tunnel up")
        return "https://quiet-river.trycloudflare.com"

    monkeypatch.setattr(lifecycle, "ensure_running", ensure_running, raising=False)
    monkeypatch.setattr(tunnel_flow, "enable_public_url", enable_public_url)
    monkeypatch.setattr(headless, "DOCKERENV", tmp_path / "no-dockerenv")
    monkeypatch.setattr(headless, "is_headless", lambda *a, **k: True)
    monkeypatch.setattr(onboard, "sleep", lambda s: None)
    monkeypatch.setattr(onboard, "run_interactive", lambda argv: rec.logins.append(list(argv)) or 0)
    return rec


def openrouter_transport(*, good: str = KEY) -> httpx.MockTransport:
    """OpenRouter/OpenAI-shaped answers for a key; any other key is rejected."""

    def handler(req: httpx.Request) -> httpx.Response:
        if good not in req.headers.get("authorization", ""):
            return httpx.Response(401, json={"error": "bad key"})
        if req.method == "POST":
            return httpx.Response(200, json=TOOL_CALL_REPLY)
        if req.url.path.endswith("/auth/key"):
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": MODEL, "context_length": 200000},
                    {"id": "gpt-4.1", "context_length": 128000},
                    {"id": "big-model", "context_length": 128000},
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def wiz(app, cfg_dir, calls, monkeypatch):
    """Invoke `foundryos` as a person on the server machine (loopback), nobody signed in yet."""
    monkeypatch.setattr(context, "http_factory", lambda url: browser(app, loopback=True))
    app.state.provider_transport = openrouter_transport()
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None, expect: int | None = 0) -> Result:
        r = runner.invoke(cli_app, list(args), input=input)
        res = Result(r.exit_code, r.stdout, r.stderr)
        if expect is not None:
            assert res.code == expect, (
                f"exit {res.code} (wanted {expect})\nout: {res.out}\nerr: {res.err}"
            )
        return res

    return invoke


def admin(app):
    """A browser on the server machine that acts as the owner (to look at what the wizard did)."""
    b = browser(app, loopback=True)
    b.headers["Authorization"] = f"Bearer {app.state.owner_token.get()}"
    return b


def companies(app) -> list[dict]:
    return admin(app).get("/api/companies").json()["items"]


def everything_written(app) -> str:
    with app.state.session_factory() as db:
        rows = db.scalars(select(AuditEvent)).all()
        return "\n".join(f"{r.action} {r.summary} {json.dumps(r.data)}" for r in rows)


# ------------------------------------------------------------------ a full interactive run


def test_full_interactive_setup_creates_everything(app, wiz, calls, cfg_dir):
    answers = "\n".join(
        [
            "y",  # keep Foundry running in the background and after reboot
            "alice",  # username
            "Alice Founder",  # display name
            "",  # email: none
            PASSWORD, PASSWORD,  # password + repeat (hidden)
            "y",  # a public URL?
            "n",  # two-factor: not now
            "Acme Labs",  # company
            IDEA,
            "",  # timezone: this machine's
            "1",  # OpenRouter
            KEY,  # API key (hidden)
            MODEL,  # model
            "n",  # pick from the provider's list? no
            "1",  # work mode: API only
            "3",  # GitHub: no repository
            "y",  # download and open the tunnel
        ]
    ) + "\n"  # fmt: skip
    r = wiz("setup", input=answers)
    out = r.out + r.err

    # the owner exists and the exposure gate is open (password_changed_at)
    with app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.username == "alice"))
        assert user is not None and user.display_name == "Alice Founder"
        assert user.password_changed_at is not None and user.totp_enabled is False
    # a personal token was saved for this machine, private, and named after the host
    token_file = config.token_path()
    assert token_file.read_text().strip().startswith("cos_pat_")
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    tokens = browser(app, loopback=True)
    tokens.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    names = [t["name"] for t in tokens.get("/api/auth/tokens").json()["items"]]
    assert len(names) == 1 and names[0].startswith("foundryos-cli@")
    # company, note, AI
    [company] = companies(app)
    assert company["name"] == "Acme Labs"
    notes = admin(app).get(api(company["id"], "/notes")).json()["items"]
    assert [n["body"] for n in notes] == [IDEA]
    ai = admin(app).get(api(company["id"], "/ai-setup")).json()
    assert ai["ready"] is True and ai["work_mode"] == "api" and ai["management"]["model"] == MODEL
    assert ai["management"]["kind"] == "openrouter"
    providers = admin(app).get(api(company["id"], "/model-providers")).json()["items"]
    assert [(p["name"], p["enabled"]) for p in providers] == [("OpenRouter", True)]
    creds = admin(app).get(api(company["id"], "/credentials")).json()["items"]
    assert [c["name"] for c in creds] == ["openrouter-key"]
    # the other packages' steps were called with the right arguments
    assert calls.service == [{"service": True, "linger": True}]
    assert calls.tunnel == [{"provider": "quick", "hostname": None}]
    assert (
        "https://quiet-river.trycloudflare.com" in out
        and "changes whenever the tunnel restarts" in out
    )
    assert "reach --watch" in out
    # the summary: local URL, how to reach it from your own computer (headless), sign-in, next commands
    assert "http://127.0.0.1:8700" in out and "ssh -N -L 8700:127.0.0.1:8700" in out
    assert "alice" in out and "foundryos status" in out
    for check in (
        "Reachable",
        "Authenticated",
        "Tool calling",
        "Context window",
        "Model available",
    ):
        assert f"[ok] {check}" in out
    # no secret anywhere: output, the config dir, audit rows
    for secret in (PASSWORD, KEY):
        assert secret not in out
        assert secret not in everything_written(app)
        for f in cfg_dir.rglob("*"):
            if f.is_file():
                assert secret not in f.read_text()


def test_two_factor_enrolment_in_the_wizard(app, wiz, calls, monkeypatch):
    seen = {}
    real_qr = onboard.onboarding.qr_text

    def spy(uri):
        seen["uri"] = uri
        return real_qr(uri)

    monkeypatch.setattr(onboard.onboarding, "qr_text", spy)

    # CliRunner input is fixed up front but the TOTP secret only exists after enrolment: answer the
    # code prompt from the otpauth URI the wizard printed (what an authenticator app would do).
    secret_holder = {}
    real_ask = onboard.Wizard.ask

    def ask(self, text, **kw):
        if text.startswith("Enter the 6-digit code"):
            secret = seen["uri"].split("secret=")[1].split("&")[0]
            secret_holder["secret"] = secret
            return pyotp.TOTP(secret).now()
        return real_ask(self, text, **kw)

    monkeypatch.setattr(onboard.Wizard, "ask", ask)
    answers = "\n".join(["alice", "Alice", "", PASSWORD, PASSWORD]) + "\n"
    r = wiz("setup", "--only", "account", input=answers)
    assert "Created the owner account alice" in r.out
    # 2FA is its own step; it signs in again with the password because --only account closed the session
    r = wiz("setup", "--only", "2fa", "--username", "alice", "--2fa", input=PASSWORD + "\n")
    out = r.out
    assert (
        "Scan this QR code" in out and secret_holder["secret"] in out and "otpauth://totp/" in out
    )
    assert "Recovery codes" in out
    with app.state.session_factory() as db:
        assert db.scalar(select(User).where(User.username == "alice")).totp_enabled is True
    codes = [
        ln.strip()
        for ln in out.splitlines()
        if ln.startswith("  ") and len(ln.strip()) == 11 and "-" in ln
    ]
    assert len(codes) >= 8
    # a rerun is a no-op
    again = wiz("setup", "--only", "2fa", input="")
    assert "Already done: two-factor sign-in is on." in again.out


# ------------------------------------------------------------------ scripted runs


def scripted_args(*extra: str) -> list[str]:
    return [
        "setup", "--non-interactive", "--secrets-stdin", "--no-service",
        "--username", "alice", "--company-name", "Acme Labs", "--idea", IDEA,
        "--timezone", "Europe/Berlin", "--provider", "openai-compatible",
        "--base-url", "https://llm.example.com/v1", "--model", "big-model", *extra,
    ]  # fmt: skip


def test_non_interactive_run_keeps_secrets_out_of_argv_output_and_audit(app, wiz, calls, cfg_dir):
    args = scripted_args("--json", "--public-url", "quick")
    assert PASSWORD not in " ".join(args) and KEY not in " ".join(args)
    r = wiz(*args, input=f"password={PASSWORD}\napi_key={KEY}\n")
    summary = r.json()
    assert summary["ok"] is True and summary["username"] == "alice"
    assert summary["company"]["name"] == "Acme Labs"
    assert summary["public_url"] == "https://quiet-river.trycloudflare.com"
    assert summary["local_url"] == "http://127.0.0.1:8700"
    assert summary["remote_access"] and summary["headless"] is True
    assert summary["token_file"].endswith("token")
    assert summary["ai"] == {
        "ready": True,
        "provider": "OpenAI-compatible",
        "kind": "openai_compatible",
        "model": "big-model",
    }
    assert summary["work"]["mode"] == "api"
    assert summary["steps"] == {
        "service": "already_done", "account": "done", "2fa": "skipped", "company": "done",
        "ai": "done", "work": "done", "repo": "skipped", "public-url": "done", "idea": "note",
    }  # fmt: skip
    # stdout is exactly one JSON document; progress went to stderr
    assert r.out.lstrip().startswith("{") and "Created the owner account" in r.err
    assert calls.tunnel == [{"provider": "quick", "hostname": None}]
    assert calls.plans and "Going ahead because --public-url was given" in r.err
    assert calls.service == []  # --no-service and the server was already up
    for secret in (PASSWORD, KEY):
        assert secret not in r.out + r.err
        assert secret not in everything_written(app)
    # the company got the timezone and the note
    [company] = companies(app)
    assert company["timezone"] == "Europe/Berlin"
    notes = admin(app).get(api(company["id"], "/notes")).json()["items"]
    assert [n["body"] for n in notes] == [IDEA]


def test_a_rerun_skips_what_is_done(app, wiz, calls):
    wiz(*scripted_args("--json"), input=f"password={PASSWORD}\napi_key={KEY}\n")
    [company] = companies(app)
    before_tokens = len(_tokens(app))
    r = wiz("setup", "--non-interactive", "--no-service", "--json")
    s = r.json()
    assert s["steps"]["account"] == "already_done" and s["steps"]["company"] == "already_done"
    assert s["steps"]["ai"] == "already_done" and s["steps"]["work"] == "already_done"
    assert "Already done" in r.err
    # nothing was created twice
    assert len(companies(app)) == 1
    assert len(_tokens(app)) == before_tokens
    assert len(admin(app).get(api(company["id"], "/model-providers")).json()["items"]) == 1
    assert len(admin(app).get(api(company["id"], "/notes")).json()["items"]) == 1
    # --only reruns just that step
    r = wiz("setup", "--only", "company", "--non-interactive", "--json")
    assert r.json()["steps"] == {"company": "already_done"}


def _tokens(app) -> list[dict]:
    b = browser(app, loopback=True)
    assert (
        b.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code
        == 200
    )
    return b.get("/api/auth/tokens").json()["items"]


def test_missing_non_interactive_inputs_exit_2_and_create_nothing(app, wiz, calls):
    r = wiz("setup", "--non-interactive", "--no-service", expect=2)
    for missing in ("--username", "password", "--company-name", "--provider"):
        assert missing in r.err
    assert companies(app) == []
    # partial: account flags but no key for a provider that needs one
    r = wiz(
        "setup", "--non-interactive", "--secrets-stdin", "--no-service", "--username", "alice",
        "--company-name", "X", "--provider", "openrouter", input=f"password={PASSWORD}\n", expect=2,
    )  # fmt: skip
    assert "--model" in r.err and "api_key" in r.err and PASSWORD not in r.err
    with app.state.session_factory() as db:
        assert (
            db.scalar(select(User).where(User.username == "alice")) is None
        )  # nothing was created
    # bad secrets file, bad flags
    assert (
        "unknown key"
        in wiz("setup", "--non-interactive", "--secrets-stdin", input="passwd=x\n", expect=2).err
    )
    wiz("setup", "--only", "nonsense", expect=2)
    wiz("setup", "--public-url", "named", expect=2)
    r = wiz("setup", "--non-interactive", "--2fa", expect=2)
    assert "authenticator" in r.err


def test_a_weak_password_asks_again_with_the_servers_message(app, wiz, calls):
    weak = "password123"
    r = wiz(
        "setup", "--only", "account", "--username", "alice", "--display-name", "Alice", "--email", "",
        input=f"{weak}\n{weak}\n{PASSWORD}\n{PASSWORD}\n",
    )  # fmt: skip
    with app.state.session_factory() as db:
        assert db.scalar(select(User).where(User.username == "alice")) is not None
    assert "! " in r.err and "Created the owner account alice" in r.out
    assert weak not in r.out + r.err  # the weak password is not echoed back either
    assert _tokens(app)  # the account works with the good password


def test_a_weak_password_in_a_script_fails_instead_of_looping(app, wiz, calls):
    r = wiz(
        "setup", "--only", "account", "--non-interactive", "--secrets-stdin", "--username", "alice",
        input="password=password123\n", expect=1,
    )  # fmt: skip
    assert "12 characters" in r.err and "password123" not in r.out + r.err
    with app.state.session_factory() as db:
        assert db.scalar(select(User).where(User.username == "alice")) is None


def test_a_non_loopback_server_needs_the_setup_token(app, cfg_dir, calls, monkeypatch):
    monkeypatch.setattr(context, "http_factory", lambda url: browser(app))  # a peer on the LAN
    app.state.provider_transport = openrouter_transport()
    runner = CliRunner()
    base = [
        "--api-url", "http://192.168.1.50:8700", "setup", "--only", "account", "--non-interactive",
        "--secrets-stdin", "--username", "alice",
    ]  # fmt: skip
    r = runner.invoke(cli_app, base, input=f"password={PASSWORD}\n")
    assert r.exit_code == 2 and "setup_token" in r.stderr
    r = runner.invoke(cli_app, base, input=f"password={PASSWORD}\nsetup_token=wrong-token\n")
    assert (
        r.exit_code != 0
        and "setup token" in r.stderr.lower()
        and "wrong-token" not in r.stdout + r.stderr
    )
    with app.state.session_factory() as db:
        assert db.scalar(select(User).where(User.username == "alice")) is None
    token = app.state.owner_token.get()
    r = runner.invoke(cli_app, base, input=f"password={PASSWORD}\nsetup_token={token}\n")
    assert r.exit_code == 0, r.stderr + r.stdout
    assert token not in r.stdout + r.stderr and PASSWORD not in r.stdout + r.stderr
    assert config.load_config()["api_url"] == "http://192.168.1.50:8700"
    r = runner.invoke(
        cli_app,
        ["--api-url", "http://192.168.1.50:8700", "setup", "--only", "service", "--service"],
    )
    assert r.exit_code == 0 and "remote server" in r.stdout
    assert calls.service == []  # a remote server: nothing to start on this machine


def test_interactive_remote_setup_asks_for_the_token_hidden(app, cfg_dir, calls, monkeypatch):
    monkeypatch.setattr(context, "http_factory", lambda url: browser(app))
    token = app.state.owner_token.get()
    r = CliRunner().invoke(
        cli_app,
        ["--api-url", "http://192.168.1.50:8700", "setup", "--only", "account"],
        input=f"alice\nAlice\n\n{token}\n{PASSWORD}\n{PASSWORD}\n",
    )
    assert r.exit_code == 0, r.stderr + r.stdout
    assert "setup token" in r.stdout.lower() and token not in r.stdout + r.stderr


# ------------------------------------------------------------------ the model API


def prepared(app, monkeypatch, wiz_company: bool = True):
    """An installation with an owner (and a company) and a token in the environment."""
    b = do_setup(app)
    token = b.post("/api/auth/tokens", json={"name": "t", "scope": "admin"}).json()["token"]
    monkeypatch.setenv("COMPANYOS_TOKEN", token)
    cid = None
    if wiz_company:
        cid = b.post("/api/companies", json={"name": "Acme Labs"}).json()["id"]
    monkeypatch.setattr(context, "http_factory", lambda url: browser(app, loopback=True))
    return b, token, cid


def test_a_failed_provider_test_loops_and_the_bad_provider_ends_disabled(
    app, wiz, calls, monkeypatch
):
    b, _, cid = prepared(app, monkeypatch)
    app.state.provider_transport = openrouter_transport(good=KEY)
    r = wiz(
        "setup", "--only", "ai",
        input="\n".join(
            [
                "1",  # OpenRouter
                BAD_KEY, MODEL,
                "o",  # the key was rejected: use another provider
                "4",  # OpenAI-compatible
                "https://llm.example.com/v1", KEY, "big-model",
                "n",  # no model list
            ]
        ) + "\n",
    )  # fmt: skip
    out = r.out + r.err
    assert "[FAIL] Authenticated" in out
    assert "failed its test and was switched off" in out
    providers = {p["name"]: p for p in admin(app).get(api(cid, "/model-providers")).json()["items"]}
    assert providers["OpenRouter"]["enabled"] is False
    assert providers["OpenAI-compatible"]["enabled"] is True
    ai = admin(app).get(api(cid, "/ai-setup")).json()
    assert ai["ready"] is True and ai["management"]["provider_name"] == "OpenAI-compatible"
    for secret in (BAD_KEY, KEY):
        assert secret not in out and secret not in everything_written(app)


def test_retrying_the_same_provider_reuses_it_and_replaces_the_key(app, wiz, calls, monkeypatch):
    b, _, cid = prepared(app, monkeypatch)
    r = wiz(
        "setup", "--only", "ai",
        input="\n".join(["1", BAD_KEY, MODEL, "r", KEY, MODEL, "n"]) + "\n",
    )  # fmt: skip
    providers = admin(app).get(api(cid, "/model-providers")).json()["items"]
    assert [(p["name"], p["enabled"]) for p in providers] == [("OpenRouter", True)]
    creds = admin(app).get(api(cid, "/credentials")).json()["items"]
    assert [c["name"] for c in creds] == ["openrouter-key"]  # rotated, not duplicated
    assert admin(app).get(api(cid, "/ai-setup")).json()["ready"] is True
    assert BAD_KEY not in r.out + r.err


def test_a_failed_provider_test_in_a_script_exits_1_and_leaves_it_disabled(
    app, wiz, calls, monkeypatch
):
    b, _, cid = prepared(app, monkeypatch)
    r = wiz(
        "setup", "--only", "ai", "--non-interactive", "--secrets-stdin", "--provider", "openrouter",
        "--model", MODEL, input=f"api_key={BAD_KEY}\n", expect=1,
    )  # fmt: skip
    assert "did not pass its test" in r.err and BAD_KEY not in r.out + r.err
    providers = admin(app).get(api(cid, "/model-providers")).json()["items"]
    assert [(p["name"], p["enabled"]) for p in providers] == [("OpenRouter", False)]
    assert admin(app).get(api(cid, "/ai-setup")).json()["ready"] is False


def test_picking_the_model_from_the_providers_list(app, wiz, calls, monkeypatch):
    b, _, cid = prepared(app, monkeypatch)
    r = wiz(
        "setup", "--only", "ai",
        input="\n".join(["1", KEY, "not-a-real-model", "y", "gpt", "1"]) + "\n",
    )  # fmt: skip
    assert "offers 3 models" in r.out and "but not 'not-a-real-model'" in r.out
    ai = admin(app).get(api(cid, "/ai-setup")).json()
    assert ai["ready"] is True and ai["management"]["model"] == "gpt-4.1"


def test_ollama_is_detected_and_registered(app, wiz, calls, monkeypatch):
    from tests.test_model_providers import FakeOllama, use_ollama

    b, _, cid = prepared(app, monkeypatch)
    use_ollama(app, FakeOllama(served=32768))
    r = wiz("setup", "--only", "ai", input="5\n\n\n")  # Ollama, default address, recommended model
    assert "qwen2.5:7b" in r.out and "[ok] Context window" in r.out
    ai = admin(app).get(api(cid, "/ai-setup")).json()
    assert ai["ready"] is True and ai["management"]["kind"] == "ollama"
    # an unreachable Ollama is an error in a script
    use_ollama(app, FakeOllama(up=False))
    err = wiz(
        "setup",
        "--only",
        "ai",
        "--non-interactive",
        "--provider",
        "ollama",
        "--model",
        "x",
        expect=1,
    )
    assert "No Ollama answered" in err.err


# ------------------------------------------------------------------ work mode and coding CLIs


@pytest.fixture
def cli_world(app, client, company, cfg_dir, calls, monkeypatch, tmp_path):
    """The owner credential, a company with a ready model, fake CLI binaries and a fake npm."""
    monkeypatch.setenv("COMPANYOS_TOKEN", app.state.owner_token.get())
    monkeypatch.setattr(context, "http_factory", lambda url: client)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    app.state.cli_env.extra_dirs.append(
        bin_dir
    )  # "on PATH" for the app, not for the developer's machine
    register_cli_adapters(app)
    make_ready(app, client, company["id"])
    return bin_dir


def fake_npm_that_installs(bin_dir: Path, tmp_path: Path, cli: str, lines: list) -> Path:
    stage = tmp_path / "stage"
    install_fake_cli(stage, cli, lines)
    source = f"""
import json, os, shutil, sys
open(os.path.join({str(bin_dir)!r}, "npm.argv"), "w").write(json.dumps(sys.argv[1:]))
print("added 1 package")
for name in ({cli!r}, {cli + ".script.json"!r}):
    shutil.copy2(os.path.join({str(stage)!r}, name), os.path.join({str(bin_dir)!r}, name))
"""
    return write_script(bin_dir, "npm", source)


def test_hybrid_installs_a_cli_signs_in_tests_and_selects_it(
    app, wiz, client, company, cli_world, calls, tmp_path
):
    bin_dir = cli_world
    fake_npm_that_installs(bin_dir, tmp_path, "codex", CODEX_OK)
    cid = company["id"]
    r = wiz(
        "setup", "--only", "work", "--work-mode", "hybrid", "--cli", "codex", "--cli-auth", "own-login",
        "--install-cli", input="y\ny\n",
    )  # fmt: skip
    out = r.out + r.err
    # install: exactly npm's command, polled to success; then the headless login ran in this terminal
    assert json.loads((bin_dir / "npm.argv").read_text()) == ["install", "-g", "@openai/codex"]
    assert "Installing: npm install -g @openai/codex" in out and "added 1 package" in out
    assert calls.logins == [[str(bin_dir / "codex"), "login", "--device-auth"]]
    assert "at most $0.05" in out
    status = client.get("/api/cli-runtimes/codex").json()
    assert status["installed"] and status["auth"]["mode"] == "own_login"
    assert status["auth"]["last_test"]["ok"] is True
    ai = client.get(api(cid, "/ai-setup")).json()
    assert ai["work_mode"] == "hybrid" and ai["work_runtime"] == "cli:codex" and ai["ready"] is True


def test_hybrid_in_a_script_with_an_api_key_and_a_failing_cli(
    app, wiz, client, company, cli_world, calls
):
    bin_dir = cli_world
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", [], exit=2, stderr="login required")
    args = ["setup", "--only", "work", "--non-interactive", "--secrets-stdin", "--work-mode", "hybrid",
            "--cli", "claude_code", "--cli-auth", "api-key"]  # fmt: skip
    r = wiz(*args, input=f"cli_api_key={CLI_KEY}\n", expect=1)
    assert "failed its test" in r.err and CLI_KEY not in r.out + r.err
    assert client.get(api(cid, "/ai-setup")).json()["work_mode"] == "api"  # nothing was selected
    # now it works
    install_fake_cli(
        bin_dir, "claude",
        [{"type": "result", "subtype": "success", "is_error": False, "result": "OK", "usage": {}}],
    )  # fmt: skip
    r = wiz(*args, "--json", input=f"cli_api_key={CLI_KEY}\n")
    assert r.json()["work"] == {"mode": "hybrid", "runtime": "cli:claude_code"}
    ai = client.get(api(cid, "/ai-setup")).json()
    assert ai["work_mode"] == "hybrid" and ai["work_runtime"] == "cli:claude_code"
    creds = [c["name"] for c in client.get(api(cid, "/credentials")).json()["items"]]
    assert "claude_code-cli-key" in creds
    assert CLI_KEY not in everything_written(app) and CLI_KEY not in r.out + r.err
    # back to API only
    r = wiz("setup", "--only", "work", "--non-interactive", "--work-mode", "api")
    assert client.get(api(cid, "/ai-setup")).json()["work_mode"] == "api"


def test_antigravity_is_never_installed_by_the_wizard(app, wiz, client, company, cli_world, calls):
    r = wiz(
        "setup", "--only", "work", "--non-interactive", "--work-mode", "hybrid", "--cli", "antigravity",
        "--cli-auth", "api-key", "--install-cli", "--secrets-stdin", input=f"cli_api_key={CLI_KEY}\n", expect=1,
    )  # fmt: skip
    assert "curl -fsSL https://antigravity.google/cli/install.sh | bash" in r.out + r.err
    assert "is not installed" in r.err
    assert client.get("/api/cli-runtimes/antigravity").json()["install"]["status"] == "none"
    assert calls.logins == []


def test_a_cli_is_not_offered_a_login_it_does_not_have(app, wiz, client, company, cli_world):
    wiz(
        "setup",
        "--only",
        "work",
        "--work-mode",
        "hybrid",
        "--cli",
        "antigravity",
        "--cli-auth",
        "own-login",
        expect=2,
    )


# ------------------------------------------------------------------ the contracts of other packages


def test_service_and_public_url_steps_receive_the_right_arguments(app, wiz, calls):
    r = wiz("setup", "--only", "service", "--service", "--no-linger")
    assert calls.service == [{"service": True, "linger": False}]
    wiz(
        "setup", "--only", "service", "--no-service", "--json"
    )  # already up and no service wanted: nothing to do
    assert calls.service == [{"service": True, "linger": False}]
    wiz(
        "setup",
        "--only",
        "public-url",
        "--public-url",
        "named",
        "--hostname",
        "foundry.example.com",
        "--non-interactive",
    )
    assert calls.tunnel == [{"provider": "named", "hostname": "foundry.example.com"}]
    wiz("setup", "--only", "public-url", "--public-url", "tailscale", "--non-interactive")
    assert calls.tunnel[-1] == {"provider": "tailscale", "hostname": None}
    n = len(calls.tunnel)
    # no --public-url: a script never opens a tunnel without being told to
    r = wiz("setup", "--only", "public-url", "--non-interactive", expect=2)
    assert "--public-url" in r.err and len(calls.tunnel) == n


def test_the_server_is_started_when_it_is_not_running(app, wiz, calls, monkeypatch):
    monkeypatch.setattr(onboard.Wizard, "_healthy", lambda self: False)
    wiz("setup", "--only", "service", "--no-service")
    assert calls.service == [{"service": False, "linger": False}]


def test_docker_and_remote_servers_skip_the_service_step(app, wiz, calls, tmp_path, monkeypatch):
    from app.ops import headless

    dockerenv = tmp_path / ".dockerenv"
    dockerenv.write_text("")
    monkeypatch.setattr(headless, "DOCKERENV", dockerenv)
    r = wiz("setup", "--only", "service", "--service")
    assert "container" in r.out and calls.service == []


def test_missing_contract_modules_skip_their_steps_with_a_message(app, wiz, calls, monkeypatch):
    import sys

    from app.ops import lifecycle

    monkeypatch.delattr(lifecycle, "ensure_running")
    monkeypatch.setitem(sys.modules, "app.cli.tunnel_flow", None)  # `import` now raises ImportError
    monkeypatch.setattr(onboard.Wizard, "_healthy", lambda self: False)
    r = wiz("setup", "--only", "service", "--service")
    assert "not available in this build" in r.err
    r = wiz("setup", "--only", "public-url", "--public-url", "quick", "--non-interactive")
    assert "not available in this build" in r.err


# ------------------------------------------------------------------ company create, the no-company hint


@pytest.fixture
def owner_cli(app, client, cfg_dir, monkeypatch):
    monkeypatch.setenv("COMPANYOS_TOKEN", app.state.owner_token.get())
    monkeypatch.setattr(context, "http_factory", lambda url: client)
    runner = CliRunner()

    def invoke(*args: str, expect: int = 0) -> Result:
        r = runner.invoke(cli_app, list(args))
        assert r.exit_code == expect, r.stderr + r.stdout
        return Result(r.exit_code, r.stdout, r.stderr)

    return invoke


def test_company_create_takes_an_idea_and_a_timezone(app, client, company, owner_cli, monkeypatch):
    from app.services import ai_setup

    monkeypatch.setattr(
        ai_setup, "ENFORCED", False
    )  # a new company is not AI-ready; notes need no gate here
    created = owner_cli(
        "company", "create", "Zed Inc", "--idea", IDEA, "--timezone", "Asia/Tokyo", "--json"
    ).json()
    assert created["timezone"] == "Asia/Tokyo" and created["idea"] == "note"
    notes = client.get(api(created["id"], "/notes")).json()["items"]
    assert [n["body"] for n in notes] == [IDEA]
    owner_cli("company", "create", "Bad", "--timezone", "Mars/Base", expect=2)
    owner_cli("company", "create", "Long", "--idea", "x" * 300, expect=2)
    assert (
        len(client.get("/api/companies").json()["items"]) == 2
    )  # Acme + Zed: the refused ones were never created


def test_company_create_idea_waits_for_the_ai(app, client, owner_cli):
    r = owner_cli("company", "create", "Zed Inc", "--idea", IDEA)
    assert "first note once the model API is ready" in r.out
    [c] = client.get("/api/companies").json()["items"]
    assert (
        client.get(api(c["id"], "/notes")).json()["items"] == []
    )  # refused until the AI works, not lost silently


def test_no_company_hint_points_to_setup(app, client, owner_cli):
    r = owner_cli("goal", "ls", expect=5)
    assert "foundryos setup" in r.err and "web app" not in r.err


def test_offer_wizard_reports_success_and_failure(app, wiz, calls, monkeypatch):
    from app.cli.context import CliState

    st = CliState()
    monkeypatch.setattr(onboard, "run_wizard", lambda st_, opts: None)
    assert onboard.offer_wizard(st) is True

    def boom(st_, opts):
        raise onboard.CliError("nope")

    monkeypatch.setattr(onboard, "run_wizard", boom)
    assert onboard.offer_wizard(st) is False


def test_setup_is_listed_in_help(wiz):
    out = wiz("--help").out
    assert "setup" in out
    h = wiz("setup", "--help").out
    for flag in (
        "--non-interactive",
        "--secrets-stdin",
        "--only",
        "--public-url",
        "--work-mode",
        "--2fa",
    ):
        assert flag in h
    # no option can carry a secret
    for forbidden in ("--password", "--api-key", "--token", "--setup-token"):
        assert forbidden not in h


def test_an_existing_account_without_a_token_signs_in_and_saves_one(app, wiz, calls, cfg_dir):
    do_setup(app)  # claimed earlier (say, a setup that died before the token was saved)
    assert not config.token_path().exists()
    r = wiz("setup", "--only", "account", input=f"alice\n{PASSWORD}\n")
    assert "not signed in to it" in r.out and "Signed in. Token saved to" in r.out
    assert config.token_path().read_text().strip().startswith("cos_pat_")
    assert stat.S_IMODE(config.token_path().stat().st_mode) == 0o600
    assert PASSWORD not in r.out + r.err
    again = wiz("setup", "--only", "account")
    assert "Already done: signed in as alice" in again.out
    # a script needs the username and the password in its secrets
    config.delete_token()
    r = wiz("setup", "--only", "account", "--non-interactive", expect=2)
    assert "--username" in r.err and "password" in r.err


def test_changing_the_provider_of_a_ready_setup_needs_a_say_so(app, wiz, calls, monkeypatch):
    b, _, cid = prepared(app, monkeypatch)
    wiz("setup", "--only", "ai", input="\n".join(["1", KEY, MODEL, "n"]) + "\n")
    r = wiz("setup", "--only", "ai", input="n\n")  # "Change it?" -> no
    assert "Already connected" in r.out or "already connected" in r.out
    r = wiz("setup", "--only", "ai", "--non-interactive")  # nothing asked for, nothing changed
    assert "Already done" in r.out
    assert len(admin(app).get(api(cid, "/model-providers")).json()["items"]) == 1


def test_the_work_mode_and_cli_menus(app, wiz, client, company, cli_world, calls):
    bin_dir = cli_world
    cid = company["id"]
    ok = [{"type": "result", "subtype": "success", "is_error": False, "result": "OK", "usage": {}}]
    install_fake_cli(bin_dir, "claude", ok)
    # --only work asks: work mode (2 = API + CLIs), which CLI (1 = Claude Code), how to sign in
    # (2 = API key), the key (hidden), and whether to run the test
    r = wiz("setup", "--only", "work", input=f"2\n1\n2\n{CLI_KEY}\ny\n")
    assert "Claude Code (installed" in r.out and "Codex CLI (not installed)" in r.out
    assert "How should it sign in?" in r.out and "at most $0.05" in r.out
    ai = client.get(api(cid, "/ai-setup")).json()
    assert ai["work_mode"] == "hybrid" and ai["work_runtime"] == "cli:claude_code"
    assert CLI_KEY not in r.out + r.err + everything_written(app)
    # declining the test keeps the work on the API
    wiz("setup", "--only", "work", "--work-mode", "api")
    r = wiz("setup", "--only", "work", input=f"2\n1\n2\n{CLI_KEY}\nn\n", expect=1)
    assert "cannot do company work until it passes its test" in r.err
    assert client.get(api(cid, "/ai-setup")).json()["work_mode"] == "api"
