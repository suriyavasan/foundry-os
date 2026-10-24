"""Builds the Hermes ``AIAgent`` for a run spec and drives it, translating Hermes callbacks into
protocol events and enforcing the hard limits (time, cancellation).

Hermes is imported lazily inside :class:`Runner` methods: ``company_agent.run`` must first have
prepared the process environment (HERMES_HOME, jail, scrubbed env) and the fd plumbing.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from company_agent import context as tool_context
from company_agent import prompts
from company_agent.approvals import make_approval_callback
from company_agent.protocol import PREVIEW_CHARS, REASONING_CHARS, EventWriter, truncate
from company_agent.spec import RunSpec

log = logging.getLogger("company_agent")

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2
EXIT_BAD_SPEC = 3
EXIT_CANCELLED = 130

MAX_COMPLETION_NUDGES = 2
# After a stop request the agent gets this long to unwind before the launcher gives up on it.
UNWIND_GRACE_SECONDS = 15.0
# Fraction of max_run_seconds after which Hermes' soft budget tells the model to wrap up.
SOFT_BUDGET_FRACTION = 0.8

USAGE_FIELDS = (
    ("input_tokens", "session_input_tokens"),
    ("output_tokens", "session_output_tokens"),
    ("cache_read_tokens", "session_cache_read_tokens"),
    ("cache_write_tokens", "session_cache_write_tokens"),
    ("reasoning_tokens", "session_reasoning_tokens"),
    ("api_calls", "session_api_calls"),
)


@dataclass
class Outcome:
    """What the worker thread concluded; the main thread turns it into the terminal event."""

    status: str = "failed"  # completed | failed | cancelled
    exit_reason: str = "error"
    exit_code: int = EXIT_FAILED
    summary: str = ""
    error: Optional[str] = None
    partial: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def usage_snapshot(agent: Any, spec: RunSpec) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, attr in USAGE_FIELDS:
        try:
            out[key] = int(getattr(agent, attr, 0) or 0) if agent is not None else 0
        except (TypeError, ValueError):
            out[key] = 0
    cost = getattr(agent, "session_estimated_cost_usd", None) if agent is not None else None
    out["estimated_cost_usd"] = float(cost) if isinstance(cost, (int, float)) else None
    out["cost_status"] = str(getattr(agent, "session_cost_status", "unknown") or "unknown") if agent is not None else "unknown"
    out["cost_source"] = str(getattr(agent, "session_cost_source", "none") or "none") if agent is not None else "none"
    out["model"] = getattr(agent, "model", None) or spec.model.model
    out["provider"] = getattr(agent, "provider", None) or spec.model.provider
    return out


class Runner:
    def __init__(self, spec: RunSpec, writer: EventWriter, toolsets: list[str]):
        self.spec = spec
        self.writer = writer
        self.toolsets = toolsets
        self.agent: Any = None
        self.cancel_requested = threading.Event()
        self.stop_reason: Optional[str] = None  # "cancelled" | "time_limit"
        self._tool_started: dict[str, float] = {}
        self._tool_lock = threading.Lock()
        self._last_usage: Optional[tuple] = None
        self._interrupted = False
        self._finish_requested = False
        self._ctx_probed = False
        self._finish_summary = ""
        self.outcome: Optional[Outcome] = None
        self._worker: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ callbacks

    def _on_tool_start(self, call_id: Any, name: Any, args: Any) -> None:
        cid = str(call_id or "")
        with self._tool_lock:
            self._tool_started[cid] = time.monotonic()
        self.writer.emit("tool.started", {
            "tool_call_id": cid, "name": str(name or "tool"),
            "args_preview": self.writer.preview(args if isinstance(args, str) else _jsonable(args)),
        })

    def _on_tool_complete(self, call_id: Any, name: Any, args: Any, result: Any) -> None:
        cid = str(call_id or "")
        with self._tool_lock:
            started = self._tool_started.pop(cid, None)
        text = result if isinstance(result, str) else _jsonable(result)
        is_error = tool_result_is_error(str(name or ""), text)
        self.writer.emit("tool.completed", {
            "tool_call_id": cid, "name": str(name or "tool"),
            "duration_ms": int((time.monotonic() - started) * 1000) if started is not None else None,
            "is_error": is_error,
            "result_preview": self.writer.preview(text, PREVIEW_CHARS),
        })
        if (not is_error and str(name) in ("company_complete_task", "company_update_task", "company_escalate")
                and self.disposition()):
            self._finish_after_disposition(args)

    def _finish_after_disposition(self, args: Any) -> None:
        """The task was completed (or blocked) through the API: that is the end of the run. Small
        models otherwise keep calling tools afterwards. Stops the loop with a plain hard interrupt."""
        if self._finish_requested:
            return
        self._finish_requested = True
        if isinstance(args, dict):
            self._finish_summary = str(args.get("result_summary") or args.get("reason") or "").strip()
        agent = self.agent
        if agent is None:
            return
        try:
            from agent.interrupt_compat import request_hard_interrupt

            request_hard_interrupt(agent)
        except Exception:
            log.exception("could not stop the agent after task disposition")

    def _on_message(self, text: Any, already_streamed: bool = False, **_: Any) -> None:
        if isinstance(text, str) and text.strip():
            self.writer.emit("message", {"text": text.strip(), "final": False})

    def _on_reasoning(self, text: Any, *_: Any, **__: Any) -> None:
        if isinstance(text, str) and text.strip():
            self.writer.emit("reasoning", {"text": truncate(text.strip(), REASONING_CHARS)})

    def _on_step(self, api_call_count: Any = None, *_: Any, **__: Any) -> None:
        self.emit_usage()
        if api_call_count == 2 and self.spec.model.is_local and not self._ctx_probed:
            self._ctx_probed = True
            threading.Thread(target=self._probe_served_context, name="company-agent-ctx-probe", daemon=True).start()

    def _probe_served_context(self) -> None:
        """Ollama ignores ``options.num_ctx`` on its OpenAI-compatible endpoint and serves its own
        default (often 4096): prompts longer than that are silently truncated from the front, which
        removes the system prompt and tool schemas. Ask the server what it really loaded and warn."""
        served = probe_ollama_context(self.spec.model.base_url, self.spec.model.model)
        if served is not None and served < self.spec.model.context_length:
            self.writer.emit("progress", {"message": (
                f"WARNING: the model server is serving a {served}-token context window, not the "
                f"{self.spec.model.context_length} this run was configured for. Long prompts will be silently "
                "truncated and the agent will behave erratically. For Ollama start the server with "
                f"OLLAMA_CONTEXT_LENGTH={self.spec.model.context_length} (the OpenAI-compatible endpoint "
                "ignores per-request num_ctx).")})

    def emit_usage(self, force: bool = False) -> None:
        usage = usage_snapshot(self.agent, self.spec)
        key = tuple(usage[k] for k, _ in USAGE_FIELDS)
        if force or key != self._last_usage:
            self._last_usage = key
            self.writer.emit("usage", usage)

    def _on_approval_denied(self, command: str, reason: str) -> None:
        self.writer.emit("progress", {"message": f"Action denied by policy: {truncate(command, 200)}. {reason}"})

    # ------------------------------------------------------------------ build

    def build_agent(self) -> Any:
        from run_agent import AIAgent  # noqa: PLC0415  (deferred: environment must be prepared first)

        spec, m = self.spec, self.spec.model
        agent = AIAgent(
            base_url=m.base_url or None,
            api_key=m.api_key,
            provider=m.provider,
            model=m.model,
            max_iterations=spec.max_iterations,
            enabled_toolsets=list(self.toolsets),
            quiet_mode=True,
            platform="company",
            ephemeral_system_prompt=prompts.build_task_prompt(spec),
            skip_memory=True,
            skip_context_files=True,
            skip_background_review=True,
            load_soul_identity=True,
            session_db=None,
            run_budget_seconds=max(30.0, spec.max_run_seconds * SOFT_BUDGET_FRACTION),
            tool_start_callback=self._on_tool_start,
            tool_complete_callback=self._on_tool_complete,
            interim_assistant_callback=self._on_message,
            reasoning_callback=self._on_reasoning,
            step_callback=self._on_step,
            clarify_callback=lambda *a, **k: "No human is available. Decide yourself and state your assumption.",
        )
        # Hermes prints status lines; the protocol has its own events.
        agent.suppress_status_output = True
        agent.stream_delta_callback = None
        agent.tool_gen_callback = None
        self.agent = agent
        return agent

    # ------------------------------------------------------------------ run

    def start(self) -> None:
        self._worker = threading.Thread(target=self._work, name="company-agent-run", daemon=True)
        self._worker.start()

    @property
    def worker_alive(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def request_stop(self, reason: str) -> None:
        """Called from the launcher thread (signal -> event loop), never from a signal handler."""
        if self.stop_reason is None:
            self.stop_reason = reason
        self.cancel_requested.set()
        agent = self.agent
        if agent is None or self._interrupted:
            return
        self._interrupted = True
        try:
            agent.interrupt(f"Run stopped: {reason}", hard_cancel=True)
        except Exception:
            log.exception("interrupt failed")

    def join(self, timeout: float) -> bool:
        if self._worker is not None:
            self._worker.join(timeout)
        return not self.worker_alive

    def _work(self) -> None:
        try:
            self.outcome = self._work_inner()
        except BaseException as exc:  # noqa: BLE001  (the main thread must always get an outcome)
            log.exception("run crashed")
            self.outcome = Outcome(status="failed", exit_reason="error", exit_code=EXIT_FAILED,
                                   error=f"{type(exc).__name__}: {exc}")

    def _work_inner(self) -> Outcome:
        spec = self.spec
        # Approval plumbing is per-thread: it must be set on the thread that runs the conversation.
        from tools.approval_context import set_hermes_interactive_context
        from tools.terminal_tool import set_approval_callback

        set_hermes_interactive_context(True)
        set_approval_callback(make_approval_callback(spec.sandbox_backend, self._on_approval_denied))

        agent = self.build_agent()
        if self.cancel_requested.is_set():
            return Outcome(status="cancelled", exit_reason=self.stop_reason or "cancelled",
                           exit_code=EXIT_CANCELLED, summary="Run cancelled before it started.")

        result: dict[str, Any] = agent.run_conversation(prompts.build_user_message(spec))
        self._emit_final_message(result)
        nudges = 0
        while self._wants_nudge(result, nudges):
            nudges += 1
            self.writer.emit("progress", {
                "message": f"Agent stopped without completing its task; reminding it ({nudges}/{MAX_COMPLETION_NUDGES})."})
            result = agent.run_conversation(prompts.build_completion_nudge(spec),
                                            conversation_history=result.get("messages"))
            self._emit_final_message(result)
        return self._outcome_from(result)

    def _emit_final_message(self, result: dict[str, Any]) -> None:
        """The model's closing text of a conversation turn (mid-turn commentary arrives via the interim
        callback). Skipped when the run was stopped by us: Hermes substitutes a placeholder then."""
        if self._finish_requested or self.cancel_requested.is_set() or result.get("interrupted") or result.get("failed"):
            return
        text = str(result.get("final_response") or "").strip()
        if text:
            self.writer.emit("message", {"text": text, "final": True})

    # ------------------------------------------------------------------ outcome

    def disposition(self) -> Optional[str]:
        return tool_context.dispositions().get(str(self.spec.task["id"]))

    def _wants_nudge(self, result: dict[str, Any], nudges: int) -> bool:
        if (nudges >= MAX_COMPLETION_NUDGES or self.cancel_requested.is_set() or self._finish_requested
                or self.disposition()):
            return False
        if "company" not in self.toolsets:
            return False
        if result.get("interrupted") or result.get("failed"):
            return False
        reason = str(result.get("turn_exit_reason") or "")
        if reason.startswith("max_iterations_reached"):
            return False
        return True

    def _outcome_from(self, result: dict[str, Any]) -> Outcome:
        final = str(result.get("final_response") or "").strip()
        reason = str(result.get("turn_exit_reason") or "")
        disp = self.disposition()
        if self._finish_requested and disp:
            # Finished through the API; whatever the model said afterwards (interrupted placeholder) is noise.
            return Outcome(status="completed", exit_reason="completed", exit_code=EXIT_COMPLETED,
                           summary=self._finish_summary or f"Task {disp}.", extra={"task_disposition": disp})
        if self.stop_reason == "time_limit":
            return Outcome(status="failed", exit_reason="time_limit", exit_code=EXIT_PARTIAL, partial=True,
                           summary=final or "Run stopped: time limit reached.",
                           error=f"The run exceeded its {self.spec.max_run_seconds}s time limit.")
        if self.stop_reason is not None or (result.get("interrupted") and self.cancel_requested.is_set()):
            return Outcome(status="cancelled", exit_reason=self.stop_reason or "cancelled",
                           exit_code=EXIT_CANCELLED, summary=final or "Run cancelled.")
        if result.get("failed"):
            err = str(result.get("error") or final or reason or "the model request failed")
            return Outcome(status="failed", exit_reason="model_error", exit_code=EXIT_FAILED,
                           summary=final, error=err)
        if reason.startswith("max_iterations_reached"):
            return Outcome(status="failed", exit_reason="max_iterations", exit_code=EXIT_PARTIAL, partial=True,
                           summary=final or "Iteration limit reached.",
                           error=f"The run used all {self.spec.max_iterations} iterations without finishing.")
        if disp:
            return Outcome(status="completed", exit_reason="completed", exit_code=EXIT_COMPLETED,
                           summary=final or f"Task {disp}.", extra={"task_disposition": disp})
        return Outcome(status="failed", exit_reason="task_not_completed", exit_code=EXIT_FAILED,
                       summary=final,
                       error="The agent stopped without calling company_complete_task "
                             "(or blocking the task) after being reminded.")

    def close(self) -> None:
        agent = self.agent
        if agent is None:
            return
        done = threading.Event()

        def _close() -> None:
            try:
                agent.close()
            except Exception:
                log.debug("agent.close failed", exc_info=True)
            finally:
                done.set()

        threading.Thread(target=_close, name="company-agent-close", daemon=True).start()
        done.wait(10.0)


def probe_ollama_context(base_url: str, model: str, timeout: float = 3.0) -> Optional[int]:
    """Context window an Ollama server actually loaded ``model`` with (GET /api/ps), or None when the
    server is not Ollama / the model is not loaded / the probe fails."""
    import json
    from urllib.parse import urlparse
    from urllib.request import ProxyHandler, build_opener

    try:
        u = urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return None
        origin = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"{origin}/api/ps", timeout=timeout) as resp:
            data = json.loads(resp.read(1_000_000))
        for m in data.get("models", []):
            if model in (m.get("name"), m.get("model")) and isinstance(m.get("context_length"), int):
                return int(m["context_length"])
    except Exception:
        return None
    return None


def _jsonable(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def tool_result_is_error(name: str, text: str) -> bool:
    """Tool results are JSON strings; an ``error`` key (or success=false) marks a failure."""
    import json

    from company_agent.guard import strip_untrusted

    text = strip_untrusted(text) if isinstance(text, str) else text
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text.lstrip().lower().startswith(("error", "tool execution failed"))
    if isinstance(data, dict):
        err = data.get("error")
        # Company API bodies (a Task) carry their own `error` object/null; only a string code is a failure.
        if err and not (name.startswith("company_") and not isinstance(err, str)):
            return True
        if data.get("success") is False:
            return True
    return False
