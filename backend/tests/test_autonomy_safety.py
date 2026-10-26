"""Safety nets and shared state of the autonomous company: circuit breaker, alerts, budget
thresholds, memory and lessons, role-scoped state, schedule limits, built-in schedules."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.schedule import Schedule
from tests.autonomy_helpers import pending_wakes, run_world, scripted_world, tasks_of
from tests.helpers import api, new_agent, new_task, set_policy
from tests.test_agent_api import start_run


@pytest.fixture
def w(client, anon_client, scripted):
    return scripted_world(client, scripted, anon_client)


def schedules_of(app, cid, **filters):
    with app.state.session_factory() as db:
        stmt = select(Schedule).where(Schedule.company_id == cid).order_by(Schedule.created_at)
        for k, v in filters.items():
            stmt = stmt.where(getattr(Schedule, k) == v)
        return list(db.scalars(stmt))


# ------------------------------------------------------------------ circuit breaker, alerts


def test_circuit_breaker_pauses_the_company_after_a_failure_spike(
    app, client, w, scheduler, worker, clock
):
    cid = w.cid
    set_policy(
        client, cid, circuit_breaker_failures=3, max_attempts_per_task=10, max_concurrent_runs=1
    )
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={"runtime_config": {"script": {"result": "failed", "error_message": "boom"}}},
    )
    for i in range(2):
        new_task(client, cid, title=f"t{i}", assignee_agent_id=w.specialist["id"])
    run_world(app, scheduler, worker, clock, rounds=10)
    company = client.get(api(cid)).json()
    assert company["status"] == "paused"
    alerts = client.get(api(cid, "/alerts"), params={"status": "open"}).json()["items"]
    assert [(a["kind"], a["severity"]) for a in alerts] == [("circuit_breaker", "critical")]
    with app.state.session_factory() as db:
        assert "company.auto_paused" in [e.action for e in db.scalars(select(AuditEvent))]
    before = len(tasks_of(app, cid))
    scheduler.tick()
    assert worker.drain() == 0  # nothing dispatches while paused
    assert len(tasks_of(app, cid)) == before
    # a human resumes: the alert resolves itself
    client.post(api(cid, "/resume"))
    assert client.get(api(cid, "/alerts"), params={"status": "open"}).json()["items"] == []
    resolved = client.get(api(cid, "/alerts"), params={"status": "resolved"}).json()["items"]
    assert resolved[0]["kind"] == "circuit_breaker" and resolved[0]["resolved_by_type"] == "human"


def test_circuit_breaker_can_be_disabled(app, client, w, scheduler, worker, clock):
    cid = w.cid
    set_policy(client, cid, circuit_breaker_failures=0, max_attempts_per_task=10)
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={"runtime_config": {"script": {"result": "failed"}}},
    )
    new_task(client, cid, title="t", assignee_agent_id=w.specialist["id"])
    run_world(app, scheduler, worker, clock, rounds=6)
    assert client.get(api(cid)).json()["status"] == "active"


def test_alert_api_permissions_and_tenancy(app, client, w, make_user, add_member, client_as):
    from app.services import alerts as alert_service
    from tests.conftest import human

    cid = w.cid
    with app.state.session_factory() as db:
        a = alert_service.raise_alert(
            db, cid, kind="test", severity="warning", title="Look", dedupe_key="k"
        )
        again = alert_service.raise_alert(db, cid, kind="test", title="Look", dedupe_key="k")
        db.commit()
        alert_id = a.id
    assert again is None  # one open alert per dedupe key
    got = client.get(api(cid, f"/alerts/{alert_id}")).json()
    assert got["status"] == "open" and got["title"] == "Look" and got["resolved_at"] is None
    viewer = make_user("v@example.com")
    add_member(cid, viewer.id, "viewer")
    v = client_as(human(viewer))
    assert v.get(api(cid, "/alerts")).status_code == 200
    assert v.post(api(cid, f"/alerts/{alert_id}/resolve")).status_code == 403
    stranger = client_as(human(make_user("s@example.com")))
    assert stranger.get(api(cid, f"/alerts/{alert_id}")).status_code == 404
    sess = start_run(app, client, cid, w.founder["id"], task_title="x", kind="planning")
    assert (
        client.post(api(cid, f"/alerts/{alert_id}/resolve"), headers=sess.headers).status_code
        == 403
    )
    ok = client.post(api(cid, f"/alerts/{alert_id}/resolve"))
    assert (
        ok.status_code == 200
        and ok.json()["status"] == "resolved"
        and ok.json()["resolved_by_type"] == "human"
    )
    assert (
        client.post(api(cid, f"/alerts/{alert_id}/resolve")).json()["status"] == "resolved"
    )  # idempotent
    with app.state.session_factory() as db:
        a2 = alert_service.raise_alert(db, cid, kind="test", title="Look", dedupe_key="k")
        db.commit()
    assert a2 is not None  # a resolved alert may fire again


def test_budget_threshold_alerts_use_measured_spend(app, client, w, scheduler, worker, clock):
    cid = w.cid
    set_policy(client, cid, monthly_budget_micros=1_000_000)
    usage = {"cost_micros": 850_000, "cost_kind": "verified", "input_tokens": 1, "output_tokens": 1}
    client.patch(
        api(cid, f"/agents/{w.specialist['id']}"),
        json={"runtime_config": {"script": {"usage": usage}}},
    )
    new_task(client, cid, title="costly", assignee_agent_id=w.specialist["id"])
    run_world(app, scheduler, worker, clock, rounds=4)
    kinds = [(a["kind"], a["severity"]) for a in client.get(api(cid, "/alerts")).json()["items"]]
    assert ("budget_threshold", "warning") in kinds and (
        "budget_threshold",
        "critical",
    ) not in kinds
    new_task(client, cid, title="costly 2", assignee_agent_id=w.specialist["id"])
    run_world(app, scheduler, worker, clock, rounds=4)
    kinds = [(a["kind"], a["severity"]) for a in client.get(api(cid, "/alerts")).json()["items"]]
    assert ("budget_threshold", "critical") in kinds


# ------------------------------------------------------------------ memory and lessons


def test_memory_owner_api_verification_and_filters(
    app, client, w, make_user, add_member, client_as
):
    from tests.conftest import human

    cid = w.cid
    made = client.post(
        api(cid, "/memory"),
        json={"kind": "fact", "title": "ICP", "body": "Freelancers", "tags": ["market"]},
    )
    assert made.status_code == 201 and made.json()["status"] == "verified"
    assert made.json()["verified_by_type"] == "human" and made.json()["source"]["type"] == "human"
    assert client.post(api(cid, "/memory"), json={"kind": "bogus", "body": "x"}).status_code == 422
    for body in (
        "token=abcdefghij123456",
        "my key is sk-abcdefghijklmnopqrstuvwxyz1234",
        "-----BEGIN RSA PRIVATE KEY-----",
    ):
        r = client.post(api(cid, "/memory"), json={"kind": "fact", "body": body})
        assert r.status_code == 422, body
    dup = client.post(api(cid, "/memory"), json={"kind": "fact", "body": "  freelancers "})
    assert (
        dup.status_code == 201 and dup.json()["id"] == made.json()["id"]
    )  # identical entries are reused
    sess = start_run(app, client, cid, w.specialist["id"], task_title="learn")
    prop = sess.post(
        "/memory",
        json={"kind": "lesson", "title": "Be brief", "body": "Short copy converts better."},
    )
    assert prop.status_code == 201 and prop.json()["status"] == "proposed"
    assert prop.json()["proposed_by_type"] == "agent" and prop.json()["source"]["type"] == "run"
    assert (
        sess.post(
            "/memory", json={"kind": "lesson", "body": "key: sk-abcdefghijklmnopqrstuvwxyz1234"}
        ).status_code
        == 422
    )
    listing = client.get(api(cid, "/memory"), params={"status": "proposed"}).json()["items"]
    assert [m["id"] for m in listing] == [prop.json()["id"]]
    assert [
        m["id"]
        for m in client.get(api(cid, "/memory"), params={"kind": "fact", "q": "freel"}).json()[
            "items"
        ]
    ] == [made.json()["id"]]
    # not visible to anyone until verified
    assert (
        start_run(app, client, cid, w.lead["id"], task_title="x")
        .get("/context")
        .json()["memory"][0]["title"]
        == "ICP"
    )
    ctx = start_run(app, client, cid, w.lead["id"], task_title="y").get("/context").json()["memory"]
    assert "Be brief" not in {m["title"] for m in ctx}
    v = client.post(api(cid, f"/memory/{prop.json()['id']}/verify"))
    assert v.status_code == 200 and v.json()["status"] == "verified" and v.json()["verified_at"]
    ctx = start_run(app, client, cid, w.lead["id"], task_title="z").get("/context").json()["memory"]
    assert "Be brief" in {m["title"] for m in ctx}
    rej = client.post(api(cid, f"/memory/{prop.json()['id']}/reject"), json={"reason": "outdated"})
    assert rej.json()["status"] == "rejected" and rej.json()["rejection_reason"] == "outdated"
    ctx = (
        start_run(app, client, cid, w.lead["id"], task_title="z2").get("/context").json()["memory"]
    )
    assert "Be brief" not in {m["title"] for m in ctx}
    viewer = make_user("v2@example.com")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    assert vc.get(api(cid, "/memory")).status_code == 200
    assert vc.post(api(cid, "/memory"), json={"kind": "fact", "body": "z"}).status_code == 403
    assert vc.post(api(cid, f"/memory/{made.json()['id']}/reject")).status_code == 403


def test_maker_checker_for_memory_and_scoped_lessons(app, client, w):
    cid = w.cid
    lead = start_run(app, client, cid, w.lead["id"], task_title="plan", kind="planning")
    founder = start_run(app, client, cid, w.founder["id"], task_title="plan", kind="planning")
    sam = start_run(app, client, cid, w.specialist["id"], task_title="work")
    lesson = lead.post(
        "/memory",
        json={"kind": "lesson", "body": "Check links before sending.", "applies_to": ["marketing"]},
    ).json()
    general = lead.post(
        "/memory", json={"kind": "lesson", "body": "Write acceptance criteria."}
    ).json()
    # specialists and leads cannot verify; the Co-founder verifies others' proposals
    assert sam.post(f"/memory/{lesson['id']}/verify").status_code == 403
    assert lead.post(f"/memory/{lesson['id']}/verify").status_code == 403
    assert founder.post(f"/memory/{lesson['id']}/verify").status_code == 200
    assert founder.post(f"/memory/{general['id']}/verify").status_code == 200
    # ...but never its own: that waits for a human
    own = founder.post(
        "/memory", json={"kind": "decision", "body": "We target freelancers."}
    ).json()
    assert founder.post(f"/memory/{own['id']}/verify").status_code == 403
    assert client.post(api(cid, f"/memory/{own['id']}/verify")).status_code == 200
    # the marketing lesson reaches a marketing agent, not a generic one
    mk = new_agent(client, cid, name="Mia", runtime="scripted")
    with app.state.session_factory() as db:
        from app.models.agent import Agent

        db.get(Agent, mk["id"]).role_family = "marketing"
        db.commit()
    mia = start_run(app, client, cid, mk["id"], task_title="copy")
    bodies = {m["body"] for m in mia.get("/context").json()["memory"]}
    assert "Check links before sending." in bodies and "Write acceptance criteria." in bodies
    sam_bodies = {m["body"] for m in sam.get("/context").json()["memory"]}
    assert (
        "Check links before sending." not in sam_bodies
        and "Write acceptance criteria." in sam_bodies
    )


def test_memory_proposals_per_agent_are_bounded(app, client, w):
    sam = start_run(app, client, w.cid, w.specialist["id"], task_title="spam")
    for i in range(20):
        assert sam.post("/memory", json={"kind": "fact", "body": f"fact {i}"}).status_code == 201
    r = sam.post("/memory", json={"kind": "fact", "body": "fact 21"})
    assert r.status_code == 409 and r.json()["error"]["details"]["limit"] == "memory_proposals"


# ------------------------------------------------------------------ state, role scoped


def test_state_is_sliced_by_role_and_tenant(app, client, w):
    cid = w.cid
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    new_task(client, other, title="secret other-company task")
    mine = client.post(
        api(cid, "/projects"),
        json={"name": "Mine", "lead_agent_id": w.lead["id"], "status": "active"},
    ).json()
    other_project = client.post(
        api(cid, "/projects"), json={"name": "Not mine", "status": "active"}
    ).json()
    t1 = new_task(
        client, cid, title="in mine", project_id=mine["id"], assignee_agent_id=w.specialist["id"]
    )
    new_task(
        client,
        cid,
        title="elsewhere",
        project_id=other_project["id"],
        assignee_agent_id=w.founder["id"],
    )
    client.post(api(cid, "/goals"), json={"title": "G", "status": "active"})
    client.post(api(cid, "/notes"), json={"body": "a thought"})

    founder = (
        start_run(app, client, cid, w.founder["id"], task_title="plan", kind="planning")
        .get("/state")
        .json()
    )
    assert founder["role"] == "founder" and founder["notes"]["unprocessed"] == 1
    assert {p["name"] for p in founder["projects"]} == {"Mine", "Not mine"}
    assert founder["budget"]["max_agent_monthly_budget_micros"] == 25_000_000
    assert {a["name"] for a in founder["agents"]} == {"Harper", "Lena", "Sam"}
    assert [g["title"] for g in founder["goals"]] == ["G"]
    assert {s["name"] for s in founder["schedules"]} >= {"heartbeat", "daily-standup"}

    lead = (
        start_run(app, client, cid, w.lead["id"], task_title="plan", kind="planning")
        .get("/state")
        .json()
    )
    assert lead["role"] == "lead" and [p["name"] for p in lead["projects"]] == ["Mine"]
    titles = {t["title"] for t in lead["tasks"]["open"]}
    assert "in mine" in titles and "elsewhere" not in titles
    assert lead["notes"] is None and lead["budget"] is None and lead["goals"] == []
    assert {a["name"] for a in lead["agents"]} == {"Sam", "Atlas"}

    sam = (
        start_run(app, client, cid, w.specialist["id"], task_title="own", project_id=mine["id"])
        .get("/state")
        .json()
    )
    assert sam["role"] == "specialist" and sam["notes"] is None and sam["budget"] is None
    assert {t["title"] for t in sam["tasks"]["open"]} <= {"own", "in mine"}
    blob = str(founder) + str(lead) + str(sam)
    assert "secret other-company task" not in blob
    assert t1["id"]
    hr = (
        start_run(app, client, cid, w.hr["id"], task_title="hire", kind="hiring")
        .get("/state")
        .json()
    )
    assert hr["role"] == "hr" and hr["headcount"]["live"] == 4


def test_state_lists_failures_blockers_and_unassigned(app, client, w):
    cid = w.cid
    failed = new_task(client, cid, title="broke", assignee_agent_id=w.specialist["id"])
    from app.models.work import Task

    with app.state.session_factory() as db:
        row = db.get(Task, failed["id"])
        row.status = "failed"
        row.error = {"code": "boom", "message": "it broke"}
        db.commit()
    new_task(client, cid, title="nobody")
    st = (
        start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
        .get("/state")
        .json()
    )
    assert [t["title"] for t in st["failures_24h"]] == ["broke"] and "it broke" in st[
        "failures_24h"
    ][0]["error"]
    assert [t["title"] for t in st["tasks"]["unassigned"]] == ["nobody"]


# ------------------------------------------------------------------ schedules


def test_builtin_schedules_exist_from_the_start(app, client, w):
    cid = w.cid
    by_name = {s.name: s for s in schedules_of(app, cid)}
    assert set(by_name) == {
        "heartbeat",
        "daily-standup",
        "weekly-strategy-review",
        "daily-budget-review",
        "daily-reflection",
        "skills-review",
    }
    hb = by_name["heartbeat"]
    assert (
        hb.kind == "heartbeat"
        and hb.interval_seconds == 1800
        and hb.agent_id == w.founder["id"]
        and hb.enabled
    )
    assert (
        by_name["daily-standup"].cron == "0 9 * * *"
        and by_name["weekly-strategy-review"].cron == "30 9 * * 1"
    )
    assert by_name["daily-budget-review"].cron == "0 18 * * *"
    assert (
        by_name["daily-reflection"].cron == "0 20 * * *"
        and by_name["daily-reflection"].agent_id == w.founder["id"]
        and "company_post_reflection" in by_name["daily-reflection"].task_template["description"]
    )
    assert (
        by_name["skills-review"].agent_id == w.hr["id"]
        and by_name["skills-review"].cron == "0 10 1 * *"
    )
    assert all(s.next_run_at is not None for s in by_name.values())
    # policy.heartbeat_interval_seconds drives the built-in heartbeat
    set_policy(client, cid, heartbeat_interval_seconds=3600)
    assert schedules_of(app, cid, name="heartbeat")[0].interval_seconds == 3600


def test_idle_schedules_skip_and_busy_ones_fire(app, client, w, scheduler, worker, scripted, clock):
    cid = w.cid
    # Only the interval heartbeat is under test: switch off the wall-clock cron routines
    # (standup 09:00, reflection 20:00, ...) so the time of day the suite runs can't fire them.
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            if s.name != "heartbeat":
                s.enabled = False
        db.commit()
    scripted.handlers["Atlas"] = lambda call: call.api.ok(
        "POST", f"/tasks/{call.request.task.id}/complete", json={"result_summary": "ok"}
    )
    clock.advance(1900)
    report = scheduler.tick()
    assert report.fired_task_ids == []  # due, but nothing to review: no task, no model run
    with app.state.session_factory() as db:
        skipped = [
            e.summary
            for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "schedule.fired"))
        ]
    assert any("nothing to review" in s for s in skipped)
    client.post(api(cid, "/goals"), json={"title": "Work exists", "status": "active"})
    clock.advance(1900)
    report = scheduler.tick()
    assert len(report.fired_task_ids) == 1
    [t] = [
        x
        for x in tasks_of(app, cid, kind="planning")
        if (x.idempotency_key or "").startswith("schedule:")
    ]
    assert t.assignee_agent_id == w.founder["id"] and "company_get_state" in t.description


def test_planning_schedules_respect_the_hourly_planning_limit(app, client, w, scheduler, clock):
    cid = w.cid
    set_policy(client, cid, max_planning_runs_per_hour=1)
    # Only the heartbeat may fire here: the wall-clock routines (standup 09:00, reflection 20:00, ...)
    # would otherwise fire whenever the suite happens to run near their time.
    with app.state.session_factory() as db:
        for s in db.scalars(select(Schedule).where(Schedule.company_id == cid)):
            if s.name != "heartbeat":
                s.enabled = False
        db.commit()
    client.post(api(cid, "/goals"), json={"title": "Work exists", "status": "active"})
    client.post(api(cid, "/cofounder/wake"))  # uses the one planning run of this hour
    clock.advance(1900)
    report = scheduler.tick()
    assert report.fired_task_ids == []
    with app.state.session_factory() as db:
        assert any(
            "rate limit" in e.summary
            for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "schedule.fired"))
        )


def test_project_health_check_follows_the_lead_and_ends_with_the_project(app, client, w):
    cid = w.cid
    p = client.post(
        api(cid, "/projects"),
        json={"name": "Site", "lead_agent_id": w.lead["id"], "status": "active"},
    ).json()
    [check] = [s for s in schedules_of(app, cid) if s.name.startswith("project-health-check")]
    assert check.name == "project-health-check: Site" and check.agent_id == w.lead["id"]
    assert (
        check.interval_seconds == 6 * 3600
        and check.enabled
        and check.task_template["project_id"] == p["id"]
    )
    other_lead = new_agent(client, cid, name="Leo", kind="lead", runtime="scripted")
    client.patch(api(cid, f"/projects/{p['id']}"), json={"lead_agent_id": other_lead["id"]})
    [check] = [s for s in schedules_of(app, cid) if s.name.startswith("project-health-check")]
    assert check.agent_id == other_lead["id"] and check.enabled
    client.patch(api(cid, f"/projects/{p['id']}"), json={"status": "completed"})
    [check] = [s for s in schedules_of(app, cid) if s.name.startswith("project-health-check")]
    assert check.enabled is False and check.next_run_at is None
    # a project with a lead assigned later also gets one
    q = client.post(api(cid, "/projects"), json={"name": "Later"}).json()
    assert not [s for s in schedules_of(app, cid) if "Later" in s.name]
    client.patch(api(cid, f"/projects/{q['id']}"), json={"lead_agent_id": w.lead["id"]})
    assert [s for s in schedules_of(app, cid) if "Later" in s.name]


def test_ops_agent_gets_failed_run_triage_on_activation(app, client, w):
    cid = w.cid
    ops = new_agent(client, cid, name="Ola", runtime="scripted", role_template="devops-engineer")
    [triage] = schedules_of(app, cid, name="failed-run-triage")
    assert triage.agent_id == ops["id"] and triage.cron == "0 8 * * *"


def test_agents_manage_schedules_only_within_limits(app, client, w):
    cid = w.cid
    founder = start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
    lead = start_run(app, client, cid, w.lead["id"], task_title="p", kind="planning")
    sam = start_run(app, client, cid, w.specialist["id"], task_title="w")
    body = {
        "name": "Weekly digest",
        "agent_id": w.specialist["id"],
        "interval_seconds": 3600,
        "task_template": {"title": "Digest"},
    }
    ok = founder.post("/schedules", json=body)
    assert ok.status_code == 201 and ok.json()["kind"] == "routine" and ok.json()["enabled"]
    # too frequent (interval or cron), heartbeat kind, unknown target
    r = founder.post("/schedules", json={**body, "interval_seconds": 300})
    assert (
        r.status_code == 409
        and r.json()["error"]["details"]["limit"] == "min_schedule_interval_seconds"
    )
    assert (
        founder.post(
            "/schedules", json={"name": "c", "agent_id": w.specialist["id"], "cron": "* * * * *"}
        ).status_code
        == 409
    )
    assert (
        founder.post(
            "/schedules", json={"name": "c", "agent_id": w.specialist["id"], "cron": "0 * * * *"}
        ).status_code
        == 201
    )
    assert founder.post("/schedules", json={**body, "kind": "heartbeat"}).status_code == 403
    # a lead schedules for itself and its reports only; a specialist not at all
    assert lead.post("/schedules", json=body).status_code == 201
    assert lead.post("/schedules", json={**body, "agent_id": w.founder["id"]}).status_code == 403
    assert sam.post("/schedules", json=body).status_code == 403
    # pause / resume by the creator's authority; heartbeats stay human
    sid = ok.json()["id"]
    assert founder.patch(f"/schedules/{sid}", json={"enabled": False}).json()["enabled"] is False
    hb = schedules_of(app, cid, name="heartbeat")[0]
    assert founder.patch(f"/schedules/{hb.id}", json={"enabled": False}).status_code == 403
    assert lead.patch(f"/schedules/{hb.id}", json={"enabled": False}).status_code == 403
    assert sam.patch(f"/schedules/{sid}", json={"enabled": True}).status_code == 403
    # humans keep full control
    assert client.patch(api(cid, f"/schedules/{hb.id}"), json={"enabled": False}).status_code == 200


def test_a_company_caps_enabled_schedules_for_agents(app, client, w):
    cid = w.cid
    founder = start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
    made = 0
    while True:
        r = founder.post(
            "/schedules",
            json={"name": f"s{made}", "agent_id": w.specialist["id"], "interval_seconds": 3600},
        )
        if r.status_code != 201:
            break
        made += 1
        assert made < 100
    assert r.status_code == 409 and r.json()["error"]["details"]["limit"] == "max_schedules"


# ------------------------------------------------------------------ goals / projects by agents


def test_goal_and_project_management_by_the_cofounder_and_leads(app, client, w):
    cid = w.cid
    founder = start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
    lead = start_run(app, client, cid, w.lead["id"], task_title="p", kind="planning")
    sam = start_run(app, client, cid, w.specialist["id"], task_title="w")
    goal = founder.post(
        "/goals", json={"title": "Reach 100 users", "description": "KR: 100 signups"}
    )
    assert goal.status_code == 201 and goal.json()["created_by_type"] == "agent"
    assert lead.post("/goals", json={"title": "mine"}).status_code == 403
    assert sam.post("/goals", json={"title": "mine"}).status_code == 403
    proj = founder.post(
        "/projects",
        json={"name": "Launch", "goal_id": goal.json()["id"], "lead_agent_id": w.lead["id"]},
    )
    assert proj.status_code == 201
    pid = proj.json()["id"]
    assert pending_wakes(app) >= 1  # the lead was woken for its new project
    # the lead may update description/status of its project, nothing else
    assert lead.patch(f"/projects/{pid}", json={"description": "scope"}).status_code == 200
    r = lead.patch(f"/projects/{pid}", json={"lead_agent_id": w.specialist["id"]})
    assert r.status_code == 403
    assert lead.patch(f"/projects/{pid}", json={"budget_micros": 1}).status_code == 403
    other = founder.post("/projects", json={"name": "Other"}).json()
    assert lead.patch(f"/projects/{other['id']}", json={"description": "x"}).status_code == 403
    assert sam.patch(f"/projects/{pid}", json={"status": "completed"}).status_code == 403
    # agents never hand out unlimited project money
    big = founder.post("/projects", json={"name": "Rich", "budget_micros": 10**12})
    assert (
        big.status_code == 409
        and big.json()["error"]["details"]["limit"] == "max_agent_monthly_budget_micros"
    )
    assert (
        founder.post("/projects", json={"name": "Fine", "budget_micros": 1_000_000}).status_code
        == 201
    )
    assert (
        client.post(
            api(cid, "/projects"), json={"name": "Human", "budget_micros": 10**12}
        ).status_code
        == 201
    )


# ------------------------------------------------------------------ contract


def test_openapi_exposes_the_schema_names_the_web_client_expects(client):
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]
    for name in (
        "FounderNote",
        "NoteDetail",
        "NoteReply",
        "NoteCreate",
        "NoteReplyCreate",
        "NoteUpdate",
        "Alert",
        "MemoryEntry",
        "MemoryCreate",
        "CofounderSummary",
    ):
        assert name in schemas, name
    assert {"last_reply", "reply_count", "triage", "pinned", "attachments"} <= set(
        schemas["FounderNote"]["properties"]
    )
    assert "replies" in schemas["NoteDetail"]["properties"]
    # fields the web client reads as required must be required in the schema (no silent optionals)
    assert "replies" in schemas["NoteDetail"]["required"]
    assert {"actions", "confidence", "category", "summary"} <= set(
        schemas["NoteTriage"]["required"]
    )
    assert "id" in schemas["MemorySource"]["required"]
    assert set(schemas["NoteUpdate"]["properties"]) == {"pinned", "status"}
    assert {
        "goals",
        "plan",
        "decisions",
        "blockers",
        "alerts",
        "last_run",
        "wake_pending",
        "unprocessed_notes",
        "agent_id",
    } <= set(schemas["CofounderSummary"]["properties"])


def test_cofounder_summary_reflects_the_company(app, client, w):
    cid = w.cid
    empty = client.get(api(cid, "/cofounder/summary")).json()
    assert (
        empty["agent_id"] == w.founder["id"] and empty["goals"] == [] and empty["last_run"] is None
    )
    goal = client.post(api(cid, "/goals"), json={"title": "G", "status": "active"}).json()
    p = client.post(
        api(cid, "/projects"),
        json={
            "name": "P",
            "goal_id": goal["id"],
            "lead_agent_id": w.lead["id"],
            "status": "active",
        },
    ).json()
    done = new_task(
        client, cid, title="done one", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    new_task(
        client, cid, title="open one", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    blocked = new_task(
        client, cid, title="stuck", project_id=p["id"], assignee_agent_id=w.specialist["id"]
    )
    client.post(api(cid, f"/tasks/{blocked['id']}/block"), json={"reason": "needs input"})
    from app.models.work import Task

    with app.state.session_factory() as db:
        db.get(Task, done["id"]).status = "done"
        db.commit()
    start_run(app, client, cid, w.founder["id"], task_title="r").post(
        "/decisions", json={"title": "Go"}
    )
    s = client.get(api(cid, "/cofounder/summary")).json()
    assert [g["title"] for g in s["goals"]] == ["G"]
    [proj] = s["plan"]["projects"]
    assert (proj["tasks_total"], proj["tasks_done"]) == (3, 1) and {
        t["title"] for t in proj["open_tasks"]
    } == {"open one", "stuck"}
    assert [b["title"] for b in s["blockers"]] == ["stuck"] and s["blockers"][0]["reason"]
    assert [d["title"] for d in s["decisions"]] == ["Go"]
    assert s["last_run"]["task_id"]


def test_wake_triggers_for_goal_escalation_hire_and_approval(app, client, w):
    cid = w.cid
    from sqlalchemy import select as sel

    from app.models.autonomy import WakeRequest

    def reasons():
        with app.state.session_factory() as db:
            return [r["trigger"] for req in db.scalars(sel(WakeRequest)) for r in req.reasons]

    assert reasons() == []
    client.post(api(cid, "/goals"), json={"title": "New direction"})
    assert reasons() == ["goal"]
    # an agent's own goal change does not wake itself
    founder = start_run(app, client, cid, w.founder["id"], task_title="p", kind="planning")
    founder.post("/goals", json={"title": "by founder"})
    assert reasons() == ["goal"]
    ret = new_agent(client, cid, name="Newbie", runtime="scripted", manager_agent_id=w.lead["id"])
    assert ret["id"]
    with app.state.session_factory() as db:
        agents_woken = {r.agent_id for r in db.scalars(sel(WakeRequest))}
    assert agents_woken == {w.founder["id"], w.lead["id"]}  # the new hire's manager hears about it
    # approval decided -> the requester is woken
    sess = start_run(app, client, cid, w.lead["id"], task_title="ask", kind="work")
    appr = sess.post("/approvals", json={"kind": "other", "title": "Ok to proceed?"}).json()
    founder.post(f"/approvals/{appr['id']}/decide", json={"decision": "approve"})
    assert "approval" in reasons()
