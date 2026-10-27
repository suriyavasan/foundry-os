"""M12 WP2: agents cannot over-decompose. Per-project and per-parent caps, and fuzzy duplicate
detection against open tasks and recently finished ones."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.models.work import Task
from app.services import tasks as task_service
from tests.autonomy_helpers import scripted_world, tasks_of
from tests.helpers import api, new_task, set_policy
from tests.test_agent_api import start_run


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def project(client, cid, lead_id, name="Landing page"):
    r = client.post(
        api(cid, "/projects"), json={"name": name, "status": "active", "lead_agent_id": lead_id}
    )
    assert r.status_code == 201, r.text
    return r.json()


def lead_session(app, client, w):
    return start_run(app, client, w.cid, w.lead["id"], task_title="Management run", kind="planning")


def set_task(app, task_id, **values):
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        for key, value in values.items():
            setattr(task, key, value)
        db.commit()


# ------------------------------------------------------------------ similarity


@pytest.mark.parametrize(
    "a,b,duplicate",
    [
        ("Write the landing page copy", "write landing-page copy!", True),
        ("Write landing pages copy", "Write the landing page copy", True),  # plural, stopword
        ("Implement user login endpoint", "Implement user login endpoints", True),
        ("Design landing page", "Write landing page copy", False),
        ("Design landing page", "Design landing page layout", False),
        ("Task 1", "Task 2", False),
        ("Set up CI", "Set up CD", False),
    ],
)
def test_title_similarity(a, b, duplicate):
    assert (task_service.title_similarity(a, b) >= task_service.DUPLICATE_SIMILARITY) is duplicate


def test_titles_made_only_of_stopwords_still_compare():
    assert task_service.title_similarity("To do", "to do") == 1.0
    assert task_service.title_similarity("", "anything") == 0.0


# ------------------------------------------------------------------ project cap


def test_agent_created_work_is_capped_per_project(app, client, w):
    cid = w.cid
    set_policy(client, cid, max_work_tasks_per_project=3)
    proj = project(client, cid, w.lead["id"])
    sess = lead_session(app, client, w)
    titles = ["Design the logo", "Write the pricing copy", "Build the signup form"]
    ids = []
    for title in titles:
        r = sess.post("/tasks", json={"title": title, "project_id": proj["id"]})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    r = sess.post("/tasks", json={"title": "Deploy to production", "project_id": proj["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    details = r.json()["error"]["details"]
    assert details["limit"] == "max_work_tasks_per_project" and details["max"] == 3
    assert "do not plan more work" in r.json()["error"]["message"]
    # humans are not capped, and what they add does not use up the agents' allowance
    assert (
        client.post(
            api(cid, "/tasks"), json={"title": "Human task", "project_id": proj["id"]}
        ).status_code
        == 201
    )
    # another project has its own allowance
    other = project(client, cid, w.lead["id"], name="Second project")
    ok = sess.post("/tasks", json={"title": "Deploy to production", "project_id": other["id"]})
    assert ok.status_code == 201
    # cancelled tasks free their place
    assert client.post(api(cid, f"/tasks/{ids[0]}/cancel")).status_code == 200
    ok = sess.post("/tasks", json={"title": "Deploy to production", "project_id": proj["id"]})
    assert ok.status_code == 201, ok.text


def test_a_human_creating_tasks_ignores_the_project_cap(client, company):
    cid = company["id"]
    set_policy(client, cid, max_work_tasks_per_project=1)
    proj = client.post(api(cid, "/projects"), json={"name": "P", "status": "active"}).json()
    for title in ("Alpha job", "Beta job", "Gamma job"):
        new_task(client, cid, title=title, project_id=proj["id"])


# ------------------------------------------------------------------ subtask cap


def test_subtasks_are_capped_per_parent(app, client, w):
    cid = w.cid
    set_policy(client, cid, max_subtasks_per_task=2, max_tasks_per_planning_cycle=50)
    sess = start_run(app, client, cid, w.specialist["id"], task_title="Big job")
    created = []
    for title in ("Collect the sources", "Draft the outline"):
        r = sess.post("/tasks", json={"title": title})
        assert r.status_code == 201 and r.json()["parent_task_id"] == sess.task_id
        created.append(r.json()["id"])
    r = sess.post("/tasks", json={"title": "Polish the final text"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit_exceeded"
    assert r.json()["error"]["details"] == {"limit": "max_subtasks_per_task", "max": 2}
    assert "Big job" in r.json()["error"]["message"]
    # cancelling one makes room; another parent has its own allowance
    assert client.post(api(cid, f"/tasks/{created[0]}/cancel")).status_code == 200
    assert sess.post("/tasks", json={"title": "Polish the final text"}).status_code == 201
    other = start_run(app, client, cid, w.specialist["id"], task_title="Another job")
    assert other.post("/tasks", json={"title": "Collect the sources"}).status_code == 201


# ------------------------------------------------------------------ duplicates


def test_a_near_duplicate_of_an_open_task_is_refused_with_the_existing_task(app, client, w):
    cid = w.cid
    proj = project(client, cid, w.lead["id"])
    sess = lead_session(app, client, w)
    first = sess.post(
        "/tasks", json={"title": "Write the landing page copy", "project_id": proj["id"]}
    )
    assert first.status_code == 201
    r = sess.post("/tasks", json={"title": "Write landing-page copy.", "project_id": proj["id"]})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["details"]["existing_task_id"] == first.json()["id"]
    assert err["details"]["limit"] == "duplicate_task"
    assert first.json()["id"] in err["message"] and "similar task" in err["message"]
    assert len([t for t in tasks_of(app, cid, project_id=proj["id"])]) == 1
    # a genuinely different task goes through
    assert (
        sess.post(
            "/tasks", json={"title": "Design landing page mockups", "project_id": proj["id"]}
        ).status_code
        == 201
    )


def test_a_task_finished_in_the_last_week_counts_as_a_duplicate(app, client, w):
    cid = w.cid
    proj = project(client, cid, w.lead["id"])
    sess = lead_session(app, client, w)
    first = sess.post("/tasks", json={"title": "Set up the database", "project_id": proj["id"]})
    task_id = first.json()["id"]
    set_task(app, task_id, status="done", completed_at=utcnow() - timedelta(days=2))
    r = sess.post("/tasks", json={"title": "Set up database", "project_id": proj["id"]})
    assert r.status_code == 409 and r.json()["error"]["details"]["existing_status"] == "done"
    assert "done recently" in r.json()["error"]["message"]
    # but not one finished long ago
    set_task(app, task_id, completed_at=utcnow() - timedelta(days=8))
    assert (
        sess.post("/tasks", json={"title": "Set up database", "project_id": proj["id"]}).status_code
        == 201
    )


def test_duplicates_are_judged_within_the_project_only(app, client, w):
    cid = w.cid
    p1 = project(client, cid, w.lead["id"])
    p2 = project(client, cid, w.lead["id"], name="Other project")
    sess = lead_session(app, client, w)
    assert (
        sess.post("/tasks", json={"title": "Write copy", "project_id": p1["id"]}).status_code == 201
    )
    assert (
        sess.post("/tasks", json={"title": "Write copy", "project_id": p2["id"]}).status_code == 201
    )


def test_cancelled_and_failed_tasks_are_not_duplicates(app, client, w):
    cid = w.cid
    proj = project(client, cid, w.lead["id"])
    sess = lead_session(app, client, w)
    a = sess.post("/tasks", json={"title": "Write the FAQ page", "project_id": proj["id"]}).json()
    assert client.post(api(cid, f"/tasks/{a['id']}/cancel")).status_code == 200
    assert (
        sess.post(
            "/tasks", json={"title": "Write the FAQ page", "project_id": proj["id"]}
        ).status_code
        == 201
    )


def test_a_refused_duplicate_is_audited_for_the_loop_guard(app, client, w):
    from sqlalchemy import select

    from app.models.audit import AuditEvent

    cid = w.cid
    proj = project(client, cid, w.lead["id"])
    sess = lead_session(app, client, w)
    sess.post("/tasks", json={"title": "Write the FAQ page", "project_id": proj["id"]})
    r = sess.post("/tasks", json={"title": "write faq page", "project_id": proj["id"]})
    assert r.status_code == 409
    with app.state.session_factory() as db:
        actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "task.duplicate_suppressed" in actions
