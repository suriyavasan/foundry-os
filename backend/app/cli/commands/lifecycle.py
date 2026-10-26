"""Running Foundry AI OS as a program: foundryos / start / stop / restart / open / logs / version / serve,
``service``, ``update`` / ``rollback`` and ``uninstall``. Nothing here goes through the HTTP API: these
commands act on this machine (processes, the user service, the install folder)."""

from __future__ import annotations

import contextlib
import os
import platform
import sys
from pathlib import Path
from typing import Annotated

import click
import typer

from app.cli import render
from app.cli.client import EXIT_ERROR, EXIT_NOT_FOUND, CliError
from app.cli.context import CliState, JsonOpt, command, guarded, state
from app.core import install
from app.core.config import Settings
from app.ops import headless, lifecycle, logs, services, updatecheck, updater

service_app = typer.Typer(
    help="Start Foundry AI OS at login: a macOS LaunchAgent or a Linux systemd user service.",
    no_args_is_help=True,
)

echo = typer.echo


def _fail(exc: Exception) -> CliError:
    message = getattr(exc, "message", None) or str(exc)
    return CliError(
        message, exit_code=getattr(exc, "exit_code", EXIT_ERROR), code=getattr(exc, "code", "error")
    )


def _update_hint(settings: Settings, *, refresh: bool) -> None:
    """One line when a newer release is known (cache only unless the daily check is due)."""
    try:
        info = updatecheck.status(settings, refresh="if-stale" if refresh else "never")
    except Exception:  # an update hint must never break a command
        return
    if info["update_available"]:
        echo(
            f"\nUpdate available: Foundry AI OS {info['latest_version']} (you have {info['current_version']}). "
            f"To update: {info['update_command']}"
        )


def _cli_state() -> CliState:
    """The state of the running command (the root options), or a default one."""
    ctx = click.get_current_context(silent=True)
    return ctx.obj if ctx is not None and isinstance(ctx.obj, CliState) else CliState()


def _wizard_hint() -> None:
    echo("Finish setup: foundryos setup")


def _offer_setup() -> None:
    """Setup is pending on a box without a browser: offer the terminal wizard (when someone can answer),
    else say how to start it."""
    if headless.stdin_is_tty():
        try:
            yes = typer.confirm("Set it up here in the terminal now?", default=True)
        except (click.Abort, EOFError):
            yes = False
        if yes:
            try:
                from app.cli.commands.onboard import offer_wizard
            except ImportError:  # the wizard ships separately; never break `foundryos` over it
                offer_wizard = None
            if offer_wizard is not None and offer_wizard(_cli_state()):
                return
    _wizard_hint()


def _print_remote_access(settings: Settings, url: str) -> None:
    """What to show instead of opening a browser: the local address, the SSH forward, the public URL."""
    echo(f"Local URL:  {url}")
    for line in headless.remote_hints(settings.port):
        echo(line)
    public = lifecycle.read_public_url(settings)
    if public:
        detail = ", ".join(str(x) for x in (public.get("provider"), public.get("state")) if x)
        echo(f"Public URL: {public['url']}" + (f"  ({detail})" if detail else ""))


def launch(no_open: bool = False) -> None:
    """``foundryos`` with no arguments."""
    settings = Settings()
    try:
        state_ = lifecycle.run_state(settings)
        if state_.running:
            echo(lifecycle.describe(state_))
        else:
            state_ = lifecycle.start(settings, echo)
            echo(f"Foundry AI OS is running at {state_.url}")
    except (lifecycle.LifecycleError, services.ServiceError) as exc:
        raise _fail(exc) from exc
    pending = lifecycle.setup_pending(settings.port)
    if headless.is_headless():
        # no screen here (SSH, container, server): never try a browser
        _print_remote_access(settings, state_.url)
        if pending:
            _offer_setup()
        echo(
            "Stop it with `foundryos stop`."
            + (
                " `foundryos service install --linger` keeps it running after you log out."
                if install.current_os() == "linux"
                else ""
            )
        )
    else:
        if pending:
            echo("First run: create your account in the browser or run `foundryos setup`.")
        if not no_open:
            if lifecycle.open_in_browser(state_.url):
                echo("Opened it in your browser.")
            else:
                echo(f"Open this address in your browser: {state_.url}")
        echo("Stop it with `foundryos stop`; `foundryos service install` starts it at every login.")
    _update_hint(settings, refresh=True)


def stop_program() -> None:
    """``foundryos stop`` (without --all): stop the background copy and the background service."""
    settings = Settings()
    try:
        stopped = lifecycle.stop(settings, echo)
    except (lifecycle.LifecycleError, services.ServiceError) as exc:
        raise _fail(exc) from exc
    echo("Stopped." if stopped else "Foundry AI OS is not running.")


def show_version(value: bool) -> None:
    if value:
        echo(f"foundryos {install.APP_VERSION}")
        raise typer.Exit(0)


def invoked_as() -> str:
    return os.environ.get("FOUNDRYOS_PROG") or Path(sys.argv[0]).name


def register(app: typer.Typer) -> None:
    app.add_typer(service_app, name="service")

    # ------------------------------------------------------------------ run it

    @command(app, "start")
    def start_cmd(ctx: typer.Context) -> None:
        """Start Foundry AI OS in the background (without opening the browser)."""
        settings = Settings()
        try:
            was = lifecycle.run_state(settings)
            if was.running:
                echo(lifecycle.describe(was))
                now = was
            else:
                now = lifecycle.start(settings, echo)
                echo(f"Foundry AI OS is running at {now.url}")
        except (lifecycle.LifecycleError, services.ServiceError) as exc:
            raise _fail(exc) from exc
        if headless.is_headless():
            _print_remote_access(settings, now.url)
            if lifecycle.setup_pending(settings.port):
                _offer_setup()

    @command(app, "restart")
    def restart_cmd(ctx: typer.Context) -> None:
        """Stop it and start it again."""
        settings = Settings()
        try:
            now = lifecycle.restart(settings, echo)
        except (lifecycle.LifecycleError, services.ServiceError) as exc:
            raise _fail(exc) from exc
        echo(f"Foundry AI OS is running at {now.url}")

    @command(app, "open")
    def open_cmd(ctx: typer.Context) -> None:
        """Open Foundry AI OS in your browser."""
        settings = Settings()
        now = lifecycle.run_state(settings)
        if not now.running:
            raise CliError(
                "Foundry AI OS is not running. Start it with `foundryos` (or `foundryos start`).",
                exit_code=EXIT_NOT_FOUND,
                code="not_running",
            )
        if headless.is_headless():
            _print_remote_access(settings, now.url)
            if lifecycle.setup_pending(settings.port):
                _wizard_hint()
        elif lifecycle.open_in_browser(now.url):
            echo(f"Opened {now.url}")
        else:
            echo(f"Open this address in your browser: {now.url}")

    @command(app, "logs")
    def logs_cmd(
        ctx: typer.Context,
        follow: Annotated[
            bool, typer.Option("--follow", "-f", help="Keep printing new lines.")
        ] = False,
        lines: Annotated[
            int, typer.Option("--lines", "-n", min=0, help="How many lines to show.")
        ] = 100,
    ) -> None:
        """Show the server log (background and service runs write it to the data folder)."""
        settings = Settings()
        path = logs.pick_log(settings)
        if path is None:
            echo(
                "No log yet. Foundry AI OS writes one when it runs in the background or as a service "
                f"({logs.log_path(settings)}). Runs in a terminal print to that terminal."
            )
            return
        echo(f"==> {path}", err=True)
        for line in logs.tail_lines(path, lines):
            echo(line)
        if follow:
            with contextlib.suppress(KeyboardInterrupt):
                logs.follow(path, echo)

    @command(app, "version")
    def version_cmd(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Print the version."""
        st = state(ctx, json_out)
        if st.json:
            settings = Settings()
            render.print_json(
                {
                    "version": install.APP_VERSION,
                    "install_kind": install.install_kind(),
                    "platform": install.platform_tag(),
                    "data_dir": str(settings.resolved_data_dir),
                    "python": platform.python_version(),
                }
            )
        else:
            echo(f"foundryos {install.APP_VERSION}")

    @command(app, "serve")
    def serve_cmd(
        ctx: typer.Context,
        port: Annotated[
            int | None,
            typer.Option(help="Port (default: FOUNDRYOS_PORT or 8700)", min=1, max=65535),
        ] = None,
        host: Annotated[
            str | None, typer.Option(help="Bind address (default: the saved preference)")
        ] = None,
    ) -> None:
        """Run in the foreground without opening a browser (what the background service runs)."""
        from app.ops.up import UpOptions, run_up

        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
        settings = Settings()
        code = run_up(
            UpOptions(port=port or settings.port, host=host, build=False),
            say=lambda line: echo(line),
        )
        if code:
            raise typer.Exit(code)

    # ------------------------------------------------------------------ the service

    @command(service_app, "install")
    def service_install(
        ctx: typer.Context,
        linger: Annotated[
            bool,
            typer.Option(
                "--linger",
                help="Linux: keep it running when you are logged out (loginctl enable-linger).",
            ),
        ] = False,
    ) -> None:
        """Install the background service and start it (macOS LaunchAgent / Linux systemd user unit)."""
        settings = Settings()
        kind = install.install_kind()
        if kind == install.HOMEBREW or services.brew_manager().installed():
            echo(services.BREW_MESSAGE)
            return
        if kind == install.DOCKER:
            raise CliError(
                "Not needed in Docker: the container is the service (docker run -d ...)."
            )
        native = services.native_manager()
        if native is None:
            raise CliError(
                "Background services are not supported on this system. Use `foundryos start`, or Docker/WSL on Windows."
            )
        try:
            healthy = lifecycle.install_service(settings, echo, native, linger=linger)
        except (lifecycle.LifecycleError, services.ServiceError) as exc:
            raise _fail(exc) from exc
        if healthy:
            echo(f"Foundry AI OS is running at http://127.0.0.1:{settings.port}")
        elif healthy is False:
            echo("It is not answering yet. Check `foundryos logs` (first start can take a minute).")

    @command(service_app, "uninstall")
    def service_uninstall(ctx: typer.Context) -> None:
        """Stop the background service and remove it. Your data and the program stay."""
        if install.install_kind() == install.HOMEBREW or services.brew_manager().installed():
            echo(services.BREW_MESSAGE)
            return
        native = services.native_manager()
        if native is None:
            echo("Background services are not supported on this system.")
            return
        try:
            for note in native.uninstall():
                echo(note)
        except services.ServiceError as exc:
            raise _fail(exc) from exc

    @command(service_app, "status")
    def service_status(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Is Foundry AI OS running, and is it set up to start at login?"""
        st = state(ctx, json_out)
        settings = Settings()
        run = lifecycle.run_state(settings)
        svc = services.active_service() or services.native_manager()
        svc_status = None
        if svc is not None:
            with contextlib.suppress(services.ServiceError):
                svc_status = svc.status()
        if st.json:
            render.print_json(
                {
                    "running": run.running,
                    "url": run.url,
                    "version": run.version,
                    "managed_by": run.managed_by,
                    "pid": run.supervisor_pid,
                    "install_kind": install.install_kind(),
                    "service": svc_status.as_dict() if svc_status else None,
                }
            )
            return
        echo(lifecycle.describe(run))
        if svc_status is None or not svc_status.installed:
            if install.install_kind() == install.HOMEBREW:
                echo("Background service: not set up (Homebrew: `brew services start foundry-os`).")
            else:
                echo(
                    "Background service: not installed (`foundryos service install` starts it at every login)."
                )
        else:
            echo(
                f"Background service ({svc_status.manager}): {svc_status.detail}; {svc_status.path}"
            )
            if svc_status.manager == "systemd" and headless.is_headless():
                lingering = services.linger_state()
                if lingering is False:
                    echo(
                        "Linger is off: it stops when you log out. `foundryos service install --linger` fixes that."
                    )

    # ------------------------------------------------------------------ updates

    @command(app, "update")
    def update_cmd(
        ctx: typer.Context,
        version: Annotated[
            str | None,
            typer.Option("--version", help="Install this version instead of the latest."),
        ] = None,
        check: Annotated[
            bool, typer.Option("--check", help="Only look for a newer release.")
        ] = False,
        auto_check: Annotated[
            bool | None,
            typer.Option(
                "--auto-check/--no-auto-check",
                help="Turn the daily 'is there a newer release?' check on or off (one GitHub request a day).",
            ),
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Update to the latest release (installer installs), or show how to update this install."""
        st = state(ctx, json_out)
        settings = Settings()
        kind = install.install_kind()
        if auto_check is not None:
            updatecheck.set_preference(settings, auto_check)
            echo("Daily update check is on." if auto_check else "Daily update check is off.")
            return
        if check:
            info = updatecheck.status(settings, refresh="always")
            if st.json:
                render.print_json(info)
            elif info["error"] and not info["latest_version"]:
                raise CliError(f"Could not check for updates: {info['error']}")
            elif info["update_available"]:
                echo(
                    f"Update available: {info['latest_version']} (you have {info['current_version']})."
                )
                echo(f"To update: {info['update_command']}")
                if info["release_url"]:
                    echo(f"Release notes: {info['release_url']}")
            elif info["latest_version"]:
                echo(
                    f"You are up to date ({info['current_version']}; latest release {info['latest_version']})."
                )
            else:
                echo(f"No release is published yet (you have {info['current_version']}).")
            return
        if kind != install.INSTALLER:
            echo(updater.other_install_instructions(kind))
            return

        def say(message: str) -> None:
            echo(message, err=st.json)  # JSON mode: progress on stderr, the result on stdout

        was_running = lifecycle.run_state(settings).running
        try:
            result = updater.update(settings, say, version=version)
        except updater.UpdateError as exc:
            raise _fail(exc) from exc
        if result.already_current:
            if st.json:
                render.print_json(result.as_dict())
            else:
                echo(f"Already on the latest release ({result.to_version}).")
            return
        if was_running:
            try:
                now = lifecycle.restart(settings, say)
            except (lifecycle.LifecycleError, services.ServiceError) as exc:
                raise CliError(
                    f"{exc}\nThe new version did not start. Go back with: foundryos rollback",
                    code="restart_failed",
                ) from exc
            say(f"Foundry AI OS {now.version or result.to_version} is running at {now.url}")
        else:
            say("It was not running; the new version starts the next time you run `foundryos`.")
        with contextlib.suppress(Exception):
            lay = updater.layout()
            if lay is not None:
                updater.prune(lay, say)
        say("Your previous version is kept: `foundryos rollback` goes back to it.")
        if result.release_url:
            say(f"Release notes: {result.release_url}")
        if st.json:
            render.print_json(result.as_dict())

    @command(app, "rollback")
    def rollback_cmd(
        ctx: typer.Context,
        restore_data: Annotated[
            bool,
            typer.Option(
                "--restore-data",
                help="Also put your data back as it was before the update (needed when the update migrated the database).",
            ),
        ] = False,
        yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
    ) -> None:
        """Go back to the previous version (installer installs)."""
        settings = Settings()
        kind = install.install_kind()
        if kind != install.INSTALLER:
            echo(updater.other_install_instructions(kind))
            raise typer.Exit(1)
        lay = updater.layout()
        assert lay is not None
        target = updater.rollback_target(lay)
        current = updater.current_version(lay)
        if target is None:
            raise CliError(
                "There is no earlier version on this computer to go back to.",
                exit_code=EXIT_NOT_FOUND,
                code="no_previous",
            )
        compatible = updater.data_compatible_with(settings, lay.version_dir(target))
        backup = None
        if restore_data:
            backup = updater.latest_pre_upgrade_backup(settings, current or "")
            if backup is None:
                raise CliError(
                    f"No automatic backup from before the update to {current} was found in "
                    f"{settings.resolved_data_dir / 'backups'}.",
                    exit_code=EXIT_NOT_FOUND,
                    code="no_backup",
                )
        elif compatible is False:
            raise CliError(
                f"Version {target} cannot open your data as it is now: the update to {current} changed "
                "the database. Nothing was changed.\n"
                "To go back anyway and restore your data as it was before that update (changes since "
                "then are set aside, not deleted), run: foundryos rollback --restore-data",
                code="data_incompatible",
            )
        elif compatible is None:
            echo("Note: could not check whether the older version can open your data.")
        if (
            restore_data
            and not yes
            and not typer.confirm(
                f"Go back to {target} and restore the data from before the update to {current}? "
                "Changes since then are moved aside, not deleted.",
                default=False,
            )
        ):
            raise CliError("Cancelled.", code="cancelled")
        was_running = lifecycle.run_state(settings).running
        try:
            if restore_data and backup is not None:
                from app.ops.backup import OpsError, restore_backup

                lifecycle.stop(settings, echo)
                try:
                    outcome = restore_backup(backup, settings, force=True, confirm=lambda _q: True)
                except OpsError as exc:
                    raise _fail(exc) from exc
                echo(f"Restored your data from {backup.name}.")
                if outcome.replaced_into:
                    echo(
                        f"The data it replaced is in {outcome.replaced_into} (delete it when you are sure)."
                    )
            updater.switch_current(lay, target)
            echo(f"Switched back to Foundry AI OS {target} (was {current}).")
            if was_running or restore_data:
                now = lifecycle.restart(settings, echo)
                echo(f"Foundry AI OS {now.version or target} is running at {now.url}")
        except (updater.UpdateError, lifecycle.LifecycleError, services.ServiceError) as exc:
            raise _fail(exc) from exc

    # ------------------------------------------------------------------ uninstall

    @command(app, "uninstall")
    def uninstall_cmd(
        ctx: typer.Context,
        purge: Annotated[
            bool,
            typer.Option(
                "--purge", help="Also delete your data folder (asks you to type a confirmation)."
            ),
        ] = False,
        yes_delete: Annotated[
            bool,
            typer.Option(
                "--yes-delete-my-data",
                help="With --purge: skip the typed confirmation (for scripts).",
            ),
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Stop Foundry AI OS and remove what the installer put on this computer. Your data is kept."""
        from app.ops import uninstall as ops_uninstall

        st = state(ctx, json_out)
        if yes_delete and not purge:
            raise CliError("--yes-delete-my-data only makes sense with --purge.", code="usage")
        settings = Settings()

        def confirm(path) -> bool:
            if yes_delete:
                return True
            echo(f"This permanently deletes ALL of your Foundry AI OS data in:\n  {path}")
            echo("(your database, keys, files the agents made, backups). It cannot be undone.")
            try:
                typed = typer.prompt(
                    f'Type "{ops_uninstall.CONFIRM_PHRASE}" to confirm',
                    default="",
                    show_default=False,
                )
            except (click.Abort, EOFError):
                return False
            return typed.strip() == ops_uninstall.CONFIRM_PHRASE

        try:
            result = ops_uninstall.uninstall(
                settings, (lambda m: echo(m, err=st.json)), purge=purge, confirm_purge=confirm
            )
        except ops_uninstall.PurgeRefused as exc:
            raise CliError(str(exc), code="purge_refused") from exc
        if st.json:
            render.print_json(result.as_dict())
            return
        for path in result.removed:
            echo(f"Removed {path}")
        for note in result.notes:
            echo(note)
        echo("Foundry AI OS is uninstalled." if result.removed else "Done.")


__all__ = ["guarded", "invoked_as", "launch", "register", "show_version", "stop_program"]
