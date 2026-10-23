"""Shared helpers for work-engine tests."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.core.time import utcnow


class FakeClock:
    """Injectable clock. Starts at the real "now" (so period math matches wall-clock queries)
    and only moves when a test advances it."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = (start or utcnow()).replace(microsecond=0)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float = 0, **kwargs) -> datetime:
        with self._lock:
            self._now += timedelta(seconds=seconds, **kwargs)
            return self._now

    def set(self, value: datetime) -> None:
        with self._lock:
            self._now = value.astimezone(UTC)


def api(cid: str, path: str = "") -> str:
    return f"/api/companies/{cid}{path}"


def new_agent(
    client: TestClient,
    cid: str,
    *,
    name: str = "Nova",
    runtime: str = "scripted",
    script: dict[str, Any] | None = None,
    activate: bool = True,
    **overrides,
) -> dict:
    body: dict[str, Any] = {"name": name, "role_title": "Engineer", "runtime": runtime, **overrides}
    if script is not None:
        body["runtime_config"] = {"script": script}
    resp = client.post(api(cid, "/agents"), json=body)
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    if activate:
        r = client.post(api(cid, f"/agents/{agent['id']}/activate"))
        assert r.status_code == 200, r.text
        agent = r.json()
    return agent


def new_task(client: TestClient, cid: str, **overrides) -> dict:
    body = {"title": "Do the thing", **overrides}
    resp = client.post(api(cid, "/tasks"), json=body)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def get_task(client: TestClient, cid: str, task_id: str) -> dict:
    resp = client.get(api(cid, f"/tasks/{task_id}"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def settle(scheduler, worker, *, rounds: int = 12) -> None:
    """Drive dispatch + execution until nothing more happens (deterministic, no sleeping)."""
    for _ in range(rounds):
        report = scheduler.tick()
        processed = worker.drain()
        if not report.dispatch.queued_run_ids and not processed:
            return


def set_policy(client: TestClient, cid: str, **fields) -> dict:
    resp = client.patch(api(cid, "/policy"), json=fields)
    assert resp.status_code == 200, resp.text
    return resp.json()


def wait_until(
    predicate, *, timeout: float = 5.0, interval: float = 0.005, what: str = "condition"
):
    """Poll a cheap in-process condition (no fixed sleeps)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def run_in_thread(fn, *args) -> threading.Thread:
    thread = threading.Thread(target=fn, args=args, daemon=True)
    thread.start()
    return thread
