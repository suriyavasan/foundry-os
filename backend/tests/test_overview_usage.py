from __future__ import annotations

from datetime import timedelta

from tests.helpers import api, new_agent, new_task, set_policy, settle
from tests.test_agent_api import start_run


def _spend(client, cid, scheduler, worker, *, name, cost, kind, project=None, model="m1"):
    usage = {"cost_micros": cost, "cost_kind": kind, "input_tokens": 100, "output_tokens": 50}
    agent = new_agent(
        client, cid, name=name, model_provider="p", model_name=model, script={"usage": usage}
    )
    task = new_task(
        client, cid, assignee_agent_id=agent["id"], **({"project_id": project} if project else {})
    )
    settle(scheduler, worker)
    return agent, task


def test_overview_aggregates_reflect_real_work(client, company, scheduler, worker, app):
    cid = company["id"]
    set_policy(client, cid, daily_budget_micros=10_000_000, monthly_budget_micros=100_000_000)
    _spend(client, cid, scheduler, worker, name="Verified", cost=300_000, kind="verified")
    _spend(client, cid, scheduler, worker, name="Estimated", cost=120_000, kind="estimated")
    waiting = new_agent(client, cid, name="NoRuntime", runtime="unassigned")
    new_task(client, cid, title="stuck", assignee_agent_id=waiting["id"])
    new_task(client, cid, title="unassigned backlog")
    runner = new_agent(client, cid, name="Held", script={"block_until_cancel": True})
    new_task(client, cid, title="in flight", assignee_agent_id=runner["id"])
    scheduler.tick()  # queues a run: its reservation is "held", not spent

    ov = client.get(api(cid, "/overview")).json()
    tasks = ov["tasks"]
    assert tasks["available"] is True and tasks["total"] == 5 and tasks["open"] == 3
    assert tasks["by_status"]["done"] == 2 and tasks["by_status"]["backlog"] == 1
    assert tasks["by_status"]["ready"] == 1 and tasks["by_status"]["in_progress"] == 1
    assert tasks["waiting_for_runtime"] == 1 and tasks["blocked_by_budget"] == 0
    today = ov["spending"]["today"]
    assert today["spent_micros"] == 420_000
    assert today["by_cost_kind"] == {
        "verified_micros": 300_000,
        "estimated_micros": 120_000,
        "unknown_micros": 0,
    }
    assert today["held_micros"] == 50_000  # the queued run's reservation, kept apart from spend
    assert today["budget_micros"] == 10_000_000
    assert ov["spending"]["month"]["spent_micros"] == 420_000
    assert ov["spending"]["month"]["budget_micros"] == 100_000_000
    assert ov["spending"]["timezone"] == "UTC"
    assert ov["founder"]["live"]["state"] in ("idle", "working")


BUILTIN_SCHEDULES = 5  # Co-founder heartbeat + 3 routines, HR skills review


def test_overview_approvals_artifacts_routines(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    sess = start_run(app, client, cid, agent["id"])
    sess.post("/approvals", json={"kind": "external_purchase", "title": "a"})
    sess.post("/approvals", json={"kind": "hire", "title": "b"})
    decided = sess.post("/approvals", json={"kind": "external_purchase", "title": "c"}).json()
    client.post(api(cid, f"/approvals/{decided['id']}/approve"))
    for i in range(7):
        client.post(api(cid, "/artifacts"), json={"title": f"Doc {i}", "content": str(i)})
    for i, secs in enumerate((7200, 3600, 60)):
        client.post(
            api(cid, "/schedules"),
            json={"name": f"S{i}", "agent_id": agent["id"], "interval_seconds": secs},
        )
    client.post(
        api(cid, "/schedules"),
        json={"name": "off", "agent_id": agent["id"], "interval_seconds": 60, "enabled": False},
    )
    ov = client.get(api(cid, "/overview")).json()
    assert ov["approvals"]["pending_total"] == 2
    assert ov["approvals"]["pending_by_approver"] == {"human": 1, "founder": 1}
    assert ov["artifacts"]["total"] == 7
    assert [a["title"] for a in ov["artifacts"]["latest"]] == [f"Doc {i}" for i in (6, 5, 4, 3, 2)]
    assert (
        ov["routines"]["total"] == 4 + BUILTIN_SCHEDULES
        and ov["routines"]["enabled"] == 3 + BUILTIN_SCHEDULES
    )
    upcoming = ov["routines"]["upcoming"]
    # soonest first, disabled excluded (the built-in schedules fire later than a minute)
    assert [u["name"] for u in upcoming][:1] == ["S2"] and "off" not in {
        u["name"] for u in upcoming
    }
    assert [u["name"] for u in upcoming if u["name"].startswith("S")] == ["S2", "S1", "S0"]
    assert upcoming[0]["agent_name"] == agent["name"] and upcoming[0]["next_run_at"].endswith("Z")


def test_overview_is_tenant_scoped(client, company, scheduler, worker):
    cid = company["id"]
    _spend(client, cid, scheduler, worker, name="Mine", cost=1000, kind="verified")
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    ov = client.get(api(other, "/overview")).json()
    assert ov["tasks"]["total"] == 0 and ov["spending"]["today"]["spent_micros"] == 0
    assert ov["artifacts"]["total"] == 0 and ov["approvals"]["pending_total"] == 0


# ------------------------------------------------------------------ usage endpoint


def test_usage_grouping_and_totals(client, company, scheduler, worker):
    cid = company["id"]
    project = client.post(api(cid, "/projects"), json={"name": "Launch"}).json()
    a1, _ = _spend(
        client,
        cid,
        scheduler,
        worker,
        name="Ada",
        cost=200_000,
        kind="verified",
        project=project["id"],
        model="big",
    )
    _spend(client, cid, scheduler, worker, name="Bob", cost=50_000, kind="estimated", model="small")
    _spend(client, cid, scheduler, worker, name="Cy", cost=70_000, kind="verified", model="big")

    by_agent = client.get(api(cid, "/usage"), params={"group_by": "agent"}).json()
    assert by_agent["group_by"] == "agent"
    assert [(r["label"], r["cost_micros"]) for r in by_agent["items"]] == [
        ("Ada", 200_000), ("Cy", 70_000), ("Bob", 50_000)
    ]  # fmt: skip
    assert by_agent["items"][0]["key"] == a1["id"]
    assert by_agent["items"][0]["by_cost_kind"]["verified_micros"] == 200_000
    assert by_agent["items"][2]["by_cost_kind"] == {
        "verified_micros": 0,
        "estimated_micros": 50_000,
        "unknown_micros": 0,
    }
    totals = by_agent["totals"]
    assert (
        totals["cost_micros"] == 320_000
        and totals["input_tokens"] == 300
        and totals["output_tokens"] == 150
    )
    assert totals["records"] == 3
    assert (
        totals["by_cost_kind"]["verified_micros"] == 270_000
        and totals["by_cost_kind"]["estimated_micros"] == 50_000
    )

    by_model = client.get(api(cid, "/usage"), params={"group_by": "model"}).json()
    assert {r["key"]: r["cost_micros"] for r in by_model["items"]} == {
        "big": 270_000,
        "small": 50_000,
    }
    by_project = client.get(api(cid, "/usage"), params={"group_by": "project"}).json()
    labelled = {r["key"]: (r["label"], r["cost_micros"]) for r in by_project["items"]}
    assert labelled[project["id"]] == ("Launch", 200_000) and labelled[None] == (None, 120_000)
    by_day = client.get(api(cid, "/usage"), params={"group_by": "day"}).json()
    assert len(by_day["items"]) == 1 and by_day["items"][0]["cost_micros"] == 320_000
    assert len(by_day["items"][0]["key"]) == 10
    assert by_agent["from"].endswith("Z") and by_agent["to"].endswith("Z")


def test_usage_date_range_and_validation(client, company, scheduler, worker, clock):
    cid = company["id"]
    _spend(client, cid, scheduler, worker, name="Ada", cost=1000, kind="verified")
    today = clock().date().isoformat()
    tomorrow = (clock().date() + timedelta(days=1)).isoformat()
    inside = client.get(
        api(cid, "/usage"), params={"from": today, "to": today}
    ).json()  # `to` day is inclusive
    assert inside["totals"]["cost_micros"] == 1000
    before = client.get(
        api(cid, "/usage"), params={"from": "2020-01-01", "to": "2020-01-31"}
    ).json()
    assert before["items"] == [] and before["totals"]["cost_micros"] == 0
    next_week = (clock().date() + timedelta(days=7)).isoformat()
    future = client.get(api(cid, "/usage"), params={"from": tomorrow, "to": next_week}).json()
    assert future["totals"]["records"] == 0
    assert client.get(api(cid, "/usage"), params={"from": tomorrow}).status_code == 400  # to=now
    assert client.get(api(cid, "/usage"), params={"from": "garbage"}).status_code == 400
    assert client.get(api(cid, "/usage"), params={"from": tomorrow, "to": today}).status_code == 400
    assert client.get(api(cid, "/usage"), params={"group_by": "color"}).status_code == 422


def test_usage_is_tenant_scoped(client, client_as, make_user, company, scheduler, worker):
    from tests.conftest import human

    cid = company["id"]
    _spend(client, cid, scheduler, worker, name="Ada", cost=1000, kind="verified")
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    assert client.get(api(other, "/usage")).json()["items"] == []
    assert client_as(human(make_user("u@x.test"))).get(api(cid, "/usage")).status_code == 404
