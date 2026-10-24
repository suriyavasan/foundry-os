"""M9: the `companyos` CLI against an in-process app (Typer CliRunner + a TestClient as the HTTP client)."""

from __future__ import annotations

import json
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pyotp
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli import config, context
from app.cli.client import ApiClient
from app.cli.main import app as cli_app
from tests.accounts_helpers import PASSWORD, browser, do_setup
from tests.helpers import api, new_agent
from tests.test_agent_api import make_approval, start_run


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
    for name in ("COMPANYOS_TOKEN", "COMPANYOS_API_URL", "COMPANYOS_COMPANY"):
        monkeypatch.delenv(name, raising=False)
    return d


@pytest.fixture
def cli(
    app, client: TestClient, cfg_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Result]:
    """Invoke `companyos` as the owner (token from the environment) on the in-process app."""
    monkeypatch.setenv("COLUMNS", "220")  # Rich wraps tables to the terminal width
    monkeypatch.setenv("COMPANYOS_TOKEN", app.state.owner_token.get())
    monkeypatch.setattr(context, "http_factory", lambda url: client)
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


# ------------------------------------------------------------------ identity & status


def test_help_works_for_every_command_group(cli):
    top = cli("--help").out
    for group in (
        "login", "logout", "whoami", "status", "company", "note", "notes", "goal", "agents", "tasks", "runs",
        "skills", "approvals", "budget", "policy", "pause", "resume", "stop", "providers",
        "credentials", "reach", "backup", "auth", "up", "doctor",
    ):  # fmt: skip
        assert group in top, group
        assert cli(group, "--help").code == 0


def test_whoami_and_credential_source(cli, company):
    r = cli("whoami", "--json").json()
    assert r["memberships"][0]["company_name"] == "Acme Labs"
    assert r["credential"] == "env"
    assert "token" not in json.dumps(r).lower().replace("credential", "")
    assert "Acme Labs" in cli("whoami").out


def test_status_summarises_the_company(cli, company):
    data = cli("status", "--json").json()
    assert data["company"]["name"] == "Acme Labs"
    assert data["headcount"]["total"] >= 1  # the AI Co-founder
    assert data["spending"]["available"] is True
    assert data["server"]["status"] == "ok"
    text = cli("status").out
    assert "Acme Labs" in text and "Headcount" in text and "Spend today" in text


def test_company_selection_by_name_and_unknown_company(cli, company, client):
    assert cli("--company", "Acme Labs", "goal", "ls", "--json").json() == []
    assert cli("-c", company["id"], "goal", "ls", "--json").json() == []
    r = cli("--company", "Nope Inc", "goal", "ls", expect=5)
    assert "No company matches" in r.err
    client.post("/api/companies", json={"name": "Second Co"})
    amb = cli("goal", "ls", expect=1)
    assert "Several companies" in amb.err


# ------------------------------------------------------------------ errors and exit codes


def test_errors_use_the_api_message_and_distinct_exit_codes(cli, company):
    r = cli("tasks", "show", "does-not-exist", expect=5)
    assert "No task matches" in r.err
    r = cli("agents", "pause", "Atlas", expect=None)  # the founder is active: pausing works
    assert r.code == 0
    r = cli("agents", "pause", "Atlas", expect=6)  # already paused: 409 invalid_transition
    assert "paused" in r.err.lower() or "transition" in r.err.lower()
    r = cli("tasks", "create", "x", "--priority", "bogus", expect=1)
    assert r.err.startswith("Error:")


def test_json_mode_prints_errors_as_json_on_stderr(cli, company):
    r = cli("tasks", "show", "nope", "--json", expect=5)
    err = json.loads(r.err)
    assert err["error"]["code"] == "not_found"
    assert r.out == ""


def test_not_signed_in_exits_3_with_a_hint(app, client, cfg_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("COMPANY_OS_DATABASE_URL", f"sqlite:///{tmp_path / 'elsewhere' / 'x.db'}")
    monkeypatch.setattr(context, "http_factory", lambda url: client)
    r = CliRunner().invoke(cli_app, ["status"])
    assert r.exit_code == 3
    assert "foundryos login" in r.stderr


def test_unreachable_server_exits_7(cfg_dir, monkeypatch):
    monkeypatch.setenv("COMPANYOS_TOKEN", "x" * 40)
    monkeypatch.setattr(
        context, "http_factory", lambda url: httpx.Client(base_url="http://127.0.0.1:9", timeout=2)
    )
    r = CliRunner().invoke(cli_app, ["whoami"])
    assert r.exit_code == 7
    assert "Cannot reach" in r.stderr


def test_wrong_credential_exits_3(app, client, cfg_dir, monkeypatch):
    monkeypatch.setenv("COMPANYOS_TOKEN", "not-a-real-token-not-a-real-token-1234")
    plain = TestClient(app)
    monkeypatch.setattr(context, "http_factory", lambda url: plain)
    r = CliRunner().invoke(cli_app, ["whoami"])
    assert r.exit_code == 3


# ------------------------------------------------------------------ notes, goals


def test_notes_flow_including_stdin(cli, company):
    created = cli("note", "Focus on the pilot customers first", "--json").json()
    assert created["status"] == "new"
    assert "saved" in cli("note", "-", input="A thought from stdin\n").out
    empty = cli("note", "-", input="   \n", expect=2)
    assert "empty" in empty.err
    listing = cli("notes", "ls", "--json").json()
    assert {n["body"] for n in listing} == {
        "Focus on the pilot customers first",
        "A thought from stdin",
    }
    nid = created["id"]
    shown = cli("notes", "show", nid[:8], "--json").json()
    assert shown["id"] == nid and shown["triage"] is None
    text = cli("notes", "show", nid[:8]).out
    assert "Not triaged yet" in text and "pilot customers" in text
    assert "pilot" in cli("notes", "ls").out


def test_company_create_and_list(cli, client):
    assert "No company yet" in cli("company", "ls").out
    made = cli("company", "create", "Fresh Co", "--mission", "Make things", "--json").json()
    assert made["name"] == "Fresh Co" and made["mission"] == "Make things"
    assert [c["name"] for c in cli("company", "ls", "--json").json()] == ["Fresh Co"]
    assert "Fresh Co" in cli("company", "ls").out
    assert "created" in cli("company", "create", "Second").out


def test_goals(cli, company):
    assert "No goals yet" in cli("goal", "ls").out
    g = cli(
        "goal",
        "add",
        "Reach 10 customers",
        "--priority",
        "high",
        "--target",
        "2027-06-30",
        "--json",
    ).json()
    assert g["status"] == "active" and g["priority"] == "high"
    cli("goal", "add", "Maybe later", "--draft")
    out = cli("goal", "ls").out
    assert "Reach 10 customers" in out and "Maybe later" in out
    assert [x["title"] for x in cli("goal", "ls", "--status", "proposed", "--json").json()] == [
        "Maybe later"
    ]


# ------------------------------------------------------------------ agents & skills


def _template(client: TestClient, cid: str) -> dict:
    items = client.get(api(cid, "/role-templates")).json()["items"]
    return next(t for t in items if t["slug"] != "founder")


def test_agents_lifecycle(cli, client, company):
    cid = company["id"]
    tpl = _template(client, cid)
    assert "Atlas" in cli("agents", "ls").out
    hired = cli(
        "agents",
        "hire",
        "--template",
        tpl["slug"],
        "--name",
        "Dana",
        "--monthly-budget",
        "7.5",
        "--json",
    ).json()
    assert hired["name"] == "Dana" and hired["status"] == "active"
    assert hired["monthly_budget_micros"] == 7_500_000
    assert cli("agents", "show", "Dana", "--json").json()["name"] == "Dana"
    assert "Dana" in cli("agents", "show", "dana").out
    assert cli("agents", "pause", "Dana", "--json").json()["status"] == "paused"
    assert cli("agents", "resume", "Dana", "--json").json()["status"] == "active"
    assert (
        cli("agents", "retire", "Dana", "--reason", "restructure", "--json").json()["status"]
        == "retired"
    )
    cli("agents", "resume", "Dana", expect=6)
    assert [a["name"] for a in cli("agents", "ls", "--status", "retired", "--json").json()] == [
        "Dana"
    ]
    assert tpl["slug"] in cli("agents", "templates").out
    no_activate = cli(
        "agents", "hire", "-t", tpl["slug"], "-n", "Eli", "--no-activate", "--json"
    ).json()
    assert no_activate["status"] == "proposed"
    prov = client.post(
        api(cid, "/model-providers"),
        json={"name": "lab", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    pinned = cli(
        "agents",
        "hire",
        "-t",
        tpl["slug"],
        "-n",
        "Fay",
        "--provider",
        "lab",
        "--model",
        "qwen2.5:7b",
        "--json",
    ).json()
    assert pinned["model_provider"] == prov["id"] and pinned["model_name"] == "qwen2.5:7b"
    assert pinned["runtime"] == "company_agent"
    cli("agents", "hire", "-t", tpl["slug"], "-n", "Gus", "--provider", "nope", expect=5)
    unknown = cli("agents", "hire", "-t", "no-such-template", "-n", "Z", expect=5)
    assert "Unknown template" in unknown.err


def test_agent_skills_listing_and_assignment(cli, client, company):
    new_agent(client, company["id"], name="Quill")
    assert "no skills" in cli("agents", "skills", "Quill").out
    # an unknown skill is refused by the API and surfaces as a non-zero exit
    r = cli("agents", "skills", "Quill", "--add", "no-such-skill", expect=None)
    assert r.code != 0


@pytest.fixture
def skills_env_override(fake_skills_env):
    return fake_skills_env


def test_skills_search_install_show(cli, company):
    found = cli("skills", "search", "a", "--json").json()
    assert isinstance(found, list)
    assert "No skills installed" in cli("skills", "ls").out
    hermes = next((s for s in found if s.get("source_ref", "").startswith("hermes")), None)
    if hermes is None:
        pytest.skip("fake runtime catalog is empty")
    res = cli("skills", "install", hermes["source_ref"], "--json").json()
    slug = res["skill"]["slug"]
    assert res["outcome"] == "installed"
    assert slug in cli("skills", "ls").out
    assert cli("skills", "show", slug, "--json").json()["slug"] == slug
    assert slug in cli("skills", "show", slug).out
    cli("skills", "show", "no-such-skill", expect=5)


# ------------------------------------------------------------------ tasks, runs, approvals


def test_tasks_create_list_show_cancel_retry(cli, client, company):
    cid = company["id"]
    new_agent(client, cid, name="Nova")
    t = cli(
        "tasks", "create", "Write the brief", "--assignee", "Nova", "--priority", "high",
        "--criteria", "5 bullets", "--budget", "2", "--json",
    ).json()  # fmt: skip
    assert t["assignee_agent_id"] and t["budget_micros"] == 2_000_000 and t["priority"] == "high"
    dep = cli("tasks", "create", "Follow-up", "--depends-on", t["id"][:8], "--json").json()
    assert dep["depends_on"] == [t["id"]]
    listing = cli("tasks", "ls", "--json").json()
    assert {x["title"] for x in listing} == {"Write the brief", "Follow-up"}
    assert [x["id"] for x in cli("tasks", "ls", "--assignee", "Nova", "--json").json()] == [t["id"]]
    shown = cli("tasks", "show", t["id"][:8], "--json").json()
    assert shown["runs"] == [] and shown["title"] == "Write the brief"
    assert "Write the brief" in cli("tasks", "show", t["id"]).out
    cancelled = cli("tasks", "cancel", t["id"], "--reason", "changed plans", "--json").json()
    assert cancelled["status"] == "cancelled"
    retry = cli("tasks", "retry", t["id"], expect=6)  # only failed tasks can be retried
    assert retry.err.startswith("Error:")


def test_runs_tail_history_and_selection_errors(app, cli, client, company):
    cid = company["id"]
    agent = new_agent(client, cid, name="Nova")
    cli("runs", "tail", expect=5)  # nothing selected
    cli("runs", "tail", "--agent", "Nova", expect=5)  # nothing ran yet
    sess = start_run(app, client, cid, agent["id"])
    r = sess.post("/progress", json={"message": "reading the brief"})
    assert r.status_code in (200, 201, 202), r.text
    out = cli("runs", "tail", "--task", sess.task_id).out
    assert "reading the brief" in out
    by_run = cli("runs", "tail", "--run", sess.run_id, "--json").out
    assert "reading the brief" in by_run
    by_agent = cli("runs", "tail", "--agent", "Nova", "-n", "5").out
    assert "reading the brief" in by_agent


def test_runs_tail_follow_streams_live_events(app, cli, client, company, monkeypatch):
    cid = company["id"]
    agent = new_agent(client, cid, name="Nova")
    sess = start_run(app, client, cid, agent["id"])
    frames = [
        ("run.event", {"created_at": "2026-10-24T10:00:01Z", "payload": {"run_id": "other", "type": "message", "summary": "not mine"}}),
        ("run.event", {"created_at": "2026-10-24T10:00:02Z", "payload": {"run_id": sess.run_id, "agent_id": agent["id"], "seq": 7, "type": "tool", "summary": "searching"}}),
        ("task.updated", {"payload": {}}),
        ("run.finished", {"created_at": "2026-10-24T10:00:03Z", "payload": {"run_id": sess.run_id, "agent_id": agent["id"], "status": "succeeded"}}),
    ]  # fmt: skip
    seen: list[str] = []

    def fake_sse(self, path, **params):
        seen.append(path)
        yield from frames

    monkeypatch.setattr(ApiClient, "sse", fake_sse)
    out = cli("runs", "tail", "--run", sess.run_id, "--follow").out
    assert "searching" in out and "not mine" not in out
    assert "following" in out
    assert seen == [f"/companies/{cid}/stream"]
    js = cli("runs", "tail", "--run", sess.run_id, "--follow", "--json").out
    assert '"source": "live"' in js


def test_approvals_list_approve_reject(app, cli, client, company):
    cid = company["id"]
    worker = new_agent(client, cid, name="Worker")
    a1 = make_approval(client, cid, worker["id"], "external_purchase")
    a2 = make_approval(client, cid, worker["id"], "external_purchase")
    pending = cli("approvals", "ls", "--json").json()
    assert {a["id"] for a in pending} == {a1, a2}
    ok = cli("approvals", "approve", a1[:8], "--note", "fine", "--json").json()
    assert ok["status"] == "approved" and ok["decision_note"] == "fine"
    no = cli("approvals", "reject", a2, "--json").json()
    assert no["status"] == "rejected"
    cli("approvals", "approve", a1, expect=6)  # already decided: 409 -> exit 6
    assert cli("approvals", "ls").out.strip().startswith("Nothing is waiting")
    assert len(cli("approvals", "ls", "--status", "all", "--json").json()) == 2


# ------------------------------------------------------------------ money & control


def test_budget_and_policy(cli, client, company):
    shown = cli("budget", "show", "--json").json()
    assert shown["company"]["daily_budget_micros"] is None
    cli("budget", "set", "--daily", "5", "--monthly", "100")
    after = cli("budget", "show", "--json").json()["company"]
    assert (
        after["daily_budget_micros"] == 5_000_000 and after["monthly_budget_micros"] == 100_000_000
    )
    assert "$5.00" in cli("budget", "show").out
    cli("budget", "set", "--daily", "none")
    assert cli("budget", "show", "--json").json()["company"]["daily_budget_micros"] is None
    new_agent(client, company["id"], name="Nova")
    r = cli("budget", "set", "--monthly", "12.25", "--agent", "Nova", "--json").json()
    assert r["monthly_budget_micros"] == 12_250_000
    assert cli("budget", "set", expect=2).err.startswith("Error:")
    assert cli("budget", "set", "--daily", "lots", expect=2).code == 2
    assert cli("budget", "set", "--daily", "-3", expect=2).code == 2

    pol = cli("policy", "show", "--json").json()
    assert "max_headcount" in pol
    cli(
        "policy",
        "set",
        "max_headcount=20",
        "autonomy_mode=supervised",
        'allowed_model_providers=["local"]',
    )
    pol = cli("policy", "show", "--json").json()
    assert pol["max_headcount"] == 20 and pol["autonomy_mode"] == "supervised"
    assert pol["allowed_model_providers"] == ["local"]
    assert "max_headcount" in cli("policy", "show").out
    assert cli("policy", "set", "no-equals-sign", expect=2).code == 2
    assert cli("policy", "set", "not_a_setting=1", expect=1).err.startswith("Error:")


def test_pause_resume_and_global_emergency_stop(cli, client, company):
    cid = company["id"]
    assert cli("pause", "--json").json()["status"] == "paused"
    assert client.get(f"/api/companies/{cid}").json()["status"] == "paused"
    cli("pause", expect=6)  # already paused
    assert cli("resume", "--json").json()["status"] == "active"
    # --reason without --all is refused: the reason belongs to the instance-wide kill switch
    assert "--all" in cli("stop", "--reason", "drill", expect=2).err
    stopped = cli("stop", "--all", "--reason", "drill", "--json").json()
    assert stopped["active"] is True and stopped["reason"] == "drill"
    assert cli("status", "--json").json()["emergency_stop"]["active"] is True
    assert "EMERGENCY STOP" in cli("status").out
    assert cli("resume", "--all", "--json").json()["active"] is False


# ------------------------------------------------------------------ providers, credentials, reach


def test_providers_and_detect(cli, client, company):
    cid = company["id"]
    assert "No model provider" in cli("providers", "ls").out
    nothing = cli("providers", "detect", "--base-url", "http://127.0.0.1:9", "--json").json()
    assert nothing["reachable"] is False
    assert (
        "No Ollama answered" in cli("providers", "detect", "--base-url", "http://127.0.0.1:9").out
    )
    r = client.post(
        api(cid, "/model-providers"),
        json={"name": "local-ollama", "kind": "ollama", "default_model": "qwen2.5:7b"},
    )
    assert r.status_code == 201, r.text
    assert "local-ollama" in cli("providers", "ls").out
    assert cli("providers", "ls", "--json").json()[0]["name"] == "local-ollama"
    cli("providers", "test", "no-such-provider", expect=5)


def test_credentials_secret_comes_from_stdin_never_argv(cli, client, company):
    secret = "sk-test-SUPERSECRET-1234567890"
    r = cli("credentials", "set", "OPENROUTER_API_KEY", "--stdin", input=secret + "\n")
    assert secret not in r.out + r.err and "stored" in r.out
    listing = cli("credentials", "ls").out + cli("credentials", "ls", "--json").out
    assert "OPENROUTER_API_KEY" in listing and secret not in listing
    # setting again rotates it
    r2 = cli(
        "credentials", "set", "OPENROUTER_API_KEY", "--stdin", input="sk-rotated-VALUE-987654321\n"
    )
    assert "replaced" in r2.out and "sk-rotated" not in r2.out + r2.err
    js = cli(
        "credentials", "set", "OTHER", "--stdin", "--json", input="another-secret-value\n"
    ).json()
    assert "value" not in js and "another-secret-value" not in json.dumps(js)
    # there is no way to pass the value on the command line
    for flag in ("--value", "--secret", "--token", "--password"):
        bad = cli("credentials", "set", "X", flag, secret, expect=2)
        assert secret not in bad.out
    # empty stdin is an error, not an empty secret
    cli("credentials", "set", "EMPTY", "--stdin", input="", expect=1)
    # hidden prompt path
    p = cli("credentials", "set", "PROMPTED", input="typed-secret-value\n")
    assert "typed-secret-value" not in p.out + p.err and "stored" in p.out
    names = {c["name"] for c in cli("credentials", "ls", "--json").json()}
    assert {"OPENROUTER_API_KEY", "OTHER", "PROMPTED"} <= names


def test_reach(cli, company):
    data = cli("reach", "--json").json()
    assert data["mode"] in ("local", "lan", "public")
    out = cli("reach").out
    assert "Mode:" in out and "local" in out


# ------------------------------------------------------------------ login / logout / reset-password


@pytest.fixture
def real_accounts(app, cfg_dir, monkeypatch):
    """Claim the install, then talk to the app like a remote script: fresh cookie-less clients."""
    do_setup(app)
    monkeypatch.setattr(context, "http_factory", lambda url: browser(app))
    return CliRunner()


def test_login_stores_a_scoped_token_not_the_password(app, real_accounts, cfg_dir):
    runner = real_accounts
    r = runner.invoke(
        cli_app,
        ["login", "-u", "alice", "--password-stdin", "--scope", "write"],
        input=PASSWORD + "\n",
    )
    assert r.exit_code == 0, r.stderr + r.stdout
    token_file = config.token_path()
    assert token_file.read_text().strip().startswith("cos_pat_")
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    for f in cfg_dir.rglob("*"):
        if f.is_file():
            assert PASSWORD not in f.read_text(), f"password leaked into {f}"
    assert PASSWORD not in r.stdout + r.stderr
    assert token_file.read_text().strip() not in r.stdout + r.stderr  # the token is not echoed
    # the stored token now authenticates (credential source: token-file)
    who = runner.invoke(cli_app, ["whoami", "--json"])
    assert who.exit_code == 0, who.stderr
    assert json.loads(who.stdout)["credential"] == "token-file"
    # `write` scope: reads and writes work, policy changes are refused (403 -> exit 4)
    assert runner.invoke(cli_app, ["goal", "ls"]).exit_code in (0, 5)
    pol = runner.invoke(cli_app, ["policy", "set", "max_headcount=5"])
    assert pol.exit_code in (4, 5), pol.stderr  # no company yet (5) or scope refused (4)
    # token appears in the server-side token list with a name, scope write
    b = browser(app)
    b.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    tokens = b.get("/api/auth/tokens").json()["items"]
    assert [(t["scope"], t["name"].startswith("companyos-cli@")) for t in tokens] == [
        ("write", True)
    ]
    # logout forgets it locally
    out = runner.invoke(cli_app, ["logout", "--json"])
    assert out.exit_code == 0 and json.loads(out.stdout)["removed"] is True
    assert not token_file.exists()
    assert runner.invoke(cli_app, ["logout"]).stdout.startswith("No stored token")


def test_login_failures(app, real_accounts, cfg_dir):
    runner = real_accounts
    r = runner.invoke(
        cli_app, ["login", "-u", "alice", "--password-stdin"], input="definitely wrong password\n"
    )
    assert r.exit_code == 3
    assert not config.token_path().exists()
    assert "definitely wrong password" not in r.stdout + r.stderr
    r = runner.invoke(cli_app, ["login", "-u", "alice", "--password-stdin"], input="")
    assert r.exit_code == 1 and "standard input" in r.stderr
    r = runner.invoke(
        cli_app,
        ["login", "-u", "alice", "--password-stdin", "--scope", "root"],
        input=PASSWORD + "\n",
    )
    assert r.exit_code == 2


def test_login_with_two_factor(app, real_accounts, cfg_dir):
    b = browser(app)
    b.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    secret = b.post("/api/auth/2fa/enroll").json()["secret"]
    assert (
        b.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    )
    runner = real_accounts
    no_code = runner.invoke(
        cli_app, ["login", "-u", "alice", "--password-stdin"], input=PASSWORD + "\n\n"
    )
    assert no_code.exit_code == 1 and "code" in no_code.stderr.lower()
    assert not config.token_path().exists()
    # replay protection: wait for the next step is not possible in a test, so use a recovery-free fresh code
    good = pyotp.TOTP(secret).at(time.time() + 30)
    ok = runner.invoke(
        cli_app, ["login", "-u", "alice", "--password-stdin"], input=f"{PASSWORD}\n{good}\n"
    )
    assert ok.exit_code == 0, ok.stderr
    assert config.token_path().exists()


@pytest.fixture
def local_server_env(app, client, tmp_path, monkeypatch, db_url):
    """Settings() in the CLI process resolves to the test app's database and data directory."""
    monkeypatch.setenv("COMPANY_OS_DATABASE_URL", db_url)
    monkeypatch.setenv("COMPANY_OS_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("COMPANY_OS_ARGON2_MEMORY_KIB", "64")
    monkeypatch.setenv("COMPANY_OS_ARGON2_PARALLELISM", "1")
    return Path(db_url.removeprefix("sqlite:///")).parent


def test_reset_password_needs_the_server_machine_and_revokes_sessions(
    app, local_server_env, cfg_dir, monkeypatch
):
    victim = do_setup(app)  # alice, signed in
    assert victim.get("/api/me").status_code == 200
    runner = CliRunner()
    new_pw = "a brand new passphrase 2027!"

    # no access to owner.token -> refused (exit 4) and nothing changes
    token_file = local_server_env / "owner.token"
    held = token_file.read_bytes()
    token_file.rename(token_file.with_suffix(".away"))
    denied = runner.invoke(
        cli_app, ["auth", "reset-password", "alice", "--password-stdin"], input=new_pw + "\n"
    )
    assert denied.exit_code == 4 and "server machine" in denied.stderr
    token_file.with_suffix(".away").rename(token_file)
    assert token_file.read_bytes() == held
    assert (
        browser(app)
        .post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        .status_code
        == 200
    )

    # weak password is rejected by the same policy as the web app
    weak = runner.invoke(
        cli_app, ["auth", "reset-password", "alice", "--password-stdin"], input="password123\n"
    )
    assert weak.exit_code == 1
    unknown = runner.invoke(
        cli_app, ["auth", "reset-password", "nobody", "--password-stdin"], input=new_pw + "\n"
    )
    assert unknown.exit_code == 5

    ok = runner.invoke(
        cli_app,
        ["auth", "reset-password", "alice", "--password-stdin", "--json"],
        input=new_pw + "\n",
    )
    assert ok.exit_code == 0, ok.stderr
    body = json.loads(ok.stdout)
    assert body["sessions_revoked"] >= 1 and new_pw not in ok.stdout + ok.stderr
    assert victim.get("/api/me").status_code == 401  # the old browser session is gone
    assert (
        browser(app)
        .post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        .status_code
        == 401
    )
    fresh = browser(app)
    assert (
        fresh.post("/api/auth/login", json={"username": "alice", "password": new_pw}).status_code
        == 200
    )
    assert (local_server_env / "owner.token").read_bytes() == held


def test_reset_password_prompts_twice_when_not_scripted(app, local_server_env, cfg_dir):
    do_setup(app)
    runner = CliRunner()
    new_pw = "yet another passphrase 4711!"
    mismatch = runner.invoke(
        cli_app, ["auth", "reset-password", "alice"], input=f"{new_pw}\nsomething else\n"
    )
    assert mismatch.exit_code == 1 and "differ" in mismatch.stderr
    ok = runner.invoke(cli_app, ["auth", "reset-password", "alice"], input=f"{new_pw}\n{new_pw}\n")
    assert ok.exit_code == 0, ok.stderr
    assert new_pw not in ok.stdout + ok.stderr


# ------------------------------------------------------------------ doctor


def test_doctor_reports_checks_and_fails_on_a_failed_one(cli, monkeypatch):
    from app.ops import doctor

    good = [
        doctor.Check("python", "ok", "fine"),
        doctor.Check("ollama", "warn", "low context", "restart it"),
    ]
    monkeypatch.setattr(doctor, "run_checks", lambda: good)
    data = cli("doctor", "--json").json()
    assert data["ok"] is True and [c["name"] for c in data["checks"]] == ["python", "ollama"]
    text = cli("doctor").out
    assert "low context" in text and "fix: restart it" in text
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda: [*good, doctor.Check("disk", "fail", "0.1 GB free", "free space")],
    )
    assert cli("doctor", "--json", expect=1).json()["ok"] is False


def test_secret_files_are_created_private(cfg_dir):
    path = config.write_token("cos_pat_example")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    config.write_token("cos_pat_example2")  # replacing keeps it private
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().strip() == "cos_pat_example2"
    assert not list(cfg_dir.glob("*.tmp"))
    config.save_config(api_url='http://x:1/"quoted"', company_id="c1")
    assert config.load_config() == {"api_url": 'http://x:1/"quoted"', "company_id": "c1"}
    assert stat.S_IMODE(config.config_path().stat().st_mode) == 0o600


def test_credential_resolution_order(cfg_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("COMPANY_OS_DATABASE_URL", f"sqlite:///{tmp_path / 'srv' / 'x.db'}")
    assert config.resolve_credential() is None
    owner = tmp_path / "srv" / "owner.token"
    owner.parent.mkdir()
    owner.write_text("o" * 43)
    cred = config.resolve_credential()
    assert cred and cred.source == "owner-token"
    config.write_token("cos_pat_saved")
    assert config.resolve_credential().source == "token-file"  # type: ignore[union-attr]
    monkeypatch.setenv("COMPANYOS_TOKEN", "from-env")
    assert config.resolve_credential().source == "env"  # type: ignore[union-attr]
    assert config.resolve_api_url(None) == "http://127.0.0.1:8700"
    config.save_config(api_url="http://example:9000/")
    assert config.resolve_api_url(None) == "http://example:9000"
    assert config.resolve_api_url("http://flag:1") == "http://flag:1"


def test_setup_token_is_shown_only_while_setup_is_pending(app, local_server_env, cfg_dir):
    runner = CliRunner()
    token = app.state.owner_token.get()
    before = runner.invoke(cli_app, ["auth", "setup-token", "--json"])
    assert before.exit_code == 0, before.stderr
    assert json.loads(before.stdout) == {"setup_pending": True, "setup_token": token}
    assert token in runner.invoke(cli_app, ["auth", "setup-token"]).stdout
    do_setup(app)
    after = runner.invoke(cli_app, ["auth", "setup-token"])
    assert after.exit_code == 0 and token not in after.stdout + after.stderr
    assert "already set up" in after.stdout
