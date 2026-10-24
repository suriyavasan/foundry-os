"""The ``companyos`` command."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli.commands import auth, company, org, setup, system, work
from app.cli.context import CliState

app = typer.Typer(
    name="companyos",
    help="Run and operate your Company OS: one command to start it, a command for every core operation.",
    no_args_is_help=True,
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
) -> None:
    ctx.obj = CliState(json=json_out, api_url=api_url, company=company_ref)


for module in (auth, company, org, work, setup, system):
    module.register(app)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
