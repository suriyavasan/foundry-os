"""Finance & governance of money (docs/specs/m6 section 6): mid-run budget checkpoints, stack
model routing, threshold alerts, external expenses and the reporting API in the company timezone."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.autonomy import Alert
from app.models.guardrails import BudgetAlertMark
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.run import CompanyEvent, TaskRun
from app.models.work import Task
from app.runtimes.scripted import ScriptedRuntime
from app.services import budget_alerts, ledger
from tests.guardrail_helpers import make_team, run_as
from tests.helpers import api, get_task, new_agent, new_task, set_policy, settle
from tests.m10_helpers import add_connection


class FakeCompanyAgent(ScriptedRuntime):
    """Stands in for the company-agent runtime so the dispatcher routes models for it."""

    id = "company_agent"
    display_name = "Fake company agent"


def usage_event(cost: int, kind: str = "estimated", tokens: int = 10) -> dict:
    return {
        "type": "usage",
        "payload": {
            "input_tokens": tokens,
            "output_tokens": tokens,
            "billing": {"cost_micros": cost, "cost_kind": kind, "local": False},
        },
    }


# ------------------------------------------------------------------ mid-run budget checkpoints


def test_the_reservation_is_extended_when_a_run_outspends_it_inside_the_budgets(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    agent = new_agent(
        client,
        cid,
        script={
            "events": [usage_event(80_000)],
            "usage": {
                "cost_micros": 80_000,
                "cost_kind": "estimated",
                "input_tokens": 10,
                "output_tokens": 10,
            },
        },
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.run_once()
    with app.state.session_factory() as db:
        [res] = db.scalars(select(BudgetReservation))
        assert res.status == "settled" and res.settled_micros == 80_000
        assert res.amount_micros > 50_000  # extended past the default 50 000 reservation
        actions = [a.action for a in db.scalars(select(AuditEvent))]
    assert "budget.reservation_extended" in actions
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_a_run_that_can_no_longer_pay_is_cancelled_and_its_task_waits_for_the_budget(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    set_policy(client, cid, daily_budget_micros=100_000)
    agent = new_agent(
        client,
        cid,
        script={
            "events": [usage_event(120_000)],
            "block_until_cancel": True,
            "block_cap_seconds": 10,
        },
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.run_once()
    [run] = list(reversed(client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"]))
    assert run["status"] == "cancelled" and run["exit_reason"] == "budget_exhausted"
    assert run["cost_micros"] == 120_000 and run["cost_kind"] == "estimated"  # what it measured
    # the task is not failed: it goes back to ready and the dispatcher parks it on the budget
    report = scheduler.tick().dispatch
    assert report.budget_blocked_task_ids == [task["id"]]
    blocked = get_task(client, cid, task["id"])
    assert blocked["status"] == "blocked" and blocked["error"]["code"] == "budget_exceeded"
    # a human raising the budget releases it
    set_policy(client, cid, daily_budget_micros=1_000_000)
    assert get_task(client, cid, task["id"])["status"] in ("backlog", "ready")


# ------------------------------------------------------------------ model routing


@pytest.fixture
def providers(app, client, company):
    cid = company["id"]
    app.state.runtimes.register(FakeCompanyAgent())
    cred = client.post(
        api(cid, "/credentials"), json={"name": "cloud", "value": "sk-cloud-0123456789"}
    )
    cloud = client.post(
        api(cid, "/model-providers"),
        json={
            "name": "Cloud",
            "kind": "openai",
            "credential_id": cred.json()["id"],
            "default_model": "big-model",
            "input_price_per_mtok_micros": 1_000_000,
            "output_price_per_mtok_micros": 5_000_000,
        },
    ).json()
    local = client.post(
        api(cid, "/model-providers"),
        json={"name": "Local", "kind": "ollama", "default_model": "qwen2.5:7b"},
    ).json()
    return cloud, local


def routed(client, cid, agent, title="t"):
    task = new_task(client, cid, title=title, assignee_agent_id=agent["id"])
    return task, lambda: list(
        reversed(client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"])
    )


def cloud_agent(client, cid, cloud, name="Cora"):
    return new_agent(
        client,
        cid,
        name=name,
        runtime="company_agent",
        model_provider=cloud["id"],
        model_name="big-model",
    )


def test_a_stack_run_uses_the_connections_model_and_a_free_one_reserves_nothing(
    app, client, company, providers, scheduler, worker
):
    cid = company["id"]
    cloud, local = providers
    add_connection(app, client, cid, "ollama", model_provider_id=local["id"], model="qwen2.5:7b")
    task, runs = routed(client, cid, cloud_agent(client, cid, cloud))  # the agent's own is paid
    scheduler.tick()
    [run] = runs()
    assert run["model_provider_id"] == local["id"] and run["model_used"] == "qwen2.5:7b"
    assert run["routing_reason"].startswith("AI stack: ") and "heavy" not in run["routing_reason"]
    with app.state.session_factory() as db:
        assert db.scalars(select(BudgetReservation.amount_micros)).one() == 0
        assert db.get(TaskRun, run["id"]).ai_connection_id is not None
    worker.run_once()
    assert get_task(client, cid, task["id"])["status"] == "done"


def test_a_paid_connection_reserves_and_needs_no_paid_fallback_approval(
    app, client, company, providers, scheduler
):
    cid = company["id"]
    cloud, local = providers
    add_connection(app, client, cid, "openai_api", model_provider_id=cloud["id"], model="big-model")
    set_policy(
        client, cid, allow_paid_fallback=False, model_routing="free_first"
    )  # legacy: ignored
    _, runs = routed(client, cid, new_agent(client, cid, name="Lola", runtime="company_agent"))
    report = scheduler.tick().dispatch
    assert len(report.queued_run_ids) == 1 and report.policy_blocked_task_ids == []
    [run] = runs()
    assert run["model_provider_id"] == cloud["id"] and run["model_used"] == "big-model"
    assert "free_first" not in run["routing_reason"]
    with app.state.session_factory() as db:
        assert db.scalars(select(BudgetReservation.amount_micros)).one() > 0


def test_a_pinned_model_keeps_the_agents_own_paid_model_outside_the_stack(
    app, client, company, providers, scheduler
):
    cid = company["id"]
    cloud, local = providers
    add_connection(app, client, cid, "ollama", model_provider_id=local["id"], model="qwen2.5:7b")
    agent = new_agent(
        client,
        cid,
        name="Pia",
        runtime="company_agent",
        model_provider=cloud["id"],
        model_name="big-model",
        runtime_config={"pin_model": True},
    )
    _, runs = routed(client, cid, agent, "pinned")
    scheduler.tick()
    [run] = runs()
    assert run["model_provider_id"] == cloud["id"] and run["model_used"] == "big-model"
    assert run["routing_reason"].startswith("pinned by the owner")
    with app.state.session_factory() as db:
        assert db.get(TaskRun, run["id"]).ai_connection_id is None


def test_other_runtimes_are_not_routed(app, client, company, scheduler):
    cid = company["id"]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    [run] = client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"]
    assert run["routing_reason"] is None and run["model_provider_id"] is None


# ------------------------------------------------------------------ threshold alerts


def alerts_of(client, cid, status="open"):
    items = client.get(api(cid, "/alerts"), params={"status": status, "limit": 100}).json()["items"]
    return [a for a in items if a["kind"] == "budget_threshold"]


def spend(client, cid, agent, cost, scheduler, worker, clock=None):
    client.patch(
        api(cid, f"/agents/{agent['id']}"),
        json={
            "runtime_config": {
                "script": {
                    "usage": {
                        "cost_micros": cost,
                        "cost_kind": "verified",
                        "input_tokens": 1,
                        "output_tokens": 1,
                    }
                }
            }
        },
    )
    new_task(client, cid, assignee_agent_id=agent["id"], title=f"spend {cost}")
    settle(scheduler, worker)


def test_threshold_alerts_fire_once_per_scope_period_and_threshold(
    app, client, company, scheduler, worker, clock
):
    cid = company["id"]
    set_policy(client, cid, monthly_budget_micros=1_000_000)
    agent = new_agent(client, cid)
    spend(client, cid, agent, 550_000, scheduler, worker)  # 55 %
    [a50] = alerts_of(client, cid)
    assert a50["severity"] == "info" and "50%" in a50["title"]
    spend(client, cid, agent, 300_000, scheduler, worker)  # 85 %
    titles = {a["title"] for a in alerts_of(client, cid)}
    assert any("80%" in t for t in titles)
    # resolving an alert does not make the same threshold fire again in the same period
    for a in alerts_of(client, cid):
        client.post(api(cid, f"/alerts/{a['id']}/resolve"))
    spend(client, cid, agent, 20_000, scheduler, worker)  # 87 %: still between 80 and 100
    assert alerts_of(client, cid) == []
    with app.state.session_factory() as db:
        marks = list(db.scalars(select(BudgetAlertMark.threshold)))
        assert sorted(marks) == [50, 80]
        budget_alerts.check(db, cid, clock())  # an extra check changes nothing
        db.commit()
        assert len(list(db.scalars(select(BudgetAlertMark)))) == 2
    spend(client, cid, agent, 200_000, scheduler, worker)  # 107 %
    crit = alerts_of(client, cid)
    assert len(crit) == 1 and crit[0]["severity"] == "critical" and "100%" in crit[0]["title"]
    # a new month is a new period: the 50 % threshold can fire again
    clock.advance(days=40)
    set_policy(client, cid, monthly_budget_micros=1_000_000)
    spend(client, cid, agent, 600_000, scheduler, worker)
    assert any("50%" in a["title"] for a in alerts_of(client, cid))
    with app.state.session_factory() as db:
        assert len(set(db.scalars(select(BudgetAlertMark.period_key)))) == 2


def test_custom_thresholds_and_per_agent_budgets(app, client, company, scheduler, worker):
    cid = company["id"]
    set_policy(client, cid, budget_alert_thresholds=[90, 10, 10])
    assert client.get(api(cid, "/policy")).json()["budget_alert_thresholds"] == [10, 90]
    agent = new_agent(client, cid, name="Wes", monthly_budget_micros=100_000)
    spend(client, cid, agent, 20_000, scheduler, worker)  # 20 % of the agent's budget
    [alert] = alerts_of(client, cid)
    assert alert["related_type"] == "agent" and alert["related_id"] == agent["id"]
    assert "Wes" in alert["title"] and "10%" in alert["title"]
    assert (
        client.patch(api(cid, "/policy"), json={"budget_alert_thresholds": [0]}).status_code == 422
    )


# ------------------------------------------------------------------ reports in the company timezone


@pytest.fixture
def la_company(client, company):
    r = client.patch(api(company["id"]), json={"timezone": "America/Los_Angeles"})
    assert r.status_code == 200, r.text
    return company


def add_usage(
    app,
    cid,
    agent_id,
    at: str,
    cost,
    kind="verified",
    *,
    status="succeeded",
    model="big-model",
    provider="p1",
    project_id=None,
    tokens=(100, 50),
):
    """A finished run + its usage record at an exact instant (UTC)."""
    when = datetime.fromisoformat(at).replace(tzinfo=UTC)
    with app.state.session_factory() as db:
        task = Task(
            company_id=cid,
            title="t",
            kind="work",
            status="done",
            created_by_type="system",
            project_id=project_id,
        )
        db.add(task)
        db.flush()
        run = TaskRun(
            company_id=cid,
            task_id=task.id,
            agent_id=agent_id,
            attempt_number=1,
            status=status,
            runtime="scripted",
            idempotency_key=f"{task.id}:1:0",
            queued_at=when,
            started_at=when,
            finished_at=when,
            cost_micros=cost,
            cost_kind=kind,
            model_used=model,
        )
        db.add(run)
        db.flush()
        ledger.record_usage(
            db,
            company_id=cid,
            agent_id=agent_id,
            dedupe_key=f"run:{run.id}",
            run_id=run.id,
            task_id=task.id,
            project_id=project_id,
            runtime="scripted",
            provider=provider,
            model=model,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cost_micros=cost,
            cost_kind=kind,
            occurred_at=when,
        )
        db.commit()
        return run.id


def test_periods_are_company_timezone_days_inclusive_and_echoed(app, client, la_company):
    cid = la_company["id"]
    agent = new_agent(client, cid)["id"]
    # 2026-01-15 07:30Z is 23:30 on Jan 14 in Los Angeles; 08:30Z is 00:30 on Jan 15
    add_usage(app, cid, agent, "2026-01-15T07:30:00", 1_000)
    add_usage(app, cid, agent, "2026-01-15T08:30:00", 20_000)
    add_usage(app, cid, agent, "2026-01-16T07:59:00", 300_000)  # still Jan 15 local
    add_usage(app, cid, agent, "2026-01-16T08:01:00", 4_000_000)  # Jan 16 local

    def summary(frm, to):
        r = client.get(api(cid, "/finance/summary"), params={"from": frm, "to": to})
        assert r.status_code == 200, r.text
        return r.json()

    jan15 = summary("2026-01-15", "2026-01-15")
    assert (jan15["from"], jan15["to"], jan15["timezone"]) == (
        "2026-01-15",
        "2026-01-15",
        "America/Los_Angeles",
    )
    assert jan15["totals"]["verified_micros"] == 320_000 and jan15["totals"]["runs"] == 2
    assert summary("2026-01-14", "2026-01-14")["totals"]["verified_micros"] == 1_000
    both = summary("2026-01-14", "2026-01-15")["totals"]["verified_micros"]
    assert both == 321_000  # `to` is inclusive of its whole local day
    assert summary("2026-01-16", "2026-01-16")["totals"]["verified_micros"] == 4_000_000

    trend = client.get(
        api(cid, "/finance/trend"), params={"from": "2026-01-13", "to": "2026-01-17"}
    ).json()
    assert [p["date"] for p in trend["points"]] == [
        "2026-01-13",
        "2026-01-14",
        "2026-01-15",
        "2026-01-16",
        "2026-01-17",
    ]
    by_day = {p["date"]: p["verified_micros"] for p in trend["points"]}
    assert by_day == {
        "2026-01-13": 0,
        "2026-01-14": 1_000,
        "2026-01-15": 320_000,
        "2026-01-16": 4_000_000,
        "2026-01-17": 0,
    }
    assert (trend["from"], trend["to"]) == ("2026-01-13", "2026-01-17")


def test_summary_splits_provenance_wasted_cost_and_budget_status(app, client, company):
    cid = company["id"]
    set_policy(client, cid, monthly_budget_micros=5_000_000, daily_budget_micros=1_000_000)
    agent = new_agent(client, cid)["id"]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    add_usage(app, cid, agent, now, 100_000, "verified")
    add_usage(app, cid, agent, now, 50_000, "estimated")
    add_usage(app, cid, agent, now, 7_000, "verified", status="failed")
    add_usage(app, cid, agent, now, 3_000, "estimated", status="cancelled")
    add_usage(app, cid, agent, now, 2_000, "estimated", status="timed_out")
    body = client.get(api(cid, "/finance/summary")).json()
    t = body["totals"]
    assert t["verified_micros"] == 107_000 and t["estimated_micros"] == 55_000
    assert t["unknown_micros"] == 0 and t["runs"] == 5 and t["input_tokens"] == 500
    assert body["wasted"] == {
        "verified_micros": 7_000,
        "estimated_micros": 5_000,
        "unknown_micros": 0,
        "runs": 3,
    }
    day = next(b for b in body["budgets"] if b["scope"] == "company" and b["period"] == "day")
    month = next(b for b in body["budgets"] if b["scope"] == "company" and b["period"] == "month")
    assert (day["limit_micros"], month["limit_micros"]) == (1_000_000, 5_000_000)
    assert day["verified_micros"] == 107_000 and month["estimated_micros"] == 55_000


def test_budget_status_lists_every_working_agent_for_both_periods(app, client, company):
    cid = company["id"]
    ann = new_agent(client, cid, name="Ann", daily_budget_micros=200_000)["id"]
    bob = new_agent(client, cid, name="Bob")["id"]
    r = client.patch(
        api(cid, f"/agents/{bob}"),
        json={"monthly_budget_micros": None, "daily_budget_micros": None},
    )
    assert r.status_code == 200, r.text
    gone = new_agent(client, cid, name="Gus")["id"]
    client.patch(
        api(cid, f"/agents/{gone}"),
        json={"monthly_budget_micros": None, "daily_budget_micros": None},
    )
    assert client.post(api(cid, f"/agents/{gone}/retire")).status_code == 200
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    add_usage(app, cid, bob, now, 40_000, "estimated")

    rows = client.get(api(cid, "/finance/summary")).json()["budgets"]
    agent_rows = {(b["agent_id"], b["period"]): b for b in rows if b["scope"] == "agent"}
    # both periods for every working agent, with or without a limit
    assert agent_rows[(ann, "day")]["limit_micros"] == 200_000
    assert (bob, "day") in agent_rows and (bob, "month") in agent_rows
    # an unbudgeted agent still reports what it spends, so a budget can be set from Finance
    assert agent_rows[(bob, "month")]["limit_micros"] is None
    assert agent_rows[(bob, "month")]["estimated_micros"] == 40_000
    # a retired agent without a limit is not listed
    assert not any(agent_id == gone for agent_id, _ in agent_rows)


def test_breakdown_by_every_dimension_and_run_pages(app, client, company):
    cid = company["id"]
    a1, a2 = new_agent(client, cid, name="Ann")["id"], new_agent(client, cid, name="Bob")["id"]
    project = client.post(api(cid, "/projects"), json={"name": "Site"}).json()["id"]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    for i in range(5):
        add_usage(
            app, cid, a1, now, 10_000 * (i + 1), model="m1", provider="px", project_id=project
        )
    add_usage(app, cid, a2, now, 1_000, "estimated", model="m2", provider="py")

    def rows(group):
        r = client.get(api(cid, "/finance/breakdown"), params={"group_by": group})
        assert r.status_code == 200, r.text
        assert r.json()["group_by"] == group
        return {i["key"]: i for i in r.json()["items"]}

    agents = rows("agent")
    assert agents[a1]["label"] == "Ann" and agents[a1]["verified_micros"] == 150_000
    assert agents[a1]["runs"] == 5 and agents[a2]["estimated_micros"] == 1_000
    assert list(agents)[0] == a1  # most expensive first
    assert rows("model")["m1"]["label"] == "m1" and set(rows("provider")) == {"px", "py"}
    assert rows("project")[project]["label"] == "Site" and "unassigned" in rows("project")
    assert rows("runtime")["scripted"]["runs"] == 6
    assert (
        client.get(api(cid, "/finance/breakdown"), params={"group_by": "bogus"}).status_code == 422
    )

    first = client.get(api(cid, "/finance/runs"), params={"limit": 4}).json()
    assert len(first["items"]) == 4 and first["next_cursor"]
    second = client.get(
        api(cid, "/finance/runs"), params={"limit": 4, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    ids = [i["run_id"] for i in first["items"] + second["items"]]
    assert len(set(ids)) == 6
    one = first["items"][0]
    assert one["task_title"] == "t" and one["cost_kind"] in ("verified", "estimated")
    assert one["model"] in ("m1", "m2")


def test_report_ranges_are_validated_and_default_to_the_month_so_far(client, company):
    cid = company["id"]
    for params in (
        {"from": "2026-13-01"},
        {"from": "20260101"},
        {"to": "x"},
        {"from": "2026-02-02", "to": "2026-02-01"},
        {"from": "2024-01-01", "to": "2026-01-01"},
    ):
        assert client.get(api(cid, "/finance/summary"), params=params).status_code == 400, params
    body = client.get(api(cid, "/finance/summary")).json()
    assert body["from"].endswith("-01") and body["totals"]["runs"] == 0
    assert body["timezone"] == "UTC"


def test_finance_is_tenant_scoped_and_humans_only(app, client, company, make_user, client_as):
    from tests.conftest import human

    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    other_agent = new_agent(client, other)["id"]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    add_usage(app, other, other_agent, now, 9_999_999)
    assert client.get(api(cid, "/finance/summary")).json()["totals"]["verified_micros"] == 0
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(cid, "/finance/summary")).status_code == 404
    team = make_team(client, cid)
    worker = run_as(app, client, cid, team.worker)
    auth = {"Authorization": f"Bearer {worker.token}"}
    assert client.get(api(cid, "/finance/summary"), headers=auth).status_code == 403


# ------------------------------------------------------------------ external expenses


def test_external_expenses_are_recorded_by_humans_and_reported_separately(
    app, client, company, make_user, add_member, client_as
):
    from tests.conftest import human

    cid = company["id"]
    body = {
        "vendor": "Namecheap",
        "description": "domain",
        "amount_micros": 12_000_000,
        "recurrence": "yearly",
        "incurred_on": "2026-03-10",
    }
    made = client.post(api(cid, "/finance/expenses"), json=body)
    assert made.status_code == 201, made.text
    assert made.json()["incurred_on"] == "2026-03-10" and made.json()["created_by_type"] == "human"
    monthly = client.post(
        api(cid, "/finance/expenses"),
        json={
            "vendor": "Hosting",
            "amount_micros": 5_000_000,
            "recurrence": "monthly",
            "incurred_on": "2026-01-31",
        },
    )
    assert monthly.status_code == 201
    client.post(
        api(cid, "/finance/expenses"),
        json={
            "vendor": "One-off",
            "amount_micros": 1_000_000,
            "recurrence": "one_time",
            "incurred_on": "2026-02-01",
        },
    )

    def total(frm, to):
        return client.get(api(cid, "/finance/summary"), params={"from": frm, "to": to}).json()[
            "external_expenses_micros"
        ]

    assert (
        total("2026-02-01", "2026-02-28") == 5_000_000 + 1_000_000
    )  # Feb: hosting (clamped to the 28th) + one-off
    assert total("2026-03-01", "2026-03-31") == 5_000_000 + 12_000_000
    assert total("2027-03-01", "2027-03-31") == 5_000_000 + 12_000_000  # yearly recurs
    assert total("2025-12-01", "2025-12-31") == 0
    listing = client.get(
        api(cid, "/finance/expenses"), params={"from": "2026-03-01", "to": "2026-03-31"}
    ).json()
    assert {e["vendor"] for e in listing["items"]} == {"Namecheap", "Hosting"}
    assert len(client.get(api(cid, "/finance/expenses")).json()["items"]) == 3
    # validation, project tenancy, audit and the live event
    assert (
        client.post(api(cid, "/finance/expenses"), json={**body, "amount_micros": -1}).status_code
        == 422
    )
    assert (
        client.post(
            api(cid, "/finance/expenses"), json={**body, "incurred_on": "tomorrow"}
        ).status_code
        == 422
    )
    assert (
        client.post(api(cid, "/finance/expenses"), json={**body, "project_id": "nope"}).status_code
        == 404
    )
    assert (
        client.post(api(cid, "/finance/expenses"), json={**body, "recurrence": "daily"}).status_code
        == 422
    )
    with app.state.session_factory() as db:
        assert (
            db.scalar(select(AuditEvent).where(AuditEvent.action == "expense.created")) is not None
        )
        assert (
            db.scalar(select(CompanyEvent).where(CompanyEvent.type == "expense.created"))
            is not None
        )
    # viewers read but cannot record; agents can do neither
    viewer = make_user("viewer@example.com")
    add_member(cid, viewer.id, "viewer")
    as_viewer = client_as(human(viewer))
    assert as_viewer.get(api(cid, "/finance/expenses")).status_code == 200
    assert as_viewer.post(api(cid, "/finance/expenses"), json=body).status_code == 403
    team = make_team(client, cid)
    worker = run_as(app, client, cid, team.worker)
    auth = {"Authorization": f"Bearer {worker.token}"}
    assert client.post(api(cid, "/finance/expenses"), json=body, headers=auth).status_code == 403
    assert client.get(api(cid, "/finance/expenses"), headers=auth).status_code == 403


def test_usage_records_and_alert_models_exist_for_the_reports(app):
    # guards the imports the reporting relies on
    assert UsageRecord.__tablename__ == "usage_records" and Alert.__tablename__ == "alerts"
