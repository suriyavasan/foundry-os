"""Shared helpers for the M21 (agent meetings) tests: a small company, meetings scheduled through
the service, turns simulated through the real agent API with a run token, and the sweep driven
with the fake clock."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.core.time import utcnow
from app.models.meetings import Meeting, MeetingMessage
from app.models.run import TaskRun
from app.models.work import Task
from app.schemas.meetings import MeetingSchedule
from app.services import agent_auth, meetings
from tests.helpers import new_agent
from tests.test_agent_api import AgentSession


@dataclass
class Team:
    cid: str
    ana: dict  # organizer
    raj: dict
    lee: dict
    zoe: dict

    @property
    def ids(self) -> dict[str, str]:
        return {
            "ana": self.ana["id"],
            "raj": self.raj["id"],
            "lee": self.lee["id"],
            "zoe": self.zoe["id"],
        }


def make_team(client, cid: str) -> Team:
    return Team(
        cid,
        *(new_agent(client, cid, name=n, runtime="scripted") for n in ("Ana", "Raj", "Lee", "Zoe")),
    )


def agent_ctx(cid: str, agent_id: str, label: str = "Agent") -> CompanyContext:
    return CompanyContext(cid, Principal(PrincipalType.AGENT, agent_id, label, cid), Role.AGENT)


def schedule(app, clock, cid: str, organizer: str, participants: list[str], **fields) -> Meeting:
    data = MeetingSchedule(
        title=fields.pop("title", "Plan the launch"),
        agenda=fields.pop("agenda", "Agree who does what for the launch."),
        expected_outcome=fields.pop("expected_outcome", "A split of the work"),
        participant_agent_ids=participants,
        **fields,
    )
    with app.state.session_factory() as db:
        return meetings.schedule(db, agent_ctx(cid, organizer), None, data, now=clock())


def drive(app, clock, seconds: float = 0) -> dict:
    if seconds:
        clock.advance(seconds)
    return meetings.advance(app.state.session_factory, clock())


def meeting_row(app, mid: str) -> Meeting:
    with app.state.session_factory() as db:
        return db.get(Meeting, mid)


def turn_tasks(app, mid: str) -> list[Task]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(Task)
                .where(Task.meeting_id == mid, Task.kind == "meeting")
                .order_by(Task.created_at, Task.id)
            )
        )


def open_turn(app, mid: str) -> Task | None:
    live = [t for t in turn_tasks(app, mid) if t.status not in ("done", "failed", "cancelled")]
    return live[-1] if live else None


def messages(app, mid: str) -> list[MeetingMessage]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(MeetingMessage)
                .where(MeetingMessage.meeting_id == mid)
                .order_by(MeetingMessage.created_at, MeetingMessage.id)
            )
        )


def start_turn(app, client, task_id: str) -> AgentSession:
    """The speaker's run begins: task in progress, a run, its token."""
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        existing = db.scalar(select(TaskRun).where(TaskRun.task_id == task_id))
        if existing is not None:  # the same run speaks again (a refused attempt, then a retry)
            token = agent_auth.issue_token(app.state.keys, existing, max_run_seconds=900)
            return AgentSession(
                client, token, run_id=existing.id, task_id=task.id, agent_id=task.assignee_agent_id
            )
        task.status = "in_progress"
        task.attempt_count = 1
        task.started_at = utcnow()
        run = TaskRun(
            company_id=task.company_id,
            task_id=task.id,
            agent_id=task.assignee_agent_id,
            attempt_number=1,
            status="running",
            runtime="scripted",
            idempotency_key=f"{task.id}:1:0",
            queued_at=utcnow(),
            started_at=utcnow(),
            heartbeat_at=utcnow(),
        )
        db.add(run)
        db.commit()
        token = agent_auth.issue_token(app.state.keys, run, max_run_seconds=900)
        return AgentSession(
            client, token, run_id=run.id, task_id=task.id, agent_id=task.assignee_agent_id
        )


def speak(app, client, mid: str, **payload):
    """The speaker of the open turn says ``payload``; returns the HTTP response."""
    task = open_turn(app, mid)
    assert task is not None, "no open turn"
    session = start_turn(app, client, task.id)
    return session.post(f"/meetings/{mid}/say", json=payload)
