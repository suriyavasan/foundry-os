"""Plan sign-in of a coding CLI in one private home directory (M17; generalizes the B9 Claude code).

Supported: Claude Code (``claude auth login|status|logout`` with ``CLAUDE_CONFIG_DIR=<home>``) and
the
Cursor CLI (``agent login|status|logout`` with an isolated ``HOME=<home>`` and
``NO_OPEN_BROWSER=1`` so it
prints the sign-in URL; cursor.com/docs/cli/reference/authentication, read 2026-10-28).

Safety rules (docs/specs/b9-claude-plan.md, docs/specs/m17-ai-stack.md): the CLI does its own
sign-in;
Foundry never reads, copies or stores a credential file, keychain entry, token or sign-in code.
State comes
only from the CLI's ``status`` command (exit code / output). Of the login output only the https
sign-in URL
(an authorization link) is kept; everything else is dropped unread. One login per home at a time, a
10-minute watchdog ends a hung one. No database access here: ``ai_stack`` records and audits."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.errors import Conflict
from app.core.time import format_ts, utcnow
from app.runtimes.cli.catalog import CLIS, assert_safe_argv
from app.runtimes.cli.env import CliEnvironment, base_env

STATUS_TTL_SECONDS = 30.0
STATUS_TIMEOUT_SECONDS = 15.0
LOGOUT_TIMEOUT_SECONDS = 30.0
LOGIN_WATCHDOG_SECONDS = 600.0
_URL_RE = re.compile(r"https://[^\s\"'<>]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SIGNED_OUT_RE = re.compile(
    r"not (?:logged|signed) in|logged out|signed out|unauthenticated|not authenticated|no active",
    re.I,
)


class CliNotInstalled(Conflict):
    code = "cli_not_installed"
    default_message = "The coding CLI is not installed"


class LoginInProgress(Conflict):
    code = "login_in_progress"
    default_message = "A sign-in is already running"


@dataclass(frozen=True)
class PlanCli:
    cli_id: str
    subdir: str  # under the data dir: ``<subdir>/<connection id>``
    hosts: tuple[str, ...]  # hosts a sign-in URL may point at
    display: str


PLAN_CLIS: dict[str, PlanCli] = {
    "claude_code": PlanCli(
        "claude_code", "claude-code", ("claude.ai", "claude.com", "anthropic.com"), "Claude Code"
    ),
    "cursor": PlanCli("cursor", "cursor-home", ("cursor.com", "cursor.sh"), "Cursor"),
}


def supported(cli_id: str | None) -> bool:
    return cli_id in PLAN_CLIS


def make_home(data_dir: Path, rel: str) -> Path:
    """Create (0700) and return a plan home given its path relative to the data dir."""
    path = Path(data_dir) / rel
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path


def remove_home(data_dir: Path, rel: str | None) -> bool:
    """Delete a plan home, but only if it is inside one of the known plan directories."""
    import shutil

    if not rel:
        return False
    root = Path(data_dir).resolve()
    path = (root / rel).resolve()
    allowed = [root / spec.subdir for spec in PLAN_CLIS.values()]
    inside = any(path == a or a in path.parents for a in allowed)
    if not inside or not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def _binary(cli_env: CliEnvironment, cli_id: str) -> str:
    binary = cli_env.find_binary(CLIS[cli_id])
    if binary is None:
        raise CliNotInstalled(f"{CLIS[cli_id].display_name} is not installed")
    return binary


def child_env(cli_env: CliEnvironment, cli_id: str, home: Path) -> dict[str, str]:
    """Minimal environment of every ``auth`` child. Claude: its own ``CLAUDE_CONFIG_DIR`` and the
    real
    ``HOME`` (the macOS Keychain lives there). Cursor: the isolated ``HOME``. No secrets."""
    env = {
        **base_env(),
        "PATH": cli_env.search_path(CLIS[cli_id]),
        "HOME": os.environ.get("HOME", str(Path.home())),
    }
    if cli_id == "claude_code":
        env.update(CLAUDE_CONFIG_DIR=str(home), DISABLE_AUTOUPDATER="1", DISABLE_TELEMETRY="1")
    else:
        env.update(HOME=str(home), NO_OPEN_BROWSER="1")
    return env


def _argv(binary: str, cli_id: str, action: str, method: str = "claudeai") -> list[str]:
    if cli_id == "claude_code":
        argv = [binary, "auth", action]
        if action == "login":
            argv.append(f"--{method}")
        return argv
    return [binary, action]


def login_argv(cli_env: CliEnvironment, cli_id: str, method: str = "claudeai") -> list[str]:
    return _argv(_binary(cli_env, cli_id), cli_id, "login", method)


def parse_status(cli_id: str, stdout: str, exit_code: int) -> dict[str, Any]:
    """``status`` output -> ``{signed_in, auth_method, email}``; anything unreadable is signed out.
    Claude: exit 0 and JSON that does not say ``loggedIn: false``. Cursor (output format
    undocumented):
    exit 0 and no "not logged in"-style text; an e-mail address in the output is shown as the
    account."""
    out: dict[str, Any] = {"signed_in": False, "auth_method": None, "email": None}
    if cli_id == "claude_code":
        try:
            data = json.loads(stdout)
        except ValueError:
            return out
        if not isinstance(data, dict):
            return out
        if exit_code == 0 and data.get("loggedIn") is not False:
            out["signed_in"] = True
        method = data.get("authMethod")
        if isinstance(method, str) and method and method != "none":
            out["auth_method"] = method[:40]
        email = data.get("email")
        if not isinstance(email, str):
            account = data.get("account")
            email = account.get("email") if isinstance(account, dict) else None
        if isinstance(email, str) and email:
            out["email"] = email[:200]
    else:
        text = stdout or ""
        if exit_code == 0 and text.strip() and not _SIGNED_OUT_RE.search(text):
            out["signed_in"] = True
            found = _EMAIL_RE.search(text)
            if found:
                out["email"] = found.group(0)[:200]
    if not out["signed_in"]:
        out["auth_method"] = None
        out["email"] = None
    return out


_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _key(home: Path) -> str:
    return str(home)


def _fetch_status(cli_env: CliEnvironment, cli_id: str, home: Path) -> dict[str, Any]:
    binary = _binary(cli_env, cli_id)
    try:
        done = subprocess.run(
            _argv(binary, cli_id, "status"),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=STATUS_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=child_env(cli_env, cli_id, home),
        )
        parsed = parse_status(
            cli_id,
            (done.stdout or "") + (done.stderr or "" if cli_id != "claude_code" else ""),
            done.returncode,
        )
    except (OSError, subprocess.TimeoutExpired):
        parsed = {"signed_in": False, "auth_method": None, "email": None}
    return {**parsed, "checked_at": format_ts(utcnow()), "config_dir_ok": home.is_dir()}


def status_cached(
    cli_env: CliEnvironment, cli_id: str, home: Path, *, refresh: bool = False
) -> dict[str, Any]:
    """Sign-in state from the CLI's own ``status`` command, cached 30 s. A missing binary is signed
    out."""
    key = _key(home)
    now = time.monotonic()
    hit = _status_cache.get(key)
    if hit and not refresh and now - hit[0] < STATUS_TTL_SECONDS:
        return hit[1]
    try:
        value = _fetch_status(cli_env, cli_id, home)
    except CliNotInstalled:
        value = {
            "signed_in": False,
            "auth_method": None,
            "email": None,
            "checked_at": format_ts(utcnow()),
            "config_dir_ok": False,
        }
    _status_cache[key] = (now, value)
    return value


def invalidate(home: Path) -> None:
    _status_cache.pop(_key(home), None)


def job_view(home: Path) -> dict[str, Any]:
    job = _jobs.get(_key(home)) or {}
    return {
        "state": job.get("state", "idle"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "url": job.get("url"),
        "message": job.get("message"),
    }


def status(
    cli_env: CliEnvironment, cli_id: str, home: Path, *, refresh: bool = False
) -> dict[str, Any]:
    """The ``ClaudeLoginStatus`` shape. 409 ``cli_not_installed`` when the binary is missing."""
    _binary(cli_env, cli_id)
    return {**status_cached(cli_env, cli_id, home, refresh=refresh), "login": job_view(home)}


def sign_in_url(cli_id: str, line: str) -> str | None:
    """The first https URL on a line that points at the vendor's sign-in pages (an authorization
    link, not
    a credential). Everything else the CLI prints is dropped."""
    hosts = PLAN_CLIS[cli_id].hosts
    for match in _URL_RE.finditer(line):
        url = match.group(0).rstrip(".,;)")
        host = (urlparse(url).hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in hosts) and len(url) <= 2000:
            return url
    return None


def is_running(home: Path) -> bool:
    job = _jobs.get(_key(home))
    return bool(job and job.get("state") == "running")


def start(
    cli_env: CliEnvironment,
    cli_id: str,
    home: Path,
    *,
    method: str = "claudeai",
    on_finish: Callable[[bool], None] | None = None,
) -> dict[str, Any]:
    """Run the CLI's own login in the background. Raises ``LoginInProgress`` if one is running
    here."""
    argv = login_argv(cli_env, cli_id, method)
    key = _key(home)
    with _lock:
        if is_running(home):
            raise LoginInProgress("A sign-in is already running; finish it or wait for it to end")
        _jobs[key] = {
            "state": "running",
            "started_at": format_ts(utcnow()),
            "finished_at": None,
            "url": None,
            "message": None,
        }
        _status_cache.pop(key, None)
        threading.Thread(
            target=_run_login,
            args=(argv, child_env(cli_env, cli_id, home), key, cli_env, cli_id, home, on_finish),
            name=f"plan-login-{cli_id}",
            daemon=True,
        ).start()
    return status(cli_env, cli_id, home)


def _run_login(argv, env, key, cli_env, cli_id, home, on_finish) -> None:
    job = _jobs[key]
    code: int | None = None
    message: str | None = None
    proc: subprocess.Popen | None = None
    display = PLAN_CLIS[cli_id].display
    try:
        assert_safe_argv(argv)
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            errors="replace",
            start_new_session=True,
        )

        def _kill() -> None:
            nonlocal message
            message = "The sign-in was not finished within 10 minutes and was stopped"
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[union-attr]
            except OSError:
                proc.kill()  # type: ignore[union-attr]

        watchdog = threading.Timer(LOGIN_WATCHDOG_SECONDS, _kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:  # only the sign-in URL is kept; the rest is dropped unread
                if job.get("url") is None:
                    found = sign_in_url(cli_id, line)
                    if found:
                        job["url"] = found
            code = proc.wait(timeout=30)
        finally:
            watchdog.cancel()
    except Exception as exc:  # the job must always end in a recorded state
        message = message or f"{display} could not run ({type(exc).__name__})"
        code = -1
        if proc is not None:
            with contextlib.suppress(OSError):
                proc.kill()
    ok = code == 0
    if not ok and message is None:
        message = f"{display}'s sign-in ended with exit code {code}"
    job.update(
        state="succeeded" if ok else "failed",
        finished_at=format_ts(utcnow()),
        message=None if ok else message,
    )
    _status_cache.pop(key, None)
    if ok:
        with contextlib.suppress(Exception):
            status_cached(cli_env, cli_id, home, refresh=True)  # warm the cache with the real state
    if on_finish is not None:
        try:
            on_finish(ok)
        except Exception:
            import logging

            logging.getLogger("company_os.plan_login").exception(
                "could not record the end of the sign-in"
            )


def logout(cli_env: CliEnvironment, cli_id: str, home: Path) -> bool:
    """Run the CLI's own logout in its home. Returns True when it reported success."""
    binary = _binary(cli_env, cli_id)
    if is_running(home):
        raise LoginInProgress("A sign-in is running; wait for it to end first")
    failed = False
    try:
        done = subprocess.run(
            _argv(binary, cli_id, "logout"),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=LOGOUT_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=child_env(cli_env, cli_id, home),
        )
        failed = done.returncode != 0
    except (OSError, subprocess.TimeoutExpired):
        failed = True
    _jobs.pop(_key(home), None)
    _status_cache.pop(_key(home), None)
    return not failed
