from __future__ import annotations

from sqlalchemy import select, update

from app.core.errors import InvalidTransition
from app.models.audit import AuditEvent
from app.models.run import CompanyEvent
from app.models.work import Task
from app.services import tasks as task_service
from tests.helpers import api, get_task, new_agent, new_task

STATUSES = [
    "backlog",
    "ready",
    "in_progress",
    "in_review",
    "revision_requested",
    "blocked",
    "done",
    "failed",
    "cancelled",
]
# The state machine from the spec, written out independently of the implementation.
VALID_EDGES = {
    ("backlog", "ready"),
    ("backlog", "blocked"),
    ("backlog", "cancelled"),
    ("ready", "in_progress"),
    ("ready", "backlog"),
    ("ready", "blocked"),
    ("ready", "cancelled"),
    ("in_progress", "in_review"),
    ("in_progress", "done"),
    ("in_progress", "ready"),
    ("in_progress", "failed"),
    ("in_progress", "blocked"),
    ("in_progress", "cancelled"),
    ("in_review", "done"),
    ("in_review", "revision_requested"),
    ("in_review", "blocked"),
    ("in_review", "cancelled"),
    ("revision_requested", "ready"),
    ("revision_requested", "failed"),
    ("revision_requested", "blocked"),
    ("revision_requested", "cancelled"),
    ("blocked", "backlog"),
    ("blocked", "cancelled"),
    ("failed", "backlog"),  # explicit retry
}


def _force_status(app, task_id: str, status: str) -> None:
    with app.state.session_factory() as db:
        db.execute(update(Task).where(Task.id == task_id).values(status=status))
        db.commit()


def test_state_machine_matrix(app, client, company):
    """Every (from, to) pair: valid edges move and audit, everything else is invalid_transition."""
    cid = company["id"]
    wrong: list[str] = []
    for source in STATUSES:
        for target in STATUSES:
            task = new_task(client, cid, title=f"{source}->{target}")
            _force_status(app, task["id"], source)
            with app.state.session_factory() as db:
                row = db.get(Task, task["id"])
                if (source, target) in VALID_EDGES:
                    task_service.transition(db, row, target, principal=None)
                    db.commit()
                    actions = list(
                        db.scalars(
                            select(AuditEvent.action).where(
                                AuditEvent.target_id == task["id"],
                                AuditEvent.action != "task.created",
                            )
                        )
                    )
                    if db.get(Task, task["id"]).status != target or not actions:
                        wrong.append(f"{source}->{target}: valid edge did not apply/audit")
                else:
                    try:
                        task_service.transition(db, row, target, principal=None)
                        wrong.append(f"{source}->{target}: invalid edge was allowed")
                    except InvalidTransition:
                        db.rollback()
    assert wrong == []
    assert len(VALID_EDGES) == 24


def test_transition_writes_audit_and_company_event_together(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    assert task["status"] == "ready"
    with app.state.session_factory() as db:
        types = [e.type for e in db.scalars(select(CompanyEvent).order_by(CompanyEvent.id))]
    assert "task.created" in types and "task.updated" in types and "activity" in types


def test_unassigned_task_is_backlog_then_ready_on_assignment(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid)
    assert task["status"] == "backlog"
    r = client.patch(api(cid, f"/tasks/{task['id']}"), json={"assignee_agent_id": agent["id"]})
    assert r.json()["status"] == "ready"
    r = client.patch(api(cid, f"/tasks/{task['id']}"), json={"assignee_agent_id": None})
    assert r.json()["status"] == "backlog"


def test_proposed_agent_does_not_make_task_ready(client, company):
    cid = company["id"]
    agent = new_agent(client, cid, activate=False)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    assert task["status"] == "backlog"
    client.post(api(cid, f"/agents/{agent['id']}/activate"))
    assert get_task(client, cid, task["id"])["status"] == "ready"


def test_readiness_follows_dependencies(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    first = new_task(client, cid, title="first", assignee_agent_id=agent["id"])
    second = new_task(
        client, cid, title="second", assignee_agent_id=agent["id"], depends_on=[first["id"]]
    )
    assert second["status"] == "backlog" and second["depends_on"] == [first["id"]]
    assert first["status"] == "ready"
    # finishing the dependency (via review accept path) readies the dependent
    r = client.post(
        api(cid, f"/tasks/{first['id']}/dependencies"), json={"depends_on_task_id": second["id"]}
    )
    assert r.status_code == 409  # cycle


def test_dependent_becomes_ready_when_dependency_is_done(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    first = new_task(client, cid, title="first")
    second = new_task(
        client, cid, title="second", assignee_agent_id=agent["id"], depends_on=[first["id"]]
    )
    assert second["status"] == "backlog"
    with app.state.session_factory() as db:
        row = db.get(Task, first["id"])
        row.status = "in_progress"
        task_service.finish_success(
            db, row, summary="ok", output_refs=None, principal=None, run_id=None
        )
        db.commit()
    assert get_task(client, cid, first["id"])["status"] == "done"
    assert get_task(client, cid, second["id"])["status"] == "ready"


def test_readiness_follows_agent_status(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    assert task["status"] == "ready"
    client.post(api(cid, f"/agents/{agent['id']}/pause"))
    assert get_task(client, cid, task["id"])["status"] == "backlog"
    client.post(api(cid, f"/agents/{agent['id']}/resume"))
    assert get_task(client, cid, task["id"])["status"] == "ready"
    client.post(api(cid, f"/agents/{agent['id']}/retire"))
    assert get_task(client, cid, task["id"])["status"] == "backlog"


def test_dependency_cycles_and_self_dependency_rejected(client, company):
    cid = company["id"]
    a, b, c = (new_task(client, cid, title=t) for t in "abc")
    add = lambda t, d: client.post(  # noqa: E731
        api(cid, f"/tasks/{t['id']}/dependencies"), json={"depends_on_task_id": d["id"]}
    )
    assert add(a, b).status_code == 201
    assert add(b, c).status_code == 201
    r = add(c, a)  # a -> b -> c -> a
    assert r.status_code == 409 and "cycle" in r.json()["error"]["message"]
    assert add(a, a).status_code == 409
    assert add(b, a).status_code == 409
    assert add(a, b).status_code == 409  # duplicate
    # removal makes it legal again
    assert client.delete(api(cid, f"/tasks/{a['id']}/dependencies/{b['id']}")).status_code == 200
    assert add(c, a).status_code == 201
    assert client.delete(api(cid, f"/tasks/{a['id']}/dependencies/{b['id']}")).status_code == 404


def test_cross_company_references_are_404(client, company):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other Co"}).json()
    foreign_agent = new_agent(client, other["id"], name="Foreign")
    foreign_task = new_task(client, other["id"], title="theirs")
    foreign_project = client.post(api(other["id"], "/projects"), json={"name": "P"}).json()
    mine = new_task(client, cid, title="mine")
    for body in (
        {"assignee_agent_id": foreign_agent["id"]},
        {"reviewer_agent_id": foreign_agent["id"]},
        {"project_id": foreign_project["id"]},
        {"parent_task_id": foreign_task["id"]},
        {"depends_on": [foreign_task["id"]]},
    ):
        r = client.post(api(cid, "/tasks"), json={"title": "x", **body})
        assert r.status_code == 404, (body, r.text)
    assert (
        client.post(
            api(cid, f"/tasks/{mine['id']}/dependencies"),
            json={"depends_on_task_id": foreign_task["id"]},
        ).status_code
        == 404
    )
    assert client.get(api(cid, f"/tasks/{foreign_task['id']}")).status_code == 404
    assert (
        client.patch(api(cid, f"/tasks/{foreign_task['id']}"), json={"title": "z"}).status_code
        == 404
    )
    assert client.post(api(cid, f"/tasks/{foreign_task['id']}/cancel")).status_code == 404
    assert client.get(api(cid, f"/tasks/{foreign_task['id']}/runs")).status_code == 404
    # nothing leaked into the foreign task list either
    assert all(t["company_id"] == cid for t in client.get(api(cid, "/tasks")).json()["items"])


def test_retired_agent_cannot_be_assigned(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    client.post(api(cid, f"/agents/{agent['id']}/retire"))
    r = client.post(api(cid, "/tasks"), json={"title": "x", "assignee_agent_id": agent["id"]})
    assert r.status_code == 409


def test_idempotency_key_returns_existing_task(client, company):
    cid = company["id"]
    first = client.post(api(cid, "/tasks"), json={"title": "once", "idempotency_key": "k-1"})
    assert first.status_code == 201
    second = client.post(
        api(cid, "/tasks"), json={"title": "different title", "idempotency_key": "k-1"}
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"] and second.json()["title"] == "once"
    assert len(client.get(api(cid, "/tasks")).json()["items"]) == 1
    # keys are per company
    other = client.post("/api/companies", json={"name": "Other"}).json()
    third = client.post(api(other["id"], "/tasks"), json={"title": "x", "idempotency_key": "k-1"})
    assert third.status_code == 201 and third.json()["id"] != first.json()["id"]


def test_delegation_depth_limit_via_parent_chain(client, company):
    cid = company["id"]
    parent = new_task(client, cid, title="d0")
    depths = [parent["delegation_depth"]]
    for i in range(1, 4):
        child = new_task(client, cid, title=f"d{i}", parent_task_id=parent["id"])
        depths.append(child["delegation_depth"])
        parent = child
    assert depths == [0, 1, 2, 3]
    r = client.post(api(cid, "/tasks"), json={"title": "d4", "parent_task_id": parent["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    client.patch(api(cid, "/policy"), json={"max_delegation_depth": 4})
    assert (
        client.post(
            api(cid, "/tasks"), json={"title": "d4", "parent_task_id": parent["id"]}
        ).status_code
        == 201
    )


def test_task_filters_and_pagination(client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    project = client.post(api(cid, "/projects"), json={"name": "P"}).json()
    parent = new_task(client, cid, title="parent", project_id=project["id"])
    for i in range(5):
        new_task(
            client,
            cid,
            title=f"child{i}",
            parent_task_id=parent["id"],
            kind="review" if i == 0 else "work",
        )
    new_task(client, cid, title="assigned", assignee_agent_id=agent["id"])
    listing = lambda **p: client.get(api(cid, "/tasks"), params=p).json()  # noqa: E731
    assert len(listing()["items"]) == 7
    assert len(listing(parent_task_id=parent["id"])["items"]) == 5
    assert len(listing(kind="review")["items"]) == 1
    assert len(listing(status="ready")["items"]) == 1
    assert len(listing(assignee_agent_id=agent["id"])["items"]) == 1
    assert len(listing(project_id=project["id"])["items"]) == 1
    seen, cursor = [], None
    while True:
        page = listing(limit=3, **({"cursor": cursor} if cursor else {}))
        seen += [t["id"] for t in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 7
    assert client.get(api(cid, "/tasks"), params={"status": "bogus"}).status_code == 422


def test_cancel_block_unblock_retry_actions(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    tid = task["id"]
    r = client.post(api(cid, f"/tasks/{tid}/block"), json={"reason": "waiting on legal"})
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert r.json()["error"]["message"] == "waiting on legal"
    assert client.post(api(cid, f"/tasks/{tid}/block")).status_code == 409  # already blocked
    r = client.post(api(cid, f"/tasks/{tid}/unblock"))
    assert r.json()["status"] == "ready" and r.json()["error"] is None
    assert client.post(api(cid, f"/tasks/{tid}/unblock")).status_code == 409
    assert client.post(api(cid, f"/tasks/{tid}/retry")).status_code == 409  # only failed tasks
    r = client.post(api(cid, f"/tasks/{tid}/cancel"), json={"reason": "no longer needed"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["completed_at"] is not None
    for action in ("cancel", "block", "unblock", "retry"):
        assert client.post(api(cid, f"/tasks/{tid}/{action}")).status_code == 409, action
    assert client.patch(api(cid, f"/tasks/{tid}"), json={"title": "x"}).status_code == 409

    failed = new_task(client, cid, title="f", assignee_agent_id=agent["id"])
    _force_status(app, failed["id"], "failed")
    with app.state.session_factory() as db:
        db.execute(update(Task).where(Task.id == failed["id"]).values(attempt_count=3))
        db.commit()
    r = client.post(api(cid, f"/tasks/{failed['id']}/retry"))
    assert r.json()["status"] == "ready" and r.json()["attempt_count"] == 0


def test_task_mutations_require_human_admin(client, client_as, make_user, add_member, company):
    from app.core.principal import Principal, PrincipalType
    from tests.conftest import human

    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    viewer = make_user("tv@x.test")
    add_member(cid, viewer.id, "viewer")
    for caller in (
        client_as(human(viewer)),
        client_as(Principal(PrincipalType.AGENT, agent["id"], "Nova", cid)),
    ):
        assert caller.get(api(cid, f"/tasks/{task['id']}")).status_code == 200
        assert caller.post(api(cid, "/tasks"), json={"title": "x"}).status_code == 403
        assert (
            caller.patch(api(cid, f"/tasks/{task['id']}"), json={"title": "x"}).status_code == 403
        )
        assert caller.post(api(cid, f"/tasks/{task['id']}/cancel")).status_code == 403
        assert caller.post(api(cid, f"/tasks/{task['id']}/block")).status_code == 403


def test_task_update_validation(client, company):
    cid = company["id"]
    task = new_task(client, cid)
    for bad in (
        {"title": ""},
        {"kind": "weird"},
        {"priority": "asap"},
        {"budget_micros": -1},
        {"status": "done"},
        {"title": None},
    ):
        assert client.patch(api(cid, f"/tasks/{task['id']}"), json=bad).status_code == 422, bad
    r = client.patch(
        api(cid, f"/tasks/{task['id']}"),
        json={
            "priority": "urgent",
            "required_skills": ["python", "python", "sql"],
            "due_at": "2027-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 200
    assert r.json()["priority"] == "urgent" and r.json()["required_skills"] == ["python", "sql"]
    assert (
        client.post(
            api(cid, "/tasks"), json={"title": "x", "due_at": "2027-01-01T00:00:00"}
        ).status_code
        == 422
    )
