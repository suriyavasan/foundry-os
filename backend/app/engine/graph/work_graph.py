"""M18 Part B: the durable work graph of one task (docs/specs/m18-low-friction-autonomy.md, B2).

::

    prepare -> act -> check -> finish
                ^  |     | self-check failed, revisions < 2
                |  |     +---------> act (same session, with the feedback)
                |  +- asked (blocking) --> wait_founder (interrupt) -> act
                |  +- needs approval ----> wait_approval (interrupt) -> act
                |  +- blocked / failed / cancelled / no proposal ----> finish

LangGraph only *orchestrates*: ``act`` calls the task's existing runtime adapter (Claude Code, the
other CLIs, or the company agent runtime) through ``Deps.run_act``. One thread per task
(``task:<id>``), checkpointed by ``SqlCheckpointSaver``; the state holds ids, counters and short
scrubbed summaries only -- never secrets or transcripts.

The ``check`` node (self-verification) is a second, tool-less ``adapter.execute`` with
``purpose="verify"`` on the same routed connection: its prompt carries the task, the acceptance
criteria and the proposed result, and it answers ``{"pass": bool, "feedback": str}``. It cannot
write anything: CLIs run it in their no-tools test mode, the company agent runtime in a
``verify`` mode without company tools. A call that fails or cannot be parsed is *inconclusive*
and never blocks the task.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app.runtimes.base import ExecutionResult, SessionSpec
from app.services import secrets_scan

MAX_REVISIONS = 2
MAX_WAITS = 5  # a task never parks more than this often in one thread
MAX_SUMMARY = 1500
MAX_FEEDBACK = 800
SESSION_LOST = "No conversation found"  # Claude Code's answer to --resume of an unknown session


class WorkState(TypedDict, total=False):
    task_id: str
    company_id: str
    agent_id: str
    acts: int
    revisions: int
    waits: int
    feedback: str | None  # self-check feedback the next act must address
    resume_text: str | None  # approval decisions (or other wake-up facts) for the next act
    context: str | None  # decisions already recorded for this task
    proposal: dict | None  # {"summary", "output_refs"} proposed through complete_task
    check: dict | None  # {"status": passed|failed|skipped|inconclusive, "feedback"}
    session: dict | None  # {"id", "connection_id", "runtime", "started"}
    summary: str  # short checkpoint summary (what happened so far)
    outcome: str | None
    waiting: str | None
    question_ids: list[str]
    approval_ids: list[str]
    result: dict | None  # the act's result: {"status", "summary", "error": {...}}
    final: dict | None


@dataclass
class ActInspection:
    task_in_progress: bool = True
    question_ids: list[str] = field(default_factory=list)
    approval_ids: list[str] = field(default_factory=list)


class Deps(Protocol):
    """Everything the nodes need from the outside (the executor supplies the real one)."""

    cancel: Any  # CancelToken
    supports_sessions: bool
    connection_id: str | None
    runtime_id: str
    check_allowed: bool  # policy.self_check_enabled, a work task, a runtime that can verify

    def node(self, node: str, outcome: str, **extra: Any) -> None: ...
    def context_text(self) -> str | None: ...
    def run_act(
        self, *, carry_extra: str | None, feedback: str | None, session: SessionSpec | None
    ) -> ExecutionResult: ...
    def clear_proposal(self) -> None: ...
    def read_proposal(self) -> dict | None: ...
    def inspect_after_act(self) -> ActInspection: ...
    def run_check(self, state: WorkState) -> dict: ...
    def apply_completion(
        self, proposal: dict, *, self_checked: bool, force_review: str | None
    ) -> bool: ...
    def approval_resume(self, approval_ids: list[str]) -> list[dict]: ...
    def question_resume(self, question_ids: list[str]) -> list[dict]: ...


# ---------------------------------------------------------------- helpers


def _clip(text: str | None, limit: int) -> str:
    text = (secrets_scan.scrub(" ".join((text or "").split())) or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _append_summary(old: str | None, line: str) -> str:
    text = f"{old}\n{line}".strip() if old else line
    return text if len(text) <= MAX_SUMMARY else "..." + text[-(MAX_SUMMARY - 3) :]


def result_to_dict(result: ExecutionResult) -> dict:
    err = result.error
    return {
        "status": result.status,
        "summary": _clip(result.summary, 1000) or None,
        "error": {"kind": str(err.kind), "message": err.message, "retryable": err.retryable}
        if err
        else None,
    }


def parse_verdict(text: str | None) -> dict:
    """``{"status": "passed"|"failed"|"inconclusive", "feedback": str}`` from the verifier."""
    raw = (text or "").strip()
    candidates = [raw]
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("pass"), bool):
            feedback = _clip(str(data.get("feedback") or ""), MAX_FEEDBACK)
            if data["pass"]:
                return {"status": "passed", "feedback": feedback}
            return {
                "status": "failed",
                "feedback": feedback or "The result does not meet the acceptance criteria.",
            }
    return {"status": "inconclusive", "feedback": ""}


# ---------------------------------------------------------------- the graph


def build_work_graph(deps: Deps, checkpointer: Any):
    def prepare(state: WorkState) -> dict:
        deps.node("prepare", "ok")
        return {
            "acts": 0,
            "revisions": 0,
            "waits": 0,
            "feedback": None,
            "resume_text": None,
            "context": deps.context_text(),
            "proposal": None,
            "check": None,
            "outcome": None,
            "waiting": None,
            "question_ids": [],
            "approval_ids": [],
            "result": None,
            "final": None,
        }

    def _plan_session(state: WorkState) -> tuple[SessionSpec | None, dict | None, bool]:
        """(spec for the adapter, session to remember, whether the prompt needs the summary).
        A session is only continued on the very connection and runtime that started it."""
        old = state.get("session")
        if not deps.supports_sessions:
            return None, old, bool(state.get("acts"))
        if (
            old
            and old.get("started")
            and old.get("connection_id") == deps.connection_id
            and old.get("runtime") == deps.runtime_id
        ):
            return SessionSpec("resume", str(old["id"])), old, False
        import uuid

        new = {
            "id": str(uuid.uuid4()),
            "connection_id": deps.connection_id,
            "runtime": deps.runtime_id,
            "started": False,
        }
        return SessionSpec("new", new["id"]), new, old is not None or bool(state.get("acts"))

    def act(state: WorkState) -> dict:
        if deps.cancel.is_set():
            deps.node("act", "cancelled")
            return {
                "outcome": "cancelled",
                "result": {"status": "cancelled", "summary": None, "error": None},
            }
        session_spec, session, with_summary = _plan_session(state)
        carry = [t for t in (state.get("context"), state.get("resume_text")) if t]
        if with_summary and state.get("summary"):
            carry.append(f"Where you stand (checkpoint of this task so far):\n{state['summary']}")
        deps.clear_proposal()
        result = deps.run_act(
            carry_extra="\n\n".join(carry) or None,
            feedback=state.get("feedback"),
            session=session_spec,
        )
        if (
            session_spec is not None
            and session_spec.mode == "resume"
            and result.status == "failed"
            and result.error is not None
            and SESSION_LOST in result.error.message
        ):
            # the session is gone (other working directory, cleaned up): new session + summary
            deps.node("act", "session_lost")
            session = None
            session_spec, session, _ = _plan_session({**state, "session": None, "acts": 1})
            carry.append(
                "Where you stand (checkpoint of this task so far):\n" + (state.get("summary") or "")
            )
            deps.clear_proposal()
            result = deps.run_act(
                carry_extra="\n\n".join(carry) or None,
                feedback=state.get("feedback"),
                session=session_spec,
            )
        if session is not None:
            session = {**session, "started": True}
        acts = int(state.get("acts") or 0) + 1
        updates: dict[str, Any] = {
            "acts": acts,
            "session": session,
            "feedback": None,
            "resume_text": None,
            "result": result_to_dict(result),
            "proposal": None,
            "question_ids": [],
            "approval_ids": [],
            "waiting": None,
        }
        if result.status != "succeeded":
            outcome = "cancelled" if result.status == "cancelled" else "failed"
            updates["outcome"] = outcome
            updates["summary"] = _append_summary(state.get("summary"), f"Act {acts}: {outcome}")
            deps.node("act", outcome)
            return updates
        inspected = deps.inspect_after_act()
        proposal = deps.read_proposal() if inspected.task_in_progress else None
        waits = int(state.get("waits") or 0)
        if inspected.task_in_progress and inspected.question_ids:
            outcome = "waiting_founder"
            updates["question_ids"] = inspected.question_ids
        elif inspected.task_in_progress and inspected.approval_ids and waits < MAX_WAITS:
            outcome = "waiting_approval"
            updates["approval_ids"] = inspected.approval_ids
        elif proposal is not None:
            outcome = "proposed"
            updates["proposal"] = proposal
        else:
            outcome = "completed"  # blocked, handed off, done another way, or no tool completion
        updates["outcome"] = outcome
        updates["summary"] = _append_summary(
            state.get("summary"),
            f"Act {acts}: {outcome}"
            + (
                f" ({_clip((proposal or {}).get('summary') or result.summary, 200)})"
                if outcome in ("proposed", "completed")
                else ""
            ),
        )
        deps.node("act", outcome)
        return updates

    def route_after_act(state: WorkState) -> str:
        return {
            "proposed": "check",
            "waiting_founder": "wait_founder",
            "waiting_approval": "wait_approval",
        }.get(state.get("outcome") or "", "finish")

    def check(state: WorkState) -> dict:
        if deps.cancel.is_set():
            deps.node("check", "cancelled")
            return {
                "outcome": "cancelled",
                "result": {"status": "cancelled", "summary": None, "error": None},
            }
        if not deps.check_allowed:
            deps.node("check", "skipped")
            return {"outcome": "checked", "check": {"status": "skipped", "feedback": ""}}
        verdict = deps.run_check(state)
        if verdict["status"] == "cancelled":
            deps.node("check", "cancelled")
            return {
                "outcome": "cancelled",
                "result": {"status": "cancelled", "summary": None, "error": None},
            }
        revisions = int(state.get("revisions") or 0)
        updates: dict[str, Any] = {"check": verdict, "outcome": "checked"}
        if verdict["status"] == "failed":
            updates["summary"] = _append_summary(
                state.get("summary"), f"Self-check failed: {_clip(verdict['feedback'], 300)}"
            )
            if revisions < MAX_REVISIONS:
                updates["revisions"] = revisions + 1
                updates["feedback"] = verdict["feedback"]
                updates["outcome"] = "revise"
        else:
            updates["summary"] = _append_summary(
                state.get("summary"), f"Self-check {verdict['status']}"
            )
        deps.node(
            "check",
            verdict["status"] + ("/revise" if updates["outcome"] == "revise" else ""),
            revisions=int(updates.get("revisions", revisions)),
        )
        return updates

    def route_after_check(state: WorkState) -> str:
        return {"revise": "act"}.get(state.get("outcome") or "", "finish")

    def wait_founder(state: WorkState) -> dict:
        answers = interrupt({"kind": "founder", "question_ids": state.get("question_ids") or []})
        deps.node("wait_founder", "resumed")
        # The answers reach the agent through the task's carry-over (questions.context_lines).
        del answers
        return {
            "waits": int(state.get("waits") or 0) + 1,
            "question_ids": [],
            "outcome": None,
            "summary": _append_summary(state.get("summary"), "The founder's question was resolved"),
        }

    def wait_approval(state: WorkState) -> dict:
        decisions = interrupt({"kind": "approval", "approval_ids": state.get("approval_ids") or []})
        deps.node("wait_approval", "resumed")
        from app.services import graph_waits

        lines = graph_waits.resume_lines(decisions if isinstance(decisions, list) else [])
        return {
            "waits": int(state.get("waits") or 0) + 1,
            "approval_ids": [],
            "outcome": None,
            "resume_text": "\n".join(lines) or None,
            "summary": _append_summary(state.get("summary"), "An approval was decided"),
        }

    def finish(state: WorkState) -> dict:
        outcome = state.get("outcome") or "completed"
        result = state.get("result") or {"status": "succeeded", "summary": None, "error": None}
        proposal = state.get("proposal")
        verdict = state.get("check") or {}
        applied = False
        if outcome == "checked" and proposal is not None:
            status = verdict.get("status")
            force = (
                (verdict.get("feedback") or "see the self-check") if status == "failed" else None
            )
            applied = deps.apply_completion(
                proposal, self_checked=status == "passed", force_review=force
            )
            result = {
                "status": "succeeded",
                "summary": proposal.get("summary") or result.get("summary"),
                "error": None,
            }
        deps.node("finish", outcome + (("/applied" if applied else "/kept") if proposal else ""))
        return {"final": result}

    builder = StateGraph(WorkState)
    builder.add_node("prepare", prepare)
    builder.add_node("act", act)
    builder.add_node("check", check)
    builder.add_node("wait_founder", wait_founder)
    builder.add_node("wait_approval", wait_approval)
    builder.add_node("finish", finish)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "act")
    builder.add_conditional_edges(
        "act", route_after_act, ["check", "wait_founder", "wait_approval", "finish"]
    )
    builder.add_conditional_edges("check", route_after_check, ["act", "finish"])
    builder.add_edge("wait_founder", "act")
    builder.add_edge("wait_approval", "act")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)
