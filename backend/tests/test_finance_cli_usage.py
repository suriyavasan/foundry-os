"""B8: per-CLI usage report (GET /finance/cli-usage)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.run import RunEvent, TaskRun
from app.models.work import Task
from app.runtimes.cli.catalog import CLIS
from app.services import ledger
from tests.conftest import human
from tests.guardrail_helpers import make_team, run_as
from tests.helpers import api, new_agent

AT = "2026-03-10T12:00:00"


def add_run(
    app,
    cid,
    agent_id,
    runtime,
    *,
    status="succeeded",
    seconds=60,
    tokens=(100, 50),
    cost=0,
    kind="unknown",
    subscription=False,
    at=AT,
):
    when = datetime.fromisoformat(at).replace(tzinfo=UTC)
    with app.state.session_factory() as db:
        task = Task(company_id=cid, title="t", kind="work", status="done", created_by_type="system")
        db.add(task)
        db.flush()
        run = TaskRun(
            company_id=cid,
            task_id=task.id,
            agent_id=agent_id,
            attempt_number=1,
            status=status,
            runtime=runtime,
            idempotency_key=f"{task.id}:1:0",
            queued_at=when,
            started_at=when,
            finished_at=when + timedelta(seconds=seconds),
        )
        db.add(run)
        db.flush()
        billing = {"cost_micros": cost, "cost_kind": kind, "local": False}
        if subscription:
            billing["subscription"] = True
        db.add(
            RunEvent(
                company_id=cid,
                run_id=run.id,
                seq=1,
                type="usage",
                payload={"input_tokens": tokens[0], "output_tokens": tokens[1], "billing": billing},
                occurred_at=when,
            )
        )
        ledger.record_usage(
            db,
            company_id=cid,
            agent_id=agent_id,
            dedupe_key=f"run:{run.id}",
            run_id=run.id,
            task_id=task.id,
            runtime=runtime,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cost_micros=cost,
            cost_kind=kind,
            occurred_at=when,
        )
        db.commit()
        return run.id


def get(client, cid, **params):
    params = {"from": "2026-03-01", "to": "2026-03-31", **params}
    r = client.get(api(cid, "/finance/cli-usage"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def by_runtime(body):
    return {i["runtime"]: i for i in body["items"]}


def test_aggregates_per_cli_and_lists_every_catalog_cli(app, client, company):
    cid = company["id"]
    a1 = new_agent(client, cid)["id"]
    a2 = new_agent(client, cid)["id"]
    add_run(app, cid, a1, "cli:claude_code", seconds=30, cost=2_000_000, kind="estimated")
    add_run(app, cid, a2, "cli:claude_code", status="failed", seconds=90, tokens=(10, 5))
    add_run(app, cid, a1, "cli:claude_code", status="timed_out", seconds=0, tokens=(0, 0))
    add_run(app, cid, a1, "cli:codex", cost=500_000, kind="verified")
    body = get(client, cid)
    assert (body["from"], body["to"], body["timezone"]) == ("2026-03-01", "2026-03-31", "UTC")
    rows = by_runtime(body)
    assert set(rows) >= {spec.runtime_id for spec in CLIS.values()}
    cc = rows["cli:claude_code"]
    assert (cc["runs"], cc["succeeded"], cc["failed"]) == (3, 1, 2)
    assert (cc["input_tokens"], cc["output_tokens"]) == (110, 55)
    assert cc["duration_seconds"] == 120
    assert cc["estimated_micros"] == 2_000_000 and cc["verified_micros"] == 0
    assert sorted(cc["agents"]) == sorted([a1, a2])
    assert rows["cli:codex"]["verified_micros"] == 500_000
    for spec in CLIS.values():
        if spec.runtime_id not in ("cli:claude_code", "cli:codex"):
            z = rows[spec.runtime_id]
            assert z["runs"] == 0 and z["agents"] == [] and z["subscription_runs"] == 0
            assert z["estimated_micros"] == z["verified_micros"] == z["unknown_micros"] == 0


def test_non_cli_runtimes_and_other_periods_excluded(app, client, company):
    cid = company["id"]
    a = new_agent(client, cid)["id"]
    add_run(app, cid, a, "scripted", cost=9_000_000, kind="verified")
    add_run(app, cid, a, "company_agent")
    add_run(app, cid, a, "cli:codex", at="2026-02-27T12:00:00")
    body = get(client, cid)
    assert all(i["runtime"].startswith("cli:") for i in body["items"])
    assert all(i["runs"] == 0 for i in body["items"])
    assert get(client, cid, **{"from": "2026-02-01", "to": "2026-02-28"})  # range honoured
    feb = by_runtime(get(client, cid, **{"from": "2026-02-01", "to": "2026-02-28"}))
    assert feb["cli:codex"]["runs"] == 1


def test_subscription_runs_counted_never_priced(app, client, company):
    cid = company["id"]
    a = new_agent(client, cid)["id"]
    add_run(app, cid, a, "cli:claude_code", subscription=True, tokens=(400, 200))
    add_run(app, cid, a, "cli:claude_code", subscription=True, status="failed")
    add_run(app, cid, a, "cli:claude_code", cost=1_000_000, kind="estimated")
    cc = by_runtime(get(client, cid))["cli:claude_code"]
    assert cc["runs"] == 3 and cc["subscription_runs"] == 2
    assert cc["input_tokens"] == 400 + 100 + 100
    assert cc["verified_micros"] == 0
    assert cc["estimated_micros"] == 1_000_000
    assert cc["unknown_micros"] == 0


def test_defaults_to_this_month_and_validates_range(client, company):
    cid = company["id"]
    body = client.get(api(cid, "/finance/cli-usage")).json()
    assert body["from"].endswith("-01") and body["items"]
    r = client.get(
        api(cid, "/finance/cli-usage"), params={"from": "2026-02-02", "to": "2026-02-01"}
    )
    assert r.status_code == 400


def test_tenant_scoped_and_access_like_other_finance_endpoints(
    app, client, company, make_user, add_member, client_as
):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    add_run(app, other, new_agent(client, other)["id"], "cli:codex")
    assert by_runtime(get(client, cid))["cli:codex"]["runs"] == 0
    stranger = client_as(human(make_user("stranger@example.com")))
    assert stranger.get(api(cid, "/finance/cli-usage")).status_code == 404
    viewer = make_user("viewer@example.com")
    add_member(cid, viewer.id, "viewer")
    assert client_as(human(viewer)).get(api(cid, "/finance/cli-usage")).status_code == 200
    team = make_team(client, cid)
    worker = run_as(app, client, cid, team.worker)
    auth = {"Authorization": f"Bearer {worker.token}"}
    assert client.get(api(cid, "/finance/cli-usage"), headers=auth).status_code == 403
