"""The ideas backlog: caps, evidence, duplicates, deciding (task, goal, project lead scope),
authority and lifecycle. Agents act through their run tokens, people through the owner API."""

from __future__ import annotations

from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.enums import TaskStatus
from app.models.radar import RadarIdea
from app.models.work import Goal, Project, Task
from app.services import tasks
from tests.helpers import api, new_agent, new_task, set_policy
from tests.radar.wp2_helpers import (
    INJECTION,
    founder_session,
    idea_row,
    make_project,
    new_idea,
    radar_world,
    researcher_session,
    set_radar_policy,
    start_run,
)

BASE = "/radar"


def propose(sess, item_ids, title="Add a guided setup wizard", **extra):
    body = {
        "title": title,
        "problem": "New users abandon setup after an hour of configuration",
        "proposal": "A five step wizard with sane defaults",
        "item_ids": item_ids,
        **extra,
    }
    return sess.post(BASE + "/ideas", json=body)


def test_a_researcher_proposes_an_idea_with_evidence(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    r = propose(sess, items[:2], impact=4, effort=2, confidence=5)
    assert r.status_code == 201, r.text
    idea = r.json()
    assert idea["status"] == "open" and idea["priority_score"] == 40  # 4 * 5 * 4 / 2
    assert idea["evidence"]["item_ids"] == items[:2]
    assert (
        "Add a guided setup wizard" in idea["content"] and "title" not in idea
    )  # text only in content
    # the cited items are now "used"; the owner sees the idea
    listed = client.get(api(cid, "/radar/items"), params={"status": "used"}).json()["items"]
    assert {i["id"] for i in listed} == set(items[:2])
    assert client.get(api(cid, "/radar/ideas")).json()["items"][0]["id"] == idea["id"]
    with app.state.session_factory() as db:
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.company_id == cid)))
    assert "radar.idea.created" in actions


def test_an_idea_without_evidence_or_with_foreign_evidence_is_refused(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    no_evidence = sess.post(BASE + "/ideas", json={"title": "x", "problem": "p", "proposal": "q"})
    assert no_evidence.status_code == 422
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "m"}).json()
    ocid, _, other_items = radar_world(app, client, other)
    foreign = propose(sess, other_items[:1])
    assert foreign.status_code == 422 and "not found in this company" in foreign.text
    assert propose(sess, ["no-such-item"]).status_code == 422
    assert propose(sess, items[:1], urls=["javascript:alert(1)"]).status_code == 422


def test_daily_and_open_caps(app, client, company):
    cid, _, items = radar_world(app, client, company)
    set_radar_policy(app, cid, radar_max_ideas_per_day=2, radar_max_open_ideas=3)
    _, sess = researcher_session(app, client, cid)
    assert propose(sess, items[:1], title="Rework the pricing page layout").status_code == 201
    assert propose(sess, items[:1], title="Translate the docs into German").status_code == 201
    third = propose(sess, items[:1], title="Offer an annual billing discount")
    assert third.status_code == 409 and third.json()["error"]["code"] == "limit_exceeded"
    assert third.json()["error"]["details"]["limit"] == "radar_max_ideas_per_day"
    # the open cap: raise the daily cap, three ideas are open already after one more
    set_radar_policy(app, cid, radar_max_ideas_per_day=10)
    assert propose(sess, items[:1], title="Add dark mode to the dashboard").status_code == 201
    fourth = propose(sess, items[:1], title="Support single sign on providers")
    assert fourth.status_code == 409
    assert fourth.json()["error"]["details"]["limit"] == "radar_max_open_ideas"


def test_duplicates_of_ideas_open_tasks_and_recent_work_are_refused(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, sess = researcher_session(app, client, cid)
    first = propose(sess, items[:1], title="Add a guided setup wizard").json()
    again = propose(sess, items[:1], title="add a guided setup wizard!")
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate_idea"
    assert again.json()["error"]["details"] == {"existing_kind": "idea", "existing_id": first["id"]}
    task = new_task(client, cid, title="Redesign the billing settings page")
    dup_task = propose(sess, items[:1], title="Redesign the billing settings page")
    assert dup_task.status_code == 409
    assert dup_task.json()["error"]["details"] == {
        "existing_kind": "task",
        "existing_id": task["id"],
    }
    # a rejected idea is not proposed again right away
    client.patch(api(cid, f"/radar/ideas/{first['id']}"), json={"action": "reject"})
    assert propose(sess, items[:1], title="Add a guided setup wizard").status_code == 409


def test_roles_the_server_checks_again(app, client, company):
    cid, _, items = radar_world(app, client, company)
    plain = new_agent(client, cid, name="Pat")
    sess = start_run(app, client, cid, plain["id"])
    assert propose(sess, items[:1]).status_code == 403  # a plain worker has no radar role
    assert sess.get(BASE + "/digest").status_code == 403
    assert sess.post(BASE + "/ideas/x/decide", json={"action": "reject"}).status_code == 403


def test_the_cofounder_accepts_an_idea_into_a_project_as_a_work_task(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, rsess = researcher_session(app, client, cid)
    idea = propose(rsess, items[:1]).json()
    project = make_project(client, cid)
    founder = founder_session(app, client, cid)
    r = founder.post(
        BASE + f"/ideas/{idea['id']}/decide",
        json={"action": "accept", "project_id": project["id"], "note": "worth it"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["idea"]["status"] == "accepted" and out["project_id"] == project["id"]
    with app.state.session_factory() as db:
        task = db.get(Task, out["task_id"])
        assert task.title == "Add a guided setup wizard" and task.project_id == project["id"]
        assert task.kind == "work" and task.priority in ("low", "normal", "high")
        assert task.idempotency_key == f"radar-idea:{idea['id']}"
        assert f"[id {idea['id']}]" in task.description  # the task points back to the idea
        row = db.get(RadarIdea, idea["id"])
        assert row.decided_by_type == "agent" and row.task_id == task.id
    shown = client.get(api(cid, f"/radar/ideas/{idea['id']}")).json()
    assert shown["task_title"] == "Add a guided setup wizard" and shown["project_name"] == "Product"
    # a decided idea cannot be decided again
    again = founder.post(BASE + f"/ideas/{idea['id']}/decide", json={"action": "reject"})
    assert again.status_code == 409


def test_accepting_obeys_the_m12_caps_and_duplicate_check(app, client, company):
    cid, _, items = radar_world(app, client, company)
    set_policy(client, cid, max_work_tasks_per_project=1)
    project = make_project(client, cid)
    _, rsess = researcher_session(app, client, cid)
    one = propose(rsess, items[:1], title="Rework the pricing page layout").json()
    two = propose(rsess, items[:1], title="Translate the docs into German").json()
    founder = founder_session(app, client, cid)
    ok = founder.post(
        BASE + f"/ideas/{one['id']}/decide", json={"action": "accept", "project_id": project["id"]}
    )
    assert ok.status_code == 200, ok.text
    capped = founder.post(
        BASE + f"/ideas/{two['id']}/decide", json={"action": "accept", "project_id": project["id"]}
    )
    assert capped.status_code == 409 and capped.json()["error"]["code"] == "limit_exceeded"
    assert idea_row(app, two["id"]).status == "open"  # nothing half-done


def test_new_goal_is_cofounder_only_and_capped_per_week(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, rsess = researcher_session(app, client, cid)
    one = propose(rsess, items[:1], title="Rework the pricing page layout").json()
    two = propose(rsess, items[:1], title="Translate the docs into German").json()
    founder = founder_session(app, client, cid)
    r = founder.post(
        BASE + f"/ideas/{one['id']}/decide", json={"action": "accept", "new_goal": True}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    with app.state.session_factory() as db:
        goal = db.get(Goal, out["goal_id"])
        project = db.get(Project, out["project_id"])
        assert goal.status == "active" and goal.created_by_type == "agent"
        assert project.goal_id == goal.id and db.get(Task, out["task_id"]).project_id == project.id
    again = founder.post(
        BASE + f"/ideas/{two['id']}/decide", json={"action": "accept", "new_goal": True}
    )
    assert (
        again.status_code == 409
        and again.json()["error"]["details"]["limit"] == "radar_max_goals_per_week"
    )
    # nobody else may create goals from ideas
    lead = new_agent(client, cid, name="Lena", kind="lead", role_title="Project Lead")
    lsess = start_run(app, client, cid, lead["id"])
    refused = lsess.post(
        BASE + f"/ideas/{two['id']}/decide", json={"action": "accept", "new_goal": True}
    )
    assert refused.status_code == 403


def test_a_project_lead_decides_only_for_its_own_project(app, client, company):
    cid, _, items = radar_world(app, client, company)
    lead = new_agent(client, cid, name="Lena", kind="lead", role_title="Project Lead")
    mine = make_project(client, cid, "Mine", lead_agent_id=lead["id"])
    other = make_project(client, cid, "Other")
    _, rsess = researcher_session(app, client, cid)
    idea = propose(rsess, items[:1]).json()
    lsess = start_run(app, client, cid, lead["id"])
    assert (
        lsess.post(
            BASE + f"/ideas/{idea['id']}/decide",
            json={"action": "accept", "project_id": other["id"]},
        ).status_code
        == 403
    )
    assert (
        lsess.post(BASE + f"/ideas/{idea['id']}/decide", json={"action": "reject"}).status_code
        == 403
    )
    ok = lsess.post(
        BASE + f"/ideas/{idea['id']}/decide", json={"action": "accept", "project_id": mine["id"]}
    )
    assert ok.status_code == 200, ok.text


def test_auto_accept_off_leaves_acceptance_to_people_but_agents_may_still_reject(
    app, client, company
):
    cid, _, items = radar_world(app, client, company)
    set_radar_policy(app, cid, radar_auto_accept=False)
    _, rsess = researcher_session(app, client, cid)
    one = propose(rsess, items[:1], title="Rework the pricing page layout").json()
    two = propose(rsess, items[:1], title="Translate the docs into German").json()
    project = make_project(client, cid)
    founder = founder_session(app, client, cid)
    refused = founder.post(
        BASE + f"/ideas/{one['id']}/decide", json={"action": "accept", "project_id": project["id"]}
    )
    assert refused.status_code == 403 and "owner decides" in refused.text
    assert (
        founder.post(BASE + f"/ideas/{two['id']}/decide", json={"action": "reject"}).status_code
        == 200
    )
    # the owner can accept
    ok = client.patch(
        api(cid, f"/radar/ideas/{one['id']}"),
        json={"action": "accept", "project_id": project["id"]},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "accepted" and ok.json()["task_id"]
    assert ok.json()["decided_by_type"] == "human"


def test_an_idea_with_instruction_like_text_needs_a_person_to_accept(app, client, company):
    cid, _, items = radar_world(app, client, company)
    _, rsess = researcher_session(app, client, cid)
    idea = propose(rsess, items[:1], title="Handle the setup complaint", proposal=INJECTION).json()
    project = make_project(client, cid)
    founder = founder_session(app, client, cid)
    refused = founder.post(
        BASE + f"/ideas/{idea['id']}/decide", json={"action": "accept", "project_id": project["id"]}
    )
    assert refused.status_code == 403 and refused.json()["error"]["details"] == {
        "reason": "flagged_text"
    }
    assert (
        founder.post(BASE + f"/ideas/{idea['id']}/decide", json={"action": "reject"}).status_code
        == 200
    )


def test_merge_moves_the_evidence_and_reopen_respects_the_cap(app, client, company):
    cid, _, items = radar_world(app, client, company)
    a = new_idea(app, cid, "Make onboarding faster", evidence_items=[items[0]])
    b = new_idea(app, cid, "Streamline the first run", evidence_items=[items[1]])
    r = client.patch(api(cid, f"/radar/ideas/{b}"), json={"action": "merge", "merge_into_id": a})
    assert (
        r.status_code == 200 and r.json()["status"] == "merged" and r.json()["merged_into_id"] == a
    )
    target = client.get(api(cid, f"/radar/ideas/{a}")).json()
    assert set(target["evidence"]["item_ids"]) == {items[0], items[1]}
    assert (
        client.patch(
            api(cid, f"/radar/ideas/{a}"), json={"action": "merge", "merge_into_id": a}
        ).status_code
        == 422
    )
    c = new_idea(app, cid, "Add dark mode to the dashboard", status="rejected")
    set_radar_policy(app, cid, radar_max_open_ideas=1)
    assert client.patch(api(cid, f"/radar/ideas/{c}"), json={"action": "reopen"}).status_code == 409
    set_radar_policy(app, cid, radar_max_open_ideas=5)
    reopened = client.patch(api(cid, f"/radar/ideas/{c}"), json={"action": "reopen"})
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"


def test_an_accepted_idea_is_done_when_its_task_is_done(app, client, company):
    cid, _, items = radar_world(app, client, company)
    project = make_project(client, cid)
    idea = new_idea(app, cid)
    r = client.patch(
        api(cid, f"/radar/ideas/{idea}"), json={"action": "accept", "project_id": project["id"]}
    )
    task_id = r.json()["task_id"]
    with app.state.session_factory() as db:
        task = db.get(Task, task_id)
        tasks.transition(db, task, TaskStatus.READY, principal=None)
        tasks.transition(db, task, TaskStatus.IN_PROGRESS, principal=None)
        tasks.transition(db, task, TaskStatus.DONE, principal=None)  # runs the hooks
        db.commit()
    assert idea_row(app, idea).status == "done"
    events = client.get(api(cid, "/activity")).json()["items"]
    assert any(e["action"] == "radar.idea.done" for e in events)


def test_tenancy_company_b_cannot_see_or_decide_company_a_ideas(app, client, company):
    cid, _, items = radar_world(app, client, company)
    idea = new_idea(app, cid)
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "m"}).json()
    ocid = other["id"]
    assert client.get(api(ocid, f"/radar/ideas/{idea}")).status_code == 404
    assert (
        client.patch(api(ocid, f"/radar/ideas/{idea}"), json={"action": "reject"}).status_code
        == 404
    )
    assert client.get(api(ocid, "/radar/ideas")).json()["items"] == []
    ofounder = founder_session(app, client, ocid)
    assert (
        ofounder.post(BASE + f"/ideas/{idea}/decide", json={"action": "reject"}).status_code == 404
    )
    assert idea_row(app, idea).status == "open"
