"""providers and credentials."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli import render
from app.cli.client import CliError
from app.cli.commands.auth import read_secret_line
from app.cli.context import JsonOpt, command, state
from app.cli.render import short
from app.cli.resolve import resolve

providers_app = typer.Typer(help="Model providers (Ollama, OpenRouter, ...).", no_args_is_help=True)
credentials_app = typer.Typer(
    help="Stored secrets (write-only: values are never shown).", no_args_is_help=True
)


def register(app: typer.Typer) -> None:
    app.add_typer(providers_app, name="providers")
    app.add_typer(credentials_app, name="credentials")

    # ------------------------------------------------------------------ providers

    @command(providers_app, "ls")
    def providers_ls(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Configured model providers and their last test."""
        st = state(ctx, json_out)
        items = st.client().paged(st.cpath("/model-providers"))
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda p: p["id"][:8]),
                ("name", "name"),
                ("kind", "kind"),
                ("model", "default_model"),
                ("local", "is_local"),
                ("default", "is_company_default"),
                ("enabled", "enabled"),
                ("last test", lambda p: (p.get("last_test") or {}).get("status")),
            ],
            empty="No model provider yet. Try: foundryos providers detect --register",
        )

    @command(providers_app, "detect")
    def providers_detect(
        ctx: typer.Context,
        base_url: Annotated[
            str | None, typer.Option(help="Ollama base URL (default http://127.0.0.1:11434)")
        ] = None,
        register: Annotated[
            bool, typer.Option("--register", help="Register the recommended model as a provider.")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Look for a local Ollama and its models (and with --register, add it)."""
        st = state(ctx, json_out)
        body: dict = {"register": register}
        if base_url:
            body["base_url"] = base_url
        result = st.client().post(st.cpath("/model-providers/detect"), body)
        if st.json:
            render.print_json(result)
            return
        if not result.get("reachable"):
            render.say(f"No Ollama answered at {result.get('base_url')}.", style="yellow")
            for w in result.get("warnings", []):
                render.say(f"  {w}", style="dim")
            return
        render.render_table(
            result.get("models", []),
            [
                ("model", "name"),
                ("size", "parameter_size"),
                ("quant", "quantization"),
                ("tools", "tool_calling"),
                ("recommended", "recommended"),
            ],
            title=f"Ollama at {result['base_url']} (served context: {result.get('served_context_length') or 'unknown'})",
            empty="Ollama has no models. Pull one, e.g. `ollama pull qwen2.5:7b`.",
        )
        for w in result.get("warnings", []):
            render.say(f"  ! {w}", style="yellow")
        reg = result.get("registered")
        if reg:
            render.say(f"Registered provider {reg['name']} ({reg['default_model']}).")

    @command(providers_app, "test")
    def providers_test(
        ctx: typer.Context,
        provider: Annotated[str, typer.Argument(help="Provider name or id")],
        json_out: JsonOpt = False,
    ) -> None:
        """Test a provider end to end (model present, tool calling, served context)."""
        st = state(ctx, json_out)
        found = resolve(st, "model-providers", provider, label="provider")
        result = st.client().post(st.cpath(f"/model-providers/{found['id']}/test"))
        test = result.get("last_test") or {}
        if st.json:
            render.print_json(result)
        else:
            render.say(
                f"{result['name']}: {test.get('status')} - {short(test.get('message'), 120)}",
                style="green" if test.get("status") == "ok" else "yellow",
            )
            for w in test.get("warnings", []):
                render.say(f"  ! {w}", style="yellow")
        if test.get("status") == "error":
            raise typer.Exit(1)

    # ------------------------------------------------------------------ credentials

    @command(credentials_app, "ls")
    def credentials_ls(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Credential names and kinds (never values)."""
        st = state(ctx, json_out)
        items = st.client().paged(st.cpath("/credentials"))
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda c: c["id"][:8]),
                ("name", "name"),
                ("kind", "kind"),
                ("provider", "provider"),
                ("created", "created_at"),
            ],
            empty="No credentials stored.",
        )

    @command(credentials_app, "set")
    def credentials_set(
        ctx: typer.Context,
        name: Annotated[str, typer.Argument(help="Credential name, e.g. OPENROUTER_API_KEY")],
        kind: Annotated[str, typer.Option(help="api_key, token or other")] = "api_key",
        stdin: Annotated[
            bool, typer.Option("--stdin", help="Read the value from standard input.")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Store (or replace) a secret. The value comes from stdin or a hidden prompt, never an argument."""
        st = state(ctx, json_out)
        value = read_secret_line(f"Value for {name}", from_stdin=stdin)
        client = st.client()
        try:
            result = client.post(
                st.cpath("/credentials"), {"name": name, "kind": kind, "value": value}
            )
            verb = "stored"
        except CliError as exc:
            if exc.exit_code != 6:
                raise
            existing = next(
                (c for c in client.paged(st.cpath("/credentials")) if c["name"] == name), None
            )
            if existing is None:
                raise
            result = client.post(
                st.cpath(f"/credentials/{existing['id']}/rotate"), {"value": value}
            )
            verb = "replaced"
        if st.json:
            render.print_json({k: v for k, v in result.items() if k != "value"})
        else:
            render.say(f"Credential {name} {verb}.")
