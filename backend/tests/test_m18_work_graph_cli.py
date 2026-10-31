"""M18 Part B: Claude Code sessions (--session-id / --resume), the tool-less self-check call and the
"proposed completion counts as completed" rule of the CLI adapters. Uses the fake `claude` binary of
the M10 tests: it records its argv."""

from __future__ import annotations

import dataclasses
import uuid

from app.models.run import TaskRun
from app.runtimes.base import CancelToken, SessionSpec
from tests.m10_helpers import CLAUDE_OK, install_fake_cli, record_of
from tests.test_m10_cli_adapters import Sink, configure, world  # noqa: F401 (fixture)


def run_claude(world, **overrides):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("done")
    request = dataclasses.replace(world.request(), **overrides)
    result = world.adapter("claude").execute(request, Sink(), CancelToken())
    return result, record_of(world.bin_dir, "claude")["argv"]


def test_first_act_starts_a_named_session_and_keeps_it(world):
    sid = str(uuid.uuid4())
    result, argv = run_claude(world, session=SessionSpec("new", sid))
    assert result.status == "succeeded"
    assert f"--session-id={sid}" in argv
    assert "--no-session-persistence" not in argv
    assert "--resume" not in argv


def test_later_acts_resume_the_same_session_never_with_no_persistence(world):
    sid = str(uuid.uuid4())
    _, argv = run_claude(world, session=SessionSpec("resume", sid))
    assert argv[argv.index("--resume") + 1] == sid
    assert "--no-session-persistence" not in argv  # the two flags never go together
    assert not any(a.startswith("--session-id") for a in argv)
    # the prompt tells the agent it is continuing its own session
    assert "RESUMING" in argv[argv.index("-p") + 1]


def test_a_run_without_a_session_stays_non_persistent(world):
    _, argv = run_claude(world)
    assert "--no-session-persistence" in argv
    assert "--resume" not in argv and not any(a.startswith("--session-id") for a in argv)


def test_a_malformed_session_id_never_reaches_the_command_line(world):
    _, argv = run_claude(world, session=SessionSpec("new", "--dangerously-skip-permissions"))
    assert "--no-session-persistence" in argv
    assert not any("dangerously" in a for a in argv if a != argv[argv.index("-p") + 1])


def test_the_self_check_is_a_tool_less_call_without_a_session(world):
    sid = str(uuid.uuid4())
    base = world.request()
    verify_task = dataclasses.replace(base.task, description='Answer {"pass": true}')
    result, argv = run_claude(
        world, purpose="verify", task=verify_task, session=SessionSpec("resume", sid)
    )
    assert result.status == "succeeded"
    assert "--tools=" in argv  # no tools at all
    assert "--strict-mcp-config" not in argv and not any(a.startswith("--mcp-config") for a in argv)
    assert "--no-session-persistence" in argv and "--resume" not in argv
    assert argv[argv.index("-p") + 1] == 'Answer {"pass": true}'


def test_a_proposed_completion_counts_as_the_cli_having_completed(world):
    install_fake_cli(world.bin_dir, "claude", CLAUDE_OK)
    configure(world, "claude", "api_key")
    world.set_task_status("in_progress")  # the graph keeps the task open until finish
    with world.app.state.session_factory() as db:
        db.add(
            TaskRun(
                id="run-1",
                company_id=world.cid,
                task_id=world.task["id"],
                agent_id=world.agent["id"],
                attempt_number=1,
                status="running",
                runtime="cli:claude_code",
                idempotency_key="proposed-1",
                graph_thread_id=f"task:{world.task['id']}",
                proposed_result={"summary": "done", "output_refs": []},
            )
        )
        db.commit()
    result = world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert result.status == "succeeded"
    with world.app.state.session_factory() as db:
        db.get(TaskRun, "run-1").proposed_result = None
        db.commit()
    again = world.adapter("claude").execute(world.request(), Sink(), CancelToken())
    assert again.status == "failed"  # no completion and no proposal: still task_not_completed
