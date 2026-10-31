"""Claude Code adapter (``cli:claude_code``). Flags verified against ``claude --help`` 2.1.292 on
2026-10-24 (docs/cli-runtimes.md). The prompt is the first positional right after ``-p``; every
variadic option is written as ``--flag=value`` so it can never swallow the prompt."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.time import format_ts, utcnow
from app.runtimes.base import (
    ExecutionError,
    HealthStatus,
    RuntimeErrorKind,
    RuntimeHealth,
)
from app.runtimes.cli.base import (
    CliAdapter,
    Invocation,
    Parsed,
    RunPrep,
    StreamParser,
    clip,
)
from app.runtimes.cli.catalog import CLIS
from app.runtimes.cli.env import claude_config_dir

FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
DENIED_TOOLS = (
    "Bash",
    "BashOutput",
    "KillShell",
    "PowerShell",
    "WebFetch",
    "WebSearch",
    "Task",
    "NotebookEdit",
    "SlashCommand",
)
MCP_PREFIX = "mcp__company__"
logger = logging.getLogger("company_os.runtime.cli.claude")

# --- plan limits and login expiry (B9). Texts quoted from https://code.claude.com/docs/en/errors and
# https://code.claude.com/docs/en/headless (read 2026-10-28); see docs/cli-runtimes.md.
USAGE_LIMIT = "usage_limit"
LOGIN_EXPIRED = "login_expired"
USAGE_LIMIT_FALLBACK = timedelta(hours=1)
MAX_PAUSE = timedelta(days=8)
RESET_SLACK = timedelta(minutes=1)
ALERT_USAGE_KEY = "claude_plan:usage_limit"
ALERT_LOGIN_KEY = "claude_plan:login_expired"
_USAGE_RE = re.compile(
    r"you['\u2019]?ve hit your ([a-z0-9 ]{1,20}?) limit|usage limit reached|hit your usage limit",
    re.I,
)
_RESET_RE = re.compile(
    r"resets?\s+(?:at\s+)?(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m",
    re.I,
)
_EPOCH_RE = re.compile(r"usage limit reached\|(\d{9,13})", re.I)
_LOGIN_PHRASES = (
    "login expired",
    "oauth session expired",
    "oauth token has expired",
    "oauth token revoked",
    "not logged in",
    "please run /login",
    "authentication required",
    "invalid authentication credentials",
    "please login again",
    "failed to authenticate",
)
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class PlanEvent:
    kind: str  # USAGE_LIMIT | LOGIN_EXPIRED
    resets_at: datetime | None = None  # USAGE_LIMIT only (UTC); None = unknown (fallback applies)
    window: str | None = None  # "session", "weekly", "Opus", ...


def parse_reset(text: str, now: datetime) -> datetime | None:
    """The reset instant in ``text`` ("resets 3:45pm", "resets Mon 12:00am"; Claude Code prints the
    machine's local time), as UTC. None when absent or implausible (more than 8 days away)."""
    m = _EPOCH_RE.search(text)
    if m:
        value = int(m.group(1))
        value = value // 1000 if value > 10**11 else value
        found = datetime.fromtimestamp(value, UTC)
    else:
        m = _RESET_RE.search(text)
        if not m:
            return None
        day, hour, minute, ap = (
            m.group(1),
            int(m.group(2)),
            int(m.group(3) or 0),
            m.group(4).lower(),
        )
        if not (1 <= hour <= 12 and 0 <= minute < 60):
            return None
        local_now = now.astimezone()
        hour24 = hour % 12 + (12 if ap == "p" else 0)
        cand = local_now.replace(hour=hour24, minute=minute, second=0, microsecond=0)
        if day:
            cand += timedelta(days=(_WEEKDAYS.index(day.lower()[:3]) - local_now.weekday()) % 7)
            if cand <= local_now:
                cand += timedelta(days=7)
        elif cand <= local_now:
            cand += timedelta(days=1)
        found = cand.astimezone(UTC)
    if found <= now or found - now > MAX_PAUSE:
        return None
    return found


def detect_plan_event(
    text: str | None, category: str | None, now: datetime | None = None
) -> PlanEvent | None:
    """Classify a failed Claude Code run: a plan usage limit, an expired login, or neither (None)."""
    now = now or utcnow()
    body = text or ""
    low = body.lower()
    m = _USAGE_RE.search(body)
    if m:
        window = (m.group(1) or "").strip() or None
        return PlanEvent(USAGE_LIMIT, parse_reset(body, now), window)
    if (category or "").lower() == "authentication_failed" or any(p in low for p in _LOGIN_PHRASES):
        return PlanEvent(LOGIN_EXPIRED)
    return None


def pause_until(event: PlanEvent, now: datetime) -> datetime:
    if event.resets_at is not None:
        return event.resets_at + RESET_SLACK
    return now + USAGE_LIMIT_FALLBACK


def usage_limit_message(until: datetime, window: str | None = None) -> str:
    which = f" ({window} limit)" if window else ""
    return f"Your Claude plan hit its usage limit{which}; resumes at {format_ts(until)}"


LOGIN_EXPIRED_MESSAGE = "Sign in to Claude again (Claude Code reports that the login expired)"


class ClaudeParser(StreamParser):
    def __init__(self, prep: RunPrep, parsed: Parsed) -> None:
        super().__init__(prep, parsed)
        self._tool_names: dict[str, str] = {}

    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = obj.get("type")
        out: list[tuple[str, dict[str, Any]]] = []
        if kind == "system" and obj.get("subtype") == "api_retry":
            if isinstance(obj.get("error"), str):
                self.parsed.error_category = obj["error"]
        elif kind == "assistant":
            if isinstance(obj.get("error"), str):
                self.parsed.error_category = obj["error"]
            for block in (obj.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    self.parsed.final_text = str(block["text"])
                    out.append(("message", {"text": clip(block["text"], 2000)}))
                elif block.get("type") == "tool_use":
                    name = str(block.get("name") or "tool")
                    if block.get("id"):
                        self._tool_names[str(block["id"])] = name
                    out.append(
                        (
                            "tool.started",
                            {
                                "name": name.removeprefix(MCP_PREFIX),
                                "tool": name,
                                "input_preview": clip(
                                    json.dumps(block.get("input"), default=str), 300
                                ),
                            },
                        )
                    )
        elif kind == "user":
            content = (obj.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name = self._tool_names.get(str(block.get("tool_use_id")), "tool")
                    result = block.get("content")
                    if isinstance(result, list):
                        result = " ".join(
                            str(b.get("text", "")) for b in result if isinstance(b, dict)
                        )
                    out.append(
                        (
                            "tool.completed",
                            {
                                "name": name.removeprefix(MCP_PREFIX),
                                "is_error": bool(block.get("is_error")),
                                "result_preview": clip(result, 300),
                            },
                        )
                    )
        elif kind == "result":
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}

            def n(key: str) -> int:
                v = usage.get(key)
                return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

            self.parsed.input_tokens = (
                n("input_tokens") + n("cache_read_input_tokens") + n("cache_creation_input_tokens")
            )
            self.parsed.output_tokens = n("output_tokens")
            cost = obj.get("total_cost_usd")
            if isinstance(cost, int | float) and not isinstance(cost, bool) and cost >= 0:
                self.parsed.cost_usd = float(cost)
            text = obj.get("result")
            if isinstance(text, str) and text.strip():
                self.parsed.final_text = text
            if obj.get("is_error") or str(obj.get("subtype") or "success") != "success":
                self.parsed.error = clip(
                    (text if obj.get("is_error") and text else None)
                    or obj.get("subtype")
                    or "error",
                    400,
                )
            else:
                self.parsed.finished = True
        return out


class ClaudeCodeAdapter(CliAdapter):
    spec = CLIS["claude_code"]
    parser_class = ClaudeParser
    tool_prefix = MCP_PREFIX
    supports_sessions = True  # M18 Part B: --session-id / --resume (see session_args)
    config_hint = "Install with `npm install -g @anthropic-ai/claude-code`; sign in with `foundryos ai login`."

    def check_health(self) -> RuntimeHealth:
        """Own-login also needs a signed-in Claude Code (``claude auth status`` through a 30 s cache)."""
        health = super().check_health()
        if health.status is not HealthStatus.OK:
            return health
        with self._session_factory() as db:
            from app.runtimes.cli import store

            mode = store.load(db, self.spec.id).get("auth_mode")
        if mode == "own_login":
            from app.services import cli_runtimes

            paused = self._plan_pause()
            if paused is not None:
                return paused
            login = cli_runtimes.claude_login_cached(self.cli_env, self._settings.resolved_data_dir)
            if not login["signed_in"]:
                return RuntimeHealth(
                    HealthStatus.NOT_CONFIGURED,
                    "Claude Code is not signed in. Sign in with your Claude account "
                    "(Settings > AI, or `foundryos ai login`).",
                )
        return health

    # ------------------------------------------------------------ plan limits (B9)

    def _plan_pause(self) -> RuntimeHealth | None:
        """Unhealthy while the plan's usage limit is in force, or after Claude Code said the login
        expired (cleared by the next successful sign-in)."""
        from app.runtimes.cli import store

        with self._session_factory() as db:
            state = store.load(db, self.spec.id)
        until = _parse_ts(state.get("paused_until"))
        if until is not None and utcnow() < until:
            return RuntimeHealth(
                HealthStatus.UNAVAILABLE, str(state.get("paused_reason") or "Claude plan paused")
            )
        if state.get("login_expired"):
            return RuntimeHealth(HealthStatus.NOT_CONFIGURED, LOGIN_EXPIRED_MESSAGE)
        return None

    def _finish(self, prep, code, parsed, stderr_tail, cancel, killed, before, emit):  # type: ignore[override]
        result = super()._finish(prep, code, parsed, stderr_tail, cancel, killed, before, emit)
        if prep.auth_mode != "own_login":
            return result
        try:
            if result.status == "succeeded":
                self._plan_recovered(prep)
                return result
            if result.status != "failed" or cancel.is_set():
                return result
            text = parsed.error or ""
            if not text and code not in (0, None):
                text = " ".join([parsed.final_text, *list(stderr_tail)])
            event = detect_plan_event(text, parsed.error_category)
            if event is None:
                return result
            message = self._apply_plan_event(prep, event)
        except Exception:  # classification must never turn a failed run into a crash
            logger.exception("could not record a Claude plan event")
            return result
        return dataclasses.replace(
            result, error=ExecutionError(RuntimeErrorKind.FAILED, message, retryable=True)
        )

    def _apply_plan_event(self, prep: RunPrep, event: PlanEvent) -> str:
        """Pause the runtime (usage limit) or mark the login expired, raise an alert, return the
        run's failure message. Both outcomes are transient for the retry policy: the task waits."""
        from app.models.enums import AlertSeverity
        from app.models.work import Task
        from app.runtimes.cli import store
        from app.services import alerts, cli_runtimes

        now = utcnow()
        if event.kind == USAGE_LIMIT:
            until = pause_until(event, now)
            message = usage_limit_message(until, event.window)
            patch: dict[str, Any] = {"paused_until": format_ts(until), "paused_reason": message}
            title, key = "Your Claude plan hit its usage limit", ALERT_USAGE_KEY
        else:
            message = LOGIN_EXPIRED_MESSAGE
            patch = {"login_expired": True}
            title, key = "Sign in to Claude again", ALERT_LOGIN_KEY
            cli_runtimes.invalidate_claude_login(self.cli_env, self._settings.resolved_data_dir)
        if prep.ai_connection_id:  # one alert per connection: resolved when its next run succeeds
            key = f"{key}:{prep.ai_connection_id}"
        with self._session_factory() as db:
            if prep.ai_connection_id:  # M17: the pause belongs to this connection, not the install
                from app.services import ai_stack

                ai_stack.record_plan_event(
                    db,
                    prep.ai_connection_id,
                    "usage_limit" if event.kind == USAGE_LIMIT else "login_expired",
                    until=until if event.kind == USAGE_LIMIT else None,
                    message=message,
                )
            else:
                store.save(db, self.spec.id, **patch)
            task = db.get(Task, prep.task_id)
            if task is not None:
                alerts.raise_alert(
                    db,
                    task.company_id,
                    kind="claude_plan_limit"
                    if event.kind == USAGE_LIMIT
                    else "claude_login_expired",
                    severity=AlertSeverity.WARNING,
                    title=title,
                    body=message + ". Work waits for Claude Code and resumes on its own.",
                    related_type="runtime",
                    related_id=self.spec.runtime_id,
                    dedupe_key=key,
                    wake_founder=False,  # the Co-founder runs on the same plan
                )
            db.commit()
        return message

    def _plan_recovered(self, prep: RunPrep) -> None:
        """A run succeeded: the limit is over and the login works. Clear stale pause state and alerts."""
        from app.models.work import Task
        from app.runtimes.cli import store
        from app.services import alerts

        with self._session_factory() as db:
            if prep.ai_connection_id:
                from app.services import ai_stack

                ai_stack.clear_plan_event(db, prep.ai_connection_id)
                task = db.get(Task, prep.task_id)
                if task is not None:
                    for key in (ALERT_USAGE_KEY, ALERT_LOGIN_KEY):
                        alerts.resolve_by_key(db, task.company_id, f"{key}:{prep.ai_connection_id}")
                db.commit()
                return
            state = store.load(db, self.spec.id)
            if not (state.get("paused_until") or state.get("login_expired")):
                return
            store.save(db, self.spec.id, paused_until=None, paused_reason=None, login_expired=None)
            task = db.get(Task, prep.task_id)
            if task is not None:
                for key in (ALERT_USAGE_KEY, ALERT_LOGIN_KEY):
                    alerts.resolve_by_key(db, task.company_id, key)
            db.commit()

    def classify_failure(self, parsed: Parsed) -> str:
        err = parsed.error or ""
        if "max_budget" in err:
            return "Claude Code stopped at its budget cap (--max-budget-usd)"
        return f"Claude Code reported an error: {err}"

    @staticmethod
    def session_args(prep: RunPrep) -> list[str] | None:
        """M18 Part B: the persistent-session flags of a work-graph act, None for a one-shot run.
        ``--session-id`` starts the conversation under our uuid, ``--resume`` continues it (it only
        exists in the CLAUDE_CONFIG_DIR of the connection that started it). A session replaces
        ``--no-session-persistence``: the two never go together."""
        if prep.purpose != "work" or prep.mode != "run" or not prep.session_mode:
            return None
        try:
            session_id = str(uuid.UUID(str(prep.session_id)))
        except ValueError:
            return None
        if prep.session_mode == "new":
            return [f"--session-id={session_id}"]
        if prep.session_mode == "resume":
            return ["--resume", session_id]
        return None

    def build(self, prep: RunPrep) -> Invocation:
        session = self.session_args(prep)
        argv = [
            prep.binary,
            "-p",
            prep.prompt,
            "--output-format=stream-json",
            "--verbose",
            *(session if session else ["--no-session-persistence"]),
            "--restricted",
            "--disable-slash-commands",
            "--permission-mode=dontAsk",
        ]
        if prep.auth_mode == "api_key":
            argv.append("--bare")  # no hooks, no auto-memory, no CLAUDE.md, API key only
        if prep.budget_usd:
            argv.append(f"--max-budget-usd={max(prep.budget_usd, 0.01):.4f}")
        if prep.model:
            argv.append(f"--model={prep.model}")
        env = self.child_env(prep)
        env["DISABLE_AUTOUPDATER"] = "1"
        env["DISABLE_TELEMETRY"] = "1"
        config_text = ""
        if prep.mode == "test":
            argv += ["--tools="]  # no tools at all
        else:
            token_file = self.write_token_file(prep)
            config = {
                "mcpServers": {
                    "company": {
                        "command": self.mcp_command(prep)[0],
                        "args": self.mcp_command(prep)[1:],
                        "env": self.mcp_server_env(prep, token_file),
                    }
                }
            }
            config_text = json.dumps(config)
            path = prep.run_dir / "mcp.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(config_text)
            argv += [
                f"--mcp-config={path}",
                "--strict-mcp-config",
                "--tools=" + ",".join(FILE_TOOLS),
                "--allowedTools=" + ",".join(("mcp__company", *FILE_TOOLS)),
                "--disallowedTools=" + ",".join(DENIED_TOOLS),
            ]
            config_text = ""  # holds paths and the API url only; nothing to scan
        if prep.auth_mode == "api_key":
            env["ANTHROPIC_API_KEY"] = prep.secret or ""
            env["CLAUDE_CONFIG_DIR"] = str(prep.home / ".claude")
        else:  # own_login: Foundry's own config dir; HOME stays the real home (macOS Keychain access)
            env["CLAUDE_CONFIG_DIR"] = str(
                prep.config_dir or claude_config_dir(self._settings.resolved_data_dir)
            )
        return Invocation(
            argv=argv, env=env, cwd=prep.workspace, prompt_index=2, config_text=config_text
        )


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
