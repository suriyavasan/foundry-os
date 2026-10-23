from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select

from app.core.errors import BudgetExceeded
from app.models.agent import Agent
from app.models.enums import ReservationStatus
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.work import Task
from app.services import ledger
from tests.helpers import api, new_agent, new_task, set_policy

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def setup(app, client, company):
    cid = company["id"]
    agent = new_agent(client, cid, name="Spender")
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    return cid, agent["id"], task["id"]


def _reserve(app, cid, agent_id, task_id, amount, now=NOW, *, commit=True):
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        task = db.get(Task, task_id)
        res = ledger.reserve(db, cid, agent, task, amount, now=now)
        if commit:
            db.commit()
        return res.id


def _committed(app, cid, **scope):
    with app.state.session_factory() as db:
        return ledger.committed_micros(db, cid, **scope)


def test_reserve_settle_release_math(app, client, setup):
    cid, agent_id, task_id = setup
    set_policy(client, cid, daily_budget_micros=1_000_000)
    r1 = _reserve(app, cid, agent_id, task_id, 400_000)
    r2 = _reserve(app, cid, agent_id, task_id, 300_000)
    assert _committed(app, cid) == 700_000
    with app.state.session_factory() as db:
        # held counts by amount; settling counts by settled amount (cheaper than reserved)
        assert ledger.settle(db, db.get(BudgetReservation, r1), 100_000)
        # releasing frees the whole hold
        assert ledger.release(db, db.get(BudgetReservation, r2))
        db.commit()
    assert _committed(app, cid) == 100_000
    # both operations are idempotent and a settled reservation cannot be released
    with app.state.session_factory() as db:
        assert not ledger.settle(db, db.get(BudgetReservation, r1), 999)
        assert not ledger.release(db, db.get(BudgetReservation, r1))
        assert not ledger.settle(db, db.get(BudgetReservation, r2), 5)
        db.commit()
        rows = {r.id: r for r in db.scalars(select(BudgetReservation))}
    assert rows[r1].status == ReservationStatus.SETTLED and rows[r1].settled_micros == 100_000
    assert rows[r2].status == ReservationStatus.RELEASED
    # an overrun settles at the actual (higher) cost and counts in full
    r3 = _reserve(app, cid, agent_id, task_id, 200_000)
    with app.state.session_factory() as db:
        ledger.settle(db, db.get(BudgetReservation, r3), 450_000)
        db.commit()
    assert _committed(app, cid) == 550_000


def _cap_company_daily(c, cid, aid, tid, pid):
    set_policy(c, cid, daily_budget_micros=100)


def _cap_company_monthly(c, cid, aid, tid, pid):
    set_policy(c, cid, monthly_budget_micros=100)


def _cap_agent_daily(c, cid, aid, tid, pid):
    c.patch(api(cid, f"/agents/{aid}"), json={"daily_budget_micros": 100})


def _cap_agent_monthly(c, cid, aid, tid, pid):
    c.patch(api(cid, f"/agents/{aid}"), json={"monthly_budget_micros": 100})


def _cap_project(c, cid, aid, tid, pid):
    c.patch(api(cid, f"/projects/{pid}"), json={"budget_micros": 100})


def _cap_task(c, cid, aid, tid, pid):
    c.patch(api(cid, f"/tasks/{tid}"), json={"budget_micros": 100})


@pytest.mark.parametrize(
    ("limit_name", "configure"),
    [
        ("company_daily", _cap_company_daily),
        ("company_monthly", _cap_company_monthly),
        ("agent_daily", _cap_agent_daily),
        ("agent_monthly", _cap_agent_monthly),
        ("project_budget", _cap_project),
        ("task_budget", _cap_task),
    ],
)
def test_every_limit_scope_is_enforced(app, client, company, limit_name, configure):
    cid = company["id"]
    agent = new_agent(client, cid)
    project = client.post(api(cid, "/projects"), json={"name": "P"}).json()
    task = new_task(client, cid, assignee_agent_id=agent["id"], project_id=project["id"])
    configure(client, cid, agent["id"], task["id"], project["id"])
    _reserve(app, cid, agent["id"], task["id"], 60)
    with pytest.raises(BudgetExceeded) as exc:
        _reserve(app, cid, agent["id"], task["id"], 60)
    assert exc.value.details["limit"] == limit_name
    assert exc.value.details["limit_micros"] == 100 and exc.value.details["committed_micros"] == 60
    _reserve(app, cid, agent["id"], task["id"], 40)  # exactly at the limit is fine


def test_other_agents_do_not_share_agent_scoped_limits(app, client, company):
    cid = company["id"]
    a1, a2 = new_agent(client, cid, name="A1"), new_agent(client, cid, name="A2")
    t1 = new_task(client, cid, assignee_agent_id=a1["id"])
    t2 = new_task(client, cid, assignee_agent_id=a2["id"])
    client.patch(api(cid, f"/agents/{a1['id']}"), json={"daily_budget_micros": 100})
    _reserve(app, cid, a1["id"], t1["id"], 100)
    _reserve(app, cid, a2["id"], t2["id"], 10_000)  # a2 has no cap; a1's spend is not a2's


def test_daily_vs_monthly_periods_in_company_timezone(app, client):
    # Company in Tokyo (UTC+9): 2026-03-10T16:00Z is already 2026-03-11 01:00 local.
    cid = client.post("/api/companies", json={"name": "Tokyo Co", "timezone": "Asia/Tokyo"}).json()[
        "id"
    ]
    agent = new_agent(client, cid)
    task = new_task(client, cid, assignee_agent_id=agent["id"])
    set_policy(client, cid, daily_budget_micros=100, monthly_budget_micros=150)
    late = datetime(2026, 3, 10, 16, 0, tzinfo=UTC)  # local: Mar 11 01:00
    early = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)  # local: Mar 10 23:00
    _reserve(app, cid, agent["id"], task["id"], 100, now=early)
    # same UTC day, but a different *local* day -> the daily budget has reset...
    _reserve(app, cid, agent["id"], task["id"], 50, now=late)
    with app.state.session_factory() as db:
        days = sorted(str(d) for d in db.scalars(select(BudgetReservation.period_day)))
    assert days == ["2026-03-10", "2026-03-11"]
    # ...but the monthly budget (150) is exhausted
    with pytest.raises(BudgetExceeded) as exc:
        _reserve(app, cid, agent["id"], task["id"], 10, now=late)
    assert exc.value.details["limit"] == "company_monthly"
    # next month everything resets
    april = datetime(2026, 4, 2, 0, 0, tzinfo=UTC)  # local: Apr 2 09:00
    _reserve(app, cid, agent["id"], task["id"], 100, now=april)
    # a daily overrun reports when the day rolls over (local midnight, as a UTC instant)
    with pytest.raises(BudgetExceeded) as exc:
        _reserve(app, cid, agent["id"], task["id"], 1, now=april)
    assert exc.value.details["limit"] == "company_daily"
    assert exc.value.details["retry_after"] == "2026-04-02T15:00:00Z"  # Apr 3 00:00 JST


def test_failed_reservation_writes_nothing(app, client, setup):
    cid, agent_id, task_id = setup
    set_policy(client, cid, daily_budget_micros=10)
    with pytest.raises(BudgetExceeded):
        _reserve(app, cid, agent_id, task_id, 11)
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(BudgetReservation.id))) == 0


def test_usage_records_are_idempotent_on_dedupe_key(app, client, setup):
    cid, agent_id, task_id = setup
    kwargs = dict(
        company_id=cid, agent_id=agent_id, task_id=task_id, dedupe_key="run:abc",
        input_tokens=10, output_tokens=5, cost_micros=1234, cost_kind="verified", occurred_at=NOW,
    )  # fmt: skip
    with app.state.session_factory() as db:
        first = ledger.record_usage(db, **kwargs)
        again = ledger.record_usage(db, **{**kwargs, "cost_micros": 999_999})
        db.commit()
        assert again.id == first.id
        assert db.scalar(select(func.count(UsageRecord.id))) == 1
        assert db.get(UsageRecord, first.id).cost_micros == 1234
    # the same key in another company is a different record
    other = client.post("/api/companies", json={"name": "Other"}).json()["id"]
    other_agent = new_agent(client, other)
    with app.state.session_factory() as db:
        ledger.record_usage(
            db, **{**kwargs, "company_id": other, "agent_id": other_agent["id"], "task_id": None}
        )
        db.commit()
        assert db.scalar(select(func.count(UsageRecord.id))) == 2


def test_reserve_takes_the_company_lock_as_its_first_statement(app, client, setup):
    cid, agent_id, task_id = setup
    seen: list[str] = []
    engine = app.state.engine

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    try:
        with app.state.session_factory() as db:
            agent, task = db.get(Agent, agent_id), db.get(Task, task_id)
            seen.clear()
            ledger.reserve(db, cid, agent, task, 1, now=NOW)
            db.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert seen[0].startswith("UPDATE company_policies SET budget_lock_version")


def test_concurrent_reservations_admit_exactly_the_limit(app, client, company):
    """N threads, separate sessions, file-backed SQLite: only K reservations fit the limit."""
    cid = company["id"]
    agent = new_agent(client, cid)
    n_threads, fits, amount = 10, 3, 100_000
    tasks = [
        new_task(client, cid, title=f"t{i}", assignee_agent_id=agent["id"])["id"]
        for i in range(n_threads)
    ]
    set_policy(client, cid, daily_budget_micros=fits * amount)

    barrier = threading.Barrier(n_threads)
    results: list[str] = [""] * n_threads

    def attempt(i: int) -> None:
        with app.state.session_factory() as db:
            ag, task = db.get(Agent, agent["id"]), db.get(Task, tasks[i])
            barrier.wait(timeout=10)
            try:
                ledger.reserve(db, cid, ag, task, amount, now=NOW)
                db.commit()
                results[i] = "ok"
            except BudgetExceeded:
                db.rollback()
                results[i] = "rejected"
            except Exception as exc:  # pragma: no cover - would fail the assertion below
                results[i] = f"error:{type(exc).__name__}:{exc}"

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert results.count("ok") == fits, results
    assert results.count("rejected") == n_threads - fits, results
    assert _committed(app, cid) == fits * amount
    with app.state.session_factory() as db:
        held = db.scalar(
            select(func.count(BudgetReservation.id)).where(
                BudgetReservation.status == ReservationStatus.HELD
            )
        )
        assert held == fits


def test_concurrent_reservations_in_two_companies_are_independent(app, client):
    """Locks are per company: each company independently admits exactly its own limit."""
    companies = []
    for name in ("Alpha", "Beta"):
        cid = client.post("/api/companies", json={"name": name}).json()["id"]
        agent = new_agent(client, cid)
        set_policy(client, cid, daily_budget_micros=200)
        task_ids = [
            new_task(client, cid, title=f"t{i}", assignee_agent_id=agent["id"])["id"]
            for i in range(6)
        ]
        companies.append((cid, agent["id"], task_ids))
    barrier = threading.Barrier(12)
    outcomes: dict[str, list[str]] = {c[0]: [] for c in companies}
    lock = threading.Lock()

    def attempt(cid: str, agent_id: str, task_id: str) -> None:
        with app.state.session_factory() as db:
            ag, task = db.get(Agent, agent_id), db.get(Task, task_id)
            barrier.wait(timeout=10)
            try:
                ledger.reserve(db, cid, ag, task, 100, now=NOW)
                db.commit()
                res = "ok"
            except BudgetExceeded:
                db.rollback()
                res = "rejected"
            with lock:
                outcomes[cid].append(res)

    threads = [
        threading.Thread(target=attempt, args=(cid, aid, tid))
        for cid, aid, tids in companies
        for tid in tids
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    for cid, results in outcomes.items():
        assert results.count("ok") == 2 and results.count("rejected") == 4, (cid, results)
