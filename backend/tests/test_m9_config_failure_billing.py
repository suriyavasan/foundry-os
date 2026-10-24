"""M9 finding: a run that fails on configuration before the runtime does anything costs nothing."""

from __future__ import annotations

from app.models.ledger import UsageRecord
from tests.helpers import api, get_task, new_agent, new_task
from tests.test_engine import runs_of


def test_config_failure_is_not_charged_as_an_estimate(app, client, company, scheduler, worker):
    cid = company["id"]
    agent = new_agent(
        client, cid, script={"raise_config_error": "Model provider 'x' has no credential"}
    )
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()
    [run] = runs_of(client, cid, task["id"])
    assert run["status"] == "failed" and run["exit_reason"] == "config"
    assert run["cost_micros"] == 0 and run["cost_kind"] == "unknown"  # not "$0.05 (estimated)"
    with app.state.session_factory() as db:
        assert db.query(UsageRecord).count() == 0
    spend = client.get(api(cid, "/overview")).json()["spending"]["today"]
    assert spend["spent_micros"] == 0 and spend["held_micros"] == 0
    assert get_task(client, cid, task["id"])["attempt_count"] >= 1


def test_a_failure_after_the_runtime_started_is_still_billed(
    app, client, company, scheduler, worker
):
    cid = company["id"]
    agent = new_agent(client, cid, script={"raise_exception": "boom"})
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    scheduler.tick()
    worker.drain()
    [run] = runs_of(client, cid, task["id"])
    assert run["exit_reason"] == "runtime_exception"
    assert run["cost_kind"] == "estimated" and run["cost_micros"] > 0  # unchanged behaviour
