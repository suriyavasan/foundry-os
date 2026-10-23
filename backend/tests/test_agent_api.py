from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.core.time import utcnow
from app.core.tokens import RunTokenClaims, mint_run_token
from app.models.audit import AuditEvent
from app.models.run import RunEvent, TaskRun
from app.models.work import Task
from app.schemas.governance import ApprovalCreate
from app.services import agent_auth, approvals
from tests.helpers import api, get_task, new_agent, new_task

BASE = "/api/agent/v1"


class AgentSession:
    """An agent acting through its run token."""

    def __init__(self, client, token: str, *, run_id: str, task_id: str, agent_id: str) -> None:
        self.client, self.token = client, token
        self.run_id, self.task_id, self.agent_id = run_id, task_id, agent_id

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _headers(self, kw: dict) -> dict[str, str]:
        return {**self.headers, **kw.pop("headers", {})}

    def get(self, path: str, **kw):
        return self.client.get(BASE + path, headers=self._headers(kw), **kw)

    def post(self, path: str, **kw):
        return self.client.post(BASE + path, headers=self._headers(kw), **kw)

    def patch(self, path: str, **kw):
        return self.client.patch(BASE + path, headers=self._headers(kw), **kw)


def start_run(
    app,
    client,
    cid: str,
    agent_id: str,
    *,
    task_title: str = "Assigned work",
    status: str = "running",
    **task_fields,
) -> AgentSession:
    """Put the agent mid-run on a fresh task (no worker needed) and hand back its token."""
    task = new_task(client, cid, title=task_title, assignee_agent_id=agent_id, **task_fields)
    with app.state.session_factory() as db:
        row = db.get(Task, task["id"])
        row.status = "in_progress"
        row.attempt_count = 1
        row.started_at = utcnow()
        run = TaskRun(
            company_id=cid,
            task_id=row.id,
            agent_id=agent_id,
            attempt_number=1,
            status=status,
            runtime="scripted",
            idempotency_key=f"{row.id}:1:0",
            queued_at=utcnow(),
            started_at=utcnow(),
            heartbeat_at=utcnow(),
        )
        db.add(run)
        db.commit()
        token = agent_auth.issue_token(app.state.keys, run, max_run_seconds=900)
        return AgentSession(client, token, run_id=run.id, task_id=row.id, agent_id=agent_id)


def finish_run(app, session: AgentSession, status: str = "succeeded") -> None:
    with app.state.session_factory() as db:
        db.execute(update(TaskRun).where(TaskRun.id == session.run_id).values(status=status))
        db.commit()


def make_approval(client, cid: str, agent_id: str, kind: str = "hire") -> str:
    """Create a pending approval requested by ``agent_id`` straight through the service."""
    app = client.app
    ctx = CompanyContext(cid, Principal(PrincipalType.AGENT, agent_id, "A", cid), Role.AGENT)
    with app.state.session_factory() as db:
        return approvals.create_approval(
            db, ctx, ApprovalCreate(kind=kind, title=f"{kind} request", scope={"x": 1})
        ).id


@pytest.fixture
def world(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid, name="Worker")
    return cid, agent, start_run(app, client, cid, agent["id"])


# ------------------------------------------------------------------ authentication


def test_token_is_required_and_must_be_valid(app, client, world):
    cid, agent, sess = world
    assert client.get(BASE + "/context").status_code == 401
    for header in (
        "Bearer",
        "Bearer not-a-token",
        "Basic abc",
        f"Bearer {sess.token[:-3]}abc",  # tampered signature
        f"Bearer {sess.token.replace('cos_run.', 'cos_run.x')}",
    ):
        r = client.get(BASE + "/context", headers={"Authorization": header})
        assert r.status_code == 401, header
        assert r.json()["error"]["code"] == "unauthenticated"
    assert sess.get("/context").status_code == 200


def test_token_signed_with_another_key_is_rejected(app, client, world):
    cid, agent, sess = world
    forged = mint_run_token(
        b"some-other-key",
        RunTokenClaims(sess.run_id, agent["id"], cid, int(utcnow().timestamp()) + 600),
    )
    assert (
        client.get(BASE + "/context", headers={"Authorization": f"Bearer {forged}"}).status_code
        == 401
    )


def test_expired_token_is_rejected(app, client, world):
    cid, agent, sess = world
    with app.state.session_factory() as db:
        run = db.get(TaskRun, sess.run_id)
        stale = agent_auth.issue_token(
            app.state.keys, run, max_run_seconds=900, now=utcnow() - timedelta(hours=2)
        )
    r = client.get(BASE + "/context", headers={"Authorization": f"Bearer {stale}"})
    assert r.status_code == 401


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "timed_out", "queued"])
def test_token_dies_with_the_run(app, client, world, status):
    cid, agent, sess = world
    assert sess.get("/context").status_code == 200
    finish_run(app, sess, status)
    r = sess.get("/context")
    assert r.status_code == 401
    assert sess.post("/progress", json={"message": "late"}).status_code == 401
    # the revoked token is not an identity on the owner API either
    assert client.get(api(cid, "/tasks"), headers=sess.headers).status_code == 401


def test_starting_run_token_works(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    sess = start_run(app, client, cid, agent["id"], status="starting")
    assert sess.get("/context").status_code == 200


def test_token_of_company_a_cannot_touch_company_b(app, client, world):
    cid, agent, sess = world
    other = client.post("/api/companies", json={"name": "Other Co"}).json()["id"]
    other_agent = new_agent(client, other, name="Theirs")
    other_task = new_task(client, other, title="secret", assignee_agent_id=other_agent["id"])
    # owner API with an agent token: bound to its own company only
    assert client.get(api(other, "/tasks"), headers=sess.headers).status_code == 404
    assert (
        client.get(api(other, f"/tasks/{other_task['id']}"), headers=sess.headers).status_code
        == 404
    )
    assert client.get(api(cid, "/tasks"), headers=sess.headers).status_code == 200
    # agent API: foreign ids simply do not exist for it
    assert (
        sess.post("/tasks", json={"title": "x", "assignee_agent_id": other_agent["id"]}).status_code
        == 404
    )
    assert (
        sess.post("/tasks", json={"title": "x", "depends_on": [other_task["id"]]}).status_code
        == 404
    )
    assert (
        sess.post("/tasks", json={"title": "x", "parent_task_id": other_task["id"]}).status_code
        == 404
    )
    assert sess.patch(f"/tasks/{other_task['id']}", json={"status": "blocked"}).status_code == 404
    assert (
        sess.post(f"/tasks/{other_task['id']}/complete", json={"result_summary": "x"}).status_code
        == 404
    )
    assert get_task(client, other, other_task["id"])["status"] == "ready"


def test_agent_cannot_change_policy_budgets_or_permissions(app, client, world):
    cid, agent, sess = world
    h = sess.headers
    r = client.patch(api(cid, "/policy"), json={"daily_budget_micros": 10**12}, headers=h)
    assert r.status_code == 403
    r = client.patch(
        api(cid, f"/agents/{agent['id']}"), json={"daily_budget_micros": 10**12}, headers=h
    )
    assert r.status_code == 403
    r = client.patch(
        api(cid, f"/agents/{agent['id']}"), json={"tool_permissions": ["*"]}, headers=h
    )
    assert r.status_code == 403
    r = client.post(api(cid, f"/agents/{agent['id']}/retire"), headers=h)
    assert r.status_code == 403
    r = client.post(api(cid, "/pause"), headers=h)
    assert r.status_code == 403
    r = client.post(api(cid, "/tasks"), json={"title": "via owner api"}, headers=h)
    assert r.status_code == 403  # agents create tasks through their run, not the owner API
    assert client.get(api(cid, "/policy")).json()["daily_budget_micros"] is None
    # nothing on the agent API reaches budgets either (unknown fields are rejected)
    assert sess.patch(f"/tasks/{sess.task_id}", json={"budget_micros": 1}).status_code == 422
    assert sess.patch(f"/tasks/{sess.task_id}", json={"status": "done"}).status_code == 422
    assert sess.post("/tasks", json={"title": "x", "company_id": "other"}).status_code == 422


# ------------------------------------------------------------------ context


def test_context_has_what_an_agent_needs(app, client, world):
    cid, agent, sess = world
    founder = client.get(api(cid, "/overview")).json()["founder"]
    client.patch(api(cid, f"/agents/{agent['id']}"), json={"manager_agent_id": founder["id"]})
    report = new_agent(client, cid, name="Report", manager_agent_id=agent["id"])
    client.patch(api(cid, "/"[:-1]), json={"mission": "Make things"})
    ctx = sess.get("/context").json()
    assert ctx["company"]["name"] == "Acme Labs" and ctx["company"]["mission"] == "Make things"
    assert ctx["run"]["id"] == sess.run_id
    assert ctx["task"]["id"] == sess.task_id and ctx["task"]["status"] == "in_progress"
    assert ctx["agent"]["id"] == agent["id"]
    assert ctx["manager"]["id"] == founder["id"]
    assert [r["id"] for r in ctx["direct_reports"]] == [report["id"]]
    assert {c["id"] for c in ctx["colleagues"]} >= {founder["id"], report["id"]}
    assert agent["id"] not in {c["id"] for c in ctx["colleagues"]}
    assert (
        ctx["policy"]["max_delegation_depth"] == 3 and ctx["policy"]["tasks_created_this_run"] == 0
    )
    assert ctx["skills_available"] is False
    # secrets / enforcement internals are not part of the context
    assert "budget_lock_version" not in str(ctx) and "runtime_config" not in str(ctx)


def test_context_includes_parent_dependencies_and_artifacts(app, client, world):
    cid, agent, sess = world
    parent = new_task(client, cid, title="the parent")
    dep = new_task(client, cid, title="the dependency")
    with app.state.session_factory() as db:
        row = db.get(Task, dep["id"])
        row.status, row.result_summary = "done", "dep finished"
        child = db.get(Task, sess.task_id)
        child.parent_task_id = parent["id"]
        db.commit()
    client.post(
        api(cid, f"/tasks/{sess.task_id}/dependencies"), json={"depends_on_task_id": dep["id"]}
    )
    sess.post("/artifacts", json={"title": "Notes", "kind": "document", "content": "# hi"})
    ctx = sess.get("/context").json()["task"]
    assert ctx["parent"]["title"] == "the parent"
    assert ctx["dependencies"][0]["result_summary"] == "dep finished"
    assert ctx["artifacts"][0]["title"] == "Notes"


# ------------------------------------------------------------------ creating work


def test_agent_creates_subtasks_attributed_and_audited(app, client, world):
    cid, agent, sess = world
    helper = new_agent(client, cid, name="Helper")
    r = sess.post(
        "/tasks",
        json={
            "title": "Subtask",
            "assignee_agent_id": helper["id"],
            "acceptance_criteria": "works",
            "priority": "high",
            "required_skills": ["sql"],
        },
    )
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["parent_task_id"] == sess.task_id  # default parent = current task
    assert sub["delegation_depth"] == 1 and sub["status"] == "ready"
    assert sub["created_by_type"] == "agent" and sub["created_by_id"] == agent["id"]
    assert sub["created_by_run_id"] == sess.run_id
    trail = client.get(api(cid, "/activity"), params={"target_id": sub["id"]}).json()["items"]
    created = next(e for e in trail if e["action"] == "task.created")
    assert created["actor_type"] == "agent" and created["actor_id"] == agent["id"]
    # explicit parent / dependency / project
    project = client.post(api(cid, "/projects"), json={"name": "P"}).json()
    r2 = sess.post(
        "/tasks",
        json={
            "title": "Second",
            "depends_on": [sub["id"]],
            "project_id": project["id"],
            "parent_task_id": sub["id"],
        },
    )
    assert r2.status_code == 201 and r2.json()["delegation_depth"] == 2
    assert r2.json()["status"] == "backlog" and r2.json()["depends_on"] == [sub["id"]]


def test_delegation_depth_limit_is_enforced_for_agents(app, client, world):
    cid, agent, sess = world
    client.patch(
        api(cid, "/policy"), json={"max_delegation_depth": 2, "max_tasks_per_planning_cycle": 50}
    )
    parent = sess.task_id
    for depth in (1, 2):
        r = sess.post("/tasks", json={"title": f"d{depth}", "parent_task_id": parent})
        assert r.status_code == 201 and r.json()["delegation_depth"] == depth
        parent = r.json()["id"]
    r = sess.post("/tasks", json={"title": "too deep", "parent_task_id": parent})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    assert r.json()["error"]["details"]["limit"] == "max_delegation_depth"


def test_per_run_task_cap_is_enforced(app, client, world):
    cid, agent, sess = world
    client.patch(api(cid, "/policy"), json={"max_tasks_per_planning_cycle": 3})
    for i in range(3):
        assert sess.post("/tasks", json={"title": f"t{i}"}).status_code == 201
    r = sess.post("/tasks", json={"title": "t3"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    assert r.json()["error"]["details"]["limit"] == "max_tasks_per_planning_cycle"
    assert sess.get("/context").json()["policy"]["tasks_created_this_run"] == 3
    # the cap is per run: a new run of the same agent starts fresh
    other_run = start_run(app, client, cid, agent["id"], task_title="another")
    assert other_run.post("/tasks", json={"title": "fresh"}).status_code == 201
    # idempotent replays do not count against the cap
    client.patch(api(cid, "/policy"), json={"max_tasks_per_planning_cycle": 1})
    assert (
        other_run.post("/tasks", json={"title": "fresh", "idempotency_key": "x"}).status_code == 409
    )


def test_agent_task_idempotency_key(app, client, world):
    cid, agent, sess = world
    first = sess.post("/tasks", json={"title": "once", "idempotency_key": "plan-1"})
    again = sess.post("/tasks", json={"title": "once", "idempotency_key": "plan-1"})
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert sess.get("/context").json()["policy"]["tasks_created_this_run"] == 1


def test_subtask_budget_cannot_exceed_parents_remaining_budget(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    sess = start_run(app, client, cid, agent["id"], budget_micros=1_000_000)
    assert sess.post("/tasks", json={"title": "a", "budget_micros": 600_000}).status_code == 201
    r = sess.post("/tasks", json={"title": "b", "budget_micros": 500_000})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    assert r.json()["error"]["details"]["remaining_micros"] == 400_000
    assert sess.post("/tasks", json={"title": "c", "budget_micros": 400_000}).status_code == 201
    uncapped = start_run(app, client, cid, agent["id"], task_title="no cap")
    assert (
        uncapped.post("/tasks", json={"title": "x", "budget_micros": 9_000_000}).status_code == 201
    )


# ------------------------------------------------------------------ editing / blocking


def test_agent_block_unblock_and_assign_permissions(app, client, world):
    cid, agent, sess = world
    helper = new_agent(client, cid, name="Helper")
    mine = sess.post("/tasks", json={"title": "mine", "assignee_agent_id": helper["id"]}).json()
    outsider_task = new_task(client, cid, title="not mine", assignee_agent_id=helper["id"])
    # block / unblock a task the agent created
    r = sess.patch(f"/tasks/{mine['id']}", json={"status": "blocked", "reason": "needs input"})
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert r.json()["error"]["message"] == "needs input"
    r = sess.patch(f"/tasks/{mine['id']}", json={"status": "unblocked"})
    assert r.json()["status"] == "ready"
    # reassign
    third = new_agent(client, cid, name="Third")
    r = sess.patch(f"/tasks/{mine['id']}", json={"assignee_agent_id": third["id"]})
    assert r.status_code == 200 and r.json()["assignee_agent_id"] == third["id"]
    # a task created by a human and not managed by this agent is off limits
    for body in ({"status": "blocked"}, {"assignee_agent_id": third["id"]}):
        assert sess.patch(f"/tasks/{outsider_task['id']}", json=body).status_code == 403
    # ...unless the agent manages the assignee
    client.patch(api(cid, f"/agents/{helper['id']}"), json={"manager_agent_id": agent["id"]})
    assert (
        sess.patch(f"/tasks/{outsider_task['id']}", json={"status": "blocked"}).status_code == 200
    )
    # an agent can always block its own current task (it keeps running until it exits)
    r = sess.patch(
        f"/tasks/{sess.task_id}", json={"status": "blocked", "reason": "waiting for approval"}
    )
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    with app.state.session_factory() as db:
        assert db.get(TaskRun, sess.run_id).cancel_requested is False
    # but cannot unblock it (not its creator / manager)
    assert sess.patch(f"/tasks/{sess.task_id}", json={"status": "unblocked"}).status_code == 403


def test_complete_task_rules(app, client, world):
    cid, agent, sess = world
    other = new_task(client, cid, title="other", assignee_agent_id=agent["id"])
    r = sess.post(f"/tasks/{other['id']}/complete", json={"result_summary": "x"})
    assert r.status_code == 403  # only the current run's task
    assert sess.post(f"/tasks/{sess.task_id}/complete", json={}).status_code == 422
    r = sess.post(
        f"/tasks/{sess.task_id}/complete",
        json={"result_summary": "shipped", "output_refs": ["art-1"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["result_summary"] == "shipped"
    assert r.json()["output_refs"] == ["art-1"]
    assert (
        sess.post(f"/tasks/{sess.task_id}/complete", json={"result_summary": "again"}).status_code
        == 409
    )


def test_complete_with_acceptance_criteria_goes_to_review_and_reviewer_decides(
    app, client, company
):
    cid = company["id"]
    founder = client.get(api(cid, "/overview")).json()["founder"]
    worker = new_agent(client, cid, name="Worker", manager_agent_id=founder["id"])
    sess = start_run(app, client, cid, worker["id"], acceptance_criteria="must pass tests")
    done = sess.post(
        f"/tasks/{sess.task_id}/complete", json={"result_summary": "implemented"}
    ).json()
    assert done["status"] == "in_review"
    # the worker cannot review its own work; the manager (founder) can
    assert (
        sess.post(f"/tasks/{sess.task_id}/review", json={"decision": "accept"}).status_code == 403
    )
    reviewer = start_run(app, client, cid, founder["id"], task_title="review it")
    r = reviewer.post(
        f"/tasks/{sess.task_id}/review", json={"decision": "request_revision", "note": "add tests"}
    )
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert r.json()["revision_count"] == 1 and r.json()["review_note"] == "add tests"
    # not in review any more
    assert (
        reviewer.post(f"/tasks/{sess.task_id}/review", json={"decision": "accept"}).status_code
        == 409
    )


def test_progress_and_decisions(app, client, world):
    cid, agent, sess = world
    r = sess.post("/progress", json={"message": "indexed 40 pages"})
    assert r.status_code == 201 and r.json()["run_id"] == sess.run_id
    assert sess.post("/progress", json={"message": "second"}).json()["seq"] == r.json()["seq"] + 1
    events = client.get(api(cid, f"/runs/{sess.run_id}/events")).json()["items"]
    assert [e["type"] for e in events] == ["progress", "progress"]
    assert events[0]["payload"] == {"message": "indexed 40 pages"}
    with app.state.session_factory() as db:
        assert db.get(TaskRun, sess.run_id).activity_summary == "second"
    assert sess.post("/progress", json={"message": ""}).status_code == 422

    d = sess.post(
        "/decisions",
        json={
            "title": "Use Postgres",
            "rationale": "need concurrency",
            "context": {"options": ["sqlite", "postgres"]},
        },
    )
    assert d.status_code == 201, d.text
    assert d.json()["made_by_type"] == "agent" and d.json()["made_by_id"] == agent["id"]
    assert d.json()["related_task_id"] == sess.task_id
    listed = client.get(api(cid, "/decisions")).json()["items"]
    assert [x["title"] for x in listed] == ["Use Postgres"]
    assert (
        client.get(api(cid, "/decisions"), params={"related_task_id": "nope"}).json()["items"] == []
    )
    assert (
        sess.post("/decisions", json={"title": "x", "context": {"token": "abc"}}).status_code == 422
    )


def test_every_agent_write_is_audited_with_the_agent_as_actor(app, client, world):
    cid, agent, sess = world
    sess.post("/progress", json={"message": "hi"})
    sess.post("/decisions", json={"title": "D"})
    sess.post("/approvals", json={"kind": "hire", "title": "need a designer"})
    sess.post("/artifacts", json={"title": "A", "content": "x"})
    sess.post("/tasks", json={"title": "T"})
    with app.state.session_factory() as db:
        rows = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.actor_id == agent["id"])
                .order_by(AuditEvent.created_at)
            )
        )
        actions = {r.action for r in rows}
    assert {
        "decision.recorded",
        "approval.requested",
        "artifact.created",
        "task.created",
    } <= actions
    assert all(r.actor_type == "agent" for r in rows)
    with app.state.session_factory() as db:
        assert db.scalar(select(RunEvent).where(RunEvent.type == "progress")) is not None


def test_agent_artifacts_json_and_multipart(app, client, world):
    cid, agent, sess = world
    r = sess.post("/artifacts", json={"title": "Spec", "kind": "document", "content": "# Spec\nv1"})
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["task_id"] == sess.task_id and art["run_id"] == sess.run_id
    assert art["created_by_type"] == "agent" and art["current_version"] == 1
    assert art["mime_type"] == "text/markdown" and art["versions"][0]["size_bytes"] == len(
        "# Spec\nv1"
    )
    # a new version of the same artifact
    r = sess.post(
        "/artifacts", json={"artifact_id": art["id"], "content": "# Spec\nv2", "note": "edit"}
    )
    assert r.status_code == 201 and r.json()["current_version"] == 2
    # multipart file
    files = {"file": ("report.csv", b"a,b\n1,2\n", "text/csv")}
    r = sess.post("/artifacts", files=files, data={"title": "Report", "kind": "data"})
    assert r.status_code == 201, r.text
    assert r.json()["mime_type"] == "text/csv" and r.json()["task_id"] == sess.task_id
    # another agent cannot add versions to it
    other = new_agent(client, cid, name="Other")
    other_sess = start_run(app, client, cid, other["id"], task_title="other work")
    r = other_sess.post("/artifacts", json={"artifact_id": art["id"], "content": "hijack"})
    assert r.status_code == 403
    assert sess.post("/artifacts", json={"kind": "document", "content": "x"}).status_code == 422
    assert (
        sess.post("/artifacts", content=b"raw", headers={"content-type": "text/plain"}).status_code
        == 422
    )


def test_agents_cannot_hire_past_the_headcount_limit(app, client, world):
    cid, agent, sess = world
    live = len(client.get(api(cid, "/agents")).json()["items"])
    client.patch(api(cid, "/policy"), json={"max_headcount": live + 1})
    token = {"Authorization": f"Bearer {sess.token}"}
    body = {"name": "Hire 1", "role_title": "Designer"}
    first = client.post(api(cid, "/agents"), json=body, headers=token)
    assert first.status_code == 201 and first.json()["status"] == "proposed"
    second = client.post(api(cid, "/agents"), json={**body, "name": "Hire 2"}, headers=token)
    assert second.status_code == 409 and second.json()["error"]["code"] == "limit_exceeded"
    # an agent cannot hand itself budget or permissions while hiring either
    forged = client.post(
        api(cid, "/agents"),
        json={**body, "name": "Hire 3", "daily_budget_micros": 10**9},
        headers=token,
    )
    assert forged.status_code == 403


def test_execution_request_repr_never_shows_the_run_token(app, client, company):
    from app.runtimes.base import (
        AgentSpec,
        CompanySpec,
        ExecutionRequest,
        Limits,
        TaskSpec,
    )

    request = ExecutionRequest(
        run_id="r1",
        attempt_number=1,
        task=TaskSpec("t1", "title", None, None, "work", "normal"),
        agent=AgentSpec("a1", "Nova", "Dev", None, None, None, None, {}, []),
        company=CompanySpec("c1", "Acme", None, []),
        limits=Limits(60, None),
        run_token="cos_run.super-secret",
        agent_api_url="http://x/api/agent/v1",
        workspace_dir="/tmp/w",
    )
    assert "super-secret" not in repr(request) and "super-secret" not in str(request)
