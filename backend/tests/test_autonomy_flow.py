"""Milestone 5, end to end with scripted agents that use the REAL agent API through their run tokens:
Founder's note -> debounced wake -> triage + goal + project with a lead -> lead decomposes ->
specialist executes -> review tasks -> project completed -> everyone reported."""

from __future__ import annotations

from sqlalchemy import select

from app.models.audit import AuditEvent
from tests.autonomy_helpers import (
    pending_wakes,
    run_world,
    scripted_world,
    tasks_of,
    wake_ready,
)
from tests.helpers import api, get_task


def test_note_to_finished_project_through_every_layer(
    app, client, anon_client, scripted, scheduler, worker, clock
):
    w = scripted_world(client, scripted, anon_client)
    cid = w.cid
    seen: dict = {"founder_runs": [], "lead_runs": []}

    def founder(call):
        kind = call.request.task.kind
        seen["founder_runs"].append(call.request.task.title)
        if kind == "planning":
            state = call.api.ok("GET", "/state")
            assert state["role"] == "founder"
            for note in state["notes"]["items"]:
                goal = call.api.ok(
                    "POST", "/goals", json={"title": "Launch the landing page", "status": "active"}
                )
                project = call.api.ok(
                    "POST",
                    "/projects",
                    json={
                        "name": "Landing page",
                        "goal_id": goal["id"],
                        "lead_agent_id": w.lead["id"],
                        "status": "active",
                    },
                )
                seen["goal"], seen["project"] = goal, project
                call.api.ok(
                    "POST",
                    f"/notes/{note['id']}/triage",
                    json={
                        "category": "directive",
                        "summary": "Launch a landing page",
                        "actions": [
                            {"type": "goal", "ref_id": goal["id"]},
                            {"type": "project", "ref_id": project["id"]},
                        ],
                        "reply": "Created the goal and a project led by Lena.",
                    },
                )
        call.api.ok(
            "POST",
            f"/tasks/{call.request.task.id}/complete",
            json={"result_summary": "Handled: " + call.request.task.title},
        )

    def lead(call):
        task = call.request.task
        seen["lead_runs"].append((task.kind, task.title))
        if task.kind == "planning":
            state = call.api.ok("GET", "/state")
            assert state["role"] == "lead" and [p["name"] for p in state["projects"]] == [
                "Landing page"
            ]
            project_id = state["projects"][0]["id"]
            if not state["projects"][0]["tasks_total"]:
                a = call.api.ok(
                    "POST",
                    "/tasks",
                    json={
                        "title": "Write positioning",
                        "project_id": project_id,
                        "acceptance_criteria": "One paragraph",
                    },
                )
                call.api.ok(
                    "POST",
                    "/tasks",
                    json={
                        "title": "Write page copy",
                        "project_id": project_id,
                        "acceptance_criteria": "Headline and three bullets",
                        "depends_on": [a["id"]],
                    },
                )
            elif state["projects"][0]["tasks_open"] == 0:  # woken because all work is finished
                call.api.ok("PATCH", f"/projects/{project_id}", json={"status": "completed"})
            call.api.ok("POST", f"/tasks/{task.id}/complete", json={"result_summary": "Planned"})
        elif task.kind == "review":
            target = call.api.ok("GET", "/context")["task"]["review_target"]
            assert (
                target["artifacts"]
                and "Positioning" in target["artifacts"][0]["excerpt"]
                or ("copy" in target["artifacts"][0]["excerpt"].lower())
            )
            call.api.ok(
                "POST",
                f"/tasks/{task.id}/review",  # the review task's own id resolves to its target
                json={"decision": "accept", "note": "good"},
            )

    def specialist(call):
        task = call.request.task
        art = call.api.ok(
            "POST",
            "/artifacts",
            json={
                "title": task.title,
                "kind": "document",
                "content": f"# {task.title}\nPositioning copy",
            },
        )
        call.api.ok(
            "POST",
            f"/tasks/{task.id}/complete",
            json={"result_summary": "Done", "output_refs": [art["id"]]},
        )

    scripted.handlers.update({"Atlas": founder, "Lena": lead, "Sam": specialist})

    note = client.post(
        api(cid, "/notes"), json={"body": "Launch a landing page for the AI assistant."}
    )
    assert note.status_code == 201 and note.json()["status"] == "new"
    note_id = note.json()["id"]
    # debounce: nothing wakes the founder inside the 60 s window
    clock.advance(30)
    scheduler.tick()
    assert pending_wakes(app) == 1 and not tasks_of(app, cid, kind="planning")
    wake_ready(clock)
    rounds = run_world(app, scheduler, worker, clock)
    assert rounds < 40, "the company did not settle"
    assert scripted.errors == [], scripted.errors

    detail = client.get(api(cid, f"/notes/{note_id}")).json()
    assert detail["status"] == "triaged" and detail["triage"]["category"] == "directive"
    assert {a["type"] for a in detail["triage"]["actions"]} == {"goal", "project"}
    assert [r["author_type"] for r in detail["replies"]] == ["agent"]
    assert detail["triaged_by_run_id"]

    projects = client.get(api(cid, "/projects")).json()["items"]
    assert [p["status"] for p in projects] == ["completed"]
    work = [t for t in tasks_of(app, cid) if t.kind == "work"]
    assert [t.title for t in work] == ["Write positioning", "Write page copy"]
    assert all(t.status == "done" and t.assignee_agent_id == w.specialist["id"] for t in work)
    reviews = [t for t in tasks_of(app, cid) if t.kind == "review"]
    assert len(reviews) == 2 and all(t.status == "done" for t in reviews)
    assert all(t.assignee_agent_id == w.lead["id"] for t in reviews)
    # the planning wake tasks all finished and the Co-founder was woken again when the project ended
    assert all(t.status == "done" for t in tasks_of(app, cid, kind="planning"))
    assert any("Planning run" in title for title in seen["founder_runs"])
    summary = client.get(api(cid, "/cofounder/summary")).json()
    assert summary["unprocessed_notes"] == 0 and summary["wake_pending"] is False
    assert summary["plan"]["summary"].startswith("Handled:")
    first = get_task(client, cid, work[0].id)
    assert first["created_by_type"] == "agent" and first["created_by_id"] == w.lead["id"]


def test_a_project_nobody_closes_is_completed_by_the_watchdog_and_the_goal_is_reviewed(
    app, client, anon_client, scripted, scheduler, worker, clock
):
    """M12: the lead is woken when the work is finished but never closes the project; the
    Co-founder is woken next; finally the system completes it and the Co-founder reviews the goal."""
    w = scripted_world(client, scripted, anon_client)
    cid = w.cid
    founder_wakes: list[str] = []
    lead_wakes: list[str] = []

    def done(call, summary="ok"):
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": summary}
        )

    def founder(call):
        founder_wakes.append(call.request.task.description or "")
        done(call)

    def lead(call):
        if call.request.task.kind == "planning":
            lead_wakes.append(call.request.task.description or "")
            done(call)  # ...and never closes the project

    def specialist(call):
        done(call, "Done")

    scripted.handlers.update({"Atlas": founder, "Lena": lead, "Sam": specialist})
    goal = client.post(api(cid, "/goals"), json={"title": "Launch", "status": "active"}).json()
    project = client.post(
        api(cid, "/projects"),
        json={
            "name": "Landing page",
            "goal_id": goal["id"],
            "lead_agent_id": w.lead["id"],
            "status": "active",
        },
    ).json()
    for title in ("Write positioning", "Write page copy"):
        r = client.post(
            api(cid, "/tasks"),
            json={
                "title": title,
                "project_id": project["id"],
                "assignee_agent_id": w.specialist["id"],
            },
        )
        assert r.status_code == 201, r.text
    run_world(app, scheduler, worker, clock)
    assert [t.status for t in tasks_of(app, cid, kind="work")] == ["done", "done"]
    assert any("is finished" in text for text in lead_wakes)  # the hook woke the lead at once
    assert client.get(api(cid, f"/projects/{project['id']}")).json()["status"] == "active"
    founder_wakes.clear()
    for _ in range(200):  # the lead did nothing: the watchdog takes over
        clock.advance(30)
        scheduler.tick()
        worker.drain()
        status = client.get(api(cid, f"/projects/{project['id']}")).json()["status"]
        if status == "completed" and any("has no open projects" in t for t in founder_wakes):
            break
    assert scripted.errors == [], scripted.errors
    assert client.get(api(cid, f"/projects/{project['id']}")).json()["status"] == "completed"
    assert any("is finished and the project is still open" in t for t in founder_wakes)
    assert any(
        "has no open projects" in t and goal["id"] in t for t in founder_wakes
    )  # the goal review
    with app.state.session_factory() as db:
        actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"project.auto_completed", "autonomy.intervened"} <= actions
