"""M21 through the real engine: the scheduler tick advances the meeting graph, turn tasks are
dispatched like any task (scripted runtime), each speaker's run ends with company_meeting_say."""

from __future__ import annotations

import re

from sqlalchemy import select

from app.models.run import TaskRun
from app.models.work import Task
from tests.helpers import api, new_agent
from tests.m21_helpers import meeting_row, messages, schedule, turn_tasks

MEETING_ID = re.compile(r"meeting id ([0-9a-f-]{36})")


def test_a_meeting_runs_end_to_end_through_the_scheduler_and_worker(
    app, client, anon_client, scheduler, worker, clock, scripted
):
    scripted.api_client = anon_client
    cid = client.post("/api/companies", json={"name": "Acme", "mission": "Ship"}).json()["id"]
    ana = new_agent(client, cid, name="Ana")
    raj = new_agent(client, cid, name="Raj")
    lee = new_agent(client, cid, name="Lee")
    seen: list[tuple[str, str]] = []

    def speaker(call):
        task = call.request.task
        if task.kind != "meeting":
            return  # the work an agreed action item became: the scripted default completes it
        mid = MEETING_ID.search(task.description or "").group(1)
        name = call.request.agent.name
        seen.append((name, task.title))
        assert call.api.get("/context").json()["task"]["kind"] == "meeting"
        proposals = [p for m in messages(app, mid) for p in (m.proposals or {}).get("proposed", [])]
        if "final summary" in task.title:
            body = {
                "final": True,
                "summary": "Agreed.",
                "decisions": [{"title": "Go with plan A", "rationale": "Simplest"}],
                "action_items": [
                    {"id": proposals[0]["id"]},
                    {"title": "Ship it", "owner_agent_id": ana["id"]},
                ],
            }
        elif name == "Ana":
            body = {
                "message": "Opening",
                "propose": [{"title": "Draft", "owner_agent_id": raj["id"]}],
            }
        elif name == "Raj":
            body = {"message": "I accept", "accept": [proposals[0]["id"]]}
        else:
            body = {"pass_turn": True}
        call.api.ok("POST", f"/meetings/{mid}/say", json=body)

    for agent in (ana, raj, lee):
        scripted.handlers[agent["name"]] = speaker

    m = schedule(app, clock, cid, ana["id"], [raj["id"], lee["id"]])
    for _ in range(80):
        clock.advance(6)
        scheduler.tick()
        worker.drain()
        if (
            meeting_row(app, m.id).status != "running"
            and meeting_row(app, m.id).status != "scheduled"
        ):
            break
    assert scripted.errors == []
    row = meeting_row(app, m.id)
    assert row.status == "concluded", (row.status, row.cancel_reason, seen)
    # Ana, Raj, Lee (their one round: Lee passes, Raj accepted everything -> early end), then Ana's summary
    assert [n for n, _ in seen] == ["Ana", "Raj", "Lee", "Ana"]
    assert "final summary" in seen[-1][1]
    # every turn was a normal budgeted run of a normal task
    with app.state.session_factory() as db:
        runs = list(
            db.scalars(
                select(TaskRun).where(TaskRun.task_id.in_([t.id for t in turn_tasks(app, m.id)]))
            )
        )
        assert len(runs) == 4 and all(r.status == "succeeded" for r in runs)
        work = list(db.scalars(select(Task).where(Task.company_id == cid, Task.kind == "work")))
        assert sorted(t.title for t in work) == ["Draft", "Ship it"]
        assert all(t.status == "done" for t in turn_tasks(app, m.id))
    items = row.outcome["action_items"]
    assert any(i["title"] == "Ship it" and i["task_id"] for i in items)
    assert any(
        i["title"] == "Draft" and i["task_id"] for i in items
    )  # Raj accepted it -> the work task
    detail = client.get(api(cid, f"/meetings/{m.id}")).json()
    assert detail["summary"].startswith("Agreed.")
