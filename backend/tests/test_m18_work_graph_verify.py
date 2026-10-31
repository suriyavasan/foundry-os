"""M18 Part B: the company agent runtime's self-check spec, and thread retention."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.engine.graph.checkpointer import SqlCheckpointSaver
from app.engine.graph.retention import RETENTION, purge_finished_threads
from app.engine.recovery import recover
from app.models.graph import GraphCheckpoint
from app.models.work import Task
from tests.helpers import new_agent, new_task
from tests.test_company_agent_adapter import env, fake_runtime_dir, skills_env_override  # noqa: F401


def test_the_company_agent_self_check_is_a_tool_less_verify_run(env):  # noqa: F811
    work = env.adapter._prepare(env.request("work"))
    assert work.spec.get("mode") is None and "company" in work.spec["toolsets"]

    verify = env.adapter._prepare(env.request("check", purpose="verify")).spec
    assert verify["mode"] == "verify"
    assert verify["toolsets"] == ["todo"] and verify["company_tools"] == []
    assert verify["task"]["description"] == "desc"  # the verification prompt is the whole task
    assert verify["limits"]["max_iterations"] == 3
    assert "workspace" not in verify
    # same model and key as the work run: the check runs on the routed connection
    assert verify["model"] == work.spec["model"]


def test_threads_of_tasks_ended_30_days_ago_are_purged(app, client, company, engine, clock):
    cid = company["id"]
    agent = new_agent(client, cid, name="Gus")
    old = new_task(client, cid, assignee_agent_id=agent["id"])
    recent = new_task(client, cid, assignee_agent_id=agent["id"])
    live = new_task(client, cid, assignee_agent_id=agent["id"])
    saver = SqlCheckpointSaver(app.state.session_factory)
    for task in (old, recent, live):
        saver.put(
            {"configurable": {"company_id": cid, "thread_id": f"task:{task['id']}"}},
            {
                "v": 1,
                "id": "cp-1",
                "ts": "2026-10-30T15:00:00Z",
                "channel_values": {},
                "channel_versions": {},
                "versions_seen": {},
                "updated_channels": [],
            },
            {"step": 1},
            {},
        )
    now = clock()
    with app.state.session_factory() as db:
        for task, status, ended in (
            (old, "done", now - RETENTION - timedelta(days=1)),
            (recent, "cancelled", now - timedelta(days=3)),
        ):
            row = db.get(Task, task["id"])
            row.status, row.completed_at = status, ended
        db.commit()
    assert recover(engine, now).purged_graph_threads == 1  # the recovery sweep does it
    with app.state.session_factory() as db:
        left = {r.thread_id for r in db.scalars(select(GraphCheckpoint))}
    assert left == {f"task:{recent['id']}", f"task:{live['id']}"}
    assert purge_finished_threads(engine, now) == 0
