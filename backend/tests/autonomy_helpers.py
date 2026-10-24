"""Shared helpers for the Milestone 5 tests: a company whose agents are driven by ScriptedRuntime
handlers that call the real agent API with the run token."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select

from app.models.autonomy import WakeRequest
from app.models.work import Task
from tests.helpers import agent_of_family, api, new_agent


@dataclass
class World:
    cid: str
    founder: dict
    hr: dict
    lead: dict | None = None
    specialist: dict | None = None


def scripted_world(client, scripted, anon_client, *, with_team: bool = True) -> World:
    """A company with a scripted founder + HR and (optionally) a lead with one specialist report."""
    scripted.api_client = anon_client  # run-token-only client: no owner cookie
    company = client.post("/api/companies", json={"name": "Acme Labs", "mission": "Ship"}).json()
    cid = company["id"]
    founder = client.get(api(cid, "/agents")).json()["items"][0]
    founder = next(
        a for a in client.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    hr = agent_of_family(client, cid, "hr")
    for agent in (founder, hr):
        r = client.patch(api(cid, f"/agents/{agent['id']}"), json={"runtime": "scripted"})
        assert r.status_code == 200, r.text
    world = World(cid, founder, hr)
    if with_team:
        world.lead = new_agent(
            client,
            cid,
            name="Lena",
            kind="lead",
            runtime="scripted",
            role_title="Marketing Lead",
            manager_agent_id=founder["id"],
        )
        world.specialist = new_agent(
            client,
            cid,
            name="Sam",
            runtime="scripted",
            role_title="Copywriter",
            manager_agent_id=world.lead["id"],
        )
    # Activating the team queued "new hire" wakes; start every test from a quiet company.
    with client.app.state.session_factory() as db:
        db.execute(delete(WakeRequest))
        db.commit()
    return world


def pending_wakes(app) -> int:
    with app.state.session_factory() as db:
        return int(db.scalar(select(func.count(WakeRequest.id))) or 0)


def open_tasks(app, cid: str) -> int:
    with app.state.session_factory() as db:
        return int(
            db.scalar(
                select(func.count(Task.id)).where(
                    Task.company_id == cid,
                    Task.status.in_(["ready", "in_progress", "in_review", "revision_requested"]),
                )
            )
            or 0
        )


def run_world(app, scheduler, worker, clock, *, rounds: int = 40, step: float = 6.0) -> int:
    """Drive the engine (wake debounce, dispatch, runs) until nothing is queued, running or
    waiting to wake anyone. Returns the number of rounds used."""
    for i in range(rounds):
        clock.advance(step)
        report = scheduler.tick()
        processed = worker.drain()
        if not report.dispatch.queued_run_ids and not processed and not pending_wakes(app):
            return i + 1
    return rounds


def wake_ready(clock, seconds: float = 90.0) -> None:
    """Move past the note debounce window (60 s, plus slack: the wake time is taken from the real
    clock while the engine runs on the fake one)."""
    clock.advance(seconds)


def tasks_of(app, cid: str, **filters: Any) -> list[Task]:
    with app.state.session_factory() as db:
        stmt = select(Task).where(Task.company_id == cid).order_by(Task.created_at, Task.id)
        for key, value in filters.items():
            stmt = stmt.where(getattr(Task, key) == value)
        return list(db.scalars(stmt))
