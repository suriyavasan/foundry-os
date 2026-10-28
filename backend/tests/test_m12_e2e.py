"""M12 end to end: the company runs by itself after one owner note.

Two scenarios, both on the real engine (scheduler tick + worker + watchdog on a fake clock) with
no model, no coding CLI and no network:

* API mode: note -> Co-founder (goal, project, triage) -> HR hires a lead and a specialist ->
  the lead plans the project with ``company_plan_project`` -> the specialist does the work ->
  reviews -> the project completes -> the goal is reviewed and achieved -> the idle company's
  initiative wake makes the Co-founder propose the next goal. Agents are ``ScriptedRuntime``
  handlers that call the REAL agent API with their run token, exactly the calls the runtime's
  company tools make.
* Hybrid mode: management stays on the API ``company_agent`` runtime while work tasks go to a CLI
  (the real OpenCode adapter driving a fake binary, tests/m10_helpers.py). Two tasks share one
  project repository: task 1's CLI writes a file that is merged, task 2's workspace contains it.

Not covered (and not claimed): real model quality, a real coding CLI, real ``complete_task`` over
MCP (the fake CLI's completion goes through the real agent API instead).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.autonomy import WakeRequest
from app.models.run import TaskRun
from app.models.work import Goal, Project, Task
from tests.autonomy_helpers import scripted_world, tasks_of
from tests.helpers import api, set_policy
from tests.m10_helpers import add_connection, make_ready

# ------------------------------------------------------------------ shared helpers


def audit_rows(app, cid: str) -> list[AuditEvent]:
    with app.state.session_factory() as db:
        rows = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.company_id == cid)
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
        db.expunge_all()
        return rows


def audit_actions(app, cid: str) -> list[str]:
    return [e.action for e in audit_rows(app, cid)]


def drive(app, scheduler, worker, clock, done, *, step: float = 30.0, rounds: int = 400, what=""):
    """Tick the scheduler (dispatch + watchdog + schedules), run what is queued, advance the fake
    clock, until ``done()`` holds. Fails loudly instead of hanging."""
    for _ in range(rounds):
        if done():
            return
        clock.advance(step)
        scheduler.tick()
        worker.drain()
    assert done(), f"the company did not reach: {what or 'the expected state'}"


def plan_project(call, project_id: str, items: list[dict]) -> dict:
    """What the ``company_plan_project`` tool does (runtime/tools/company_tools.py): one
    ``POST /tasks`` per item in dependency order, ``depends_on_keys`` resolved to the ids created
    earlier in the same call, stopping at the first limit error."""
    ids: dict[str, str] = {}
    created: list[dict] = []
    for item in items:
        body = {
            "title": item["title"],
            "description": item.get("description"),
            "acceptance_criteria": item.get("acceptance_criteria"),
            "assignee_agent_id": item.get("assignee_agent_id"),
            "project_id": project_id,
            "depends_on": [ids[k] for k in item.get("depends_on_keys", [])] or None,
            "idempotency_key": f"{call.request.run_id}:{item['key']}",
        }
        body = {k: v for k, v in body.items() if v is not None}
        task = call.api.ok("POST", "/tasks", json=body)
        ids[item["key"]] = task["id"]
        created.append(task)
    return {"project_id": project_id, "created": created}


def one(app, model, row_id):
    with app.state.session_factory() as db:
        row = db.get(model, row_id)
        db.expunge(row)
        return row


# ------------------------------------------------------------------ 1. API mode


@pytest.mark.parametrize(
    "lead_closes_project", [True, False], ids=["lead-closes", "watchdog-closes"]
)
def test_api_mode_a_note_becomes_finished_work_and_the_next_goal_without_a_human(
    app, client, anon_client, scripted, scheduler, worker, clock, lead_closes_project
):
    w = scripted_world(client, scripted, anon_client, with_team=False)
    cid = w.cid
    make_ready(app, client, cid)  # API mode: one model provider, no coding CLI
    assert client.get(api(cid, "/policy")).json()["work_mode"] == "api"
    set_policy(client, cid, initiative_interval_seconds=600, max_planning_runs_per_hour=60)
    with app.state.session_factory() as db:  # start from a quiet company
        db.execute(delete(WakeRequest))
        db.commit()

    seen: dict = {"founder": [], "lead": [], "hr": [], "wakes": []}
    team: dict = {}

    def complete(call, summary="ok"):
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": summary}
        )

    def founder(call):
        task = call.request.task
        seen["founder"].append(task.description or "")
        if task.kind != "planning":
            return complete(call, "routine handled")
        text = task.description or ""
        state = call.api.ok("GET", "/state")
        for note in state["notes"]["items"]:
            goal = call.api.ok(
                "POST", "/goals", json={"title": "Launch the landing page", "status": "active"}
            )
            project = call.api.ok(
                "POST",
                "/projects",
                json={"name": "Landing page", "goal_id": goal["id"], "status": "active"},
            )  # no lead: the platform asks HR for one
            team["goal"], team["project"] = goal, project
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
                    "reply": "Created the goal and a project; HR will hire a lead.",
                },
            )
        if "has no open projects" in text:  # goal review: the work is finished
            for goal in state["goals"]:
                if goal["status"] == "active" and goal["open_projects"] == 0:
                    assert goal["ended_projects"] >= 1
                    call.api.ok("PATCH", f"/goals/{goal['id']}", json={"status": "achieved"})
                    seen["achieved"] = goal["id"]
        if "nothing in flight" in text:  # initiative: propose the next piece of work
            nxt = call.api.ok(
                "POST",
                "/goals",
                json={"title": "Grow signups from the landing page", "status": "proposed"},
            )
            seen["next_goal"] = nxt["id"]
        complete(call, "Handled: " + (task.title or ""))

    def hr(call):
        task = call.request.task
        seen["hr"].append(task.description or "")
        if task.kind != "hiring":
            return complete(call, "ok")
        if task.title.startswith("Hire: lead for"):
            lead = call.api.ok(
                "POST",
                "/hires",
                json={
                    "role_template": "marketing-lead",
                    "name": "Lena",
                    "manager_agent_id": w.founder["id"],
                    "reason": "The Landing page project has no lead.",
                    "task_id": task.id,
                },
            )
            assert lead["auto_activated"] is True, lead
            team["lead"] = lead["agent"]
            # Hiring the lead hands it the project; the platform leaves this run's own "hire a
            # lead" task alone (hiring.cancel_lead_hiring), so HR completes it normally.
            call.api.ok(
                "POST",
                f"/tasks/{task.id}/complete",
                json={"result_summary": "Hired the lead"},
            )
        else:  # a hiring request from the lead for a specialist
            spec = call.api.ok(
                "POST",
                "/hires",
                json={
                    "role_template": "growth-marketer",
                    "name": "Sam",
                    "manager_agent_id": team["lead"]["id"],
                    "reason": "The lead asked for a copywriter.",
                    "task_id": task.id,
                },
            )
            assert spec["auto_activated"] is True, spec
            team["specialist"] = spec["agent"]
            complete(call, "Hired the specialist")

    def lead(call):
        task = call.request.task
        seen["lead"].append((task.kind, task.title, task.description or ""))
        if task.kind == "planning":
            state = call.api.ok("GET", "/state")
            assert state["role"] == "lead"
            [proj] = [p for p in state["projects"] if p["name"] == "Landing page"]
            reports = [a for a in call.api.ok("GET", "/agents")["items"] if a["name"] == "Sam"]
            if proj["tasks_total"] == 0 and not reports:
                req = call.api.ok(
                    "POST",
                    "/hiring-requests",
                    json={
                        "required_skills": ["copywriting"],
                        "role_hint": "Copywriter",
                        "reason": "Nobody on the team can write the page copy.",
                        "task_id": task.id,
                    },
                    expect=(200, 201),
                )
                seen["hire_request"] = req["hiring_task_id"]
            elif proj["tasks_total"] == 0:
                plan_project(
                    call,
                    proj["id"],
                    [
                        {
                            "key": "a",
                            "title": "Write positioning",
                            "acceptance_criteria": "One paragraph",
                            "assignee_agent_id": reports[0]["id"],
                        },
                        {
                            "key": "b",
                            "title": "Write page copy",
                            "acceptance_criteria": "Headline and three bullets",
                            "assignee_agent_id": reports[0]["id"],
                            "depends_on_keys": ["a"],
                        },
                        {
                            "key": "c",
                            "title": "Build the page",
                            "acceptance_criteria": "A page exists",
                            "assignee_agent_id": reports[0]["id"],
                            "depends_on_keys": ["b"],
                        },
                    ],
                )
            elif proj["tasks_open"] == 0 and lead_closes_project:
                call.api.ok("PATCH", f"/projects/{proj['id']}", json={"status": "completed"})
            complete(call, "Planned")
        elif task.kind == "review":
            call.api.ok(
                "POST", f"/tasks/{task.id}/review", json={"decision": "accept", "note": "good"}
            )

    def specialist(call):
        task = call.request.task
        art = call.api.ok(
            "POST",
            "/artifacts",
            json={"title": task.title, "kind": "document", "content": f"# {task.title}\nDone."},
        )
        call.api.ok(
            "POST",
            f"/tasks/{task.id}/complete",
            json={"result_summary": "Done", "output_refs": [art["id"]]},
        )

    scripted.handlers.update({w.founder["name"]: founder, w.hr["name"]: hr})
    scripted.handlers.update({"Lena": lead, "Sam": specialist})

    # ---- stage 0: the owner's one and only action
    note = client.post(
        api(cid, "/notes"), json={"body": "Launch a landing page for the AI assistant."}
    )
    assert note.status_code == 201 and note.json()["status"] == "new"
    note_id = note.json()["id"]
    after_note = {e.id for e in audit_rows(app, cid)}

    # ---- stage 1: the Co-founder wakes (debounced), makes a goal + project, triages the note
    drive(app, scheduler, worker, clock, lambda: "project" in team, what="goal and project")
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: client.get(api(cid, f"/notes/{note_id}")).json()["status"] == "triaged",
        what="the note being triaged",
    )
    detail = client.get(api(cid, f"/notes/{note_id}")).json()
    assert detail["triage"]["category"] == "directive"
    assert {a["type"] for a in detail["triage"]["actions"]} == {"goal", "project"}
    goal_id, project_id = team["goal"]["id"], team["project"]["id"]
    assert one(app, Goal, goal_id).created_by_type == "agent"
    assert one(app, Project, project_id).created_by_type == "agent"
    assert {"goal.created", "project.created", "note.triaged"} <= set(audit_actions(app, cid))

    # ---- stage 2: no lead exists, so the platform asks HR, who hires one; the project is handed over
    drive(app, scheduler, worker, clock, lambda: "lead" in team, what="HR hiring a lead")
    [hiring] = [
        t
        for t in tasks_of(app, cid, kind="hiring")
        if t.idempotency_key == f"hiring:lead:{project_id}"
    ]
    assert hiring.assignee_agent_id == w.hr["id"] and hiring.created_by_type == "system"
    lead_id = team["lead"]["id"]
    assert one(app, Project, project_id).lead_agent_id == lead_id
    assert one(app, Agent, lead_id).status == "active" and one(app, Agent, lead_id).kind == "lead"
    assert "hire.created" in audit_actions(app, cid)

    # ---- stage 2b: the woken lead has no team, asks for a copywriter; HR hires one under the lead
    drive(
        app, scheduler, worker, clock, lambda: "specialist" in team, what="HR hiring a specialist"
    )
    spec_id = team["specialist"]["id"]
    assert "hire_request" in seen
    assert one(app, Task, seen["hire_request"]).assignee_agent_id == w.hr["id"]
    assert one(app, Agent, spec_id).manager_agent_id == lead_id
    assert one(app, Agent, spec_id).status == "active"

    # ---- stage 3: the lead plans the project once (company_plan_project)
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: len(tasks_of(app, cid, kind="work")) >= 3,
        what="the lead's plan",
    )
    work = tasks_of(app, cid, kind="work")
    assert [t.title for t in work] == ["Write positioning", "Write page copy", "Build the page"]
    assert all(t.project_id == project_id and t.assignee_agent_id == spec_id for t in work)
    assert all(t.created_by_type == "agent" and t.created_by_id == lead_id for t in work)
    for before, after_ in zip(work, work[1:], strict=False):  # the plan's dependency chain
        deps = client.get(api(cid, f"/tasks/{after_.id}")).json()["depends_on"]
        assert deps == [before.id]

    # ---- stage 4: the specialist works, the lead reviews, everything ends done
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: all(t.status == "done" for t in tasks_of(app, cid, kind="work")),
        what="all work tasks done",
    )
    reviews = tasks_of(app, cid, kind="review")
    assert len(reviews) == 3 and all(t.status == "done" for t in reviews)
    assert all(t.assignee_agent_id == lead_id for t in reviews)

    # ---- stage 5: the project closes (the lead, or the watchdog when nobody does)
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: one(app, Project, project_id).status == "completed",
        what="project completion",
        step=60.0,
    )
    actions = audit_actions(app, cid)
    if lead_closes_project:
        assert "project.auto_completed" not in actions
    else:
        assert "project.auto_completed" in actions and "autonomy.intervened" in actions

    # ---- stage 6: the goal is reviewed and achieved by the Co-founder
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: one(app, Goal, goal_id).status == "achieved",
        what="the goal being achieved",
        step=60.0,
    )
    assert seen["achieved"] == goal_id
    assert any("has no open projects" in text for text in seen["founder"])

    # ---- stage 7: idle company + initiative_enabled -> the Co-founder proposes the next goal
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: "next_goal" in seen,
        what="the initiative wake and the next goal",
        step=120.0,
        rounds=600,
    )
    nxt = one(app, Goal, seen["next_goal"])
    assert nxt.status == "proposed" and nxt.created_by_type == "agent"
    assert any("nothing in flight" in text for text in seen["founder"])

    # ---- the whole way no run failed, nothing was left half done, and no person acted
    assert scripted.errors == [], scripted.errors
    with app.state.session_factory() as db:
        runs = list(db.scalars(select(TaskRun).where(TaskRun.company_id == cid)))
        assert runs and {r.runtime for r in runs} == {"scripted"}
        # every run succeeded, including HR's "hire a lead" run that made the hire
        assert {r.status for r in runs} == {"succeeded"}
        hiring_status = {
            t.id: t.status for t in db.scalars(select(Task).where(Task.kind == "hiring"))
        }
        assert hiring_status == {hiring.id: "done", seen["hire_request"]: "done"}
        open_left = db.scalars(
            select(Task).where(
                Task.company_id == cid,
                Task.status.in_(["ready", "in_progress", "in_review", "revision_requested"]),
            )
        ).all()
        assert open_left == []
    after = [e for e in audit_rows(app, cid) if e.id not in after_note]
    assert after, "nothing was audited after the note"
    assert {e.actor_type for e in after} <= {"agent", "system"}, {
        (e.actor_type, e.action) for e in after if e.actor_type not in ("agent", "system")
    }
    assert {"hire.created", "goal.updated", "project.updated"} <= {e.action for e in after} or (
        "project.auto_completed" in {e.action for e in after}
    )


# ------------------------------------------------------------------ 2. Hybrid mode

# A stand-in "coding CLI": a plain Python script (not a model, not a real Claude Code / Codex /
# OpenCode). Per invocation it records which files its working directory already holds, then
# writes the files the script asks for and prints an OpenCode-format stream. Invocation n uses
# ``runs[n]`` of ``opencode.script.json`` (the tasks run one after the other).
FAKE_OPENCODE = r"""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:2] == ["--version"]:
    print("1.18.3"); sys.exit(0)
script = json.load(open(os.path.join(here, "opencode.script.json")))
log = os.path.join(here, "opencode.records.jsonl")
n = sum(1 for _ in open(log)) if os.path.exists(log) else 0
cwd = os.getcwd()
seen = {}
for root, dirs, files in os.walk(cwd):
    dirs[:] = [d for d in dirs if d != ".git"]
    for name in files:
        if name != ".git":
            rel = os.path.relpath(os.path.join(root, name), cwd)
            seen[rel] = open(os.path.join(root, name)).read()
with open(log, "a") as fh:
    fh.write(json.dumps({"n": n, "cwd": cwd, "files_at_start": seen, "prompt": max(sys.argv[1:], key=len)}) + chr(10))
for item in script["runs"][n]["write_files"]:
    path = os.path.join(cwd, item["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(item["content"])
for line in script["lines"]:
    print(json.dumps(line), flush=True)
"""


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_hybrid_mode_management_on_the_api_work_on_the_cli_and_task_two_sees_task_ones_file(
    app, client, anon_client, scheduler, worker, clock, tmp_path
):
    from app.runtimes.base import ExecutionRequest
    from app.runtimes.cli.opencode import OpenCodeAdapter
    from app.runtimes.scripted import AgentApi, ScriptedRuntime
    from app.services import project_workspaces as pw
    from tests.helpers import new_agent
    from tests.m10_helpers import OPENCODE_OK, write_script

    class ApiAgent(ScriptedRuntime):
        """The API ``company_agent`` runtime (management runs), as a scripted stand-in."""

        id = "company_agent"
        display_name = "Company agent (scripted stand-in)"

    class CompletingOpenCode(OpenCodeAdapter):
        """The REAL OpenCode adapter (argv, isolation, workspace, parsing) driving the fake
        binary. A real CLI calls ``complete_task`` through the company MCP server while it runs; a
        fake binary cannot, so this hook makes that same call to the real agent API with the run's
        token just before the adapter checks whether the task is still open."""

        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self.requests: dict[str, ExecutionRequest] = {}
            self.api_client = None

        def execute(self, request, sink, cancel):
            self.requests[request.task.id] = request
            return super().execute(request, sink, cancel)

        def _task_open(self, task_id):
            request = self.requests.pop(task_id, None)
            if request is not None:
                AgentApi(self.api_client, request.run_token).ok(
                    "POST",
                    f"/tasks/{task_id}/complete",
                    json={"result_summary": f"CLI finished: {request.task.title}"},
                )
            return super()._task_open(task_id)

    api_agent = ApiAgent()
    api_agent.api_client = anon_client
    app.state.runtimes.register(api_agent)
    cli = CompletingOpenCode(
        app.state.settings, app.state.session_factory, app.state.keys, app.state.cli_env
    )
    cli.api_client = anon_client
    app.state.runtimes.register(cli)

    company = client.post("/api/companies", json={"name": "Hybrid Co", "mission": "Ship"}).json()
    cid = company["id"]
    make_ready(app, client, cid)
    bin_dir = tmp_path / "bin"
    app.state.cli_env.extra_dirs.append(bin_dir)
    write_script(bin_dir, "opencode", FAKE_OPENCODE)
    (bin_dir / "opencode.script.json").write_text(
        json.dumps(
            {
                "lines": OPENCODE_OK,
                "runs": [
                    {"write_files": [{"path": "index.html", "content": "<h1>Hello</h1>\n"}]},
                    {"write_files": [{"path": "css/style.css", "content": "h1 { color: red }\n"}]},
                ],
            }
        )
    )
    # the stack: the API model for management (heavy), OpenCode with a key for work (everyday)
    first = client.get(api(cid, "/ai-stack")).json()["items"][0]
    client.patch(api(cid, f"/ai-stack/{first['id']}"), json={"use_for": "heavy"})
    cred = client.post(
        api(cid, "/credentials"), json={"name": "oc", "value": "sk-test-0123456789abcdef"}
    ).json()
    add_connection(
        app, client, cid, "opencode", credential_id=cred["id"], model="anthropic/claude-x",
        use_for="everyday",
    )  # fmt: skip
    set_policy(client, cid, max_planning_runs_per_hour=60)

    founder = next(
        a for a in client.get(api(cid, "/agents")).json()["items"] if a["kind"] == "founder"
    )
    assert (
        client.patch(
            api(cid, f"/agents/{founder['id']}"), json={"runtime": "company_agent"}
        ).status_code
        == 200
    )
    lead = new_agent(
        client, cid, name="Lena", kind="lead", runtime="company_agent",
        role_title="Engineering Lead", manager_agent_id=founder["id"],
    )  # fmt: skip
    sam = new_agent(
        client, cid, name="Sam", runtime="company_agent", role_title="Software Engineer",
        manager_agent_id=lead["id"],
    )  # fmt: skip
    with app.state.session_factory() as db:
        db.execute(delete(WakeRequest))
        db.commit()

    seen: dict = {"review_view": []}

    def finish(call, summary="ok"):
        call.api.ok(
            "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": summary}
        )

    def founder_handler(call):
        if call.request.task.kind == "planning":
            for note in call.api.ok("GET", "/state")["notes"]["items"]:
                goal = call.api.ok(
                    "POST", "/goals", json={"title": "Ship the site", "status": "active"}
                )
                project = call.api.ok(
                    "POST",
                    "/projects",
                    json={
                        "name": "Website",
                        "goal_id": goal["id"],
                        "lead_agent_id": lead["id"],
                        "status": "active",
                    },
                )
                seen["project_id"] = project["id"]
                call.api.ok(
                    "POST",
                    f"/notes/{note['id']}/triage",
                    json={
                        "category": "directive",
                        "summary": "Build the site",
                        "actions": [
                            {"type": "goal", "ref_id": goal["id"]},
                            {"type": "project", "ref_id": project["id"]},
                        ],
                        "reply": "Website project started, led by Lena.",
                    },
                )
        finish(call)

    def lead_handler(call):
        task = call.request.task
        if task.kind == "planning":
            state = call.api.ok("GET", "/state")
            proj = next(p for p in state["projects"] if p["name"] == "Website")
            if proj["tasks_total"] == 0:
                plan_project(
                    call,
                    proj["id"],
                    [
                        {
                            "key": "a",
                            "title": "Create the index page",
                            "acceptance_criteria": "index.html exists",
                            "assignee_agent_id": sam["id"],
                        },
                        {
                            "key": "b",
                            "title": "Add the stylesheet",
                            "acceptance_criteria": "css/style.css exists next to index.html",
                            "assignee_agent_id": sam["id"],
                            "depends_on_keys": ["a"],
                        },
                    ],
                )
            elif proj["tasks_open"] == 0:
                call.api.ok("PATCH", f"/projects/{proj['id']}", json={"status": "completed"})
            finish(call, "Planned")
        elif task.kind == "review":
            # a reviewer runs on the API runtime in a READ-ONLY copy of the project as merged so far
            ws = Path(call.request.workspace_dir)
            seen["review_view"].append(
                (
                    call.request.workspace_kind,
                    sorted(p.name for p in ws.rglob("*") if p.is_file() and p.name != ".git"),
                )
            )
            call.api.ok(
                "POST", f"/tasks/{task.id}/review", json={"decision": "accept", "note": "ok"}
            )

    api_agent.handlers.update({founder["name"]: founder_handler, "Lena": lead_handler})
    # Sam's work tasks never reach this runtime in hybrid mode: if one did, fail loudly.
    api_agent.handlers["Sam"] = lambda call: (_ for _ in ()).throw(
        AssertionError(f"work task '{call.request.task.title}' ran on the API runtime")
    )

    note = client.post(api(cid, "/notes"), json={"body": "Build the website."})
    assert note.status_code == 201
    after_note = {e.id for e in audit_rows(app, cid)}

    def work_titles_done():
        return {t.title for t in tasks_of(app, cid, kind="work") if t.status == "done"}

    drive(
        app, scheduler, worker, clock,
        lambda: work_titles_done() == {"Create the index page", "Add the stylesheet"},
        what="both CLI work tasks done", step=30.0,
    )  # fmt: skip
    project_id = seen["project_id"]
    drive(
        app, scheduler, worker, clock,
        lambda: one(app, Project, project_id).status == "completed",
        what="the project completing", step=60.0,
    )  # fmt: skip
    assert api_agent.errors == [], api_agent.errors

    # ---- which runtime ran what: management on the API, work on the CLI
    with app.state.session_factory() as db:
        rows = db.execute(
            select(TaskRun, Task, Agent)
            .join(Task, Task.id == TaskRun.task_id)
            .join(Agent, Agent.id == TaskRun.agent_id)
            .where(TaskRun.company_id == cid)
            .order_by(TaskRun.created_at)
        ).all()
        assert rows and all(run.status == "succeeded" for run, _, _ in rows)
        by_kind: dict[str, set[str]] = {}
        for run, task, _ in rows:
            by_kind.setdefault(task.kind, set()).add(run.runtime)
        assert by_kind["work"] == {"cli:opencode"}
        assert by_kind["planning"] == {"company_agent"}  # the Co-founder's and the lead's wakes
        assert by_kind["review"] == {"company_agent"}
        assert all(
            (run.routing_reason or "").endswith("for everyday work") and run.ai_connection_id
            for run, task, _ in rows
            if task.kind == "work"
        )
        management_agents = {a.name for run, _, a in rows if run.runtime == "company_agent"}
        assert {"Lena", founder["name"]} <= management_agents and "Sam" not in management_agents
        cli_runs = [(run, task) for run, task, _ in rows if run.runtime == "cli:opencode"]
        assert [t.title for _, t in cli_runs] == ["Create the index page", "Add the stylesheet"]

    # ---- the shared project repository: task 1's file was merged, task 2's workspace held it
    records = [
        json.loads(line) for line in (bin_dir / "opencode.records.jsonl").read_text().splitlines()
    ]
    assert len(records) == 2
    first, second = records
    assert first["files_at_start"] == {}  # nothing in the repository yet
    assert second["files_at_start"] == {"index.html": "<h1>Hello</h1>\n"}  # task 1's file, intact
    assert first["cwd"] != second["cwd"] and cid in first["cwd"] and cid in second["cwd"]
    assert "index.html" in second["prompt"] and "PROJECT REPOSITORY" in second["prompt"]
    assert "no files yet" in first["prompt"]
    repo = pw.main_repo(app.state.settings.workspaces_dir, cid, project_id)
    tree = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert sorted(tree) == ["css/style.css", "index.html"]
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--merges", "--format=%s", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    assert len(log) == 2  # one --no-ff merge per CLI run

    refs = [run.workspace_ref for run, _ in cli_runs]
    for ref in refs:
        assert ref["kind"] == "project" and ref["merged"] is True and ref["conflict"] is False
        assert ref["base"] and ref["commit"] and ref["base"] != ref["commit"]
    assert refs[1]["base"] != refs[0]["base"]  # task 2 started from the main task 1 had moved
    assert "workspace.merged" in audit_actions(app, cid)

    # ---- reviewers (API runtime) looked at a read-only copy of the merged project
    kinds = {kind for kind, _ in seen["review_view"]}
    assert kinds == {"review"} and len(seen["review_view"]) == 2
    assert seen["review_view"][0][1] == ["index.html"]
    assert seen["review_view"][1][1] == ["index.html", "style.css"]

    # ---- no person acted after the note
    after = [e for e in audit_rows(app, cid) if e.id not in after_note]
    assert {e.actor_type for e in after} <= {"agent", "system"}, {
        (e.actor_type, e.action) for e in after if e.actor_type not in ("agent", "system")
    }
