"""M16 end to end: the outside world becomes finished work without a human.

feed item -> fetch job -> stored items -> the scheduled radar routine of the (hired) Market
Researcher: digest, page read, brief, two ideas (one duplicate refused) -> the Co-founder's triage
routine accepts one into a project -> the work task is dispatched and completed by the M12 engine
-> the idea is ``done``.

Everything runs on the real engine (scheduler tick + worker + watchdog on a fake clock) and the real
agent API; the agents are ``ScriptedRuntime`` handlers making exactly the calls the radar tools
make. The internet is a fake (``tests/radar/conftest.py``): no real network.

Not covered (and not claimed): real feeds, a real model deciding what is relevant, real Hacker News
or One Page Love responses.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.engine.worker import Worker
from app.models.audit import AuditEvent
from app.models.radar import RadarBrief, RadarIdea, RadarItem
from app.models.schedule import Schedule
from app.models.work import Task
from app.radar import OFFLINE_ENV
from tests.autonomy_helpers import scripted_world, tasks_of
from tests.helpers import api, new_agent, set_policy
from tests.radar.conftest import web  # noqa: F401  (fixture: the fake internet)
from tests.radar.radar_helpers import clear_radar, new_keyword, new_source
from tests.test_m12_e2e import drive

FIX = Path(__file__).parent / "fixtures" / "radar"
RSS = (FIX / "rss2.xml").read_bytes()
HN = (FIX / "hn_algolia.json").read_bytes()
PAGE = b"<html><head><title>Agent teams</title></head><body><h1>Why agent teams fail</h1><p>Setup is slow.</p></body></html>"


@pytest.fixture(autouse=True)
def radar_online_against_the_fake_web(monkeypatch):
    """The suite runs with the radar offline (``COMPANY_OS_RADAR_OFFLINE``); this test drives the
    fetcher through the fake internet (``web``), so it lifts the switch itself. Sockets stay blocked
    by the fake transport: nothing leaves the process."""
    monkeypatch.delenv(OFFLINE_ENV, raising=False)


def due_only(app, cid: str, name: str, clock) -> None:
    """Exactly this schedule is due; every other one is off (wall-clock cron routines)."""
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            s.enabled = s.name == name
            s.next_run_at = clock() - timedelta(minutes=1) if s.enabled else None
        db.commit()


def actions(app, cid: str) -> list[str]:
    with app.state.session_factory() as db:
        return [
            e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.company_id == cid))
        ]


def test_a_feed_item_becomes_a_finished_task_through_brief_idea_and_triage(
    app,
    client,
    anon_client,
    scripted,
    scheduler,
    worker,
    engine,
    clock,
    web,  # noqa: F811
):
    w = scripted_world(client, scripted, anon_client, with_team=False)
    cid = w.cid
    set_policy(client, cid, initiative_enabled=False, max_planning_runs_per_hour=60)

    # ---- the team: a Market Researcher, a project lead and a project
    researcher = new_agent(
        client, cid, name="Rhea", role_title="Market Researcher", role_template="market-researcher"
    )
    lead = new_agent(
        client,
        cid,
        name="Lena",
        kind="lead",
        role_title="Product Lead",
        manager_agent_id=w.founder["id"],
    )
    project = client.post(
        api(cid, "/projects"),
        json={"name": "Product", "status": "active", "lead_agent_id": lead["id"]},
    ).json()

    # ---- the fake internet and the company's radar: one feed, Hacker News, one page
    web.site("blog.example.test")
    web.add("https://blog.example.test/feed.xml", RSS)
    web.site("hn.algolia.com")
    web.add(
        "https://hn.algolia.com/api/v1/search_by_date?query=agent&tags=story",
        HN,
        content_type="application/json",
    )
    web.site("blog.example.test")
    web.add("https://blog.example.test/agents-fail", PAGE, content_type="text/html")
    with app.state.session_factory() as db:
        clear_radar(db, cid)
        new_source(
            db,
            cid,
            "example-blog",
            category="blog",
            fetch_url="https://blog.example.test/feed.xml",
            keep_unmatched=True,
        )
        new_source(
            db,
            cid,
            "hn",
            category="forum",
            adapter="hn_algolia",
            fetch_url="https://hn.algolia.com/api/v1/search_by_date?query={query}&tags=story",
            query_mode="per_keyword",
        )
        new_keyword(db, cid, "agent", kind="topic", weight=4)
        db.commit()

    # ---- stage 1: the fetch job (enqueued as the owner's "fetch now", run by a radar worker)
    assert client.post(api(cid, "/radar/fetch-now")).status_code == 202
    clock.advance(5)  # the job was queued on the wall clock; the engine runs on the fake one
    Worker(engine, worker_id="radar-worker", monitor_interval=0.01, kinds=("radar_fetch",)).drain()
    with app.state.session_factory() as db:
        stored = list(db.scalars(select(RadarItem).where(RadarItem.company_id == cid)))
    assert len(stored) == 5 and {i.status for i in stored} == {
        "new"
    }  # 2 feed items (one has no title/link) + 3 from HN
    item_ids = [i.id for i in stored]
    assert "radar.fetched" in actions(app, cid)

    # ---- stage 2: hiring the Market Researcher moves the daily routine to it
    seen: dict = {"routine": [], "triage": [], "work": []}

    def done(call, summary="ok"):
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": summary}
        )

    def researcher_handler(call):
        task = call.request.task
        seen["routine"].append(task.description or "")
        digest = call.api.ok("GET", "/radar/digest")
        ids = [i["id"] for i in digest["items"]]
        assert set(ids) == set(item_ids) and digest["caps"]["ideas_remaining_today"] == 5
        page = call.api.ok(
            "POST", "/radar/read", json={"url": "https://blog.example.test/agents-fail"}
        )
        assert "Setup is slow." in page["content"]
        brief = call.api.ok(
            "POST",
            "/radar/briefs",
            json={
                "kind": "daily",
                "summary": "People say agent teams are hard to set up.",
                "themes": [{"title": "Setup pain", "kind": "critique", "item_ids": ids}],
                "relevance": [{"item_id": i, "relevance": 80} for i in ids],
                "body": "# Daily radar\n\nSetup is slow for agent teams.",
            },
        )
        seen["brief"] = brief["id"]
        idea = {
            "title": "Add a guided setup wizard",
            "problem": "Posts say setting up an agent team takes too long",
            "proposal": "A five step wizard with sane defaults",
            "item_ids": ids,
            "impact": 4, "effort": 2, "confidence": 4,
        }  # fmt: skip
        first = call.api.ok("POST", "/radar/ideas", json=idea)
        seen["idea"] = first["id"]
        dup = call.api.ok(
            "POST", "/radar/ideas", json={**idea, "title": "Add a guided setup wizard!"}, expect=409
        )
        assert (
            dup["error"]["code"] == "duplicate_idea"
            and dup["error"]["details"]["existing_id"] == first["id"]
        )
        call.api.ok(
            "POST", "/radar/ideas", json={**idea, "title": "Cache the planner output", "impact": 2}
        )
        done(call, "Radar brief written")

    def founder_handler(call):
        task = call.request.task
        if task.kind == "review":  # the work of the project lead is reviewed by its manager
            return call.api.ok(
                "POST", f"/tasks/{task.id}/review", json={"decision": "accept", "note": "good"}
            )
        if task.title != "Radar ideas triage":
            return done(call, "handled")
        seen["triage"].append(task.description or "")
        digest = call.api.ok("GET", "/radar/digest")
        best = max(digest["open_ideas"], key=lambda i: i["priority_score"])
        out = call.api.ok(
            "POST",
            f"/radar/ideas/{best['id']}/decide",
            json={"action": "accept", "project_id": project["id"], "note": "clear win"},
        )
        seen["accepted"] = out
        for other in digest["open_ideas"]:
            if other["id"] != best["id"]:
                call.api.ok(
                    "POST",
                    f"/radar/ideas/{other['id']}/decide",
                    json={"action": "reject", "note": "later"},
                )
        done(call, "Triaged")

    def lead_handler(call):
        task = call.request.task
        if task.kind == "review":
            call.api.ok(
                "POST", f"/tasks/{task.id}/review", json={"decision": "accept", "note": "good"}
            )
        elif task.kind == "work":
            seen["work"].append(task.description or "")
            art = call.api.ok(
                "POST",
                "/artifacts",
                json={"title": task.title, "kind": "document", "content": "# Wizard\nBuilt."},
            )
            call.api.ok(
                "POST",
                f"/tasks/{task.id}/complete",
                json={"result_summary": "Built the wizard", "output_refs": [art["id"]]},
            )
        else:
            done(call)

    scripted.handlers.update(
        {"Rhea": researcher_handler, w.founder["name"]: founder_handler, "Lena": lead_handler}
    )
    scripted.handlers.update({w.hr["name"]: lambda call: done(call)})

    def radar_schedule_agent(name: str) -> str:
        with app.state.session_factory() as db:
            return (
                db.scalars(
                    select(Schedule).where(Schedule.company_id == cid, Schedule.name == name)
                )
                .one()
                .agent_id
            )

    # the hire (activation hook) moved the daily routine from the Co-founder to the researcher
    assert radar_schedule_agent("market-radar") == researcher["id"]
    clock.advance(120)
    scheduler.tick()  # the watchdog runs about once a minute
    assert radar_schedule_agent("market-radar") == researcher["id"]
    assert radar_schedule_agent("ideas-triage") == w.founder["id"]

    # ---- stage 3: the daily radar routine fires and the researcher does its job
    due_only(app, cid, "market-radar", clock)
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: "idea" in seen and tasks_done(app, cid, "Daily market radar"),
        what="the radar run",
    )
    [routine_task] = [t for t in tasks_of(app, cid) if t.title == "Daily market radar"]
    assert routine_task.assignee_agent_id == researcher["id"] and routine_task.status == "done"
    text = seen["routine"][0]
    assert "Radar: 5 new items from 2 sources, 0 flagged; 0 open ideas." in text
    assert "Why agent teams fail" not in text and "Plain post" not in text  # counts only (F6)
    with app.state.session_factory() as db:
        brief = db.get(RadarBrief, seen["brief"])
        assert brief.author_agent_id == researcher["id"] and brief.ideas_created == 2
        ideas = list(db.scalars(select(RadarIdea).where(RadarIdea.company_id == cid)))
        assert {i.status for i in ideas} == {"open"} and len(
            ideas
        ) == 2  # the duplicate was refused
        assert {i.status for i in db.scalars(select(RadarItem))} == {"used"}

    # ---- stage 4: the Co-founder's triage accepts the best idea into the project
    due_only(app, cid, "ideas-triage", clock)
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: "accepted" in seen and tasks_done(app, cid, "Radar ideas triage"),
        what="the triage",
    )
    accepted = seen["accepted"]
    assert accepted["idea"]["status"] == "accepted" and accepted["project_id"] == project["id"]
    assert "Radar:" in seen["triage"][0] and "Add a guided setup wizard" not in seen["triage"][0]

    # ---- stage 5: the M12 engine runs the work task; the idea is done
    task_id = accepted["task_id"]
    try:
        drive(
            app,
            scheduler,
            worker,
            clock,
            lambda: one_task(app, task_id).status == "done",
            what="the work task",
        )
    except AssertionError:
        raise AssertionError(debug_task(app, task_id)) from None
    work = one_task(app, task_id)
    assert work.title == "Add a guided setup wizard" and work.project_id == project["id"]
    assert work.assignee_agent_id == lead["id"] and f"radar idea [id {seen['idea']}]" in (
        seen["work"][0] or ""
    ).lower().replace("from ", "")
    assert "Why agent teams fail" not in work.description  # no item text rode into the task
    with app.state.session_factory() as db:
        by_id = {i.id: i for i in db.scalars(select(RadarIdea).where(RadarIdea.company_id == cid))}
    assert by_id[seen["idea"]].status == "done" and by_id[seen["idea"]].task_id == task_id
    assert sorted(i.status for i in by_id.values()) == ["done", "rejected"]
    trail = set(actions(app, cid))
    assert {
        "radar.fetched",
        "radar.brief.created",
        "radar.idea.created",
        "radar.idea.accepted",
        "radar.idea.rejected",
        "radar.idea.done",
        "task.created",
    } <= trail
    # the owner sees the whole story through the API
    status = client.get(api(cid, "/radar")).json()
    assert (
        status["runner_role"] == "market_researcher"
        and status["open_ideas"] == 0
        and status["ideas_accepted_this_week"] == 1
    )
    shown = client.get(api(cid, f"/radar/ideas/{seen['idea']}")).json()
    assert (
        shown["status"] == "done"
        and shown["task_status"] == "done"
        and shown["project_name"] == "Product"
    )


def tasks_done(app, cid: str, title: str) -> bool:
    return any(t.title == title and t.status == "done" for t in tasks_of(app, cid))


def one_task(app, task_id: str) -> Task:
    with app.state.session_factory() as db:
        row = db.get(Task, task_id)
        db.expunge(row)
        return row


def debug_task(app, task_id):
    from app.models.agent import Agent
    from app.models.run import TaskRun

    with app.state.session_factory() as db:
        t = db.get(Task, task_id)
        a = db.get(Agent, t.assignee_agent_id)
        runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id == task_id)))
        return f"task {t.status} agent {a.name} {a.status} {a.runtime} runs {[(r.status, r.error) for r in runs]} next {t.next_attempt_at} attempts {t.attempt_count}"
