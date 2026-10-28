"""``foundryos setup``: the whole onboarding in a terminal (milestone M13).

Most installs happen over SSH on a box with no browser. After ``brew install``, the one-line installer or
Docker, this wizard takes a person from nothing to a working company: owner account, company and idea,
a model API that passed its live test, the work mode (API only, or API plus a coding CLI on this machine),
the background service and a free public URL.

It talks to the server over HTTP like every other command (docs/architecture.md D23). It never puts a
secret in argv: passwords, API keys and the setup token come from a hidden prompt or ``--secrets-stdin``.
It is resumable: every step asks the server first and says "already done"; ``--only <step>`` reruns one.
The pieces written by other packages (``app.ops.lifecycle.ensure_running``, ``app.ops.headless``,
``app.cli.tunnel_flow.enable_public_url``) are imported lazily; when one is missing that step is skipped
with a clear message.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import click
import typer

from app.cli import config, onboarding
from app.cli import render as render_mod
from app.cli.client import (
    EXIT_AUTH,
    EXIT_CONFLICT,
    EXIT_ERROR,
    ApiClient,
    CliError,
)
from app.cli.commands import repo as repo_cmd
from app.cli.commands.auth import csrf_headers, end_session, mint_cli_token
from app.cli.context import CliState, JsonOpt, command, state
from app.cli.onboarding import (
    ALL_PRESETS,
    BLOCKING_REASON_TEXT,
    CLI_AUTH_FLAGS,
    GITHUB_CI_FLAGS,
    GITHUB_MERGE_FLAGS,
    PRESET_BY_FLAG,
    STEPS,
    ModelOption,
    ProviderPreset,
    WizardOptions,
    usage,
)
from app.runtimes.cli.catalog import CLIS, assert_safe_argv
from app.schemas.repository import default_policy

PROVIDER_TEST_TIMEOUT = 180.0  # a live test of a slow provider (it may load a local model)
CLI_TEST_TIMEOUT = 120.0  # the server limits the CLI test to 60 s; this leaves room on top
INSTALL_POLL_SECONDS = 1.0
INSTALL_WAIT_SECONDS = 960.0  # the server stops an install after 900 s
CLI_TEST_SPEND_CAP = "$0.05"

# Replaced in tests.
sleep: Callable[[float], None] = time.sleep


def run_interactive(argv: list[str]) -> int:
    """Run a CLI's own login in THIS terminal with inherited stdio (device codes and URLs must reach
    the person). Returns the exit code; a missing binary is reported, not raised."""
    assert_safe_argv(argv)
    try:
        return subprocess.run(argv, check=False).returncode
    except OSError as exc:
        typer.echo(f"Could not start {argv[0]}: {exc}", err=True)
        return 127


@dataclass
class _Attempt:
    """One go at connecting a model provider (a retry reuses what the last go already stored)."""

    preset: ProviderPreset
    base_url: str | None = None
    model: str | None = None
    credential_id: str | None = None
    provider_id: str | None = None
    api_key: str | None = None  # in memory only until the server has it


class Wizard:
    def __init__(self, st: CliState, opts: WizardOptions) -> None:
        self.st = st
        self.opts = opts
        self.json = st.json
        self.interactive = not opts.non_interactive
        self.secrets = dict(opts.secrets)
        self.steps: dict[str, str] = {}
        self.summary: dict[str, Any] = {}
        self.session: ApiClient | None = (
            None  # the signed-in setup session (cookies), until 2FA is done
        )
        self._anon: ApiClient | None = None
        self.company_id: str | None = None
        self.company_name: str | None = None
        self.idea: str = (opts.idea or "").strip()
        self.username: str | None = None
        self.management: dict[str, Any] | None = None
        self.fresh_ai = False
        self._public_choice: str | None = None
        self.public_url: str | None = None
        self.token_file: str | None = None
        self.two_factor: bool | None = None

    # ------------------------------------------------------------------ terminal

    @property
    def remote(self) -> bool:
        return not onboarding.is_loopback_url(self.st.api_base())

    def say(self, message: str = "") -> None:
        """Progress. In ``--json`` mode it goes to stderr so stdout stays one JSON document."""
        typer.echo(message, err=self.json)

    def warn(self, message: str) -> None:
        typer.echo(f"! {message}", err=True)

    def header(self, title: str) -> None:
        self.say()
        self.say(f"== {title}")

    def need(self, what: str) -> CliError:
        return usage(f"{what} is required with --non-interactive.")

    def ask(self, text: str, *, default: str | None = None, hide: bool = False) -> str:
        return typer.prompt(
            text, default=default, hide_input=hide, err=self.json, show_default=bool(default)
        )

    def confirm(self, text: str, *, default: bool = True) -> bool:
        return typer.confirm(text, default=default, err=self.json)

    def menu(self, title: str, options: list[str], *, default: int = 1) -> int:
        """Numbered choices; returns the 1-based number picked."""
        self.say(title)
        for i, label in enumerate(options, start=1):
            self.say(f"  {i}) {label}")
        return typer.prompt(
            "Choose", type=click.IntRange(1, len(options)), default=default, err=self.json
        )

    def secret(self, key: str, prompt: str, *, optional: bool = False) -> str | None:
        """A secret from --secrets-stdin, else a hidden prompt. Never an option, never echoed."""
        if key in self.secrets:
            return self.secrets[key]
        if not self.interactive:
            if optional:
                return None
            raise self.need(f"{key} in --secrets-stdin ({prompt})")
        value = self.ask(
            prompt + (" (Enter for none)" if optional else ""),
            hide=True,
            default="" if optional else None,
        )
        return value or None

    def record(self, step: str, status: str, **info: Any) -> None:
        self.steps[step] = status
        if info:
            self.summary.setdefault(step.replace("-", "_"), {}).update(info)

    # ------------------------------------------------------------------ api helpers

    def anon(self) -> ApiClient:
        if self._anon is None:
            self._anon = self.st.anonymous_client()
        return self._anon

    @property
    def api(self) -> ApiClient:
        return self.st.client()

    def ensure_company(self) -> str:
        if self.company_id is None:
            self.company_id = self.st.company_id()
        return self.company_id

    def cpath(self, suffix: str = "") -> str:
        return f"/companies/{self.ensure_company()}{suffix}"

    def ai_status(self) -> dict:
        return self.api.get(self.cpath("/ai-setup"))

    def paged_names(self, path: str) -> list[dict]:
        return self.api.paged(path)

    # ------------------------------------------------------------------ plan

    def plan(self) -> list[str]:
        only = self.opts.only
        return [s for s in STEPS if only in (None, s)]

    def run(self) -> None:
        onboarding.validate_options(self.opts)
        plan = self.plan()
        if self.opts.two_fa and not self.interactive:
            raise usage(
                "--2fa needs a code from your authenticator app, so it cannot run with "
                "--non-interactive. Run `foundryos setup --only 2fa` in a terminal."
            )
        self.say(
            "Foundry AI OS setup. Finished steps are skipped, so you can run this again at any time."
        )
        if "service" in plan:
            self.optional("service", self.step_service)
        if not self.interactive:
            self.preflight(plan)
        for step in plan:
            if step == "service":
                continue
            if step == "account":
                self.step_account()
            elif step == "2fa":
                self.optional("2fa", self.step_two_factor)
            elif step == "company":
                self.step_company()
            elif step == "ai":
                self.step_ai()
            elif step == "work":
                self.step_work()
            elif step == "repo":
                self.repo_step_guarded()
            elif step == "public-url":
                self.optional("public-url", self.step_public_url)
        self.close_session()
        if self.opts.only in (None, "company", "ai", "work", "repo"):
            self.seed_idea()
        self.finish()

    def optional(self, step: str, fn: Callable[[], None]) -> None:
        """A step whose failure must not undo the rest (service, 2FA, public URL)."""
        try:
            fn()
        except CliError as exc:
            if exc.code == "usage":
                raise
            self.warn(f"{step}: {exc.message}")
            self.record(step, "failed", error=exc.message)

    # ------------------------------------------------------------------ preflight (non-interactive)

    def preflight(self, plan: list[str]) -> None:
        """Before anything is created: list every input a non-interactive run still lacks (exit 2)."""
        o, missing = self.opts, []
        state_ = (self.anon().get("/auth/session") or {}).get("state")
        setup_needed = state_ == "setup_required"
        credential_ok, companies, ready = False, [], None
        if not setup_needed:
            try:
                self.api.get("/me")
                credential_ok = True
                companies = self.api.paged("/companies")
                if companies:
                    self.company_id = self.st.company_id()
                    ready = self.ai_status().get("ready") is True
            except CliError as exc:
                if exc.exit_code != EXIT_AUTH:
                    raise
        if "account" in plan and (setup_needed or not credential_ok):
            if not o.username:
                missing.append("--username")
            if "password" not in self.secrets:
                missing.append("password (a 'password=' line in --secrets-stdin)")
            if setup_needed and self.remote and "setup_token" not in self.secrets:
                sess = self.anon().get("/auth/session")
                if sess.get("setup_token_required"):
                    missing.append("setup_token (a 'setup_token=' line in --secrets-stdin)")
        will_have_company = bool(companies) or "company" in plan
        if (
            "company" in plan
            and not companies
            and (setup_needed or credential_ok)
            and not o.company_name
        ):
            missing.append("--company-name")
        if "ai" in plan and will_have_company and ready is not True:
            if not o.provider:
                missing.append("--provider")
            else:
                preset = PRESET_BY_FLAG[o.provider]
                if preset.id != "ollama" and not o.model:
                    missing.append("--model")
                if preset.needs_base_url and preset.id != "ollama" and not o.base_url:
                    missing.append("--base-url")
                if preset.needs_key and "api_key" not in self.secrets:
                    missing.append("api_key (an 'api_key=' line in --secrets-stdin)")
        hybrid = (o.work_mode or ("hybrid" if o.cli else "api")) == "hybrid"
        if "work" in plan and hybrid:
            if not o.cli:
                missing.append("--cli")
            elif not o.cli_auth:
                missing.append("--cli-auth")
            elif o.cli_auth == "api-key" and "cli_api_key" not in self.secrets:
                missing.append("cli_api_key (a 'cli_api_key=' line in --secrets-stdin)")
        if (
            "repo" in plan
            and o.github in ("new", "existing")
            and not self._repo_already_bound(bool(companies) and credential_ok)
        ):
            if o.github == "existing" and not o.github_repo:
                missing.append("--github-repo")
            if "github_token" not in self.secrets and not self._has_github_account(
                bool(companies) and credential_ok
            ):
                missing.append("github_token (a 'github_token=' line in --secrets-stdin)")
        if "public-url" in plan and self.opts.only == "public-url" and not o.public_url:
            missing.append("--public-url")
        if missing:
            raise usage("--non-interactive is missing: " + ", ".join(missing) + ".")

    # ------------------------------------------------------------------ step: the server

    def _in_docker(self) -> bool:
        try:
            from app.ops import headless

            return Path(headless.DOCKERENV).exists()
        except Exception:
            return Path("/.dockerenv").exists()

    def _healthy(self) -> bool:
        try:
            health = self.anon().get("/health")
        except CliError:
            return False
        return isinstance(health, dict) and health.get("status") == "ok"

    def step_service(self) -> None:
        self.header("Server")
        if self.remote:
            self.say("Using a remote server: nothing to start on this machine.")
            self.record("service", "skipped", reason="remote")
            return
        if self._in_docker():
            self.say(
                "Running in a container: the container's own supervisor keeps Foundry running."
            )
            self.record("service", "skipped", reason="docker")
            return
        want = self.opts.service
        if want is None:
            want = (
                self.confirm(
                    "Keep Foundry running in the background and after reboot?", default=True
                )
                if self.interactive
                else False
            )
        linger = True if self.opts.linger is None else self.opts.linger
        if not want and self._healthy():
            self.say("Foundry is already running.")
            self.record("service", "already_done", background_service=False)
            return
        try:
            from app.core.config import Settings
            from app.ops import lifecycle

            ensure_running = lifecycle.ensure_running
        except (ImportError, AttributeError):
            self.warn(
                "Starting the server from here is not available in this build; run `foundryos` first."
            )
            self.record("service", "skipped", reason="unavailable")
            return
        try:
            ensure_running(Settings(), self.say, service=bool(want), linger=bool(want) and linger)
        except CliError:
            raise
        except Exception as exc:
            raise CliError(str(getattr(exc, "message", None) or exc)) from exc
        self.record("service", "done", background_service=bool(want), linger=bool(want) and linger)

    # ------------------------------------------------------------------ step: account

    def step_account(self) -> None:
        self.header("Account")
        anon = self.anon()
        sess = anon.get("/auth/session") or {}
        if sess.get("state") == "setup_required":
            self._create_account(anon, sess)
            self.record("account", "done", username=self.username, token_file=self.token_file)
            return
        try:
            me = self.api.get("/me")
        except CliError as exc:
            if exc.exit_code != EXIT_AUTH:
                raise
            self.say("The account exists, but this machine is not signed in to it. Signing in...")
            session = self._login_session()
            created, path = mint_cli_token(
                self.st, session, name_prefix="foundryos-cli", keep_session=True
            )
            self.session = session
            self.token_file = str(path)
            self.username = self.username or self.opts.username
            self.say(f"Signed in. Token saved to {path} (scope {created.get('scope')}).")
            self.record("account", "done", username=self.username, token_file=self.token_file)
            return
        user = me.get("user") or {}
        self.username = user.get("username") or user.get("email") or user.get("display_name")
        self.say(f"Already done: signed in as {self.username}.")
        self.record("account", "already_done", username=self.username)

    def _read_password(self) -> str:
        while True:
            first = self.ask("Password", hide=True)
            if self.ask("Repeat password", hide=True) == first:
                return first
            self.warn("The two entries differ. Try again.")

    def _create_account(self, anon: ApiClient, sess: dict) -> None:
        o = self.opts
        username = o.username or (self.ask("Username") if self.interactive else None)
        if not username:
            raise self.need("--username")
        display = o.display_name or (
            self.ask("Display name", default=username) if self.interactive else username
        )
        email = o.email
        if email is None and self.interactive:
            email = self.ask("Email (optional)", default="") or None
        token = None
        if sess.get("setup_token_required"):
            self.say("This server is not on this machine, so it asks for its setup token.")
            self.say("Print it on the server with: foundryos auth setup-token")
            token = self.secret("setup_token", "Setup token")
        password = self.secrets.pop("password", None)
        if password is None:
            if not self.interactive:
                raise self.need("password in --secrets-stdin")
            password = self._read_password()
        for _ in range(8):
            body: dict[str, Any] = {
                "username": username,
                "display_name": display,
                "password": password,
            }
            if email:
                body["email"] = email
            if token:
                body["setup_token"] = token
            try:
                result = anon.request("POST", "/auth/setup", json_body=body)
                break
            except CliError as exc:
                field = self._error_field(exc)
                if not self.interactive or (field is None and exc.code not in _SETUP_TOKEN_CODES):
                    raise CliError(
                        onboarding.redact(exc.message, password, token),
                        exit_code=exc.exit_code,
                        code=exc.code,
                        details=exc.details,
                    ) from None
                self.warn(onboarding.redact(exc.message, password, token))
                if exc.code in _SETUP_TOKEN_CODES:
                    token = self.ask("Setup token", hide=True)
                elif field == "password":
                    password = self._read_password()
                elif field == "username":
                    username = self.ask("Username")
                elif field == "email":
                    email = self.ask("Email (optional)", default="") or None
                elif field == "display_name":
                    display = self.ask("Display name", default=username)
                else:
                    raise
        else:
            raise CliError("Could not create the account after several tries.")
        if not isinstance(result, dict) or result.get("state") != "authenticated":
            raise CliError("The server did not finish creating the account.")
        self.username = (result.get("user") or {}).get("username") or username
        self.say(f"Created the owner account {self.username}.")
        created, path = mint_cli_token(
            self.st, anon, name_prefix="foundryos-cli", keep_session=True
        )
        self.session = anon
        self.token_file = str(path)
        self.say(
            f"Saved a personal API token for this machine to {path} (mode 600, scope {created.get('scope')})."
        )

    @staticmethod
    def _error_field(exc: CliError) -> str | None:
        details = exc.details
        if isinstance(details, list) and details and isinstance(details[0], dict):
            loc = details[0].get("loc")
            if isinstance(loc, list) and loc:
                return str(loc[-1])
        return None

    def _login_session(self) -> ApiClient:
        """A cookie session for the existing account (password, and a 2FA code if it has one)."""
        o = self.opts
        username = o.username or (self.ask("Username") if self.interactive else None)
        if not username:
            raise self.need("--username")
        password = self.secrets.get("password")
        if password is None:
            if not self.interactive:
                raise self.need("password in --secrets-stdin")
            password = self.ask("Password", hide=True)
        client = self.st.anonymous_client()
        body = {"username": username, "password": password}
        result = client.request("POST", "/auth/login", json_body=body)
        if isinstance(result, dict) and result.get("two_factor_required"):
            if not self.interactive:
                raise usage(
                    "This account uses two-factor sign-in: run `foundryos setup` in a terminal."
                )
            code = self.ask("Authenticator or recovery code")
            result = client.request("POST", "/auth/login", json_body={**body, "totp_code": code})
        if not isinstance(result, dict) or result.get("state") != "authenticated":
            raise CliError("Sign-in did not complete.", exit_code=EXIT_AUTH, code="unauthenticated")
        self.username = (result.get("user") or {}).get("username") or username
        return client

    def close_session(self) -> None:
        if self.session is not None:
            end_session(self.session)
            self.session = None

    # ------------------------------------------------------------------ step: two-factor

    def public_planned(self) -> bool:
        """Is a public URL going to be set up? (It changes the 2FA default.)"""
        if self.opts.public_url is not None:
            return self.opts.public_url != "off"
        if "public-url" not in self.plan() or not self.interactive:
            return False
        return self.public_choice() != "off"

    def step_two_factor(self) -> None:
        self.header("Two-factor sign-in")
        try:
            sess = self.api.get("/auth/session") or {}
        except CliError:
            sess = {}
        enabled = (sess.get("user") or {}).get("two_factor_enabled") is True
        if enabled:
            self.say("Already done: two-factor sign-in is on.")
            self.record("2fa", "already_done")
            self.two_factor = True
            self.close_session()
            return
        want = self.opts.two_fa
        if want is None:
            want = (
                self.confirm(
                    "Protect sign-in with an authenticator app (two-factor)? "
                    "Strongly recommended if Foundry will have a public URL.",
                    default=self.public_planned(),
                )
                if self.interactive
                else False
            )
        if not want:
            self.say("Skipped. Add it later with: foundryos setup --only 2fa")
            self.record("2fa", "skipped")
            self.two_factor = False
            self.close_session()
            return
        client, csrf = self._account_client()
        enrol = client.request("POST", "/auth/2fa/enroll", json_body={}, headers=csrf)
        uri, secret = enrol["otpauth_uri"], enrol["secret"]
        self.say(
            "Scan this QR code with an authenticator app (Aegis, 1Password, Google Authenticator...):"
        )
        qr = onboarding.qr_text(uri)
        if qr:
            self.say(qr)
        self.say("Or add it by hand:")
        self.say(f"  secret: {secret}")
        self.say(f"  link:   {uri}")
        result = None
        for attempt in range(3):
            code = self.ask("Enter the 6-digit code from the app")
            try:
                result = client.request(
                    "POST", "/auth/2fa/verify", json_body={"code": code}, headers=csrf
                )
                break
            except CliError as exc:
                if attempt == 2 or exc.exit_code not in (EXIT_AUTH, EXIT_ERROR):
                    raise
                self.warn(exc.message)
        codes = (result or {}).get("recovery_codes") or []
        self.say(
            "Two-factor sign-in is on. Recovery codes (each works once; they are shown only now):"
        )
        for line in codes:
            self.say(f"  {line}")
        self.say("Store them somewhere safe, not on this machine.")
        self.record("2fa", "done")
        self.two_factor = True
        self.close_session()

    def _account_client(self) -> tuple[ApiClient, dict[str, str]]:
        """The credential that may manage the account: the setup session, the owner token on the server
        machine, or a fresh password sign-in. (A personal API token may not enrol 2FA.)"""
        if self.session is not None:
            return self.session, csrf_headers(self.session)
        cred = config.resolve_credential()
        if cred is not None and cred.source == "owner-token":
            return self.api, {}
        self.session = self._login_session()
        return self.session, csrf_headers(self.session)

    # ------------------------------------------------------------------ step: company

    def step_company(self) -> None:
        self.header("Company")
        companies = self.api.paged("/companies")
        if companies:
            self.company_id = self.st.company_id()
            match = next((c for c in companies if c["id"] == self.company_id), companies[0])
            self.company_name = match["name"]
            self.say(f"Already done: you have the company {self.company_name}.")
            self.record("company", "already_done", id=self.company_id, name=self.company_name)
            return
        o = self.opts
        name = o.company_name or (self.ask("Company name") if self.interactive else None)
        if not name:
            raise self.need("--company-name")
        name = name.strip()
        if not 1 <= len(name) <= 120:
            raise usage("The company name must be 1 to 120 characters.")
        idea = self.idea
        if o.idea is None and self.interactive:
            while True:
                idea = self.ask("What is it going to do? One line (optional)", default="").strip()
                try:
                    onboarding.idea_error(idea)
                    break
                except CliError as exc:
                    self.warn(exc.message)
        self.idea = idea
        tz = o.timezone
        if tz is None:
            detected = onboarding.detect_timezone()
            tz = detected
            if self.interactive:
                while True:
                    tz = self.ask("Timezone", default=detected)
                    if onboarding.is_valid_timezone(tz):
                        break
                    self.warn(
                        f"Unknown timezone {tz!r}. Use an IANA name such as Europe/Berlin or UTC."
                    )
        created = self.api.post(
            "/companies", {"name": name, "timezone": tz, "founder": {"name": "Atlas"}}
        )
        self.company_id = created["id"]
        self.st._company_id = created["id"]
        self.company_name = created["name"]
        self.say(f"Created {self.company_name} ({tz}) with its AI Co-founder.")
        self.record("company", "done", id=self.company_id, name=self.company_name, timezone=tz)

    # ------------------------------------------------------------------ step: the model API

    def step_ai(self) -> None:
        self.header("Model API")
        self.ensure_company()
        status = self.ai_status()
        o = self.opts
        forced = o.only == "ai" or o.provider is not None
        mgmt = status.get("management") or {}
        if status.get("ready") is True:
            where = f"{mgmt.get('provider_name')} ({mgmt.get('model')})"
            change = forced and (
                o.provider is not None
                or (
                    self.interactive
                    and self.confirm(
                        f"The model API is already connected to {where}. Change it?", default=False
                    )
                )
            )
            if not change:
                self.say(f"Already done: {where}, ready.")
                self.management = self._management_of(status)
                self.record("ai", "already_done", **self._ai_info(status))
                return
        else:
            self.say(
                onboarding.BLOCKING_REASON_TEXT.get(
                    status.get("blocking_reason") or "", BLOCKING_REASON_TEXT["no_tested_provider"]
                )
            )
        provider, model = self._connect_ai()
        status = self._commit_management(provider["id"], model, status)
        self.fresh_ai = True
        self.management = self._management_of(status)
        self.say(f"The model API is ready: {provider['name']} ({model}).")
        self.record("ai", "done", **self._ai_info(status))

    @staticmethod
    def _management_of(status: dict) -> dict[str, Any]:
        m = status.get("management") or {}
        return {
            "provider_id": m.get("provider_id"),
            "name": m.get("provider_name"),
            "kind": m.get("kind"),
            "model": m.get("model"),
        }

    def _ai_info(self, status: dict) -> dict[str, Any]:
        m = self._management_of(status)
        return {
            "ready": status.get("ready") is True,
            "provider": m["name"],
            "kind": m["kind"],
            "model": m["model"],
        }

    def _pick_preset(self, *, first: bool) -> ProviderPreset:
        if first and self.opts.provider:
            return PRESET_BY_FLAG[self.opts.provider]
        if not self.interactive:
            raise self.need("--provider")
        n = self.menu(
            "Which AI should run your company?",
            [f"{p.label}: {p.blurb}" for p in ALL_PRESETS],
        )
        return ALL_PRESETS[n - 1]

    def _connect_ai(self) -> tuple[dict, str]:
        """Connect a provider and get it through its live test; loop on failure (interactive)."""
        att = _Attempt(self._pick_preset(first=True))
        first = True
        while True:
            problem: str
            try:
                provider, test = self._attempt(att, first)
                if provider is not None and onboarding.provider_test_passed(test):
                    return provider, att.model or provider["default_model"]
                problem = onboarding.failure_summary(test)
            except CliError as exc:
                if exc.code == "usage" or exc.exit_code not in (EXIT_ERROR, EXIT_CONFLICT):
                    raise
                problem = exc.message
                self.warn(problem)
            first = False
            if not self.interactive:
                raise CliError(
                    f"The model API did not pass its test. {problem} "
                    "Nothing was selected; the provider was left switched off."
                )
            self.say()
            choice = typer.prompt(
                "[r]etry this provider, use an[o]ther one, or [q]uit",
                type=click.Choice(["r", "o", "q"]),
                default="r",
                err=self.json,
            )
            if choice == "q":
                raise CliError(f"Stopped: {problem} Run `foundryos setup --only ai` to try again.")
            if choice == "o":
                att = _Attempt(self._pick_preset(first=False))

    def _attempt(self, att: _Attempt, first: bool) -> tuple[dict | None, dict | None]:
        """Create or update the provider, test it, and on a failed test switch it off.
        Returns the provider and its last test."""
        if att.preset.id == "ollama":
            provider, test = self._attempt_ollama(att, first)
        else:
            provider, test = self._attempt_cloud(att, first)
        self.say()
        for line in onboarding.check_lines(test):
            self.say(f"  {line}")
        served = (test or {}).get("served_context")
        if served:
            self.say(f"  Served context window: {served:,} tokens.")
        for warning in (test or {}).get("warnings") or []:
            self.warn(warning)
        if onboarding.provider_test_passed(test):
            return provider, test
        if provider is not None:
            try:  # as the web does: a provider that failed its test is switched off
                self.api.patch(self.cpath(f"/model-providers/{provider['id']}"), {"enabled": False})
            except CliError as exc:
                self.warn(f"Could not switch the failed provider off: {exc.message}")
            else:
                self.say(f"  {provider['name']} failed its test and was switched off.")
        return provider, test

    def _find_provider(self, name: str) -> dict | None:
        return next(
            (p for p in self.api.paged(self.cpath("/model-providers")) if p.get("name") == name),
            None,
        )

    def _gather_cloud(self, att: _Attempt, first: bool) -> None:
        p, o = att.preset, self.opts
        prior = self._find_provider(p.label) if att.provider_id is None else None
        if prior is not None:
            att.provider_id = prior["id"]
            att.credential_id = att.credential_id or prior.get("credential_id")
            att.base_url = att.base_url or prior.get("base_url")
            att.model = att.model or prior.get("default_model")
        # base URL
        if p.needs_base_url:
            if first and o.base_url:
                att.base_url = o.base_url
            elif self.interactive:
                while True:
                    att.base_url = self.ask(
                        "Base URL (usually ending in /v1)", default=att.base_url or p.base_url_hint
                    ).strip()
                    err = onboarding.base_url_error(att.base_url)
                    if not err:
                        break
                    self.warn(err)
            elif not att.base_url:
                raise self.need("--base-url")
        # API key
        if p.takes_key:
            if first and "api_key" in self.secrets:
                att.api_key = self.secrets["api_key"]
            elif self.interactive:
                while True:
                    if att.credential_id:
                        hint = " (Enter keeps the key already stored)"
                    else:
                        hint = "" if p.needs_key else " (Enter if it needs none)"
                    key = self.ask(f"{p.key_label}{hint}", hide=True, default="")
                    if key or att.credential_id or not p.needs_key:
                        att.api_key = key or None
                        break
                    self.warn("An API key is required for this provider.")
            elif p.needs_key and not att.credential_id:
                raise self.need("api_key in --secrets-stdin")
            if att.api_key:
                err = onboarding.api_key_error(att.api_key)
                if err:
                    att.api_key = None
                    raise CliError(err)
        # model
        if first and o.model:
            att.model = o.model
        elif self.interactive:
            if p.model_suggestions:
                self.say(f"Examples: {', '.join(p.model_suggestions)}")
            default = att.model or (p.model_suggestions[0] if p.model_suggestions else None)
            att.model = self.ask("Model", default=default).strip()
        elif not att.model:
            raise self.need("--model")

    def _upsert_credential(self, name: str, value: str) -> str:
        """Store a secret (or replace the value of a credential with that name). Returns its id."""
        try:
            return self.api.post(
                self.cpath("/credentials"), {"name": name, "kind": "api_key", "value": value}
            )["id"]
        except CliError as exc:
            if exc.exit_code != EXIT_CONFLICT:
                raise CliError(
                    onboarding.redact(exc.message, value), exit_code=exc.exit_code, code=exc.code
                ) from None
        existing = next(
            (c for c in self.api.paged(self.cpath("/credentials")) if c["name"] == name), None
        )
        if existing is None:
            raise CliError(f"A credential named {name} exists but could not be found.")
        self.api.post(self.cpath(f"/credentials/{existing['id']}/rotate"), {"value": value})
        return existing["id"]

    def _attempt_cloud(self, att: _Attempt, first: bool) -> tuple[dict | None, dict | None]:
        p, o = att.preset, self.opts
        self._gather_cloud(att, first)
        assert att.model
        if att.api_key:
            att.credential_id = self._upsert_credential(onboarding.credential_name(p), att.api_key)
            att.api_key = None  # the server has it
            self.say("  API key stored (encrypted, write-only).")
        body = onboarding.provider_body(
            p,
            base_url=att.base_url,
            model=att.model,
            credential_id=att.credential_id,
            allow_private_network=o.allow_private_network,
        )
        try:
            if att.provider_id:
                patch = {k: v for k, v in body.items() if k not in ("name", "kind", "is_local")}
                if p.kind == "openai_compatible":  # a new address can change whether it is local
                    patch["is_local"] = body["is_local"]
                provider = self.api.patch(self.cpath(f"/model-providers/{att.provider_id}"), patch)
            else:
                provider = self.api.post(self.cpath("/model-providers"), body)
        except CliError as exc:
            raise CliError(
                exc.message, exit_code=exc.exit_code, code=exc.code, details=exc.details
            ) from None
        att.provider_id = provider["id"]
        if self.interactive:
            self._offer_model_picker(att)
        self.say(
            "Testing the connection: reachable, authenticated, tool calling, context, model..."
        )
        provider = self._run_test(att)
        return provider, provider.get("last_test")

    def _run_test(self, att: _Attempt) -> dict:
        body = {"allow_private_network": True} if self.opts.allow_private_network else None
        return self.api.post(
            self.cpath(f"/model-providers/{att.provider_id}/test"),
            body,
            timeout=PROVIDER_TEST_TIMEOUT,
        )

    def _offer_model_picker(self, att: _Attempt) -> None:
        try:
            listing = self.api.get(self.cpath(f"/model-providers/{att.provider_id}/models"))
        except CliError:
            return
        models = onboarding.normalize_models(listing) if listing.get("available") else []
        if not models:
            return
        known = any(m.id == att.model for m in models)
        self.say(
            f"{att.preset.label} offers {len(models)} models"
            + ("." if known else f", but not {att.model!r}.")
        )
        if not self.confirm("Pick the model from that list?", default=not known):
            return
        choice = self._pick_model(models, att.model or "")
        if choice and choice != att.model:
            att.model = choice
            self.api.patch(
                self.cpath(f"/model-providers/{att.provider_id}"), {"default_model": choice}
            )

    def _pick_model(self, models: list[ModelOption], current: str) -> str:
        while True:
            query = self.ask(f"Search (part of a name; Enter keeps {current})", default="").strip()
            if not query:
                return current
            matches = onboarding.filter_models(models, query, 15)
            if not matches:
                self.warn("Nothing matches. Try another part of the name.")
                continue
            for i, m in enumerate(matches, start=1):
                ctx = f"  ({m.context_length:,} tokens)" if m.context_length else ""
                self.say(f"  {i:>2}. {m.id}{ctx}")
            picked = self.ask("Number to choose (Enter to search again)", default="").strip()
            if picked.isdigit() and 1 <= int(picked) <= len(matches):
                return matches[int(picked) - 1].id

    def _attempt_ollama(self, att: _Attempt, first: bool) -> tuple[dict | None, dict | None]:
        o = self.opts
        base_url = o.base_url if first else None
        if self.interactive and not (first and o.base_url):
            while True:
                base_url = self.ask(
                    "Ollama address", default=att.base_url or onboarding.OLLAMA_PRESET.base_url_hint
                ).strip()
                err = onboarding.base_url_error(base_url)
                if not err:
                    break
                self.warn(err)
        att.base_url = base_url
        path = self.cpath("/model-providers/detect")
        extra: dict[str, Any] = {}
        if base_url:
            extra["base_url"] = base_url
        if o.allow_private_network:
            extra["allow_private_network"] = True
        probe = self.api.post(path, {"register": False, **extra})
        if not probe.get("reachable"):
            for w in probe.get("warnings") or []:
                self.warn(w)
            raise CliError(
                f"No Ollama answered at {probe.get('base_url')}. Start it (`ollama serve`) or check the address."
            )
        models = probe.get("models") or []
        if not models:
            raise CliError("Ollama has no models. Pull one, for example: ollama pull qwen2.5:7b")
        self.say(f"Ollama at {probe['base_url']} has {len(models)} model(s):")
        for m in models:
            flags = ", ".join(
                x
                for x in (
                    m.get("parameter_size"),
                    "tools" if m.get("tool_calling") else None,
                    "recommended" if m.get("recommended") else None,
                )
                if x
            )
            self.say(f"  - {m['name']}" + (f" ({flags})" if flags else ""))
        for w in probe.get("warnings") or []:
            self.warn(w)
        model = o.model if first and o.model else None
        if model is None:
            default = probe.get("recommended_model")
            model = self.ask("Model", default=default).strip() if self.interactive else default
        if not model:
            raise CliError(
                "None of Ollama's models can call tools. Pull one, for example: ollama pull qwen2.5:7b"
            )
        att.model = model
        reg = self.api.post(
            path,
            {"register": True, "model": model, "make_default": True, **extra},
            timeout=PROVIDER_TEST_TIMEOUT,
        )
        provider = reg.get("registered")
        if not provider:
            for w in reg.get("warnings") or []:
                self.warn(w)
            raise CliError(f"{model} could not be registered (it needs to support tool calling).")
        att.provider_id = provider["id"]
        test = provider.get("last_test")
        if not onboarding.provider_test_passed(test):
            self.say("Testing the connection...")
            provider = self._run_test(att)
            test = provider.get("last_test")
        return provider, test

    def _commit_management(self, provider_id: str, model: str, status: dict) -> dict:
        """Select the tested provider as the management model (keeping a working hybrid choice)."""
        mode = status.get("work_mode") or "api"
        runtime = status.get("work_runtime") or "company_agent"
        if mode != "hybrid":
            mode, runtime = "api", "company_agent"
        body = {
            "management_provider_id": provider_id,
            "management_model": model,
            "work_mode": mode,
            "work_runtime": runtime,
        }
        try:
            result = self.api.put(self.cpath("/ai-setup"), body)
        except CliError as exc:
            if mode != "hybrid":
                raise self._ai_not_ready(exc) from None
            body.update(work_mode="api", work_runtime="company_agent")
            result = self.api.put(self.cpath("/ai-setup"), body)
            self.warn(
                "The coding CLI choice is not ready any more, so the work mode went back to API only."
            )
        if result.get("ready") is not True:
            reason = result.get("blocking_reason") or "no_tested_provider"
            raise CliError(BLOCKING_REASON_TEXT.get(reason, "The AI setup is not ready yet."))
        return result

    @staticmethod
    def _ai_not_ready(exc: CliError) -> CliError:
        reason = exc.details.get("reason") if isinstance(exc.details, dict) else None
        extra = f" {BLOCKING_REASON_TEXT[reason]}" if reason in BLOCKING_REASON_TEXT else ""
        return CliError(
            exc.message + extra, exit_code=exc.exit_code, code=exc.code, details=exc.details
        )

    # ------------------------------------------------------------------ step: work mode

    def step_work(self) -> None:
        self.header("Work mode")
        self.ensure_company()
        status = self.ai_status()
        if status.get("ready") is not True:
            raise CliError("The model API is not ready yet. Run `foundryos setup --only ai` first.")
        o = self.opts
        mode = o.work_mode or ("hybrid" if o.cli else None)
        forced = o.only == "work" or mode is not None
        current = status.get("work_mode") or "api"
        if mode is None and not (self.fresh_ai or forced):
            label = "API only" if current == "api" else f"API + {status.get('work_runtime')}"
            self.say(f"Already done: {label}.")
            self.record("work", "already_done", mode=current, runtime=status.get("work_runtime"))
            return
        if mode is None:
            if not self.interactive:
                mode = "api"
            else:
                n = self.menu(
                    "How should your employees do their hands-on work?",
                    [
                        "API only: everything runs through the model API (simplest)",
                        "API + coding CLIs: employees' work tasks run in Claude Code, Codex, "
                        "OpenCode, Antigravity or Cursor on this machine",
                    ],
                )
                mode = "api" if n == 1 else "hybrid"
        m = self._management_of(status)
        runtime: str | None = None
        if mode == "hybrid":
            runtime = self._setup_cli(m)
            if runtime is None:
                mode = "api"
        if mode == "api":
            if current != "api":
                self.api.put(
                    self.cpath("/ai-setup"),
                    {
                        "management_provider_id": m["provider_id"],
                        "management_model": m["model"],
                        "work_mode": "api",
                        "work_runtime": "company_agent",
                    },
                )
            self.say("Work mode: API only.")
            self.record("work", "done", mode="api", runtime="company_agent")
            return
        try:
            self.api.put(
                self.cpath("/ai-setup"),
                {
                    "management_provider_id": m["provider_id"],
                    "management_model": m["model"],
                    "work_mode": "hybrid",
                    "work_runtime": runtime,
                },
            )
        except CliError as exc:
            raise self._ai_not_ready(exc) from None
        self.say(f"Work mode: API for management + {runtime} for the work.")
        self.record("work", "done", mode="hybrid", runtime=runtime)

    def _cli_status(self, cli_id: str) -> dict:
        return self.api.get(f"/cli-runtimes/{cli_id}")

    def _setup_cli(self, mgmt: dict) -> str | None:
        """Install (if needed), sign in and test a coding CLI. Returns its runtime id, or None to stay on
        API only (interactive fallback)."""
        o = self.opts
        cli_id = o.cli
        if cli_id is None:
            if not self.interactive:
                raise self.need("--cli")
            rows = []
            for cid in onboarding.CLI_IDS:
                try:
                    s = self._cli_status(cid)
                    note = (
                        f"installed {s.get('version') or ''}".strip()
                        if s.get("installed")
                        else "not installed"
                    )
                except CliError:
                    note = "status unknown"
                rows.append(f"{CLIS[cid].display_name} ({note})")
            cli_id = onboarding.CLI_IDS[self.menu("Which coding CLI?", rows) - 1]
        spec = CLIS[cli_id]
        cli = self._cli_status(cli_id)
        if not cli.get("installed"):
            cli = self._install_cli(cli, spec)
        else:
            self.say(
                f"{spec.display_name} is installed ({cli.get('version') or 'version unknown'})."
            )
        while True:
            self._cli_auth(cli, spec, mgmt)
            result = self._test_cli(spec)
            last = ((result.get("auth") or {}).get("last_test")) or {}
            if last.get("ok") is True:
                self.say(f"{spec.display_name} works: {last.get('detail') or 'test passed'}")
                return spec.runtime_id
            detail = last.get("detail") or "the test did not pass"
            if not self.interactive:
                raise CliError(f"{spec.display_name} failed its test: {detail}")
            self.warn(f"{spec.display_name} failed its test: {detail}")
            if not self.confirm("Set it up again (sign in / key) and retest?", default=True):
                self.say("Staying on API only. Run `foundryos setup --only work` to try again.")
                return None

    def _install_cli(self, cli: dict, spec) -> dict:
        install = cli.get("install") or {}
        if spec.npm_package is None:
            manual = spec.manual_install or install.get("manual")
            self.say(
                f"{spec.display_name} has no automatic install (the vendor ships a shell installer)."
            )
            self.say("Run this yourself, in another terminal, after reading the script:")
            self.say(f"  {manual}")
            if not self.interactive:
                raise CliError(
                    f"{spec.display_name} is not installed. Install it with the command above and rerun."
                )
            if not self.confirm("Press Enter once it is installed (n to stop)", default=True):
                raise CliError("Stopped. Rerun `foundryos setup --only work` after installing it.")
            cli = self._cli_status(spec.id)
            if not cli.get("installed"):
                raise CliError(f"{spec.display_name} is still not found on this machine.")
            return cli
        if not install.get("available"):
            raise CliError(
                f"{spec.display_name} is not installed and cannot be installed from here "
                f"(npm was not found on the server machine). {spec.login_hint}"
            )
        command_text = install.get("command")
        if self.opts.install_cli:
            approved = True
        elif self.interactive:
            approved = self.confirm(
                f"Install {spec.display_name} now? This runs: {command_text}", default=True
            )
        else:
            raise usage(
                f"{spec.display_name} is not installed. Pass --install-cli to run `{command_text}`."
            )
        if not approved:
            raise CliError(f"{spec.display_name} is not installed. Install it and rerun.")
        self.say(f"Installing: {command_text}")
        self.api.post(f"/cli-runtimes/{spec.id}/install", {"confirm": True})
        seen = 0
        waited = 0.0
        while True:
            log = self.api.get(f"/cli-runtimes/{spec.id}/install-log")
            lines = log.get("lines") or []
            for line in lines[seen:]:
                self.say(f"  {line}")
            seen = len(lines)
            if log.get("status") == "succeeded":
                break
            if log.get("status") == "failed":
                raise CliError(
                    f"Installing {spec.display_name} failed (exit {log.get('exit_code')}). See the output above."
                )
            waited += INSTALL_POLL_SECONDS
            if waited > INSTALL_WAIT_SECONDS:
                raise CliError(
                    f"Installing {spec.display_name} is taking too long. Check `foundryos setup --only work` later."
                )
            sleep(INSTALL_POLL_SECONDS)
        cli = self._cli_status(spec.id)
        if not cli.get("installed"):
            raise CliError(
                f"The install finished, but {spec.display_name} was not found on the server's PATH."
            )
        self.say(f"{spec.display_name} is installed ({cli.get('version') or 'version unknown'}).")
        return cli

    def _cli_auth(self, cli: dict, spec, mgmt: dict) -> None:
        o = self.opts
        modes = cli.get("auth_modes") or list(spec.auth_modes)
        choice = CLI_AUTH_FLAGS.get(o.cli_auth) if o.cli_auth else None
        if choice is None:
            if not self.interactive:
                raise self.need("--cli-auth")
            options, keys = [], []
            if "own_login" in modes:
                options.append("My own login (the account already signed in to the CLI)")
                keys.append("own_login")
            if "api_key" in modes:
                options.append("An API key (recommended: strongest isolation)")
                keys.append("api_key")
            if spec.id == "opencode":
                options.append("The model provider I just connected")
                keys.append("provider")
            choice = keys[
                self.menu(
                    "How should it sign in?",
                    options,
                    default=keys.index("api_key") + 1 if "api_key" in keys else 1,
                )
                - 1
            ]
        if choice == "own_login":
            if "own_login" not in modes:
                raise usage(f"{spec.display_name} has no own-login mode; use --cli-auth api-key.")
            self._own_login(spec)
            self.api.put(f"/cli-runtimes/{spec.id}/auth", {"mode": "own_login"})
        elif choice == "api_key":
            if spec.id == "opencode":
                if not self.interactive:
                    raise usage(
                        "OpenCode needs a provider/model name with an API key; use --cli-auth provider instead."
                    )
                model = self.ask(
                    "OpenCode model (provider/model, e.g. anthropic/claude-sonnet-4-5)"
                )
            else:
                model = None
            key = self.secret(
                "cli_api_key",
                f"{spec.display_name} API key" + (f" ({spec.key_env})" if spec.key_env else ""),
            )
            assert key
            err = onboarding.api_key_error(key)
            if err:
                raise CliError(err)
            cred_id = self._upsert_credential(f"{spec.id}-cli-key", key)
            body: dict[str, Any] = {"mode": "api_key", "credential_id": cred_id}
            if model:
                body["model"] = model
            self.api.put(f"/cli-runtimes/{spec.id}/auth", body)
            self.say(f"  {spec.display_name} will use the stored API key (encrypted, write-only).")
        else:  # provider: OpenCode on the company's model provider
            if spec.id != "opencode":
                raise usage("--cli-auth provider only works with OpenCode.")
            self.api.put(
                f"/cli-runtimes/{spec.id}/auth",
                {
                    "mode": "api_key",
                    "model_provider_id": mgmt["provider_id"],
                    "model": mgmt["model"],
                },
            )

    def _own_login(self, spec) -> None:
        """Offer to run the CLI's own login here, in this terminal. The argv is UNVERIFIED against the
        real CLIs (see ``headless_login_argv`` in the catalog); the test afterwards is the proof."""
        argv = list(spec.headless_login_argv or ())
        if self.remote:
            self.say(
                f"The CLI runs on the server machine. There, as the user that runs Foundry: {spec.login_hint}"
            )
            return
        if not argv or not self.interactive:
            self.say(
                f"Assuming {spec.display_name} is already signed in on this machine ({spec.login_hint}). The test below checks it."
            )
            return
        if not self.confirm(
            f"Sign in to {spec.display_name} now, in this terminal? (Answer no if you already did.)",
            default=True,
        ):
            return
        cli = self._cli_status(spec.id)
        argv[0] = cli.get("binary_path") or argv[0]
        if spec.id == "claude_code":
            self.say("Claude Code will start. Type /login, finish the sign-in, then type /exit.")
        self.say(f"Running: {' '.join(argv)}")
        code = run_interactive(argv)
        if code != 0:
            self.warn(
                f"{spec.display_name} exited with status {code}; the test below shows whether you are signed in."
            )

    def _test_cli(self, spec) -> dict:
        self.say(
            f'Testing {spec.display_name}: it gets one tiny prompt ("Reply with OK") and may spend '
            f"at most {CLI_TEST_SPEND_CAP} on the account or key it signs in with."
        )
        if self.interactive and not self.confirm("Run the test?", default=True):
            raise CliError(
                f"{spec.display_name} cannot do company work until it passes its test. Rerun `foundryos setup --only work`."
            )
        return self.api.post(f"/cli-runtimes/{spec.id}/test", timeout=CLI_TEST_TIMEOUT)

    # ------------------------------------------------------------------ the first note

    def seed_idea(self) -> None:
        if not self.idea:
            return
        try:
            cid = self.ensure_company()
            if self.ai_status().get("ready") is not True:
                self.say(
                    "Your idea will become the first note once the model API is ready (`foundryos setup --only ai`)."
                )
                self.record("idea", "waiting")
                return
            outcome = onboarding.seed_idea(self.api, cid, self.idea)
        except CliError as exc:
            self.warn(
                f'The company exists, but the idea could not be saved: {exc.message}. Add it with: foundryos note "..."'
            )
            self.record("idea", "failed")
            return
        text = {
            "note": "Saved your idea as the first Founder's note; the AI Co-founder will triage it.",
            "exists": "Your idea is already saved as a note.",
            "waiting": "Your idea will become the first note once the model API is ready.",
        }.get(outcome)
        if text:
            self.say(text)
        self.record("idea", outcome)

    # ------------------------------------------------------------------ step: the repository (M14)

    def _has_github_account(self, company_exists: bool) -> bool:
        if not company_exists:
            return False
        try:
            return bool(repo_cmd.working_github_accounts(self.api, self.ensure_company()))
        except CliError:
            return False

    def _repo_already_bound(self, company_exists: bool) -> bool:
        """Would the repository step only repeat what is bound already? (Then it needs no token.)"""
        if not company_exists:
            return False
        o = self.opts
        try:
            owner = name = None
            if o.github_repo:
                owner, name = onboarding.split_repo_ref(o.github, o.github_repo)
            return self._bound_match(self._bound_repos(), o.github, owner, name) is not None
        except CliError:
            return False

    def repo_step_guarded(self) -> None:
        """The repository step must finish before the idea becomes a note; when it fails, say how the
        idea still gets saved."""
        try:
            self.step_repo()
        except CliError as exc:
            if exc.code != "usage" and self.idea:
                self.warn(
                    "Your idea is saved as the first note once this step is done: "
                    'foundryos setup --only repo --idea "..."'
                )
            raise

    def _bound_repos(self) -> list[dict]:
        items = self.api.paged(self.cpath("/repositories"))
        return [r for r in items if r.get("status") != "disconnected"]

    def _company_label(self) -> str:
        if not self.company_name:
            wanted = self.ensure_company()
            for c in self.api.paged("/companies"):
                if c["id"] == wanted:
                    self.company_name = c["name"]
        return self.company_name or "company"

    @staticmethod
    def _bound_match(
        bound: list[dict], choice: str | None, owner: str | None, name: str | None
    ) -> dict | None:
        """The binding a rerun would only repeat: the named repository, or (new, no name) the company's."""
        if name:
            for r in bound:
                same = (r.get("full_name") or "").lower() == f"{owner}/{name}".lower()
                if (owner and same) or (
                    not owner and (r.get("name") or "").lower() == name.lower()
                ):
                    return r
            return None
        if choice == "new":
            return next((r for r in bound if r.get("is_company_default")), None)
        return None

    def _repo_info(self, repo: dict) -> dict[str, Any]:
        return {
            "repository_id": repo.get("id"),
            "full_name": repo.get("full_name"),
            "project": repo.get("project_name"),
            "repo_status": repo.get("status"),
            "origin": repo.get("origin"),
            "visibility": repo.get("visibility"),
            "default_branch": repo.get("default_branch"),
            "work_branch": repo.get("work_branch"),
            "merge_mode": (repo.get("policy") or {}).get("merge_mode"),
            "onboarding": repo.get("onboarding_status"),
        }

    def _pick(self, title: str, options: list[tuple[str, str]], default: str) -> str:
        """A menu of ``(value, label)``; returns the value."""
        values = [v for v, _ in options]
        n = self.menu(title, [label for _, label in options], default=values.index(default) + 1)
        return values[n - 1]

    def step_repo(self) -> None:
        self.header("Repository")
        cid = self.ensure_company()
        o = self.opts
        if o.github == "none":
            self.say("Skipped: no GitHub repository. Add one later: foundryos repo connect")
            self.record("repo", "skipped")
            return
        wanted_owner = wanted_name = None
        if o.github_repo:
            wanted_owner, wanted_name = onboarding.split_repo_ref(o.github, o.github_repo)
        bound = self._bound_repos()
        if bound:
            same = self._bound_match(bound, o.github, wanted_owner, wanted_name)
            more = (
                o.github is None
                and self.interactive
                and o.only == "repo"
                and self.confirm(
                    f"{onboarding.repo_line(bound[0])}. Connect another repository?", default=False
                )
            )
            if same is not None or (o.github is None and not more):
                shown = same or bound[0]
                self.say(f"Already done: {onboarding.repo_line(shown)}.")
                self.record("repo", "already_done", **self._repo_info(shown))
                return
        elif o.github is None and not self.interactive:
            self.say("Skipped: no GitHub repository requested (--github new or --github existing).")
            self.record("repo", "skipped")
            return
        avail = repo_cmd.availability(self.api, cid)
        if avail.get("available") is False:
            reason = f"GitHub-backed projects are not available here: {avail.get('reason')}"
            if o.github in ("new", "existing"):
                raise CliError(reason, code="not_available")
            self.warn(reason)
            self.record("repo", "skipped", reason="unavailable")
            return
        choice = o.github or self._ask_repo_choice()
        if choice == "none":
            self.say("Skipped: no GitHub repository. Add one later: foundryos repo connect")
            self.record("repo", "skipped")
            return
        mode = "create" if choice == "new" else "connect"
        account = self._github_account(mode)
        first = repo_cmd.inspect_github(self.api, cid, account["id"], mode)
        self.say()
        for line in onboarding.inspect_lines(first):
            self.say(line)
        if (
            first.get("missing_permissions")
            and self.interactive
            and not self.confirm(
                "The token lacks permissions listed above. Continue anyway?", default=False
            )
        ):
            raise CliError(
                "Stopped. Fix the token (docs/github.md), then run `foundryos setup --only repo`."
            )
        if choice == "new":
            body, has_workflows = self._new_repo_body(account, first, wanted_owner, wanted_name)
        else:
            body, has_workflows = self._existing_repo_body(
                account, first, wanted_owner, wanted_name
            )
        body["policy"] = self._repo_policy(mode, has_workflows)
        if not body["policy"]:
            del body["policy"]
        project = o.project_name or (
            self.ask("Project name", default=body.get("name") or body["full_name"].split("/")[-1])
            if self.interactive
            else None
        )
        body["project_name"] = (
            project or body.get("name") or body["full_name"].split("/")[-1]
        ).strip()
        self.say()
        self.say(
            "Creating the repository on GitHub..."
            if choice == "new"
            else "Connecting the repository..."
        )
        created = repo_cmd.create_binding(self.api, cid, body)
        if created.get("status_reason"):
            self.say(f"Note: {created['status_reason']}")
        final = repo_cmd.wait_for_active(
            self.api,
            cid,
            created,
            say=self.say,
            dot=lambda: typer.echo(".", nl=False, err=True),
            sleep=lambda seconds: sleep(seconds),
        )
        info = self._repo_info(final)
        if final.get("status") != "active":
            self.warn(
                f"{final.get('full_name')} is still {final.get('status')}. It keeps going in the "
                "background; check it with: foundryos repo status"
            )
            self.record("repo", "pending", **info)
            return
        self.say(f"{final['full_name']} is ready: {onboarding.repo_line(final)}.")
        if final.get("onboarding_status") == "pending":
            self.say("Your AI team will first read the codebase and write down how it works.")
        self.record("repo", "done", **info)

    def _ask_repo_choice(self) -> str:
        if not self.interactive:
            return "none"
        self.say("Should your projects live in a GitHub repository?")
        return self._pick(
            "Pick one",
            [
                ("new", "New GitHub repository: Foundry creates it and keeps it up to date"),
                ("existing", "Existing repository: your AI team works in code you already have"),
                (
                    "none",
                    "No GitHub: projects stay on this machine (you can add a repository later)",
                ),
            ],
            "none",
        )

    def _github_account(self, mode: str) -> dict:
        """A working GitHub account: a connected one (reused), else a token from --secrets-stdin or a
        hidden prompt. The token is dropped from memory as soon as the server has it."""
        cid = self.ensure_company()
        accounts = repo_cmd.working_github_accounts(self.api, cid)
        token = self.secrets.pop("github_token", None)
        if token is None and accounts:
            account = accounts[0]
            login = (account.get("metadata") or {}).get("login", "?")
            if not self.interactive or self.confirm(
                f"Use the connected GitHub account {account['label']} ({login})?", default=True
            ):
                self.say(f"Using the connected GitHub account {account['label']} ({login}).")
                return account
        if self.interactive and token is None:
            self.say()
            for line in onboarding.token_instructions(mode):
                self.say(line)
        for _ in range(3 if self.interactive else 1):
            if token is None:
                token = self.secret("github_token", "GitHub token (hidden)")
            error = onboarding.github_token_error(token or "")
            if error:
                if not self.interactive:
                    raise usage(error + ".")
                self.warn(error)
                token = None
                continue
            try:
                return repo_cmd.connect_token(self.api, cid, token or "")
            except CliError as exc:
                if not self.interactive or exc.code != "invalid_token":
                    raise
                self.warn(onboarding.redact(exc.message, token))
                token = None
        raise CliError("No GitHub token worked. Run `foundryos setup --only repo` to try again.")

    def _check_repo(self, account: dict, mode: str, full_name: str, *, want_new: bool) -> dict:
        """Inspect one repository. ``problem`` is a message when it cannot be used, else None."""
        result = repo_cmd.inspect_github(
            self.api, self.ensure_company(), account["id"], mode, full_name=full_name
        )
        repo = result.get("repository") or {}
        lines = onboarding.inspect_lines(result)[1:]
        if want_new and not repo.get("exists"):  # "does not exist" is the good news here
            lines = [f"{full_name} is free."] + [
                x for x in lines if not x.startswith("Repository ")
            ]
        for line in lines:
            self.say(line)
        problem = None
        if want_new and repo.get("exists"):
            problem = f"{full_name} already exists. Choose another name, or connect it as an existing repository."
        elif not want_new and not repo.get("exists"):
            problem = f"{full_name} does not exist, or this token cannot see it."
        elif not want_new and not repo.get("accessible", True):
            problem = (
                f"This token cannot see {full_name}: add it under the token's repository access."
            )
        elif not want_new and repo.get("archived"):
            problem = (
                f"{full_name} is archived, so it is read-only. Unarchive it on github.com first."
            )
        return {"problem": problem, "result": result}

    def _new_repo_body(
        self, account: dict, first: dict, owner: str | None, name: str | None
    ) -> tuple[dict, bool]:
        o = self.opts
        login = first.get("login")
        owners = [c["login"] for c in first.get("owners") or []]
        for attempt in range(3):
            if name is None:
                default = onboarding.slugify_repo_name(self._company_label())
                name = default
                if self.interactive:
                    name = self.ask("Repository name", default=default).strip()
                    if not onboarding.valid_repo_name(name):
                        self.warn("Use letters, digits, '.', '_' and '-' (at most 100).")
                        name = None
                        continue
            if owner is None and self.interactive and len(owners) > 1:
                n = self.menu("Who should own it?", owners, default=1)
                owner = owners[n - 1]
            target = f"{owner or login}/{name}"
            checked = self._check_repo(account, "create", target, want_new=True)
            if not checked["problem"]:
                break
            if not self.interactive or attempt == 2:
                raise CliError(checked["problem"], code="repository_exists")
            self.warn(checked["problem"])
            name = None
        visibility = o.github_visibility or "private"
        if self.interactive and o.github_visibility is None:
            visibility = self._pick(
                "Who may see the repository?",
                [
                    ("private", "Private: only you and people you invite"),
                    ("public", "Public: anyone; everything your agents commit becomes public"),
                ],
                "private",
            )
        if (
            visibility == "public"
            and not o.github_public_ok
            and (
                not self.interactive
                or not self.confirm(
                    "Your agents' code will be PUBLIC on GitHub. Really make it public?",
                    default=False,
                )
            )
        ):
            raise usage("A public repository needs confirmation (--github-public-ok).")
        branch = o.github_default_branch
        if branch is None and self.interactive:
            branch = self.ask("Default branch", default="main").strip()
        body: dict[str, Any] = {
            "mode": "create",
            "connected_account_id": account["id"],
            "name": name,
            "visibility": visibility,
            "confirm_public": visibility == "public",
        }
        if owner:
            body["owner"] = owner
        if branch and branch != "main":
            body["default_branch"] = branch
        if self.idea:
            body["description"] = self.idea[:350]
        return body, False

    def _existing_repo_body(
        self, account: dict, first: dict, owner: str | None, name: str | None
    ) -> tuple[dict, bool]:
        o = self.opts
        full_name = f"{owner}/{name}" if owner and name else None
        result: dict = {}
        for attempt in range(3):
            if full_name is None:
                text = self.ask("Repository (OWNER/NAME or a github.com URL)").strip()
                ref = onboarding.parse_repo_ref(text)
                if ref is None:
                    self.warn("Expected OWNER/NAME or https://github.com/OWNER/NAME.")
                    continue
                full_name = f"{ref[0]}/{ref[1]}"
            checked = self._check_repo(account, "connect", full_name, want_new=False)
            result = checked["result"]
            if not checked["problem"]:
                break
            if not self.interactive or attempt == 2:
                raise CliError(checked["problem"], code="repository_not_accessible")
            self.warn(checked["problem"])
            full_name = None
        repo = result.get("repository") or {}
        if (
            repo.get("can_push") is False
            and self.interactive
            and not self.confirm(
                "This token cannot push to the repository, so agents could not deliver work. "
                "Continue anyway?",
                default=False,
            )
        ):
            raise CliError("Stopped. Give the token Contents: Read and write (docs/github.md).")
        branch = o.github_default_branch
        if branch is None and self.interactive and not repo.get("empty"):
            asked = self.ask(
                "Base branch for pull requests", default=repo.get("default_branch") or "main"
            )
            branch = asked.strip()
        body: dict[str, Any] = {
            "mode": "connect",
            "connected_account_id": account["id"],
            "full_name": full_name,
        }
        if branch and branch != repo.get("default_branch"):
            body["default_branch"] = branch
        return body, bool(repo.get("has_workflows"))

    def _repo_policy(self, mode: str, has_workflows: bool) -> dict[str, str]:
        """Only what the person chose differently from the server's defaults for this mode."""
        o = self.opts
        defaults = default_policy(mode, has_workflows=has_workflows)  # type: ignore[arg-type]
        merge = GITHUB_MERGE_FLAGS.get(o.github_merge or "")
        ci = GITHUB_CI_FLAGS.get(o.github_ci or "")
        issues = o.github_issues
        if self.interactive:
            if merge is None:
                merge = self._pick(
                    "How should finished work reach GitHub?",
                    [
                        (
                            "pr_auto_merge",
                            "Pull requests that the agents review and merge themselves once CI is green",
                        ),
                        ("pr_human_merge", "Pull requests that you merge"),
                        (
                            "direct_push",
                            "Straight to the default branch (only if it is not protected)",
                        ),
                    ],
                    defaults.merge_mode,
                )
            if ci is None:
                ci = self._pick(
                    "What must CI say before a merge?",
                    [
                        ("if_present", "Green if the repository has CI; no CI is fine"),
                        ("required", "At least one check, all green"),
                        ("ignore", "Ignore CI (not recommended)"),
                    ],
                    defaults.ci_mode,
                )
            if issues is None:
                issues = self._pick(
                    "Which GitHub issues should your team pick up?",
                    [
                        ("collaborators", "Issues opened by owners, members and collaborators"),
                        ("labeled", "Only issues labelled 'foundry'"),
                        ("everyone", "Every issue (anyone can create work for your agents)"),
                        ("off", "None"),
                    ],
                    defaults.issue_intake,
                )
        chosen = {"merge_mode": merge, "ci_mode": ci, "issue_intake": issues}
        return {
            key: value
            for key, value in chosen.items()
            if value is not None and value != getattr(defaults, key)
        }

    # ------------------------------------------------------------------ step: public URL

    def public_choice(self) -> str:
        """quick | named | tailscale | off. Asked once; non-interactive runs need the flag."""
        if self._public_choice is None:
            if self.opts.public_url is not None:
                self._public_choice = self.opts.public_url
            elif self.interactive:
                self._public_choice = (
                    "quick"
                    if self.confirm(
                        "Get a free public URL (a Cloudflare quick tunnel) so you can reach Foundry from your "
                        "phone or anywhere?",
                        default=True,
                    )
                    else "off"
                )
            else:
                self._public_choice = "off"
        return self._public_choice

    def _confirm_plan(self, plan: dict) -> bool:
        self.say("This is what will be set up:")
        for key, value in plan.items():
            self.say(f"  {key}: {value}")
        if not self.interactive:
            self.say("Going ahead because --public-url was given.")
            return True
        return self.confirm("Download it and open the tunnel?", default=True)

    def step_public_url(self) -> None:
        self.header("Public URL")
        choice = self.public_choice()
        forced = self.opts.only == "public-url" or self.opts.public_url is not None
        try:
            reach = self.api.get("/reachability") or {}
        except CliError:
            reach = {}
        existing = (reach.get("tunnel") or {}).get("url")
        if existing and not forced:
            self.say(f"Already done: {existing}")
            self.public_url = existing
            self.record("public-url", "already_done", url=existing)
            return
        if choice == "off":
            self.say("Skipped. Later: foundryos setup --only public-url --public-url quick")
            self.record("public-url", "skipped")
            return
        try:
            from app.cli.tunnel_flow import enable_public_url
        except ImportError:
            self.warn(
                "Public URLs are not available in this build of the CLI. Use the web app: Reachability."
            )
            self.record("public-url", "skipped", reason="unavailable")
            return
        url = enable_public_url(
            self.st,
            provider=choice,
            hostname=self.opts.hostname,
            confirm=self._confirm_plan,
            say=self.say,
        )
        if not url:
            self.warn("No public URL yet. Check `foundryos reach` in a minute.")
            self.record("public-url", "failed", provider=choice)
            return
        self.public_url = url
        self.say(f"Public URL: {url}")
        if choice == "quick":
            self.say(
                "It changes whenever the tunnel restarts; `foundryos reach --watch` and `foundryos status` show the current one."
            )
        if self.two_factor is not True:
            self.warn(
                "Anyone who finds this URL can try to sign in. Turn on two-factor sign-in: foundryos setup --only 2fa"
            )
        self.record("public-url", "done", url=url, provider=choice)

    # ------------------------------------------------------------------ summary

    def finish(self) -> None:
        base = self.st.api_base()
        port = onboarding.url_port(base)
        hints: list[str] = []
        headless = False
        try:
            from app.ops import headless as headless_mod

            headless = bool(headless_mod.is_headless())
            if headless and not self.remote:
                hints = list(headless_mod.remote_hints(port))
        except Exception:
            pass
        ai = self.summary.get("ai") or {}
        work = self.summary.get("work") or {}
        self.say()
        self.say(
            "Foundry AI OS is set up." if self.opts.only is None else f"Done: {self.opts.only}."
        )
        self.say(f"  Local URL   {base}")
        for line in hints:
            self.say(f"  {line}")
        if self.public_url:
            self.say(f"  Public URL  {self.public_url}")
        if self.username:
            self.say(f"  Sign in as  {self.username} (with the password you chose)")
        if self.company_name:
            self.say(f"  Company     {self.company_name}")
        if ai.get("provider"):
            self.say(f"  Model API   {ai['provider']} ({ai.get('model')})")
        repo_info = self.summary.get("repo") or {}
        if repo_info.get("full_name"):
            self.say(
                f"  Repository  {repo_info['full_name']} ({repo_info.get('merge_mode') or '?'}, "
                f"{repo_info.get('repo_status')})"
            )
        if work.get("mode"):
            self.say(
                f"  Work mode   {work['mode']}"
                + (
                    f" ({work.get('runtime')})"
                    if work.get("runtime") not in (None, "company_agent")
                    else ""
                )
            )
        self.say("Next:")
        self.say("  foundryos status             the company at a glance")
        self.say('  foundryos note "..."         tell your AI Co-founder something')
        self.say("  foundryos runs tail -f       watch the work happen")
        self.say("  foundryos doctor             check this machine")
        if self.st.json:
            render_mod.print_json(
                {
                    **self.summary,
                    "ok": True,
                    "api_url": base,
                    "local_url": base,
                    "remote_access": hints,
                    "headless": headless,
                    "username": self.username,
                    "company": {"id": self.company_id, "name": self.company_name},
                    "public_url": self.public_url,
                    "token_file": self.token_file,
                    "two_factor": self.two_factor,
                    "steps": self.steps,
                }
            )


_SETUP_TOKEN_CODES = ("setup_token_required", "invalid_setup_token")


# ---------------------------------------------------------------------- entry points other code calls


def run_wizard(st: CliState, opts: WizardOptions) -> None:
    """Run the whole wizard. Returns normally when it completed; raises ``CliError`` otherwise."""
    wizard = Wizard(st, opts)
    try:
        wizard.run()
    except click.Abort:
        raise CliError(
            "Setup was interrupted. Run `foundryos setup` again: finished steps are skipped.",
            code="interrupted",
        ) from None
    except CliError as exc:
        if exc.code != "usage":
            typer.echo(
                "Fix that, then run `foundryos setup` again: finished steps are skipped.", err=True
            )
        raise
    finally:
        wizard.close_session()


def offer_wizard(st: CliState) -> bool:
    """Run the wizard (the caller already asked the person); True if setup completed."""
    try:
        run_wizard(st, WizardOptions())
    except CliError as exc:
        typer.echo(f"Error: {exc.message}", err=True)
        return False
    except (typer.Exit, click.Abort):
        return False
    return True


# ---------------------------------------------------------------------- the command


def register(app: typer.Typer) -> None:
    @command(app, "setup")
    def setup_cmd(
        ctx: typer.Context,
        only: Annotated[
            str | None,
            typer.Option("--only", help="Run one step again: " + ", ".join(STEPS) + "."),
        ] = None,
        non_interactive: Annotated[
            bool,
            typer.Option(
                "--non-interactive", help="Never prompt; every needed value must be given."
            ),
        ] = False,
        json_out: JsonOpt = False,
        username: Annotated[
            str | None, typer.Option("--username", help="Owner account username.")
        ] = None,
        display_name: Annotated[
            str | None, typer.Option("--display-name", help="Default: the username.")
        ] = None,
        email: Annotated[str | None, typer.Option("--email", help="Optional.")] = None,
        company_name: Annotated[
            str | None, typer.Option("--company-name", help="Name of the company to create.")
        ] = None,
        idea: Annotated[
            str | None,
            typer.Option(
                "--idea", help="One line (at most 280 characters): what the company will do."
            ),
        ] = None,
        timezone: Annotated[
            str | None, typer.Option("--timezone", help="IANA name. Default: this machine's.")
        ] = None,
        provider: Annotated[
            str | None,
            typer.Option(
                "--provider", help="openrouter, anthropic, openai, openai-compatible or ollama."
            ),
        ] = None,
        model: Annotated[
            str | None, typer.Option("--model", help="The model to run the company on.")
        ] = None,
        base_url: Annotated[
            str | None, typer.Option("--base-url", help="openai-compatible / ollama address.")
        ] = None,
        allow_private_network: Annotated[
            bool,
            typer.Option(
                "--allow-private-network",
                help="The model server is on a private network (192.168.x.x, 10.x.x.x, Tailscale).",
            ),
        ] = False,
        work_mode: Annotated[
            str | None, typer.Option("--work-mode", help="api, or hybrid (API + a coding CLI).")
        ] = None,
        cli: Annotated[
            str | None,
            typer.Option("--cli", help="claude_code, codex, opencode, antigravity or cursor."),
        ] = None,
        cli_auth: Annotated[
            str | None,
            typer.Option(
                "--cli-auth",
                help="own-login, api-key or provider (OpenCode on your model provider).",
            ),
        ] = None,
        install_cli: Annotated[
            bool, typer.Option("--install-cli", help="Run the CLI's npm install if it is missing.")
        ] = False,
        service: Annotated[
            bool | None,
            typer.Option(
                "--service/--no-service",
                help="Keep Foundry running in the background and after reboot.",
            ),
        ] = None,
        linger: Annotated[
            bool | None,
            typer.Option(
                "--linger/--no-linger",
                help="Linux: keep the service running when you log out (default on).",
            ),
        ] = None,
        two_fa: Annotated[
            bool | None,
            typer.Option("--2fa/--no-2fa", help="Two-factor sign-in (needs a terminal)."),
        ] = None,
        public_url: Annotated[
            str | None,
            typer.Option(
                "--public-url", help="quick (free, changes on restart), named, tailscale or off."
            ),
        ] = None,
        hostname: Annotated[
            str | None, typer.Option("--hostname", help="For --public-url named / tailscale.")
        ] = None,
        github: Annotated[
            str | None,
            typer.Option(
                "--github",
                help="Back your projects with GitHub: new (create a repository), existing, or none. "
                "With --non-interactive the default is none.",
            ),
        ] = None,
        github_repo: Annotated[
            str | None,
            typer.Option(
                "--github-repo",
                help="OWNER/NAME or a github.com URL (new: NAME alone is fine; the owner is then "
                "the token's account).",
            ),
        ] = None,
        github_visibility: Annotated[
            str | None,
            typer.Option(
                "--github-visibility", help="New repository: private (default) or public."
            ),
        ] = None,
        github_public_ok: Annotated[
            bool,
            typer.Option(
                "--github-public-ok",
                help="Confirm that a public repository is intended (needed with --non-interactive).",
            ),
        ] = False,
        github_default_branch: Annotated[
            str | None,
            typer.Option(
                "--github-default-branch", help="The branch pull requests target (new: main)."
            ),
        ] = None,
        github_merge: Annotated[
            str | None,
            typer.Option(
                "--github-merge",
                help="pr-human (you merge), pr-auto (agents merge after review and CI) or direct. "
                "Default: pr-auto for a new repository, pr-human for an existing one.",
            ),
        ] = None,
        github_ci: Annotated[
            str | None,
            typer.Option("--github-ci", help="if-present, required or ignore."),
        ] = None,
        github_issues: Annotated[
            str | None,
            typer.Option("--github-issues", help="off, collaborators, labeled or everyone."),
        ] = None,
        project_name: Annotated[
            str | None,
            typer.Option("--project-name", help="Name of the project bound to the repository."),
        ] = None,
        secrets_stdin: Annotated[
            bool,
            typer.Option(
                "--secrets-stdin",
                help="Read key=value lines from stdin: password, api_key, cli_api_key, setup_token, "
                "github_token. "
                "Secrets are never accepted as options.",
            ),
        ] = False,
    ) -> None:
        """Set up Foundry AI OS in this terminal: account, company, model API, work mode, GitHub repository, background service, public URL.

        Safe to run again: finished steps are skipped. A coding-CLI test sends one tiny prompt and is
        capped at $0.05; --work-mode hybrid counts as your consent to run it.
        """
        st = state(ctx, json_out)
        opts = WizardOptions(
            only=only,
            non_interactive=non_interactive,
            username=username,
            display_name=display_name,
            email=email,
            company_name=company_name,
            idea=idea,
            timezone=timezone,
            provider=provider,
            model=model,
            base_url=base_url,
            allow_private_network=allow_private_network,
            work_mode=work_mode,
            cli=cli,
            cli_auth=cli_auth,
            install_cli=install_cli,
            service=service,
            linger=linger,
            two_fa=two_fa,
            public_url=public_url,
            hostname=hostname,
            github=github,
            github_repo=github_repo,
            github_visibility=github_visibility,
            github_public_ok=github_public_ok,
            github_default_branch=github_default_branch,
            github_merge=github_merge,
            github_ci=github_ci,
            github_issues=github_issues,
            project_name=project_name,
            secrets_stdin=secrets_stdin,
        )
        onboarding.validate_options(opts)
        if secrets_stdin:
            opts.secrets = onboarding.parse_secrets(sys.stdin.read())
        run_wizard(st, opts)


__all__ = ["Wizard", "offer_wizard", "register", "run_wizard"]
