"""The worker process (companyos-worker) and the embedded-worker lifespan."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import BACKEND_DIR
from app.main import create_app
from app.runtimes.registry import build_default_registry
from tests.helpers import api, get_task, new_agent, new_task, wait_until


def test_embedded_worker_runs_tasks_inside_the_api_process(make_settings):
    settings = make_settings(
        embedded_worker=True,
        enable_test_runtimes=True,
        engine_tick_seconds=0.05,
        engine_poll_seconds=0.05,
    )
    app = create_app(settings, runtime_registry=build_default_registry(settings))
    with TestClient(app) as client:
        cid = client.post("/api/companies", json={"name": "Embedded Co"}).json()["id"]
        agent = new_agent(client, cid, script={"summary": "embedded works"})
        task = new_task(client, cid, assignee_agent_id=agent["id"])
        done = wait_until(
            lambda: (t := get_task(client, cid, task["id"]))["status"] == "done" and t,
            timeout=15,
            what="the embedded engine to finish the task",
        )
        assert done["result_summary"] == "embedded works"
        runs = client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"]
        assert runs[0]["status"] == "succeeded" and runs[0]["worker_id"]
    # leaving the lifespan stopped the engine threads
    names = {t.name for t in threading.enumerate()}
    assert "engine-worker" not in names and "engine-scheduler" not in names


def test_api_without_the_flag_does_not_start_an_engine(client, company):
    assert not any(t.name.startswith("engine-") for t in threading.enumerate())


def _spawn_worker(db_url: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "COMPANY_OS_DATABASE_URL": db_url,
        "COMPANY_OS_ENABLE_TEST_RUNTIMES": "1",
        "COMPANY_OS_ENGINE_TICK_SECONDS": "0.1",
        "COMPANY_OS_ENGINE_POLL_SECONDS": "0.1",
        "PYTHONUNBUFFERED": "1",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "app.engine.cli"],
        cwd=BACKEND_DIR,
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )


def _watch(proc: subprocess.Popen) -> queue.Queue:
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in proc.stderr:  # type: ignore[union-attr]
            lines.put(line)

    threading.Thread(target=pump, daemon=True).start()
    return lines


def _wait_for_line(lines: queue.Queue, needle: str, timeout: float = 30) -> list[str]:
    seen: list[str] = []
    while True:
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"never saw {needle!r}; got: {seen}") from None
        seen.append(line)
        if needle in line:
            return seen


def test_worker_process_shuts_down_gracefully_on_sigterm(client, company, db_url):
    cid = company["id"]
    agent = new_agent(client, cid, script={"block_until_cancel": True})
    task = new_task(client, cid, assignee_agent_id=agent["id"])

    proc = _spawn_worker(db_url)
    lines = _watch(proc)
    try:
        _wait_for_line(lines, "engine started")
        # the process picks the task up and starts the (blocking) run
        wait_until(
            lambda: (
                get_task(client, cid, task["id"])["status"] == "in_progress"
                and client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"][0]["status"]
                == "running"
            ),
            timeout=30,
            what="the worker process to start the run",
        )
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    log = "".join(lines.queue)
    assert "shutting down" in log and "engine stopped" in log
    # the in-flight run was cancelled cooperatively and the attempt refunded, not lost
    run = client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"][0]
    assert run["status"] == "cancelled" and run["exit_reason"] == "worker_shutdown"
    after = get_task(client, cid, task["id"])
    assert after["status"] == "ready" and after["attempt_count"] == 0


def test_worker_process_refuses_to_start_without_a_schema(tmp_path):
    proc = _spawn_worker(f"sqlite:///{tmp_path / 'empty.db'}")
    err = proc.stderr.read()  # type: ignore[union-attr]
    assert proc.wait(timeout=30) == 1
    assert "alembic upgrade head" in err
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with engine.connect() as conn:
        assert (
            conn.execute(text("select count(*) from sqlite_master where type='table'")).scalar()
            == 0
        )
    engine.dispose()
