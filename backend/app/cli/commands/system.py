"""up, doctor, backup, restore: commands that work on this machine, not through the API."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from app.cli import render
from app.cli.client import CliError
from app.cli.context import JsonOpt, command, state

backup_app = typer.Typer(help="Back up and restore this install's data.", no_args_is_help=True)

_STYLE = {"ok": "green", "warn": "yellow", "fail": "red", "info": "dim"}
_MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "info"}


def _settings_for(data_dir: Path | None):
    from app.core.config import Settings

    if data_dir is None:
        return Settings()
    root = data_dir.expanduser().resolve()
    return Settings(database_url=f"sqlite:///{root / 'company_os.db'}", data_dir=root)


def _read_passphrase(from_stdin: bool, *, confirm: bool) -> str:
    """The backup passphrase: one line from stdin (``--passphrase-stdin``, for scripts) or a hidden
    prompt. Never an argument: argv is visible to other users and lands in shell history."""
    if from_stdin:
        line = sys.stdin.readline()
        value = line.rstrip("\r\n")
    else:
        value = typer.prompt("Backup passphrase", hide_input=True, confirmation_prompt=confirm)
    if not value:
        raise CliError("The passphrase is empty.", exit_code=2, code="passphrase_required")
    return value


def register(app: typer.Typer) -> None:
    app.add_typer(backup_app, name="backup")

    @command(app, "up")
    def up(
        ctx: typer.Context,
        port: Annotated[
            int | None,
            typer.Option(
                help="Port for the API and the web UI (default: FOUNDRYOS_PORT or 8700)",
                min=1,
                max=65535,
            ),
        ] = None,
        host: Annotated[
            str | None,
            typer.Option(help="Bind address (default: the saved Reachability preference)"),
        ] = None,
        no_worker: Annotated[
            bool, typer.Option("--no-worker", help="Do not start the worker/scheduler.")
        ] = False,
        open_browser: Annotated[
            bool, typer.Option("--open", help="Open the browser when ready.")
        ] = False,
        dev: Annotated[
            bool,
            typer.Option(
                "--dev", help="Run the Vite dev server (hot reload) instead of the built UI."
            ),
        ] = False,
        no_build: Annotated[bool, typer.Option("--no-build", help="Never build web/dist.")] = False,
    ) -> None:
        """Start everything: migrate the database, build the UI if needed, run the API and the worker."""
        from app.core.config import Settings
        from app.ops.up import UpOptions, run_up

        st = state(ctx)
        code = run_up(
            UpOptions(
                port=port or Settings().port,
                host=host,
                no_worker=no_worker,
                open_browser=open_browser,
                dev=dev,
                build=not no_build,
            ),
            say=lambda line: typer.echo(line),
        )
        if st is not None and code:
            raise typer.Exit(code)

    @command(app, "migrate")
    def migrate(ctx: typer.Context) -> None:
        """Upgrade the database to this version and exit (taking the automatic safety backup first).

        `up` / `serve` do this on every start; containers and scripts can call it on its own."""
        from app.core.config import Settings
        from app.ops import up as up_ops
        from app.ops import upgrade

        settings = Settings()
        if not upgrade.upgrade_database(settings, typer.echo, up_ops.ensure_migrated):
            raise typer.Exit(1)
        typer.echo("The database is up to date.")

    @command(app, "doctor")
    def doctor(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Check this machine: Python, database, runtime, Ollama, Docker, disk, file permissions, auth."""
        from app.ops.doctor import run_checks

        st = state(ctx, json_out)
        checks = run_checks()
        failed = any(c.status == "fail" for c in checks)
        if st.json:
            render.print_json({"ok": not failed, "checks": [c.as_dict() for c in checks]})
        else:
            for c in checks:
                render.say(f"[{_MARK[c.status]}] {c.name:<14} {c.detail}", style=_STYLE[c.status])
                if c.fix and c.status != "ok":
                    render.say(f"       fix: {c.fix}", style="dim")
            render.say(
                "\n" + ("Problems found." if failed else "No blocking problems."),
                style="red" if failed else "green",
            )
        if failed:
            raise typer.Exit(1)

    # ------------------------------------------------------------------ backup / restore

    @command(backup_app, "create")
    def backup_create(
        ctx: typer.Context,
        out: Annotated[
            Path | None,
            typer.Option("--out", help="Archive path or directory (default: <data dir>/backups/)"),
        ] = None,
        data_dir: Annotated[
            Path | None, typer.Option(help="Use this data directory instead of the configured one")
        ] = None,
        encrypt: Annotated[
            bool,
            typer.Option(
                "--encrypt",
                help="Encrypt the archive (AES-256-GCM, scrypt key) -> *.tar.gz.enc. "
                "Asks for a passphrase; there is no way to recover a lost one.",
            ),
        ] = False,
        passphrase_stdin: Annotated[
            bool,
            typer.Option(
                "--passphrase-stdin",
                help="With --encrypt: read the passphrase from the first line of stdin "
                "(for scripts) instead of prompting.",
            ),
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Snapshot the database (SQLite online backup, safe while running) and the data files."""
        from app.ops.backup import OpsError, create_backup

        st = state(ctx, json_out)
        if passphrase_stdin and not encrypt:
            raise CliError("--passphrase-stdin only makes sense with --encrypt.", exit_code=2)
        passphrase = _read_passphrase(passphrase_stdin, confirm=True) if encrypt else None
        try:
            result = create_backup(_settings_for(data_dir), out, passphrase=passphrase)
        except OpsError as exc:
            raise CliError(exc.message, exit_code=exc.exit_code, code=exc.code) from exc
        if st.json:
            render.print_json(
                {
                    "path": str(result.path),
                    "files": result.files,
                    "bytes": result.bytes,
                    "alembic_revision": result.alembic_revision,
                    "encrypted": result.encrypted,
                }
            )
        else:
            render.say(
                f"Backup written to {result.path} ({result.files} files, {result.bytes:,} bytes)."
            )
            if result.encrypted:
                render.say(
                    "Encrypted with your passphrase (AES-256-GCM). Without the passphrase it cannot "
                    "be restored: store the passphrase somewhere safe, separate from the file."
                )
            else:
                render.say(
                    "It contains your secret key and owner token and is NOT encrypted: keep it "
                    "private (the file is mode 0600) or make backups with --encrypt.",
                    style="yellow",
                )

    @command(backup_app, "restore")
    def backup_restore(
        ctx: typer.Context,
        archive: Annotated[
            Path, typer.Argument(help="A backup archive made by `foundryos backup create`")
        ],
        data_dir: Annotated[
            Path | None,
            typer.Option(help="Restore into this data directory instead of the configured one"),
        ] = None,
        force: Annotated[
            bool,
            typer.Option(
                "--force", help="Allow replacing an existing install (it is moved aside)."
            ),
        ] = False,
        yes: Annotated[
            bool, typer.Option("--yes", help="Answer the confirmation (only with --force).")
        ] = False,
        verify_only: Annotated[
            bool, typer.Option("--verify-only", help="Check the archive's checksums and stop.")
        ] = False,
        passphrase_stdin: Annotated[
            bool,
            typer.Option(
                "--passphrase-stdin",
                help="Encrypted backups: read the passphrase from the first line of stdin "
                "instead of prompting.",
            ),
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Restore into a STOPPED install. Verifies every checksum before touching anything.

        Encrypted backups (*.tar.gz.enc) are recognised automatically and ask for the passphrase."""
        from app.ops.backup import OpsError, is_encrypted, restore_backup, verify_archive

        st = state(ctx, json_out)
        passphrase = None
        if archive.is_file() and is_encrypted(archive):
            passphrase = _read_passphrase(passphrase_stdin, confirm=False)
        try:
            if verify_only:
                manifest = verify_archive(archive, passphrase=passphrase)
                result = {
                    "verified": True,
                    "files": len(manifest["files"]),
                    "created_at": manifest.get("created_at"),
                }
                if st.json:
                    render.print_json(result)
                else:
                    render.say(
                        f"Archive is intact: {result['files']} files, made {result['created_at']}."
                    )
                return

            def confirm(question: str) -> bool:
                if yes:
                    return True
                if st.json:
                    return False  # never block a script on a prompt
                return typer.confirm(question, default=False)

            outcome = restore_backup(
                archive,
                _settings_for(data_dir),
                force=force,
                confirm=confirm,
                passphrase=passphrase,
            )
        except OpsError as exc:
            raise CliError(exc.message, exit_code=exc.exit_code, code=exc.code) from exc
        if st.json:
            render.print_json(
                {
                    "restored": True,
                    "files": outcome.files,
                    "previous_data": str(outcome.replaced_into) if outcome.replaced_into else None,
                    "alembic_revision": outcome.alembic_revision,
                }
            )
        else:
            render.say(f"Restored {outcome.files} files.")
            if outcome.replaced_into:
                render.say(
                    f"The previous data was moved to {outcome.replaced_into} (delete it when you are sure)."
                )
            render.say(
                "Start it with: foundryos up   (the database is migrated to the current version on start)"
            )
