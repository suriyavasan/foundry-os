"""login, logout, whoami, and `auth reset-password` (server machine only)."""

from __future__ import annotations

import contextlib
import socket
import sys
from pathlib import Path
from typing import Annotated

import typer

from app.cli import config, render
from app.cli.client import ApiClient, CliError
from app.cli.context import CliState, JsonOpt, command, state

auth_app = typer.Typer(help="Account tasks that need the server machine.", no_args_is_help=True)


def read_secret_line(prompt: str, *, from_stdin: bool, confirm: bool = False) -> str:
    """A secret comes from stdin (scripts) or a hidden prompt. Never from an option."""
    if from_stdin:
        value = sys.stdin.readline().rstrip("\r\n")
        if not value:
            raise CliError("Expected the secret on standard input, got nothing.")
        return value
    value = typer.prompt(prompt, hide_input=True)
    if confirm:
        again = typer.prompt("Repeat", hide_input=True)
        if again != value:
            raise CliError("The two entries differ.")
    return value


def csrf_headers(client: ApiClient) -> dict[str, str]:
    """The double-submit CSRF header for a cookie session held by ``client``."""
    return {"X-CSRF-Token": client.http.cookies.get("cos_csrf") or ""}


def end_session(client: ApiClient) -> None:
    """Sign the browser-style session out (idempotent; a failure here is never worth stopping for)."""
    with contextlib.suppress(CliError):
        client.request("POST", "/auth/logout", headers=csrf_headers(client))


def mint_cli_token(
    st: CliState,
    session: ApiClient,
    *,
    scope: str = "admin",
    name_prefix: str = "companyos-cli",
    keep_session: bool = False,
) -> tuple[dict, Path]:
    """With a signed-in session (``session`` carries its cookies), create a personal API token named
    ``<name_prefix>@<host>``, save it for this machine (mode 0600) and remember the server URL.

    The session is signed out afterwards unless ``keep_session`` (``foundryos setup`` still needs it for
    two-factor enrolment, which a personal token may not do). Returns the API's token record (its ``token``
    is the secret: never print it) and the file it was saved to."""
    host = socket.gethostname().split(".")[0] or "cli"
    created = session.request(
        "POST",
        "/auth/tokens",
        json_body={"name": f"{name_prefix}@{host}", "scope": scope},
        headers=csrf_headers(session),
    )
    if not keep_session:
        end_session(session)  # the browser-style session is not needed any more
    path = config.write_token(created["token"])
    updates: dict[str, str | None] = {"token_id": created.get("id")}
    if st.api_url:
        updates["api_url"] = st.api_url.rstrip("/")
    config.save_config(**updates)
    st._client = None  # new credential
    return created, path


def register(app: typer.Typer) -> None:
    app.add_typer(auth_app, name="auth")

    @command(app, "login")
    def login(
        ctx: typer.Context,
        username: Annotated[str | None, typer.Option("--username", "-u")] = None,
        password_stdin: Annotated[
            bool,
            typer.Option(
                "--password-stdin", help="Read the password (and a 2FA code, line 2) from stdin."
            ),
        ] = False,
        scope: Annotated[str, typer.Option(help="Token scope: read, write or admin.")] = "admin",
        company_ref: Annotated[
            str | None, typer.Option("--set-company", help="Remember this company (id or name).")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Sign in with username and password and store a personal API token (the password is not kept)."""
        st = state(ctx, json_out)
        if scope not in ("read", "write", "admin"):
            raise CliError("--scope must be read, write or admin", code="usage")
        name = username or typer.prompt("Username")
        password = read_secret_line("Password", from_stdin=password_stdin)
        anon = st.anonymous_client()
        body = {"username": name, "password": password}
        result = anon.request("POST", "/auth/login", json_body=body)
        if isinstance(result, dict) and result.get("two_factor_required"):
            code = (
                sys.stdin.readline().strip()
                if password_stdin
                else typer.prompt("Authenticator or recovery code")
            )
            if not code:
                raise CliError("This account uses two-factor sign-in: a code is required.")
            result = anon.request("POST", "/auth/login", json_body={**body, "totp_code": code})
        if not isinstance(result, dict) or result.get("state") != "authenticated":
            raise CliError("Sign-in did not complete.", exit_code=3, code="unauthenticated")
        created, path = mint_cli_token(st, anon, scope=scope)
        token_id = created.get("id")
        if company_ref:
            st.company = company_ref
            config.save_config(company_id=st.company_id())
        user = result.get("user") or {}
        if st.json:
            render.print_json(
                {
                    "user": user,
                    "scope": scope,
                    "token_file": str(path),
                    "token_id": token_id,
                }
            )
        else:
            render.say(
                f"Signed in as {user.get('username') or name}. Token saved to {path} (scope {scope})."
            )

    @command(app, "logout")
    def logout(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Forget the stored token on this machine."""
        st = state(ctx, json_out)
        token_id = config.load_config().get("token_id")
        removed = config.delete_token()
        config.save_config(token_id=None)
        if st.json:
            render.print_json({"removed": removed, "token_id": token_id})
            return
        if removed:
            render.say("Stored token removed.")
            render.say(
                "It stays valid on the server until you revoke it (web app: Account > API tokens);"
                " a token cannot revoke itself.",
                style="dim",
            )
        else:
            render.say("No stored token.")

    @command(app, "whoami")
    def whoami(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Who the server thinks you are."""
        st = state(ctx, json_out)
        cred = config.resolve_credential()
        me = st.client().get("/me")
        data = {**me, "api_url": st.api_base(), "credential": cred.source if cred else None}
        if st.json:
            render.print_json(data)
            return
        user = me["user"]
        render.render_kv(
            {
                "user": f"{user.get('display_name')} ({user.get('username') or user.get('email') or '-'})",
                "server": st.api_base(),
                "credential": data["credential"],
                "companies": [f"{m['company_name']} [{m['role']}]" for m in me["memberships"]],
            },
            [
                ("user", "user"),
                ("server", "server"),
                ("credential", "credential"),
                ("companies", "companies"),
            ],
        )

    @command(auth_app, "reset-password")
    def reset_password(
        ctx: typer.Context,
        username: Annotated[str, typer.Argument(help="The account to reset.")],
        password_stdin: Annotated[bool, typer.Option("--password-stdin")] = False,
        revoke_tokens: Annotated[
            bool, typer.Option("--revoke-tokens", help="Also revoke the account's API tokens.")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Set a new password from the server console. Needs read access to the data dir's owner.token."""
        st = state(ctx, json_out)
        from app.ops.local import open_local_session, require_owner_token_access

        settings = require_owner_token_access()
        password = read_secret_line("New password", from_stdin=password_stdin, confirm=True)
        from sqlalchemy import select

        from app.core.crypto import CredentialCipher
        from app.core.errors import AppError
        from app.core.keys import SecretKeyProvider
        from app.core.owner_token import OwnerTokenProvider
        from app.models.user import User
        from app.services.auth import AuthService, normalize_username

        with open_local_session(settings) as db:
            user = db.scalar(select(User).where(User.username == normalize_username(username)))
            if user is None:
                raise CliError(f"No account named {username!r}.", exit_code=5, code="not_found")
            keys = SecretKeyProvider(settings)
            svc = AuthService(settings, keys, CredentialCipher(keys), OwnerTokenProvider(settings))
            try:
                revoked = svc.reset_password_local(db, user, password, revoke_tokens=revoke_tokens)
            except AppError as exc:
                raise CliError(exc.message, code=exc.code, details=exc.details) from exc
        if st.json:
            render.print_json(
                {
                    "username": user.username,
                    "sessions_revoked": revoked,
                    "tokens_revoked": revoke_tokens,
                }
            )
        else:
            render.say(f"Password for {user.username} changed; {revoked} session(s) signed out.")

    @command(auth_app, "setup-token")
    def setup_token(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Print the first-run setup token (only while the install is unclaimed; server machine only)."""
        st = state(ctx, json_out)
        from app.core.crypto import CredentialCipher
        from app.core.keys import SecretKeyProvider
        from app.core.owner_token import OwnerTokenProvider
        from app.ops.local import open_local_session, require_owner_token_access
        from app.services.auth import AuthService

        settings = require_owner_token_access()
        with open_local_session(settings) as db:
            keys = SecretKeyProvider(settings)
            owner = OwnerTokenProvider(settings)
            pending = AuthService(settings, keys, CredentialCipher(keys), owner).setup_pending(db)
        if not pending:
            if st.json:
                render.print_json({"setup_pending": False})
            else:
                render.say("This install is already set up; there is no setup token to show.")
            return
        if st.json:
            render.print_json({"setup_pending": True, "setup_token": owner.ensure()})
        else:
            render.say("Setup token (needed to create the first account from another device):")
            render.say(f"  {owner.ensure()}")
