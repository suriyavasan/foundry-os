"""M18 Part B: the durable work graph (prepare -> act -> check -> finish, interrupts, revisions).

Driven end to end by the ScriptedRuntime through the real scheduler, worker, executor and agent API.
Graph-level behaviour that needs no database of its own (crash resume, sessions) uses a fake
``Deps`` in test_m18_work_graph_units.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.models.audit import AuditEvent
from app.models.graph import GraphCheckpoint
from app.models.questions import FounderQuestion
from app.models.run import RunEvent, TaskRun
from app.models.work import Task
from app.services import questions
from tests.helpers import api, get_task, new_agent, new_task, settle

CRITERIA = "The summary names the result."


@pytest.fixture
def world(app, client, company, scripted, anon_client):
    scripted.api_client = anon_client  # run-token-only client
    cid = company["id"]
    agent = new_agent(client, cid, name="Gus")
    return cid, agent


def complete(call, summary="done"):
    return call.api.ok(
        "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": summary}
    )


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


def purposes(scripted, task_id) -> list[str]:
    return [r.purpose for r in scripted.requests if r.task.id == task_id]


def audit_reasons(app, task_id) -> list[str]:
    with app.state.session_factory() as db:
        return [
            str(e.data.get("reason"))
            for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == task_id))
        ]


def owner_ctx(client, cid) -> CompanyContext:
    me = client.get("/api/me").json()
    return CompanyContext(
        cid, Principal(PrincipalType.HUMAN, me["user"]["id"], "Owner"), Role.OWNER
    )


# ------------------------------------------------------------------ proposed completion + check


def test_complete_only_proposes_and_the_finish_node_completes(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    seen: dict = {}

    def gus(call):
        seen["answer"] = complete(call, "all done")
        with app.state.session_factory() as db:
            seen["status_after"] = db.get(Task, call.request.task.id).status

    scripted.handlers["Gus"] = gus
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert seen["answer"] == {"ok": True, "status": "proposed", "task_id": task["id"]}
    assert seen["status_after"] == "in_progress"  # nothing was finalized by the tool call
    final = get_task(client, cid, task["id"])
    assert final["status"] == "done" and final["result_summary"] == "all done"
    [run] = runs_of(app, task["id"])
    assert run.graph_thread_id == f"task:{task['id']}" and run.status == "succeeded"
    assert node_events(app, run.id) == [
        ("prepare", "ok"),
        ("act", "proposed"),
        ("check", "skipped"),  # no acceptance criteria: nothing to verify
        ("finish", "checked/applied"),
    ]
    assert purposes(scripted, task["id"]) == ["work"]


def test_check_pass_goes_straight_to_done_without_a_review_cycle(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    scripted.handlers["Gus"] = lambda call: complete(call)
    boss = new_agent(client, cid, name="Boss", kind="lead")
    client.patch(api(cid, f"/agents/{agent['id']}"), json={"manager_agent_id": boss["id"]})
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert get_task(client, cid, task["id"])["status"] == "done"  # the manager review is skipped
    assert purposes(scripted, task["id"]) == ["work", "verify"]
    [run] = runs_of(app, task["id"])
    assert ("check", "passed") in node_events(app, run.id)


def test_an_explicit_reviewer_keeps_the_review_after_a_passed_check(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    reviewer = new_agent(client, cid, name="Rae")
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(
        client,
        cid,
        assignee_agent_id=agent["id"],
        acceptance_criteria=CRITERIA,
        reviewer_agent_id=reviewer["id"],
    )
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "in_review"
    assert purposes(scripted, task["id"]) == ["work", "verify"]


def test_failed_check_revises_in_the_same_run_then_passes(
    app, client, company, scripted, anon_client, scheduler, worker
):
    scripted.api_client = anon_client
    cid = company["id"]
    agent = new_agent(
        client,
        cid,
        name="Gus",
        script={
            "verify": [{"pass": False, "feedback": "Name the owner."}, {"pass": True}],
            "verify_usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert purposes(scripted, task["id"]) == ["work", "verify", "work", "verify"]
    second_act = [r for r in scripted.requests if r.purpose == "work"][1]
    assert "Name the owner." in (second_act.task.review_note or "")
    runs = runs_of(app, task["id"])
    assert len(runs) == 1  # one TaskRun for the whole loop
    assert [n for n in node_events(app, runs[0].id) if n[0] in ("check", "act")] == [
        ("act", "proposed"),
        ("check", "failed/revise"),
        ("act", "proposed"),
        ("check", "passed"),
    ]
    # the check calls' usage is part of the run's usage
    assert runs[0].input_tokens == 20 and runs[0].output_tokens == 10


def test_check_loop_is_capped_at_two_revisions_then_in_review_with_the_feedback(
    app, client, company, scripted, anon_client, scheduler, worker
):
    scripted.api_client = anon_client
    cid = company["id"]
    agent = new_agent(
        client, cid, name="Gus", script={"verify": {"pass": False, "feedback": "Still wrong."}}
    )
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert purposes(scripted, task["id"]) == ["work", "verify"] * 3  # 1 act + 2 revisions
    final = get_task(client, cid, task["id"])
    assert final["status"] == "in_review"
    assert any("Still wrong." in r for r in audit_reasons(app, task["id"]))
    [run] = runs_of(app, task["id"])
    assert run.status == "succeeded"
    assert node_events(app, run.id)[-1] == ("finish", "checked/applied")


def test_an_inconclusive_check_never_blocks_the_task(
    app, client, company, scripted, anon_client, scheduler, worker
):
    scripted.api_client = anon_client
    cid = company["id"]
    agent = new_agent(client, cid, name="Gus", script={"verify": {"raw": "I think it is fine"}})
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert purposes(scripted, task["id"]) == ["work", "verify"]


def test_self_check_can_be_switched_off_in_the_policy(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    r = client.patch(api(cid, "/policy"), json={"self_check_enabled": False})
    assert r.status_code == 200 and r.json()["self_check_enabled"] is False
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert purposes(scripted, task["id"]) == ["work"]


def test_management_runs_use_the_graph_without_the_check(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(
        client, cid, assignee_agent_id=agent["id"], kind="planning", acceptance_criteria=CRITERIA
    )
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    assert purposes(scripted, task["id"]) == ["work"]
    [run] = runs_of(app, task["id"])
    assert run.graph_thread_id is not None
    assert ("check", "skipped") in node_events(app, run.id)


def test_work_graph_off_keeps_todays_one_shot_behaviour(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    assert client.patch(api(cid, "/policy"), json={"work_graph_enabled": False}).status_code == 200
    seen: dict = {}

    def gus(call):
        seen["answer"] = complete(call)

    scripted.handlers["Gus"] = gus
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert seen["answer"]["status"] == "done"  # a task body, finalized by the tool call itself
    assert purposes(scripted, task["id"]) == ["work"]
    [run] = runs_of(app, task["id"])
    assert run.graph_thread_id is None and run.proposed_result is None
    assert node_events(app, run.id) == []
    with app.state.session_factory() as db:
        assert db.scalars(select(GraphCheckpoint)).first() is None


# ------------------------------------------------------------------ interrupts


def test_blocking_question_interrupts_and_the_answer_resumes_the_thread(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    calls: list[int] = []

    def gus(call):
        calls.append(1)
        if len(calls) == 1:
            call.api.ok(
                "POST",
                "/questions",
                json={
                    "question": "Which tone?",
                    "default_answer": "friendly",
                    "blocking": True,
                },
            )
        else:
            complete(call, "wrote it in the founder's tone")

    scripted.handlers["Gus"] = gus
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    parked = get_task(client, cid, task["id"])
    assert parked["blocked_reason"] == "waiting_for_founder" and parked["status"] != "done"
    assert parked["attempt_count"] == 0  # the wait is not a failed attempt
    [first] = runs_of(app, task["id"])
    assert first.status == "succeeded" and first.exit_reason == "waiting"  # slot freed
    assert node_events(app, first.id)[-2:] == [
        ("act", "waiting_founder"),
        ("wait_founder", "waiting"),
    ]
    with app.state.session_factory() as db:
        qid = db.scalars(select(FounderQuestion.id)).one()
    with app.state.session_factory() as db:
        questions.answer(db, owner_ctx(client, cid), qid, "formal")
    assert get_task(client, cid, task["id"])["status"] == "ready"
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert get_task(client, cid, task["id"])["status"] == "done"
    first, second = runs_of(app, task["id"])
    assert node_events(app, second.id)[:3] == [
        ("graph", "resumed_after_wait"),
        ("wait_founder", "resumed"),
        ("act", "proposed"),
    ]
    resumed_act = [r for r in scripted.requests if r.purpose == "work"][1]
    assert "formal" in (resumed_act.task.carry_over or "")


def test_pending_approval_interrupts_and_the_decision_resumes_the_thread(
    app, client, world, scripted, scheduler, worker
):
    cid, agent = world
    calls: list[int] = []

    def gus(call):
        calls.append(1)
        if len(calls) == 1:
            call.api.ok(
                "POST",
                "/approvals",
                json={"kind": "external_purchase", "title": "Buy the thing", "risk": "low"},
            )
        else:
            complete(call, "bought")

    scripted.handlers["Gus"] = gus
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    parked = get_task(client, cid, task["id"])
    assert parked["blocked_reason"] == "waiting_for_approval" and parked["status"] != "done"
    [first] = runs_of(app, task["id"])
    assert first.exit_reason == "waiting"
    approval = client.get(api(cid, "/approvals?status=pending")).json()["items"][0]
    r = client.post(api(cid, f"/approvals/{approval['id']}/approve"), json={"note": "ok"})
    assert r.status_code == 200, r.text
    assert get_task(client, cid, task["id"])["status"] == "ready"
    settle(scheduler, worker)
    assert scripted.errors == [], scripted.errors
    assert get_task(client, cid, task["id"])["status"] == "done"
    resumed_act = [r for r in scripted.requests if r.purpose == "work"][1]
    assert "Approval granted: Buy the thing" in (resumed_act.task.carry_over or "")


def test_an_expired_approval_also_releases_the_waiting_task(
    app, client, world, scripted, scheduler, worker, clock
):
    from datetime import timedelta

    from app.services import approvals

    cid, agent = world
    calls: list[int] = []

    def gus(call):
        calls.append(1)
        if len(calls) == 1:
            call.api.ok("POST", "/approvals", json={"kind": "external_purchase", "title": "Buy it"})
        else:
            complete(call, "found another way")

    scripted.handlers["Gus"] = gus
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["blocked_reason"] == "waiting_for_approval"
    with app.state.session_factory() as db:
        approvals.sweep_deadlines(db, clock() + timedelta(days=3))
    assert get_task(client, cid, task["id"])["status"] == "ready"
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    resumed_act = [r for r in scripted.requests if r.purpose == "work"][1]
    assert "Do NOT repeat" in (resumed_act.task.carry_over or "")


# ------------------------------------------------------------------ crash / retry resume


def test_a_failed_act_resumes_the_same_thread_from_its_checkpoint(
    app, client, company, scripted, anon_client, scheduler, worker, clock
):
    scripted.api_client = anon_client
    cid = company["id"]
    # an unexpected error inside the act node leaves the thread at "act" (nothing is re-prepared)
    agent = new_agent(
        client, cid, name="Hal", script={"per_call": [{"raise_exception": "boom"}, {}]}
    )
    scripted.handlers["Hal"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    for _ in range(4):
        settle(scheduler, worker)
        if get_task(client, cid, task["id"])["status"] == "done":
            break
        clock.advance(3600)
    assert get_task(client, cid, task["id"])["status"] == "done"
    first, second = runs_of(app, task["id"])[:2]
    assert first.status == "failed"
    assert [n[0] for n in node_events(app, first.id)] == ["prepare"]
    assert node_events(app, second.id)[0] == ("graph", "continued_from_checkpoint")
    assert "prepare" not in [n[0] for n in node_events(app, second.id)]


# ------------------------------------------------------------------ tenancy


def test_the_thread_belongs_to_the_runs_company(app, client, world, scripted, scheduler, worker):
    cid, agent = world
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "x"}).json()
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        rows = list(db.scalars(select(GraphCheckpoint)))
    assert rows and {r.company_id for r in rows} == {cid}
    assert {r.thread_id for r in rows} == {f"task:{task['id']}"}
    from app.engine.graph.checkpointer import SqlCheckpointSaver

    saver = SqlCheckpointSaver(app.state.session_factory)
    foreign = {"configurable": {"thread_id": f"task:{task['id']}", "company_id": other["id"]}}
    assert saver.get_tuple(foreign) is None  # another company reads nothing


def test_policy_fields_are_owner_only_and_audited(app, client, world):
    cid, _ = world
    pol = client.get(api(cid, "/policy")).json()
    assert pol["self_check_enabled"] is True and pol["work_graph_enabled"] is True
    r = client.patch(api(cid, "/policy"), json={"work_graph_enabled": False})
    assert r.status_code == 200 and r.json()["work_graph_enabled"] is False
    assert client.patch(api(cid, "/policy"), json={"self_check_enabled": None}).status_code == 422
    with app.state.session_factory() as db:
        changed = [
            e.data.get("changed")
            for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "policy.updated"))
        ]
    assert ["work_graph_enabled"] in changed


# ------------------------------------------------------------------ cancel


def test_a_run_cancelled_after_proposing_never_completes_the_task(
    app, client, company, scripted, anon_client, scheduler, worker
):
    from tests.helpers import run_in_thread, wait_until

    scripted.api_client = anon_client
    cid = company["id"]
    agent = new_agent(client, cid, name="Gus", script={"block_until_cancel": True})
    scripted.handlers["Gus"] = lambda call: complete(call)
    task = new_task(client, cid, assignee_agent_id=agent["id"], acceptance_criteria=CRITERIA)
    scheduler.tick()
    thread = run_in_thread(worker.run_once)

    def proposed():
        [run] = runs_of(app, task["id"])
        return run.proposed_result is not None

    wait_until(proposed, what="the proposal")
    [run] = runs_of(app, task["id"])
    assert client.post(api(cid, f"/runs/{run.id}/cancel"), json={}).status_code == 200
    thread.join(timeout=10)
    assert not thread.is_alive()
    after = get_task(client, cid, task["id"])
    assert after["status"] == "blocked"  # a person cancelled the run: nothing was completed
    assert purposes(scripted, task["id"]) == ["work"]  # and no self-check was started
    [run] = runs_of(app, task["id"])
    assert run.status == "cancelled"
