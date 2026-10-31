"""M18 Part B: the end-to-end autonomy flows in the DEFAULT (work graph) mode.

The legacy end-to-end tests (test_autonomy_flow, test_m12_e2e, test_autonomy_loops) are pinned to the
old one-shot mode by ``ONE_SHOT_TESTS`` in conftest.py. These are their graph-mode counterparts, on
the same real engine (scheduler tick + worker + watchdog on a fake clock), the same scripted agents
that call the REAL agent API with their run tokens, and no model, no network.

What differs from one-shot mode, and is asserted explicitly here:

* every run emits ``graph.node`` events (prepare -> act -> check -> finish);
* ``company_complete_task`` only PROPOSES a result; the finish node completes the task;
* a work task with acceptance criteria whose self-check passes goes straight to done: no manager
  review task, unless the task has an explicit reviewer or a code deliverable.

Not covered (and not claimed): a real model's verdicts (the scripted verifier says "pass" unless the
agent's script says otherwise), a real coding CLI (a stand-in Python script drives the real OpenCode
adapter).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from sqlalchemy import delete, select

from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.autonomy import WakeRequest
from app.models.graph import GraphCheckpoint
from app.models.questions import FounderQuestion
from app.models.run import RunEvent, TaskRun
from app.models.work import Goal, Project, Task
from tests.autonomy_helpers import (
    pending_wakes,
    run_world,
    scripted_world,
    tasks_of,
    wake_ready,
)
from tests.helpers import api, get_task, new_agent, new_task, set_policy, settle
from tests.m10_helpers import OPENCODE_OK, add_connection, make_ready, write_script
from tests.test_m12_e2e import audit_actions, audit_rows, drive, one, plan_project

# ------------------------------------------------------------------ helpers


def runs_of(app, task_id) -> list[TaskRun]:
    with app.state.session_factory() as db:
        rows = list(
            db.scalars(
                select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.attempt_number)
            )
        )
        for r in rows:
            db.expunge(r)
        return rows


def node_events(app, run_id) -> list[tuple[str, str]]:
    with app.state.session_factory() as db:
        return [
            (e.payload["node"], e.payload["outcome"])
            for e in db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.type == "graph.node")
                .order_by(RunEvent.seq)
            )
        ]


def graph_runs(app, cid) -> list[tuple[Task, TaskRun]]:
    """Every (task, run) of the company, oldest first."""
    with app.state.session_factory() as db:
        rows = db.execute(
            select(Task, TaskRun)
            .join(TaskRun, TaskRun.task_id == Task.id)
            .where(TaskRun.company_id == cid)
            .order_by(TaskRun.created_at, TaskRun.id)
        ).all()
        db.expunge_all()
        return [(t, r) for t, r in rows]


def assert_every_run_used_the_graph(app, cid) -> None:
    """Graph mode is the default: every run of the company has a thread and graph.node events."""
    rows = graph_runs(app, cid)
    assert rows
    for task, run in rows:
        assert run.graph_thread_id == f"task:{task.id}", (task.title, run.graph_thread_id)
        nodes = node_events(app, run.id)
        assert nodes and nodes[0] == ("prepare", "ok"), (task.title, nodes)
        assert nodes[-1][0] in ("finish", "wait_founder", "wait_approval"), (task.title, nodes)


def assert_work_task_went_straight_to_done(app, cid) -> None:
    """Every work task: act proposed -> self-check passed -> finish applied it (no review cycle)."""
    work = [(t, r) for t, r in graph_runs(app, cid) if t.kind == "work"]
    assert work
    for task, run in work:
        assert node_events(app, run.id) == [
            ("prepare", "ok"),
            ("act", "proposed"),
            ("check", "passed"),
            ("finish", "checked/applied"),
        ], task.title


def audit_reasons(app, task_id) -> list[str]:
    with app.state.session_factory() as db:
        return [
            str(e.data.get("reason"))
            for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == task_id))
        ]


# ------------------------------------------------------------------ 1. note -> finished project


def test_graph_mode_note_to_finished_project_through_every_layer(
    app, client, anon_client, scripted, scheduler, worker, clock
):
    """Counterpart of test_autonomy_flow::test_note_to_finished_project_through_every_layer."""
    w = scripted_world(client, scripted, anon_client)
    cid = w.cid
    seen: dict = {"founder_runs": [], "lead_runs": [], "proposed": []}

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
        assert task.kind != "review", "a passed self-check must not create a manager review task"
        if task.kind == "planning":
            state = call.api.ok("GET", "/state")
            assert state["role"] == "lead"
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

    def specialist(call):
        task = call.request.task
        art = call.api.ok(
            "POST",
            "/artifacts",
            json={"title": task.title, "kind": "document", "content": f"# {task.title}\nCopy"},
        )
        answer = call.api.ok(
            "POST",
            f"/tasks/{task.id}/complete",
            json={"result_summary": "Done", "output_refs": [art["id"]]},
        )
        seen["proposed"].append(answer["status"])  # the tool call only PROPOSES in graph mode
        with app.state.session_factory() as db:
            seen.setdefault("status_after_complete", []).append(db.get(Task, task.id).status)

    scripted.handlers.update({"Atlas": founder, "Lena": lead, "Sam": specialist})

    note = client.post(
        api(cid, "/notes"), json={"body": "Launch a landing page for the AI assistant."}
    )
    assert note.status_code == 201 and note.json()["status"] == "new"
    note_id = note.json()["id"]
    clock.advance(30)  # debounce: nothing wakes the founder inside the 60 s window
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

    assert [p["status"] for p in client.get(api(cid, "/projects")).json()["items"]] == ["completed"]
    work = [t for t in tasks_of(app, cid) if t.kind == "work"]
    assert [t.title for t in work] == ["Write positioning", "Write page copy"]
    assert all(t.status == "done" and t.assignee_agent_id == w.specialist["id"] for t in work)
    # --- graph mode: no review task at all, the self-check replaced the manager review
    assert [t for t in tasks_of(app, cid) if t.kind == "review"] == []
    assert all(t.status == "done" for t in tasks_of(app, cid, kind="planning"))
    assert any("Planning run" in title for title in seen["founder_runs"])
    summary = client.get(api(cid, "/cofounder/summary")).json()
    assert summary["unprocessed_notes"] == 0 and summary["wake_pending"] is False
    assert summary["plan"]["summary"].startswith("Handled:")
    first = get_task(client, cid, work[0].id)
    assert first["created_by_type"] == "agent" and first["created_by_id"] == w.lead["id"]

    # --- graph mode: completion is proposed, then finalized by the finish node
    assert seen["proposed"] == ["proposed", "proposed"]
    assert seen["status_after_complete"] == ["in_progress", "in_progress"]
    assert_every_run_used_the_graph(app, cid)
    assert_work_task_went_straight_to_done(app, cid)
    for task, run in graph_runs(app, cid):
        if task.kind == "planning":  # management runs use the graph without the check
            assert ("check", "skipped") in node_events(app, run.id), task.title
    with app.state.session_factory() as db:
        assert {r.runtime for r in db.scalars(select(TaskRun))} == {"scripted"}
        assert {r.status for r in db.scalars(select(TaskRun))} == {"succeeded"}


# ------------------------------------------------------------------ 2. API mode


@pytest.mark.parametrize(
    "lead_closes_project", [True, False], ids=["lead-closes", "watchdog-closes"]
)
def test_graph_mode_api_mode_a_note_becomes_finished_work_and_the_next_goal_without_a_human(
    app, client, anon_client, scripted, scheduler, worker, clock, lead_closes_project
):
    """Counterpart of test_m12_e2e::test_api_mode_a_note_becomes_finished_work_and_the_next_goal_
    without_a_human, with the graph-mode differences asserted."""
    w = scripted_world(client, scripted, anon_client, with_team=False)
    cid = w.cid
    make_ready(app, client, cid)  # API mode: one model provider, no coding CLI
    assert client.get(api(cid, "/policy")).json()["work_mode"] == "api"
    assert client.get(api(cid, "/policy")).json()["work_graph_enabled"] is True
    set_policy(client, cid, initiative_interval_seconds=600, max_planning_runs_per_hour=60)
    with app.state.session_factory() as db:  # start from a quiet company
        db.execute(delete(WakeRequest))
        db.commit()

    seen: dict = {"founder": [], "lead": [], "hr": []}
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
            call.api.ok(
                "POST", f"/tasks/{task.id}/complete", json={"result_summary": "Hired the lead"}
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
        assert task.kind != "review", "a passed self-check must not create a manager review task"
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
    goal_id, project_id = team["goal"]["id"], team["project"]["id"]
    assert one(app, Goal, goal_id).created_by_type == "agent"
    assert one(app, Project, project_id).created_by_type == "agent"
    assert {"goal.created", "project.created", "note.triaged"} <= set(audit_actions(app, cid))

    # ---- stage 2: HR hires a lead, then the lead asks for and gets a specialist
    drive(app, scheduler, worker, clock, lambda: "lead" in team, what="HR hiring a lead")
    lead_id = team["lead"]["id"]
    assert one(app, Project, project_id).lead_agent_id == lead_id
    drive(
        app, scheduler, worker, clock, lambda: "specialist" in team, what="HR hiring a specialist"
    )
    spec_id = team["specialist"]["id"]
    assert one(app, Agent, spec_id).manager_agent_id == lead_id

    # ---- stage 3: the lead plans the project once
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

    # ---- stage 4: the specialist works; the self-check passes; no review cycle exists at all
    drive(
        app,
        scheduler,
        worker,
        clock,
        lambda: all(t.status == "done" for t in tasks_of(app, cid, kind="work")),
        what="all work tasks done",
    )
    assert tasks_of(app, cid, kind="review") == []
    assert_work_task_went_straight_to_done(app, cid)

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

    # ---- stage 7: idle company + initiative -> the Co-founder proposes the next goal
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

    # ---- the whole way no run failed, every run went through the graph, no person acted
    assert scripted.errors == [], scripted.errors
    with app.state.session_factory() as db:
        runs = list(db.scalars(select(TaskRun).where(TaskRun.company_id == cid)))
        assert runs and {r.runtime for r in runs} == {"scripted"}
        assert {r.status for r in runs} == {"succeeded"}
        open_left = db.scalars(
            select(Task).where(
                Task.company_id == cid,
                Task.status.in_(["ready", "in_progress", "in_review", "revision_requested"]),
            )
        ).all()
        assert open_left == []
    assert_every_run_used_the_graph(app, cid)
    after = [e for e in audit_rows(app, cid) if e.id not in after_note]
    assert after, "nothing was audited after the note"
    assert {e.actor_type for e in after} <= {"agent", "system"}, {
        (e.actor_type, e.action) for e in after if e.actor_type not in ("agent", "system")
    }


# ------------------------------------------------------------------ 3. hybrid mode (CLI work)

# The stand-in coding CLI of test_m12_e2e (a plain Python script, not a model), extended for the
# graph's self-check: the check is a SECOND, tool-less call of the same binary per work task. It is
# recognised by its prompt; it answers {"pass": true} and does not count as a work invocation.
FAKE_OPENCODE_GRAPH = r"""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1:2] == ["--version"]:
    print("1.18.3"); sys.exit(0)
script = json.load(open(os.path.join(here, "opencode.script.json")))
prompt = max(sys.argv[1:], key=len)
if "You verify the result of a task against its acceptance criteria" in prompt:
    with open(os.path.join(here, "opencode.verify.jsonl"), "a") as fh:
        fh.write(json.dumps({"cwd": os.getcwd(), "argv": sys.argv[1:]}) + chr(10))
    for line in script["verify_lines"]:
        print(json.dumps(line), flush=True)
    sys.exit(0)
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
    fh.write(json.dumps({"n": n, "cwd": cwd, "files_at_start": seen, "prompt": prompt}) + chr(10))
for item in script["runs"][n]["write_files"]:
    path = os.path.join(cwd, item["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(item["content"])
for line in script["lines"]:
    print(json.dumps(line), flush=True)
"""

VERIFY_PASS_LINES = [
    {"type": "step_start", "sessionID": "v1", "part": {"type": "step-start"}},
    {
        "type": "text",
        "part": {"type": "text", "text": '{"pass": true, "feedback": ""}'},
    },
    {
        "type": "step_finish",
        "part": {
            "reason": "stop",
            "tokens": {
                "total": 10,
                "input": 8,
                "output": 2,
                "reasoning": 0,
                "cache": {"write": 0, "read": 0},
            },
            "cost": 0,
        },
    },
]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_graph_mode_hybrid_management_on_the_api_work_on_the_cli_and_task_two_sees_task_ones_file(
    app, client, anon_client, scheduler, worker, clock, tmp_path
):
    """Counterpart of test_m12_e2e::test_hybrid_mode_management_on_the_api_work_on_the_cli_and_
    task_two_sees_task_ones_file. Graph differences: each CLI work run is act + a tool-less CLI
    self-check call; with the check passed the task is done without a lead review task."""
    from app.runtimes.base import ExecutionRequest
    from app.runtimes.cli.opencode import OpenCodeAdapter
    from app.runtimes.scripted import AgentApi, ScriptedRuntime
    from app.services import project_workspaces as pw

    class ApiAgent(ScriptedRuntime):
        id = "company_agent"
        display_name = "Company agent (scripted stand-in)"

    class CompletingOpenCode(OpenCodeAdapter):
        """The REAL OpenCode adapter driving the fake binary. A real CLI calls ``complete_task``
        through the company MCP server while it runs; this hook makes that same call to the real
        agent API with the run's token just before the adapter checks whether the task is open
        (in graph mode that call only PROPOSES the result)."""

        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self.requests: dict[str, ExecutionRequest] = {}
            self.api_client = None
            self.purposes: list[str] = []
            self.proposals: list[dict] = []

        def execute(self, request, sink, cancel):
            self.purposes.append(request.purpose)
            if request.purpose == "work":
                self.requests[request.task.id] = request
            return super().execute(request, sink, cancel)

        def _task_open(self, task_id):
            request = self.requests.pop(task_id, None)
            if request is not None:
                self.proposals.append(
                    AgentApi(self.api_client, request.run_token).ok(
                        "POST",
                        f"/tasks/{task_id}/complete",
                        json={"result_summary": f"CLI finished: {request.task.title}"},
                    )
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
    write_script(bin_dir, "opencode", FAKE_OPENCODE_GRAPH)
    (bin_dir / "opencode.script.json").write_text(
        json.dumps(
            {
                "lines": OPENCODE_OK,
                "verify_lines": VERIFY_PASS_LINES,
                "runs": [
                    {"write_files": [{"path": "index.html", "content": "<h1>Hello</h1>\n"}]},
                    {"write_files": [{"path": "css/style.css", "content": "h1 { color: red }\n"}]},
                ],
            }
        )
    )
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

    seen: dict = {}

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
        assert task.kind != "review", "a passed self-check must not create a lead review task"
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

    api_agent.handlers.update({founder["name"]: founder_handler, "Lena": lead_handler})
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

    # ---- which runtime ran what: management on the API, work (and its self-check) on the CLI
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
        assert by_kind["planning"] == {"company_agent"}
        assert "review" not in by_kind  # the self-check replaced the lead's review
        cli_runs = [(run, task) for run, task, _ in rows if run.runtime == "cli:opencode"]
        assert [t.title for _, t in cli_runs] == ["Create the index page", "Add the stylesheet"]
    assert_every_run_used_the_graph(app, cid)
    assert_work_task_went_straight_to_done(app, cid)
    # the CLI was asked for work, then for the (tool-less) check, once per task
    assert cli.purposes == ["work", "verify", "work", "verify"]
    assert [p["status"] for p in cli.proposals] == ["proposed", "proposed"]
    assert len((bin_dir / "opencode.verify.jsonl").read_text().splitlines()) == 2

    # ---- the shared project repository: task 1's file was merged, task 2's workspace held it
    records = [
        json.loads(line) for line in (bin_dir / "opencode.records.jsonl").read_text().splitlines()
    ]
    assert len(records) == 2  # the self-checks are not work invocations
    first_rec, second_rec = records
    assert first_rec["files_at_start"] == {}
    assert second_rec["files_at_start"] == {"index.html": "<h1>Hello</h1>\n"}
    assert first_rec["cwd"] != second_rec["cwd"]
    assert "index.html" in second_rec["prompt"] and "PROJECT REPOSITORY" in second_rec["prompt"]
    assert "no files yet" in first_rec["prompt"]
    repo = pw.main_repo(app.state.settings.workspaces_dir, cid, project_id)
    tree = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert sorted(tree) == ["css/style.css", "index.html"]
    merges = subprocess.run(
        ["git", "-C", str(repo), "log", "--merges", "--format=%s", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    assert len(merges) == 2  # one --no-ff merge per CLI work run (the check merges nothing)

    refs = [run.workspace_ref for run, _ in cli_runs]
    for ref in refs:
        assert ref["kind"] == "project" and ref["merged"] is True and ref["conflict"] is False
        assert ref["base"] and ref["commit"] and ref["base"] != ref["commit"]
    assert refs[1]["base"] != refs[0]["base"]  # task 2 started from the main task 1 had moved
    assert "workspace.merged" in audit_actions(app, cid)

    after = [e for e in audit_rows(app, cid) if e.id not in after_note]
    assert {e.actor_type for e in after} <= {"agent", "system"}, {
        (e.actor_type, e.action) for e in after if e.actor_type not in ("agent", "system")
    }


# ------------------------------------------------------------------ 4. review / revision loops


def review_tasks(app, cid) -> list[Task]:
    """Review tasks proper (not the failure analyses, which are review-kind tasks too)."""
    return [
        t
        for t in tasks_of(app, cid, kind="review")
        if (t.idempotency_key or "").startswith("review:")
    ]


def test_graph_mode_explicit_reviewer_keeps_the_review_loop_and_stops_at_the_limit(
    app, client, anon_client, scripted, scheduler, worker, clock
):
    """Counterpart of test_autonomy_loops::test_revision_loop_makes_a_new_review_task_per_revision_
    and_stops_at_the_limit. In graph mode a passed self-check replaces the MANAGER review, so the
    loop needs an explicit reviewer to exist at all."""
    w = scripted_world(client, scripted, anon_client)
    cid = w.cid
    set_policy(client, cid, max_revisions=1)

    def sam(call):
        task = call.request.task
        art = call.api.ok(
            "POST", "/artifacts", json={"title": "Copy", "kind": "document", "content": "meh"}
        )
        call.api.ok(
            "POST",
            f"/tasks/{task.id}/complete",
            json={"result_summary": "written", "output_refs": [art["id"]]},
        )

    def lena(call):
        task = call.request.task
        mode = (call.api.ok("GET", "/context")["task"]["review_target"] or {}).get("mode")
        if mode == "review":
            call.api.ok(
                "POST",
                f"/tasks/{task.id}/review",
                json={"decision": "request_revision", "note": "Make it punchier"},
            )
        else:  # a failure analysis: acknowledge
            call.api.ok("POST", f"/tasks/{task.id}/complete", json={"result_summary": "analysed"})

    scripted.handlers.update({"Sam": sam, "Lena": lena})
    t = new_task(
        client, cid, title="Write copy", assignee_agent_id=w.specialist["id"],
        acceptance_criteria="Punchy", reviewer_agent_id=w.lead["id"],
    )  # fmt: skip
    run_world(app, scheduler, worker, clock)
    assert scripted.errors == [], scripted.errors
    final = get_task(client, cid, t["id"])
    assert final["status"] == "failed" and final["error"]["code"] == "max_revisions"
    reviews = review_tasks(app, cid)
    assert [r.idempotency_key for r in reviews] == [f"review:{t['id']}:0", f"review:{t['id']}:1"]
    assert all(r.status == "done" for r in reviews)
    assert final["review_note"] == "Make it punchier"  # the revision note reached the assignee

    # two work runs (the original and the one revision), each a full graph pass whose self-check
    # passed -- the explicit reviewer, not the check, decides whether the work is accepted
    runs = runs_of(app, t["id"])
    assert len(runs) == 2 and all(r.status == "succeeded" for r in runs)
    for run in runs:
        assert node_events(app, run.id) == [
            ("prepare", "ok"),
            ("act", "proposed"),
            ("check", "passed"),
            ("finish", "checked/applied"),
        ]
    second_act = [r for r in scripted.requests if r.task.id == t["id"] and r.purpose == "work"][1]
    assert "Make it punchier" in (second_act.task.review_note or "")


# The other half of item 4 -- "the self-check fails twice, the task goes to in_review carrying the
# feedback" -- is already covered end to end by
# tests/test_m18_work_graph.py::test_check_loop_is_capped_at_two_revisions_then_in_review_with_the_feedback
# (same run, 3 acts + 3 checks, in_review, the feedback in the audit reason). What is added here is
# the next step of that flow in a company with a manager: who picks the task up from in_review.


def test_graph_mode_failed_self_check_hands_the_task_to_the_manager_review_which_can_finish_it(
    app, client, anon_client, scripted, scheduler, worker, clock
):
    scripted.api_client = anon_client
    w = scripted_world(client, scripted, anon_client)
    cid = w.cid
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={
            "runtime_config": {"script": {"verify": {"pass": False, "feedback": "Still wrong."}}}
        },
    )
    reviewed: list[dict] = []

    def sam(call):
        call.api.ok(
            "POST",
            f"/tasks/{call.request.task.id}/complete",
            json={"result_summary": "my attempt"},
        )

    def lena(call):
        task = call.request.task
        if task.kind == "review":
            reviewed.append(call.api.ok("GET", "/context")["task"]["review_target"])
            call.api.ok(
                "POST", f"/tasks/{task.id}/review", json={"decision": "accept", "note": "fine"}
            )

    scripted.handlers.update({"Sam": sam, "Lena": lena})
    t = new_task(
        client, cid, title="Write copy", assignee_agent_id=w.specialist["id"],
        acceptance_criteria="Punchy",
    )  # fmt: skip
    run_world(app, scheduler, worker, clock)
    assert scripted.errors == [], scripted.errors
    # 1 act + 2 revisions, every check failed: the work graph gave up and sent it to review
    work_requests = [r.purpose for r in scripted.requests if r.task.id == t["id"]]
    assert work_requests == ["work", "verify"] * 3
    assert any("Still wrong." in r for r in audit_reasons(app, t["id"]))
    [run] = runs_of(app, t["id"])
    assert node_events(app, run.id)[-1] == ("finish", "checked/applied")
    # ...where the manager (Lena) reviewed it and, accepting, finished the task
    assert len(reviewed) == 1 and reviewed[0]["task_id"] == t["id"]
    assert get_task(client, cid, t["id"])["status"] == "done"
    assert [r.status for r in review_tasks(app, cid)] == ["done"]


# ------------------------------------------------------------------ 5. crash mid-graph


class SimulatedCrash(BaseException):
    """The process died: not an Exception, so nothing in the engine can swallow it."""


def test_graph_mode_a_worker_dying_after_the_act_is_recovered_without_repeating_the_act(
    app, client, company, scripted, anon_client, scheduler, worker, clock, engine, monkeypatch
):
    from app.engine import recovery
    from app.engine.graph import runner as graph_runner

    scripted.api_client = anon_client
    cid = company["id"]
    agent = new_agent(client, cid, name="Gus")
    acts: list[int] = []

    def gus(call):
        acts.append(1)
        art = call.api.ok(
            "POST", "/artifacts", json={"title": "Out", "kind": "document", "content": "x"}
        )
        call.api.ok(
            "POST",
            f"/tasks/{call.request.task.id}/complete",
            json={"result_summary": "all done", "output_refs": [art["id"]]},
        )

    scripted.handlers["Gus"] = gus
    real_check = graph_runner.RunDeps.run_check
    state = {"crashed": False}

    def dying_check(self, graph_state):
        if not state["crashed"]:
            state["crashed"] = True
            raise SimulatedCrash()  # the process is gone right after the act was checkpointed
        return real_check(self, graph_state)

    monkeypatch.setattr(graph_runner.RunDeps, "run_check", dying_check)
    task = new_task(
        client, cid, title="Crashy", assignee_agent_id=agent["id"], acceptance_criteria="Done"
    )
    scheduler.tick()
    with pytest.raises(SimulatedCrash):
        worker.run_once()
    assert acts == [1] and state["crashed"]
    [dead] = runs_of(app, task["id"])
    assert dead.status == "running"  # nobody finalized it: exactly what a SIGKILL leaves behind
    assert node_events(app, dead.id) == [("prepare", "ok"), ("act", "proposed")]
    assert get_task(client, cid, task["id"])["status"] == "in_progress"
    with app.state.session_factory() as db:  # the proposal and the graph state were persisted
        assert db.get(TaskRun, dead.id).proposed_result is not None
        assert db.scalars(select(GraphCheckpoint)).first() is not None

    # recovery: the stale heartbeat is noticed, the run is failed as worker_lost, the task retried
    clock.advance(120)
    report = recovery.recover(engine)
    assert report.stale_run_ids == [dead.id]
    for _ in range(6):
        settle(scheduler, worker)
        if get_task(client, cid, task["id"])["status"] == "done":
            break
        clock.advance(3600)
    assert scripted.errors == [], scripted.errors
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert get_task(client, cid, task["id"])["result_summary"] == "all done"

    first, second = runs_of(app, task["id"])[:2]
    assert first.status == "failed" and first.exit_reason == "worker_lost"
    assert second.status == "succeeded"
    events = node_events(app, second.id)
    assert events[0] == ("graph", "continued_from_checkpoint")
    assert [n for n, _ in events if n in ("prepare", "act")] == []  # nothing was redone
    assert events[1:] == [("check", "passed"), ("finish", "checked/applied")]
    assert acts == [1]  # the adapter was asked to do the work exactly once
    assert [r.purpose for r in scripted.requests if r.task.id == task["id"]] == [
        "work",
        "verify",
    ]


# ------------------------------------------------------------------ 6. blocking founder question


def test_graph_mode_a_blocking_question_parks_the_task_and_the_api_answer_resumes_it(
    app, client, company, scripted, anon_client, scheduler, worker
):
    scripted.api_client = anon_client
    cid = company["id"]
    agent = new_agent(client, cid, name="Gus")
    calls: list[int] = []

    def gus(call):
        calls.append(1)
        if len(calls) == 1:
            q = call.api.ok(
                "POST",
                "/questions",
                json={
                    "question": "Which tone should the page use?",
                    "default_answer": "friendly",
                    "options": ["friendly", "formal"],
                    "blocking": True,
                },
                expect=(200, 201),
            )
            assert q["blocking"] is True and q["status"] == "open"
        else:
            call.api.ok(
                "POST",
                f"/tasks/{call.request.task.id}/complete",
                json={"result_summary": "wrote it in the founder's tone"},
            )

    scripted.handlers["Gus"] = gus
    task = new_task(
        client, cid, title="Write the page", assignee_agent_id=agent["id"],
        acceptance_criteria="Uses the founder's tone",
    )  # fmt: skip
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors

    # ---- parked: waiting for the founder, not failed, slot freed, one run
    parked = get_task(client, cid, task["id"])
    assert parked["blocked_reason"] == "waiting_for_founder" and parked["status"] != "done"
    assert parked["attempt_count"] == 0
    [first] = runs_of(app, task["id"])
    assert first.status == "succeeded" and first.exit_reason == "waiting"
    assert node_events(app, first.id)[-2:] == [
        ("act", "waiting_founder"),
        ("wait_founder", "waiting"),
    ]
    assert len(calls) == 1
    open_qs = client.get(api(cid, "/questions"), params={"status": "open"})
    assert open_qs.status_code == 200, open_qs.text
    [q] = open_qs.json()["items"]
    assert q["blocking"] is True and q["task_id"] == task["id"]
    # nothing runs while the founder has not answered
    settle(scheduler, worker)
    assert len(calls) == 1 and len(runs_of(app, task["id"])) == 1

    # ---- the human answers through the real HTTP API
    answered = client.post(api(cid, f"/questions/{q['id']}/answer"), json={"answer": "formal"})
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "answered" and answered.json()["answer"] == "formal"
    assert get_task(client, cid, task["id"])["status"] == "ready"

    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    final = get_task(client, cid, task["id"])
    assert final["status"] == "done" and final["result_summary"].startswith("wrote it")
    first, second = runs_of(app, task["id"])
    assert second.status == "succeeded"
    assert node_events(app, second.id) == [
        ("graph", "resumed_after_wait"),
        ("wait_founder", "resumed"),
        ("act", "proposed"),
        ("check", "passed"),
        ("finish", "checked/applied"),
    ]
    # the answer was visible to the agent on the resumed act (and not on the first one)
    first_act, resumed_act = [r for r in scripted.requests if r.purpose == "work"]
    assert "formal" not in (first_act.task.carry_over or "")
    assert "The founder answered: formal" in (resumed_act.task.carry_over or "")
    assert "Which tone should the page use?" in (resumed_act.task.carry_over or "")
    # the question is closed and the answer audited
    with app.state.session_factory() as db:
        assert db.get(FounderQuestion, q["id"]).status == "answered"
        assert "question.answered" in [
            e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == q["id"]))
        ]
