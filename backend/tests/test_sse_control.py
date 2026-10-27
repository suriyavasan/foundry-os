"""Live stream (generator driven directly, no socket) and the pause/resume kill switch."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.api import sse
from app.models.run import CompanyEvent
from app.services import events
from tests.conftest import human
from tests.helpers import (
    api,
    get_task,
    new_agent,
    new_task,
    run_in_thread,
    set_policy,
    settle,
    wait_until,
)

# ------------------------------------------------------------------ SSE


def _parse(frame: str) -> dict:
    out: dict = {}
    for line in frame.strip().splitlines():
        key, _, value = line.partition(": ")
        out[key] = value
    return out


def _collect(agen, count: int) -> list[str]:
    async def run():
        frames = []
        async for frame in agen:
            frames.append(frame)
            if len(frames) >= count:
                break
        await agen.aclose()
        return frames

    return asyncio.run(run())


def _emit(app, cid, n, start=0):
    with app.state.session_factory() as db:
        for i in range(start, start + n):
            events.emit(db, cid, "test.event", {"i": i})
        db.commit()


async def _no_sleep(_seconds):
    await asyncio.sleep(0)


def test_stream_delivers_events_in_order_with_ids(app, client, company):
    cid = company["id"]
    _emit(app, cid, 5)
    frames = _collect(
        sse.event_stream(app.state.session_factory, cid, 0, sleep=_no_sleep), 1 + 5 + 1
    )
    assert frames[0] == "retry: 3000\n\n"
    parsed = [_parse(f) for f in frames[1:]]
    seen = [json.loads(p["data"]) for p in parsed]
    # `company` creation already wrote activity events; ours follow in order
    assert [p["id"] for p in parsed] == [str(s["id"]) for s in seen]
    ids = [s["id"] for s in seen]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    ours = [s["payload"]["i"] for s in seen if s["type"] == "test.event"]
    assert ours == sorted(ours)
    assert all(p["event"] == s["type"] for p, s in zip(parsed, seen, strict=True))
    assert all(s["company_id"] == cid and s["created_at"].endswith("Z") for s in seen)


def test_stream_resumes_after_last_event_id_without_gaps_or_repeats(app, client, company):
    cid = company["id"]
    _emit(app, cid, 6)
    with app.state.session_factory() as db:
        all_ids = [
            e.id
            for e in db.scalars(
                select(CompanyEvent).where(CompanyEvent.company_id == cid).order_by(CompanyEvent.id)
            )
        ]
    first = _collect(sse.event_stream(app.state.session_factory, cid, 0, sleep=_no_sleep), 1 + 4)
    last_seen = int(_parse(first[-1])["id"])
    assert last_seen == all_ids[3]
    # the client reconnects with Last-Event-ID = last_seen
    cursor = sse.resolve_cursor(None, str(last_seen), latest=all_ids[-1])
    resumed = _collect(
        sse.event_stream(app.state.session_factory, cid, cursor, sleep=_no_sleep),
        1 + len(all_ids) - 4,
    )
    assert [int(_parse(f)["id"]) for f in resumed[1:]] == all_ids[4:]


def test_stream_picks_up_new_events_and_sends_heartbeats(app, client, company):
    cid = company["id"]
    latest = sse.latest_id(app.state.session_factory, cid)
    ticks = {"n": 0}
    clock = {"t": 0.0}

    async def fake_sleep(seconds):
        ticks["n"] += 1
        clock["t"] += seconds
        if ticks["n"] == 5:  # something happens while the client is connected
            _emit(app, cid, 1, start=99)
        await asyncio.sleep(0)

    frames = _collect(
        sse.event_stream(
            app.state.session_factory, cid, latest, poll_interval=6.0, heartbeat_interval=15.0,
            sleep=fake_sleep, monotonic=lambda: clock["t"],
        ),
        3,
    )  # fmt: skip
    assert frames[0] == "retry: 3000\n\n"
    assert frames[1] == ": keepalive\n\n"  # 15+ s of silence (virtual time) -> heartbeat comment
    event = _parse(frames[2])
    assert event["event"] == "test.event" and json.loads(event["data"])["payload"] == {"i": 99}


def test_stream_stops_when_the_client_disconnects(app, client, company):
    cid = company["id"]
    calls = {"n": 0}

    async def disconnected():
        calls["n"] += 1
        return calls["n"] > 2

    async def run():
        return [
            f
            async for f in sse.event_stream(
                app.state.session_factory, cid, 10**9, sleep=_no_sleep, is_disconnected=disconnected
            )
        ]

    assert asyncio.run(run()) == ["retry: 3000\n\n"]


def test_stream_only_returns_the_callers_company(app, client, company):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    _emit(app, other, 3)
    frames = _collect(sse.event_stream(app.state.session_factory, cid, 0, sleep=_no_sleep), 2)
    assert json.loads(_parse(frames[1])["data"])["company_id"] == cid
    all_frames = _collect(sse.event_stream(app.state.session_factory, other, 0, sleep=_no_sleep), 4)
    assert {json.loads(_parse(f)["data"])["company_id"] for f in all_frames[1:]} == {other}


def test_resolve_cursor_precedence():
    assert sse.resolve_cursor(5, "9", latest=100) == 5
    assert sse.resolve_cursor(None, "9", latest=100) == 9
    assert sse.resolve_cursor(None, " 12 ", latest=100) == 12
    assert sse.resolve_cursor(None, "abc", latest=100) == 100
    assert sse.resolve_cursor(None, None, latest=100) == 100
    assert sse.resolve_cursor(0, "9", latest=100) == 0  # ?after=0 replays history


def test_stream_route_requires_membership(client, client_as, make_user, company):
    cid = company["id"]
    outsider = client_as(human(make_user("sse@x.test")))
    r = outsider.get(api(cid, "/stream"))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get(api("no-such-company", "/stream")).status_code == 404
    assert client.get(api(cid, "/stream"), params={"after": "abc"}).status_code == 422


def test_old_events_are_pruned(app, client, company, clock):
    from datetime import timedelta

    cid = company["id"]
    _emit(app, cid, 2)
    with app.state.session_factory() as db:
        before = db.query(CompanyEvent).count()
        assert events.prune(db, clock()) == 0
        assert events.prune(db, clock() + timedelta(days=8)) == before
        db.commit()
        assert db.query(CompanyEvent).count() == 0


# ------------------------------------------------------------------ pause / resume


def test_pause_stops_dispatch_and_resume_restarts_it(client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    paused = client.post(api(cid, "/pause"))
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert get_task(client, cid, task["id"])["status"] == "backlog"  # not ready while paused
    assert scheduler.tick().dispatch.queued_run_ids == []
    assert client.get(api(cid, f"/agents/{agent['id']}/activity-now")).json()["state"] == "paused"
    new = new_task(client, cid, title="while paused", assignee_agent_id=agent["id"])
    assert new["status"] == "backlog"
    assert client.post(api(cid, "/pause")).status_code == 409
    resumed = client.post(api(cid, "/resume"))
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"
    assert get_task(client, cid, task["id"])["status"] == "ready"
    assert client.post(api(cid, "/resume")).status_code == 409
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert get_task(client, cid, new["id"])["status"] == "done"
    actions = [
        e["action"]
        for e in client.get(api(cid, "/activity"), params={"target_type": "company"}).json()[
            "items"
        ]
    ]
    assert "company.paused" in actions and "company.resumed" in actions


def test_pause_cancels_queued_and_running_runs_without_penalty(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    set_policy(client, cid, management_reserved_slots=0)  # both work runs dispatch at once
    running_agent = new_agent(client, cid, name="Runner", script={"block_until_cancel": True})
    queued_agent = new_agent(client, cid, name="Waiter")
    t_run = new_task(client, cid, title="running", assignee_agent_id=running_agent["id"])
    t_queued = new_task(client, cid, title="queued", assignee_agent_id=queued_agent["id"])
    assert len(scheduler.tick().dispatch.queued_run_ids) == 2
    # execute the blocking one in the background; leave the other queued
    from app.models.run import TaskRun

    thread = run_in_thread(worker.run_once)  # leases the oldest queued job
    wait_until(lambda: _any_running(app), what="a run to start")
    client.post(api(cid, "/pause"))
    thread.join(timeout=5)
    assert not thread.is_alive()
    with app.state.session_factory() as db:
        runs = {r.task_id: r for r in db.scalars(select(TaskRun))}
    assert {r.status for r in runs.values()} == {"cancelled"}
    assert {r.exit_reason or r.cancel_reason for r in runs.values()} == {"company_paused"}
    # leftover jobs of cancelled runs are no-ops: nothing executes while paused
    worker.drain()
    with app.state.session_factory() as db:
        assert {r.status for r in db.scalars(select(TaskRun))} == {"cancelled"}
    for t in (t_run, t_queued):
        row = get_task(client, cid, t["id"])
        assert row["status"] == "backlog" and row["attempt_count"] == 0  # attempts refunded
    # after resume the work simply runs (the formerly blocking agent now behaves)
    client.patch(
        api(cid, f"/agents/{running_agent['id']}"), json={"runtime_config": {"script": {}}}
    )
    client.post(api(cid, "/resume"))
    settle(scheduler, worker)
    for t in (t_run, t_queued):
        assert get_task(client, cid, t["id"])["status"] == "done"


def _any_running(app) -> bool:
    from app.models.run import TaskRun

    with app.state.session_factory() as db:
        return "running" in set(db.scalars(select(TaskRun.status)))


def test_patching_company_status_is_the_same_kill_switch(client, company, scheduler):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    client.patch(api(cid, ""), json={"status": "paused"})
    assert get_task(client, cid, task["id"])["status"] == "backlog"
    assert scheduler.tick().dispatch.queued_run_ids == []
    client.patch(api(cid, ""), json={"status": "active"})
    assert get_task(client, cid, task["id"])["status"] == "ready"


def test_only_human_admins_can_pause(client, client_as, make_user, add_member, company):
    from app.core.principal import Principal, PrincipalType

    cid = company["id"]
    viewer = make_user("pv@x.test")
    add_member(cid, viewer.id, "viewer")
    agent = new_agent(client, cid)
    for caller in (
        client_as(human(viewer)),
        client_as(Principal(PrincipalType.AGENT, agent["id"], "N", cid)),
    ):
        assert caller.post(api(cid, "/pause")).status_code == 403
        assert caller.post(api(cid, "/resume")).status_code == 403
    assert client.get(api(cid, "")).json()["status"] == "active"
