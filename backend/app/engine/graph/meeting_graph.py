"""M21: the meeting graph (LangGraph thread ``meeting:<id>``; checkpoints: ``SqlCheckpointSaver``).

    open -> turn (one per speaker) -> end_round -> turn ... -> synthesize -> commit -> close

* ``turn`` / ``synthesize`` create the speaker's ``meeting`` task through the normal task service
  and **interrupt**: the graph sleeps in the checkpoint while the agent's run happens in the
  engine. The scheduler sweep (``meetings.advance``) resumes it with ``Command(resume=...)`` once
  the turn task has ended (done, failed, cancelled or timed out; the last three count as a pass).
* No model call ever happens inside the graph: every invocation is a few short database writes.
* A node re-runs from its start when resumed, so everything before ``interrupt`` is idempotent
  (the turn task is created once per key).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from typing_extensions import TypedDict

from app.engine.graph.checkpointer import SqlCheckpointSaver
from app.services import meetings

RECURSION_LIMIT = 80
LOCK_RETRIES = 4


class MeetingState(TypedDict, total=False):
    meeting_id: str
    company_id: str
    order: list[str]  # agent ids: organizer first, then the invitation order
    round: int
    idx: int  # index in ``order`` of the next speaker
    max_rounds: int
    to_synth: bool


def build_graph(runner: MeetingRunner):
    factory = runner.factory

    def with_db(fn):
        """One short transaction. A SQLite "database is locked" (a read snapshot overtaken by
        another writer) is retried on a fresh session: every step is idempotent."""
        for attempt in range(LOCK_RETRIES):
            try:
                with factory() as db:
                    result = fn(db)
                    db.commit()
                    return result
            except OperationalError:
                if attempt == LOCK_RETRIES - 1:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def open_node(state: MeetingState) -> dict[str, Any]:
        info = with_db(lambda db: meetings.open_meeting(db, state["meeting_id"], runner.now))
        return {
            "order": info["order"],
            "max_rounds": info["max_rounds"],
            "round": 1,
            "idx": 0,
            "to_synth": False,
        }

    def turn_node(state: MeetingState) -> dict[str, Any]:
        agent_id = state["order"][state["idx"]]
        round_no = state["round"]
        task_id = with_db(
            lambda db: meetings.ensure_turn_task(
                db, state["meeting_id"], agent_id, round_no, state["idx"], "turn", runner.now
            )
        )
        if task_id is not None:
            interrupt({"task_id": task_id, "agent_id": agent_id, "round": round_no, "kind": "turn"})
        with_db(
            lambda db: meetings.finish_turn(
                db, state["meeting_id"], agent_id, task_id, round_no, "turn", runner.now
            )
        )
        return {"idx": state["idx"] + 1}

    def after_turn(state: MeetingState) -> str:
        return "turn" if state["idx"] < len(state["order"]) else "end_round"

    def end_round_node(state: MeetingState) -> dict[str, Any]:
        quiet = with_db(lambda db: meetings.round_is_quiet(db, state["meeting_id"], state["round"]))
        if quiet or state["round"] >= state["max_rounds"]:
            return {"to_synth": True}
        nxt = state["round"] + 1
        with_db(lambda db: meetings.set_round(db, state["meeting_id"], nxt, runner.now))
        return {"round": nxt, "idx": 0}

    def after_end_round(state: MeetingState) -> str:
        return "synthesize" if state.get("to_synth") else "turn"

    def synthesize_node(state: MeetingState) -> dict[str, Any]:
        organizer = state["order"][0]
        round_no = state["round"]
        task_id = with_db(
            lambda db: meetings.ensure_turn_task(
                db, state["meeting_id"], organizer, round_no, 0, "synthesis", runner.now
            )
        )
        if task_id is not None:
            interrupt(
                {"task_id": task_id, "agent_id": organizer, "round": round_no, "kind": "synthesis"}
            )
        with_db(
            lambda db: meetings.finish_turn(
                db, state["meeting_id"], organizer, task_id, round_no, "synthesis", runner.now
            )
        )
        return {}

    def commit_node(state: MeetingState) -> dict[str, Any]:
        with_db(lambda db: meetings.commit_outcome(db, state["meeting_id"], runner.now))
        return {}

    def close_node(state: MeetingState) -> dict[str, Any]:
        with_db(lambda db: meetings.close_meeting(db, state["meeting_id"], runner.now))
        return {}

    builder = StateGraph(MeetingState)
    builder.add_node("open", open_node)
    builder.add_node("turn", turn_node)
    builder.add_node("end_round", end_round_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_node("commit", commit_node)
    builder.add_node("close", close_node)
    builder.add_edge(START, "open")
    builder.add_edge("open", "turn")
    builder.add_conditional_edges("turn", after_turn, {"turn": "turn", "end_round": "end_round"})
    builder.add_conditional_edges(
        "end_round", after_end_round, {"turn": "turn", "synthesize": "synthesize"}
    )
    builder.add_edge("synthesize", "commit")
    builder.add_edge("commit", "close")
    builder.add_edge("close", END)
    return builder.compile(checkpointer=SqlCheckpointSaver(factory))


class MeetingRunner:
    """Starts, resumes and inspects meeting graphs. ``now`` is the scheduler's clock this tick."""

    def __init__(self, factory: sessionmaker[Session] | Any, now: datetime) -> None:
        self.factory = factory
        self.now = now
        self.graph = build_graph(self)

    @staticmethod
    def _config(meeting_id: str, company_id: str) -> dict[str, Any]:
        return {
            "configurable": {"company_id": company_id, "thread_id": meetings.thread_id(meeting_id)},
            "recursion_limit": RECURSION_LIMIT,
        }

    def start(self, meeting_id: str, company_id: str) -> None:
        self.graph.invoke(
            {"meeting_id": meeting_id, "company_id": company_id},
            self._config(meeting_id, company_id),
            durability="sync",
        )

    def resume(self, meeting_id: str, company_id: str, value: dict[str, Any]) -> None:
        self.graph.invoke(
            Command(resume=value), self._config(meeting_id, company_id), durability="sync"
        )

    def resume_failed(self, meeting_id: str, company_id: str) -> None:
        """Continue from the checkpoint after a node raised (it re-runs from its start)."""
        self.graph.invoke(None, self._config(meeting_id, company_id), durability="sync")

    def snapshot(self, meeting_id: str, company_id: str):
        return self.graph.get_state(self._config(meeting_id, company_id))

    def pending(self, meeting_id: str, company_id: str) -> dict[str, Any] | None:
        """The interrupt the graph sleeps on ({task_id, agent_id, round, kind}), if any."""
        snap = self.snapshot(meeting_id, company_id)
        for task in snap.tasks:
            for itr in task.interrupts:
                if isinstance(itr.value, dict) and "task_id" in itr.value:
                    return itr.value
        return None

    def finished(self, meeting_id: str, company_id: str) -> bool:
        """True when the thread has no checkpoint or no node left to run."""
        snap = self.snapshot(meeting_id, company_id)
        return not snap.next
