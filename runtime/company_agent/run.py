"""Company OS agent launcher: ``python -m company_agent.run``.

Protocol (docs/specs/m3-company-agent-runtime.md, A3):

* stdin   one JSON run spec (never argv / env / disk: the agent's own tools could read those)
* stdout  JSONL events, one per line (see ``company_agent.protocol``)
* stderr  redacted logs
* exit    0 completed, 1 failed, 2 partial (iteration / time limit), 3 bad spec, 130 cancelled

Order of operations matters: fd plumbing -> read spec -> scrub environment + prepare HERMES_HOME
-> only then import Hermes (which reads .env / config.yaml / SOUL.md from HERMES_HOME at import).

SIGTERM / SIGINT only set a flag; the main thread performs the interrupt (calling
``agent.interrupt`` from inside a signal handler could deadlock on Hermes' locks).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from company_agent import PROTOCOL_VERSION, UPSTREAM_COMMIT, __version__
from company_agent.protocol import EventWriter, Redactor
from company_agent.spec import RunSpec, SpecError, load_spec_from_stdin

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2
EXIT_BAD_SPEC = 3
EXIT_CANCELLED = 130

RUNTIME_ROOT = str(Path(__file__).resolve().parent.parent)
log = logging.getLogger("company_agent")


class _RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor):
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._redactor.text(record.getMessage())
            record.args = None
            if record.exc_info:
                import traceback

                record.msg += "\n" + self._redactor.text("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
                record.exc_text = None
        except Exception:
            pass
        return True


def _claim_stdout() -> Any:
    """Dup the real stdout for the protocol writer, then point fd 1 and sys.stdout at stderr so
    nothing Hermes (or a subprocess) prints can corrupt the JSONL stream."""
    real = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1, newline="\n")
    sys.stdout.flush()
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real


def _detach_stdin() -> None:
    """Spec consumed: the agent (and any subprocess it starts) must not be able to read stdin."""
    try:
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.close(devnull)
    except OSError:
        pass


def _harden_sys_path() -> None:
    """Import only from the runtime tree: drop '' / cwd entries so a file in the (agent-writable)
    workspace can never shadow a module."""
    cwd = os.getcwd()
    cleaned = [p for p in sys.path if p not in ("", ".", cwd)]
    if RUNTIME_ROOT in cleaned:
        cleaned.remove(RUNTIME_ROOT)
    sys.path[:] = [RUNTIME_ROOT, *cleaned]


def _install_signal_flag(flag: threading.Event, which: list[str]) -> None:
    def handler(signum: int, _frame: Any) -> None:
        which.append(signal.Signals(signum).name)
        flag.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _setup_logging(redactor: Redactor) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactingFilter(redactor))
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    log.setLevel(logging.INFO)


def _fail_early(writer: EventWriter, exit_reason: str, message: str, code: int) -> int:
    writer.emit("run.failed", {
        "summary": "", "exit_reason": exit_reason, "error": message,
        "usage": _empty_usage(None),
    })
    return code


def _empty_usage(spec: Optional[RunSpec]) -> dict[str, Any]:
    return {
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "reasoning_tokens": 0, "api_calls": 0, "estimated_cost_usd": None,
        "cost_status": "unknown", "cost_source": "none",
        "model": spec.model.model if spec else None, "provider": spec.model.provider if spec else None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(json.dumps({"company_agent": __version__, "protocol": PROTOCOL_VERSION,
                          "upstream_hermes_commit": UPSTREAM_COMMIT}))
        return 0

    _harden_sys_path()
    out = _claim_stdout()
    stop_flag = threading.Event()
    signals_seen: list[str] = []
    _install_signal_flag(stop_flag, signals_seen)

    writer = EventWriter(out, on_broken=stop_flag.set)
    _setup_logging(writer.redactor)

    # ---- 1. spec ------------------------------------------------------------------------
    try:
        spec = load_spec_from_stdin()
    except SpecError as exc:
        log.error("bad run spec: %s", exc)
        return _fail_early(writer, "bad_spec", "Invalid run spec: " + "; ".join(exc.problems), EXIT_BAD_SPEC)
    finally:
        _detach_stdin()
    writer.add_secrets(spec.secrets)

    # ---- 2. environment + home (before ANY Hermes import) ---------------------------------
    from company_agent import home, prompts, toolpolicy

    toolsets, warnings = toolpolicy.resolve_toolsets(spec)
    try:
        home.ensure_dirs(spec)
        home.remove_dotenv(spec)
        home.apply_process_env(spec)
        home.write_soul(spec, prompts.build_identity_prompt(spec))
        home.write_config(spec, toolsets)
        os.chdir(spec.workspace_dir)
    except OSError as exc:
        return _fail_early(writer, "setup_failed", f"Could not prepare the run directories: {exc}", EXIT_FAILED)

    writer.emit("run.started", {
        "run_id": spec.run_id,
        "protocol": PROTOCOL_VERSION,
        "runtime": {"name": "company_agent", "version": __version__, "upstream_hermes_commit": UPSTREAM_COMMIT},
        **spec.public_dict(),
        "toolsets": toolsets,
    })
    for w in warnings:
        writer.emit("progress", {"message": w})

    # ---- 3. Hermes ------------------------------------------------------------------------
    from company_agent import context as tool_context
    from company_agent.runner import UNWIND_GRACE_SECONDS, Outcome, Runner, usage_snapshot

    runner = Runner(spec, writer, toolsets)
    tool_context.configure(
        spec.agent_api_url, spec.agent_api_token,
        run_id=spec.run_id, task_id=str(spec.task["id"]), workspace_dir=spec.workspace_dir,
        on_artifact=lambda artifact_id, title: writer.emit("artifact", {"artifact_id": artifact_id, "title": title}),
        allowed_tools=spec.company_tools,
        task_kind=str(spec.task.get("kind") or ""),
    )

    # Milestone 6: tool registry + untrusted-content hooks on Hermes' plugin lifecycle.
    from company_agent import guard as guardrails

    tool_guard = guardrails.build_guard(
        spec.guardrails, notify=lambda message: writer.emit("progress", {"message": message}))
    if tool_guard is not None:
        guardrails.install(tool_guard)
    else:
        writer.emit("progress", {"message": "Guardrails are not active: the control plane sent no "
                                            "guardrails block (older than Milestone 6)."})

    started = time.monotonic()
    deadline = started + spec.max_run_seconds
    runner.start()
    while runner.worker_alive:
        if stop_flag.wait(0.2):
            if signals_seen:
                log.warning("received %s; stopping the run", signals_seen[0])
            runner.request_stop("cancelled")
            break
        if time.monotonic() >= deadline:
            log.warning("run exceeded %ss; stopping", spec.max_run_seconds)
            runner.request_stop("time_limit")
            break
    if runner.stop_reason is not None:
        if not runner.join(UNWIND_GRACE_SECONDS):
            log.error("agent did not unwind within the grace period; abandoning it")
    else:
        runner.join(5.0)

    outcome: Optional[Outcome] = runner.outcome
    if outcome is None:  # worker still stuck after a stop request
        stop = runner.stop_reason or "cancelled"
        if stop == "time_limit":
            outcome = Outcome(status="failed", exit_reason="time_limit", exit_code=EXIT_PARTIAL, partial=True,
                              summary="Run stopped: time limit reached.",
                              error=f"The run exceeded its {spec.max_run_seconds}s time limit.")
        else:
            outcome = Outcome(status="cancelled", exit_reason="cancelled", exit_code=EXIT_CANCELLED,
                              summary="Run cancelled.")

    usage = usage_snapshot(runner.agent, spec)
    if runner.agent is not None and not writer.broken:
        runner.emit_usage(force=True)
    payload: dict[str, Any] = {
        "summary": outcome.summary,
        "exit_reason": outcome.exit_reason,
        "usage": usage,
        "duration_ms": int((time.monotonic() - started) * 1000),
        **outcome.extra,
    }
    if outcome.error:
        payload["error"] = outcome.error
    if outcome.partial:
        payload["partial"] = True
    terminal = {"completed": "run.completed", "failed": "run.failed", "cancelled": "run.cancelled"}[outcome.status]
    writer.emit(terminal, payload)

    runner.close()
    tool_context.reset()
    return outcome.exit_code


def cli() -> None:
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    # Hard exit: Hermes may leave non-daemon threads (HTTP pools, MCP, background tasks) that would
    # otherwise hold the process open after the terminal event.
    os._exit(code)


if __name__ == "__main__":
    cli()
