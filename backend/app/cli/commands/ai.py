"""``foundryos ai list | add | remove | test | login | logout``: the company's AI stack (M17).

``login`` runs the CLI's own sign-in itself, with the connection's private home and the terminal's stdio, so
the browser flow, the printed link and the paste-the-code fallback (SSH, ``docker exec -it``) all happen
inside the CLI. Foundry never sees a password, code or token, and never reads a CLI's credential files: the
signed-in state comes from the CLI's own ``status`` command only. ``login`` acts on the machine it runs on
(the one that runs Foundry); everything else goes through the API."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from app.cli import render
from app.cli.client import CliError
from app.cli.context import JsonOpt, command, state

ai_app = typer.Typer(
    help="The AI your company runs on: add, test, sign in, reorder.", no_args_is_help=True
)


def local_env():
    """(CliEnvironment, data dir) of the Foundry install on this machine. Replaced in tests."""
    from app.core.config import Settings
    from app.runtimes.cli.env import CliEnvironment

    settings = Settings()
    env = CliEnvironment(
        extra_dirs=[p for p in map(_path, getattr(settings, "cli_search_paths", []))],
        data_dir=settings.resolved_data_dir,
    )
    return env, settings.resolved_data_dir


def _path(value):
    from pathlib import Path

    return Path(value)


def sign_in(conn: dict[str, Any], *, console: bool = False) -> int:
    """The connection's CLI login in THIS terminal, with its private home. Returns the exit code."""
    from app.cli.commands import onboard
    from app.services import plan_login

    cli_id, rel = conn.get("cli_id"), conn.get("home_dir")
    if not plan_login.supported(cli_id) or not rel:
        raise CliError(
            f"{conn.get('label')} is not a plan connection: there is nothing to sign in."
        )
    env, data_dir = local_env()
    home = plan_login.make_home(data_dir, rel)
    argv = plan_login.login_argv(env, cli_id, "console" if console else "claudeai")
    return onboard.run_interactive(argv, env=plan_login.child_env(env, cli_id, home))


def resolve(items: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    """An id, an id prefix or a label (case-insensitive) -> one connection."""
    low = ref.lower()
    found = [i for i in items if i["id"] == ref]
    found = found or [i for i in items if i["id"].startswith(ref)]
    found = found or [i for i in items if i["label"].lower() == low]
    if len(found) != 1:
        names = ", ".join(i["label"] for i in items) or "none yet"
        raise CliError(
            f"No single AI connection matches {ref!r}. Yours: {names}.",
            exit_code=5,
            code="not_found",
        )
    return found[0]


def _stack(st) -> dict[str, Any]:
    return st.client().get(st.cpath("/ai-stack"))


def _status_text(item: dict[str, Any]) -> str:
    status = item["status"]
    text = status["state"].replace("_", " ")
    return f"{text}: {status['message']}" if status.get("message") else text


def register(app: typer.Typer) -> None:
    app.add_typer(ai_app, name="ai")

    @command(ai_app, "list")
    def ai_list(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Your AI connections, in order, with their state and share."""
        st = state(ctx, json_out)
        stack = _stack(st)
        if st.json:
            render.print_json(stack)
            return
        render.render_table(
            stack["items"],
            [
                ("#", "rank_label"),
                ("Label", "label"),
                ("Model", "model"),
                ("Use for", "use_for"),
                (
                    "Importance",
                    lambda r: {4: "Most", 3: "More", 2: "Normal", 1: "Less", 0: "Backup only"}[
                        r["share"]
                    ],
                ),
                ("State", _status_text),
                ("Runs 24h", "runs_24h"),
            ],
            empty="No AI yet. Add one with: foundryos ai add",
        )
        for klass, title in (("heavy", "Heavy lifting"), ("everyday", "Everyday work")):
            parts = [
                f"{e['label']} {e['percent']}%"
                for e in stack["summary"][klass]
                if not e.get("backup")
            ]
            backups = [e["label"] for e in stack["summary"][klass] if e.get("backup")]
            line = ", ".join(parts) or "nothing ready"
            typer.echo(f"{title}: {line}" + (f" (backup: {', '.join(backups)})" if backups else ""))
        if not stack["ready"]:
            typer.echo(f"Not ready yet: {stack['blocking_reason']}")

    @command(ai_app, "add")
    def ai_add(
        ctx: typer.Context,
        provider: Annotated[
            str | None,
            typer.Argument(
                help="claude-plan, cursor-plan, openrouter, anthropic, openai, openai-compatible or ollama "
                "(Cursor / Codex / OpenCode / Antigravity API keys are added through the menu)."
            ),
        ] = None,
        model: Annotated[str | None, typer.Option("--model", help="The model to use.")] = None,
        base_url: Annotated[
            str | None, typer.Option("--base-url", help="openai-compatible / ollama address.")
        ] = None,
        allow_private_network: Annotated[bool, typer.Option("--allow-private-network")] = False,
        non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Connect another AI, test it, and add it to the stack."""
        from app.cli.commands.onboard import Wizard
        from app.cli.onboarding import WizardOptions, validate_options

        st = state(ctx, json_out)
        opts = WizardOptions(
            provider=provider,
            model=model,
            base_url=base_url,
            allow_private_network=allow_private_network,
            non_interactive=non_interactive,
            install_cli=False,
        )
        validate_options(opts)
        wizard = Wizard(st, opts)
        stack = _stack(st)
        conn = wizard.add_connection(stack, first=not stack["items"])
        if st.json:
            render.print_json(conn)
        else:
            typer.echo(f"Added {conn['label']}. See: foundryos ai list")

    @command(ai_app, "remove")
    def ai_remove(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="Id or label.")],
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Remove a connection (a plan connection is signed out and its private home deleted)."""
        st = state(ctx, json_out)
        conn = resolve(_stack(st)["items"], ref)
        if not yes and not typer.confirm(f"Remove {conn['label']}?", default=False, err=st.json):
            raise typer.Exit(1)
        st.client().delete(st.cpath(f"/ai-stack/{conn['id']}"))
        typer.echo(f"Removed {conn['label']}.", err=st.json)

    @command(ai_app, "test")
    def ai_test(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="Id or label.")],
        json_out: JsonOpt = False,
    ) -> None:
        """Test a connection with its own sign-in (a CLI gets one tiny prompt, at most $0.05)."""
        st = state(ctx, json_out)
        conn = resolve(_stack(st)["items"], ref)
        result = st.client().post(st.cpath(f"/ai-stack/{conn['id']}/test"), timeout=180.0)
        if st.json:
            render.print_json(result)
        else:
            last = result["status"].get("last_test") or {}
            typer.echo(
                f"{result['label']}: {'ok' if last.get('ok') else 'failed'} {last.get('detail') or ''}".rstrip()
            )
        if (result["status"].get("last_test") or {}).get("ok") is not True:
            raise typer.Exit(1)

    @command(ai_app, "login")
    def ai_login(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="Id or label of a plan connection.")],
        console: Annotated[
            bool,
            typer.Option(
                "--console", help="Claude: use an Anthropic Console account instead of a plan."
            ),
        ] = False,
    ) -> None:
        """Sign a plan connection in (opens the browser; over SSH paste the code the CLI asks for)."""
        st = state(ctx, False)
        conn = resolve(_stack(st)["items"], ref)
        if conn["auth_mode"] != "plan":
            raise CliError(f"{conn['label']} signs in with a key, not a plan.")
        code = sign_in(conn, console=console)
        if code != 0:
            raise CliError(f"The sign-in ended with exit code {code}.")
        typer.echo("Done. Check it with: foundryos ai test " + conn["label"])

    @command(ai_app, "logout")
    def ai_logout(
        ctx: typer.Context,
        ref: Annotated[str, typer.Argument(help="Id or label of a plan connection.")],
    ) -> None:
        """Sign a plan connection out (its private home only)."""
        st = state(ctx, False)
        conn = resolve(_stack(st)["items"], ref)
        if conn["auth_mode"] != "plan":
            raise CliError(f"{conn['label']} signs in with a key, not a plan.")
        st.client().delete(st.cpath(f"/ai-stack/{conn['id']}/login"))
        typer.echo("Signed out.")
