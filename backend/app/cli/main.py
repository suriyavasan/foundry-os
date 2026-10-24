"""The ``foundryos`` command (``companyos`` is kept as an alias)."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.commands import auth, company, lifecycle, org, setup, system, work
from app.cli.context import CliState, guarded

app = typer.Typer(
    name="foundryos",
    help=(
        "Foundry AI OS: run and operate your autonomous company of AI agents. "
        "Run it with no arguments to start it in the background and open it in your browser."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
    rich_markup_mode=None,
)


@app.callback()
def root(
    ctx: typer.Context,
    json_out: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
    api_url: Annotated[
        str | None,
        typer.Option(
            "--api-url",
            help="Server URL (default: saved, COMPANYOS_API_URL or http://127.0.0.1:8700)",
        ),
    ] = None,
    company_ref: Annotated[
        str | None,
        typer.Option(
            "--company", "-c", help="Company id or name (default: the only one, or the saved one)"
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Print the version and exit.",
            callback=lifecycle.show_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    ctx.obj = CliState(json=json_out, api_url=api_url, company=company_ref)
    if ctx.invoked_subcommand is None:
        if lifecycle.invoked_as().startswith("companyos"):
            # the old name keeps its old behaviour: no arguments shows the help
            typer.echo(ctx.get_help())
            raise typer.Exit(0)
        guarded(lambda ctx: lifecycle.launch())(ctx)


for module in (auth, company, org, work, setup, system, lifecycle):
    module.register(app)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
