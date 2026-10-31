"""M18 A2: founder questions -- non-blocking by default, every one with a default and a deadline,
rate limited in code, answered only by people (owners/admins), tenant scoped."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ValidationFailed
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.core.time import utcnow
from app.models.audit import AuditEvent
from app.models.autonomy import WakeRequest
from app.models.questions import FounderQuestion
from app.models.run import CompanyEvent
from app.models.work import Task
from app.services import attempt_memory, questions
from tests.conftest import human
from tests.helpers import api, new_agent
from tests.test_agent_api import start_run


@pytest.fixture
def world(app, client, company):
    cid = company["id"]
    worker = new_agent(client, cid, name="Worker")
    return cid, worker, start_run(app, client, cid, worker["id"])


def ask(sess, **over):
    body = {"question": "Which tone?", "default_answer": "friendly", **over}
    return sess.post("/questions", json=body)


def qrow(app, qid) -> FounderQuestion:
    with app.state.session_factory() as db:
        q = db.get(FounderQuestion, qid)
        db.expunge(q)
        return q


def task_row(app, task_id) -> Task:
    with app.state.session_factory() as db:
        t = db.get(Task, task_id)
        db.expunge(t)
        return t


def triggers(app, agent_id):
    with app.state.session_factory() as db:
        return [
            r["trigger"]
            for req in db.scalars(select(WakeRequest).where(WakeRequest.agent_id == agent_id))
            for r in req.reasons
        ]


def owner_ctx(client, cid) -> CompanyContext:
    me = client.get("/api/me").json()
    return CompanyContext(
        cid, Principal(PrincipalType.HUMAN, me["user"]["id"], "Owner"), Role.OWNER
    )


# ------------------------------------------------------------------ asking


def test_an_agent_asks_with_a_default_and_a_deadline(app, client, world):
    cid, worker, sess = world
    r = ask(sess, options=["friendly", "formal", " formal ", ""], deadline_hours=6)
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["status"] == "open" and q["blocking"] is False
    assert q["options"] == ["friendly", "formal"]  # trimmed, de-duplicated, no empty ones
    assert q["task_id"] == sess.task_id and q["run_id"] == sess.run_id
    assert q["agent_id"] == worker["id"] and q["answer"] is None
    row = qrow(app, q["id"])
    assert abs((row.deadline - utcnow()).total_seconds() - 6 * 3600) < 30
    # deadline_hours clamps to 1-24 and defaults to 4
    assert (
        abs(
            (
                qrow(app, ask_other(app, client, cid, deadline_hours=500)).deadline - utcnow()
            ).total_seconds()
            - 24 * 3600
        )
        < 30
    )
    assert (
        abs(
            (
                qrow(app, ask_other(app, client, cid, deadline_hours=-3)).deadline - utcnow()
            ).total_seconds()
            - 3600
        )
        < 30
    )
    # audit, event (ids only) and a notification to the owner
    with app.state.session_factory() as db:
        assert "question.asked" in [
            e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == q["id"]))
        ]
        [event] = [
            e
            for e in db.scalars(select(CompanyEvent).where(CompanyEvent.type == "question.created"))
            if e.payload["question_id"] == q["id"]
        ]
        assert event.payload["agent_id"] == worker["id"] and "question" not in event.payload
    notes = client.get("/api/notifications").json()["items"]
    [n] = [n for n in notes if n["kind"] == "founder_question" and n["related_id"] == q["id"]]
    assert "Which tone?" in n["title"] and "friendly" in n["body"]


def ask_other(app, client, cid, **over) -> str:
    agent = new_agent(client, cid, name=f"Other{len(over)}{over.get('deadline_hours')}")
    sess = start_run(app, client, cid, agent["id"])
    r = ask(sess, **over)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_the_question_needs_a_default_and_sane_sizes_and_no_secrets(world):
    _, _, sess = world
    assert sess.post("/questions", json={"question": "Q?"}).status_code == 422  # no default
    assert ask(sess, default_answer="").status_code == 422
    assert ask(sess, question="x" * 1001).status_code == 422
    assert ask(sess, default_answer="x" * 501).status_code == 422
    assert ask(sess, options=["a"] * 7).status_code == 422
    assert ask(sess, options=["x" * 121]).status_code == 422
    leak = ask(sess, question="Use sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 for it?")
    assert leak.status_code == 422 and "secret" in leak.json()["error"]["message"]


def test_question_limits_five_open_per_company_two_per_agent_per_day(app, client, company):
    cid = company["id"]
    # 2 per agent per 24 h
    agent = new_agent(client, cid, name="Chatty")
    sess = start_run(app, client, cid, agent["id"])
    assert ask(sess).status_code == 201
    assert ask(sess, question="Another?").status_code == 201
    third = ask(sess, question="And another?")
    assert third.status_code == 429 and third.json()["error"]["code"] == "question_limit"
    assert "company_record_decision" in third.json()["error"]["message"]
    # the window slides: a day later the agent may ask again
    with app.state.session_factory() as db:
        for q in db.scalars(select(FounderQuestion)):
            q.created_at = utcnow() - timedelta(hours=25)
            q.status = "cancelled"
        db.commit()
    assert ask(sess, question="Fresh?").status_code == 201
    # 5 open per company, whoever asks
    for i in range(4):
        other = new_agent(client, cid, name=f"Agent{i}")
        assert ask(start_run(app, client, cid, other["id"]), question=f"Q{i}?").status_code == 201
    last = new_agent(client, cid, name="Last")
    r = ask(start_run(app, client, cid, last["id"]), question="One too many?")
    assert r.status_code == 429 and r.json()["error"]["code"] == "question_limit"
    assert "5 open" in r.json()["error"]["message"]


# ------------------------------------------------------------------ answering


def test_a_non_blocking_answer_wakes_the_agent_only_when_it_differs_from_the_default(
    app, client, company
):
    cid = company["id"]
    a1 = new_agent(client, cid, name="One")
    a2 = new_agent(client, cid, name="Two")
    q1 = ask(start_run(app, client, cid, a1["id"])).json()
    q2 = ask(start_run(app, client, cid, a2["id"])).json()
    r = client.post(api(cid, f"/questions/{q1['id']}/answer"), json={"answer": "  Friendly "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "answered" and body["answer"] == "Friendly"
    assert body["answered_via"] == "web" and body["answered_by_user_id"] and body["answered_at"]
    assert triggers(app, a1["id"]) == []  # same as the default (case/space-insensitive)
    r = client.post(api(cid, f"/questions/{q2['id']}/answer"), json={"answer": "formal"})
    assert r.json()["status"] == "answered"
    assert "founder_answered" in triggers(app, a2["id"])
    # an answered question is final
    again = client.post(api(cid, f"/questions/{q2['id']}/answer"), json={"answer": "other"})
    assert again.status_code == 409
    assert client.post(api(cid, f"/questions/{q2['id']}/cancel")).status_code == 409
    with app.state.session_factory() as db:
        [event] = [
            e
            for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == q2["id"]))
            if e.action == "question.answered"
        ]
        assert event.data["via"] == "web" and event.data["differs_from_default"] is True


def test_answer_validation_channels_and_secrets(app, client, world):
    cid, _, sess = world
    qid = ask(sess).json()["id"]
    url = api(cid, f"/questions/{qid}/answer")
    assert client.post(url, json={"answer": ""}).status_code == 422
    assert client.post(url, json={"answer": "x" * 501}).status_code == 422
    leak = client.post(
        url, json={"answer": "use sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"}
    )
    assert leak.status_code == 422
    ctx = owner_ctx(client, cid)
    with app.state.session_factory() as db, pytest.raises(ValidationFailed):
        questions.answer(db, ctx, qid, "fine", via="carrier-pigeon")
    with app.state.session_factory() as db:  # M19 answers through this very function
        done = questions.answer(db, ctx, qid, "formal", via="telegram")
        assert done.answered_via == "telegram" and done.status == "answered"


def test_the_list_filters_by_status_and_paginates(client, world):
    cid, worker, sess = world
    q1 = ask(sess).json()
    other = new_agent(client, cid, name="B")
    q2 = ask(start_run(client.app, client, cid, other["id"])).json()
    client.post(api(cid, f"/questions/{q2['id']}/cancel"))
    open_ = client.get(api(cid, "/questions"), params={"status": "open"}).json()
    assert [i["id"] for i in open_["items"]] == [q1["id"]] and open_["next_cursor"] is None
    cancelled = client.get(api(cid, "/questions"), params={"status": "cancelled"}).json()
    assert [i["id"] for i in cancelled["items"]] == [q2["id"]]
    page = client.get(api(cid, "/questions"), params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert client.get(api(cid, "/questions"), params={"status": "bogus"}).status_code == 422


# ------------------------------------------------------------------ who may answer


def test_agents_cannot_answer_or_cancel_and_viewers_and_members_cannot_either(
    app, client, client_as, make_user, add_member, world
):
    cid, worker, sess = world
    qid = ask(sess).json()["id"]
    url = api(cid, f"/questions/{qid}")
    # the asking agent (its run token) and a manager-like agent
    assert (
        client.post(url + "/answer", json={"answer": "mine"}, headers=sess.headers).status_code
        == 403
    )
    assert client.post(url + "/cancel", headers=sess.headers).status_code == 403
    assert client.get(api(cid, "/questions"), headers=sess.headers).status_code == 403
    for role in ("viewer", "member"):
        user = make_user(f"{role}@example.com")
        add_member(cid, user.id, role)
        c = client_as(human(user))
        assert c.post(url + "/answer", json={"answer": "x"}).status_code == 403
        assert c.post(url + "/cancel").status_code == 403
        assert c.get(api(cid, "/questions")).status_code == 403
    admin = make_user("admin@example.com")
    add_member(cid, admin.id, "admin")
    assert client_as(human(admin)).post(url + "/answer", json={"answer": "ok"}).status_code == 200
    assert qrow(app, qid).status == "answered"
    # an agent cannot ask through the service with a human principal either
    ctx = owner_ctx(client, cid)
    from app.core.errors import Forbidden
    from app.schemas.questions import QuestionAsk

    with app.state.session_factory() as db, pytest.raises(Forbidden):
        questions.ask(db, ctx, None, QuestionAsk(question="q", default_answer="d"))


def test_questions_are_tenant_scoped(app, client, client_as, make_user, world):
    cid, worker, sess = world
    qid = ask(sess).json()["id"]
    other = client.post("/api/companies", json={"name": "Other Co", "mission": "x"}).json()
    # the same user in another company sees nothing and gets 404 on the foreign question
    assert client.get(api(other["id"], "/questions")).json()["items"] == []
    assert (
        client.post(api(other["id"], f"/questions/{qid}/answer"), json={"answer": "x"}).status_code
        == 404
    )
    assert client.post(api(other["id"], f"/questions/{qid}/cancel")).status_code == 404
    # a stranger who is a member of nothing: the company itself is a 404
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(cid, "/questions")).status_code == 404
    assert (
        stranger.post(api(cid, f"/questions/{qid}/answer"), json={"answer": "x"}).status_code == 404
    )
    assert qrow(app, qid).status == "open"


# ------------------------------------------------------------------ blocking questions


def test_a_blocking_question_needs_a_task_and_parks_the_task_until_answered(app, client, world):
    cid, worker, sess = world
    r = ask(sess, blocking=True, default_answer="friendly", deadline_hours=2)
    assert r.status_code == 201 and r.json()["blocking"] is True
    qid = r.json()["id"]
    # the run ends (the tool told the agent to stop): the task is parked, not completed
    done = sess.post(f"/tasks/{sess.task_id}/complete", json={"result_summary": "stopping to ask"})
    assert done.status_code == 200, done.text
    parked = task_row(app, sess.task_id)
    assert parked.blocked_reason == "waiting_for_founder"
    assert parked.status == "backlog" and parked.result_summary is None
    assert parked.attempt_count == 0  # the attempt did not fail
    # the answer returns it to the queue; no separate wake: the next run gets the answer
    r = client.post(api(cid, f"/questions/{qid}/answer"), json={"answer": "formal"})
    assert r.status_code == 200
    back = task_row(app, sess.task_id)
    assert back.status == "ready" and back.blocked_reason is None
    assert triggers(app, worker["id"]) == []
    with app.state.session_factory() as db:
        text = attempt_memory.carry_over_text(db, db.get(Task, sess.task_id))
    assert "The founder answered: formal" in text and "Which tone?" in text


def test_a_blocking_question_without_a_task_is_refused(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid, name="Taskless")
    sess = start_run(app, client, cid, agent["id"])
    # a run row without a task cannot exist; the service guards the same rule for callers
    with app.state.session_factory() as db:
        from app.schemas.questions import QuestionAsk

        ctx = CompanyContext(cid, Principal(PrincipalType.AGENT, agent["id"], "A", cid), Role.AGENT)
        with pytest.raises(ValidationFailed, match="needs a task"):
            questions.ask(
                db, ctx, None, QuestionAsk(question="q", default_answer="d", blocking=True)
            )
    assert sess.task_id  # (the HTTP route always has one)


def test_the_deadline_applies_the_default_and_releases_a_parked_task(app, client, world):
    cid, worker, sess = world
    qid = ask(sess, blocking=True, deadline_hours=1).json()["id"]
    sess.post(f"/tasks/{sess.task_id}/complete", json={"result_summary": "asked"})
    assert task_row(app, sess.task_id).blocked_reason == "waiting_for_founder"
    with app.state.session_factory() as db:
        assert questions.sweep_deadlines(db, utcnow() + timedelta(minutes=30)) == 0  # too early
    with app.state.session_factory() as db:
        assert questions.sweep_deadlines(db, utcnow() + timedelta(hours=2)) == 1
    q = qrow(app, qid)
    assert q.status == "defaulted" and q.answer == "friendly" and q.answered_at
    assert q.answered_by_user_id is None and q.answered_via is None
    t = task_row(app, sess.task_id)
    assert t.status == "ready" and t.blocked_reason is None
    with app.state.session_factory() as db:
        text = attempt_memory.carry_over_text(db, db.get(Task, sess.task_id))
        assert "No answer from the founder; use your default: friendly" in text
        assert "question.defaulted" in [
            e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.target_id == qid))
        ]
    assert triggers(app, worker["id"]) == []  # the default stands: nothing to adjust


def test_a_non_blocking_default_does_not_touch_the_task(app, client, world):
    cid, worker, sess = world
    qid = ask(sess).json()["id"]
    with app.state.session_factory() as db:
        assert questions.sweep_deadlines(db, utcnow() + timedelta(hours=5)) == 1
    assert qrow(app, qid).status == "defaulted"
    assert task_row(app, sess.task_id).status == "in_progress"


def test_cancelling_releases_a_parked_task_and_the_default_stands(app, client, world):
    cid, worker, sess = world
    qid = ask(sess, blocking=True).json()["id"]
    sess.post(f"/tasks/{sess.task_id}/complete", json={"result_summary": "asked"})
    r = client.post(api(cid, f"/questions/{qid}/cancel"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert task_row(app, sess.task_id).status == "ready"


def test_the_sweep_skips_paused_companies(app, client, world):
    cid, _, sess = world
    qid = ask(sess).json()["id"]
    client.post(api(cid, "/pause"))
    with app.state.session_factory() as db:
        assert questions.sweep_deadlines(db, utcnow() + timedelta(days=2)) == 0
    assert qrow(app, qid).status == "open"
