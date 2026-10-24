"""Per-invocation state (JSON mode, API client, selected company) and the error guard."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

import typer

from app.cli import config, render
from app.cli.client import (
    EXIT_AUTH,
    ApiClient,
    CliError,
    HttpFactory,
    default_http_factory,
)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]

# Tests replace this to talk to an in-process app (httpx-compatible TestClient).
http_factory: HttpFactory = default_http_factory


def set_http_factory(factory: HttpFactory) -> None:
    global http_factory
    http_factory = factory


@dataclass
class CliState:
    json: bool = False
    api_url: str | None = None
    company: str | None = None
    _client: ApiClient | None = field(default=None, repr=False)
    _company_id: str | None = field(default=None, repr=False)

    def api_base(self) -> str:
        return config.resolve_api_url(self.api_url)

    def anonymous_client(self) -> ApiClient:
        """No stored credential (login, public endpoints)."""
        return ApiClient(http_factory(self.api_base()), None)

    def client(self) -> ApiClient:
        if self._client is None:
            cred = config.resolve_credential()
            if cred is None:
                raise CliError(
                    "Not signed in. Run `foundryos login` (or run this on the server machine, "
                    "where the owner token file is used).",
                    exit_code=EXIT_AUTH,
                    code="unauthenticated",
                )
            self._client = ApiClient(http_factory(self.api_base()), cred.token)
        return self._client

    def company_id(self) -> str:
        if self._company_id:
            return self._company_id
        import os

        wanted = (
            self.company
            or os.environ.get("COMPANYOS_COMPANY")
            or config.load_config().get("company_id")
        )
        companies = self.client().paged("/companies")
        if wanted:
            for c in companies:
                if wanted in (c["id"], c.get("slug"), c.get("name")) or (
                    c["name"].lower() == wanted.lower()
                ):
                    self._company_id = c["id"]
                    return c["id"]
            raise CliError(
                f"No company matches {wanted!r}. Run `foundryos status` to see your companies.",
                exit_code=5,
                code="not_found",
            )
        if len(companies) == 1:
            self._company_id = companies[0]["id"]
            return self._company_id
        if not companies:
            raise CliError(
                "You have no company yet. Create one in the web app.", exit_code=5, code="not_found"
            )
        names = ", ".join(f"{c['name']} ({c['id'][:8]})" for c in companies)
        raise CliError(
            f"Several companies: {names}. Pick one with --company or `foundryos login --company`.",
            code="ambiguous_company",
        )

    def cpath(self, suffix: str = "") -> str:
        return f"/companies/{self.company_id()}{suffix}"


def state(ctx: typer.Context, json_out: bool = False) -> CliState:
    st: CliState = ctx.obj if isinstance(ctx.obj, CliState) else CliState()
    ctx.obj = st
    if json_out:
        st.json = True
    return st


def guarded(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn ``CliError`` into a one-line message (or a JSON error object) and the right exit code."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = next((a for a in args if isinstance(a, typer.Context)), kwargs.get("ctx"))
        try:
            return fn(*args, **kwargs)
        except CliError as exc:
            st = ctx.obj if ctx is not None and isinstance(ctx.obj, CliState) else CliState()
            if kwargs.get("json_out") or st.json:
                sys.stderr.write(
                    render.to_json(
                        {
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            }
                        }
                    )
                    + "\n"
                )
            else:
                render.say(f"Error: {exc.message}", style="red", err=True)
            raise typer.Exit(exc.exit_code) from None

    return wrapper


def command(app: typer.Typer, name: str | None = None, **kwargs: Any):
    """``@command(app, "ls")``: a Typer command wrapped in the error guard."""

    def deco(fn: Callable[..., Any]):
        return app.command(name, **kwargs)(guarded(fn))

    return deco
