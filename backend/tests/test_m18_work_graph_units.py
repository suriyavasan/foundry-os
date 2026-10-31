"""M18 Part B: the graph with a fake ``Deps`` -- crash resume from a checkpoint, session continuity
across acts/connections, the revision cap, and what the thread state may hold."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.engine.graph.checkpointer import SqlCheckpointSaver
from app.engine.graph.runner import WorkGraphRunner
from app.engine.graph.sink import UsageTotals
from app.engine.graph.work_graph import ActInspection, parse_verdict
from app.models.graph import GraphCheckpoint
from app.runtimes.base import (
    CancelToken,
    ExecutionError,
    ExecutionResult,
    RuntimeErrorKind,
    SessionSpec,
)


class SimulatedCrash(BaseException):
    """The process died: not an Exception, so nothing in the graph can swallow it."""


class FakeDeps:
    supports_sessions = True
    runtime_id = "cli:claude_code"
    check_allowed = True

    def __init__(self, company_id, *, connection_id="conn-A", task_id="t1", agent_id="a1"):
        self.company_id, self.task_id, self.agent_id = company_id, task_id, agent_id
        self.connection_id = connection_id
        self.cancel = CancelToken()
        self.totals = UsageTotals()
        self.artifacts: list = []
        self.nodes: list[tuple[str, str]] = []
        self.acts: list[SimpleNamespace] = []
        self.checks = 0
        self.applied: list[dict] = []
        self.verdicts: list[dict] = [{"status": "passed", "feedback": ""}]
        self.act_script: list = []  # per act: ExecutionResult | BaseException
        self.crash_in_check = False
        self.proposal = None
        self.inspection = ActInspection()

    def node(self, node, outcome, **extra):
        self.nodes.append((node, outcome))

    def context_text(self):
        return None

    def run_act(self, *, carry_extra, feedback, session: SessionSpec | None):
        self.acts.append(SimpleNamespace(carry=carry_extra, feedback=feedback, session=session))
        step = self.act_script.pop(0) if self.act_script else ExecutionResult("succeeded")
        if isinstance(step, BaseException):
            raise step
        self.proposal = {"summary": f"result {len(self.acts)}", "output_refs": []}
        return step

    def clear_proposal(self):
        self.proposal = None

    def read_proposal(self):
        return self.proposal

    def inspect_after_act(self):
        return self.inspection

    def run_check(self, state):
        self.checks += 1
        if self.crash_in_check:
            raise SimulatedCrash()
        return self.verdicts[min(self.checks - 1, len(self.verdicts) - 1)]

    def apply_completion(self, proposal, *, self_checked, force_review):
        self.applied.append({**proposal, "self_checked": self_checked, "force": force_review})
        return True

    def approval_resume(self, ids):
        return [{"id": i, "title": "Buy", "status": "approved", "note": None} for i in ids]

    def question_resume(self, ids):
        return [{"id": i, "status": "answered"} for i in ids]


@pytest.fixture
def runner(app):
    return WorkGraphRunner(SimpleNamespace(session_factory=app.state.session_factory))


def names(deps):
    return [n for n, _ in deps.nodes]


def test_a_crash_between_nodes_resumes_from_the_last_checkpoint(runner, company):
    cid = company["id"]
    first = FakeDeps(cid)
    first.act_script = [SimulatedCrash()]
    with pytest.raises(SimulatedCrash):
        runner.run(first)
    assert names(first) == ["prepare"]  # prepare was checkpointed, the act never finished

    second = FakeDeps(cid)  # a new worker, a new saver, the same thread
    out = runner.run(second)
    assert second.nodes[0] == ("graph", "continued_from_checkpoint")
    assert "prepare" not in names(second)  # not started over
    assert names(second)[1:] == ["act", "check", "finish"]
    assert out.result.status == "succeeded" and out.waiting is None
    assert second.applied == [
        {"summary": "result 1", "output_refs": [], "self_checked": True, "force": None}
    ]


def test_a_crash_during_the_check_does_not_redo_the_act(runner, company):
    cid = company["id"]
    first = FakeDeps(cid)
    first.crash_in_check = True
    with pytest.raises(SimulatedCrash):
        runner.run(first)
    assert names(first) == ["prepare", "act"]

    second = FakeDeps(cid)
    runner.run(second)
    assert second.acts == []  # the proposed result was checkpointed: go on with the check
    assert names(second)[:2] == ["graph", "check"]
    assert second.applied and second.applied[0]["summary"] == "result 1"


def test_revisions_are_capped_at_two_and_the_task_goes_to_review(runner, company):
    deps = FakeDeps(company["id"])
    deps.verdicts = [{"status": "failed", "feedback": "nope"}]
    runner.run(deps)
    assert len(deps.acts) == 3 and deps.checks == 3
    assert [a.feedback for a in deps.acts] == [None, "nope", "nope"]
    assert deps.applied[0]["force"] == "nope" and deps.applied[0]["self_checked"] is False


def test_same_connection_resumes_the_session_and_a_new_one_starts_a_new_session(runner, company):
    cid = company["id"]
    deps = FakeDeps(cid, connection_id="conn-A")
    deps.verdicts = [{"status": "failed", "feedback": "fix"}, {"status": "passed", "feedback": ""}]
    runner.run(deps)
    first, second = deps.acts
    assert first.session.mode == "new" and second.session.mode == "resume"
    assert second.session.id == first.session.id
    assert second.carry is None  # the session itself carries the memory

    other = FakeDeps(cid, connection_id="conn-B")  # the task runs again, routed elsewhere
    runner.run(other)
    [act] = other.acts
    assert act.session.mode == "new" and act.session.id != first.session.id
    assert "Where you stand" in (act.carry or "") and "Act 2" in (act.carry or "")
    again = FakeDeps(cid, connection_id="conn-B")  # now B has a started session of its own
    runner.run(again)
    assert again.acts[0].session.mode == "resume" and again.acts[0].session.id == act.session.id


def test_a_lost_session_falls_back_to_a_new_one_with_the_summary(runner, company):
    cid = company["id"]
    deps = FakeDeps(cid)
    runner.run(deps)  # starts a session
    sid = deps.acts[0].session.id
    again = FakeDeps(cid)
    again.act_script = [
        ExecutionResult(
            "failed",
            error=ExecutionError(RuntimeErrorKind.FAILED, "No conversation found with session ID"),
        )
    ]
    runner.run(again)
    lost, fresh = again.acts
    assert lost.session.mode == "resume" and lost.session.id == sid
    assert fresh.session.mode == "new" and fresh.session.id != sid
    assert "Where you stand" in (fresh.carry or "")


def test_runtimes_without_sessions_start_fresh_with_the_summary(runner, company):
    deps = FakeDeps(company["id"])
    deps.supports_sessions = False
    deps.verdicts = [{"status": "failed", "feedback": "fix"}, {"status": "passed", "feedback": ""}]
    runner.run(deps)
    first, second = deps.acts
    assert first.session is None and second.session is None
    assert first.carry is None and "Where you stand" in second.carry


def test_the_state_holds_ids_counters_and_short_text_only(runner, company, app):
    deps = FakeDeps(company["id"])
    runner.run(deps)
    saver = SqlCheckpointSaver(app.state.session_factory)
    cfg = {"configurable": {"thread_id": "task:t1", "company_id": company["id"]}}
    values = saver.get_tuple(cfg).checkpoint["channel_values"]
    blob = json.dumps(values, default=str)
    assert len(blob) < 4000
    assert set(values["session"]) == {"id", "connection_id", "runtime", "started"}


def test_every_checkpoint_of_the_thread_belongs_to_the_runs_company(runner, company, app):
    runner.run(FakeDeps(company["id"]))
    with app.state.session_factory() as db:
        rows = list(db.query(GraphCheckpoint).all())
    assert rows and {r.company_id for r in rows} == {company["id"]}


def test_a_thread_of_another_assignee_is_never_continued(runner, company):
    cid = company["id"]
    first = FakeDeps(cid, agent_id="a1")
    first.act_script = [SimulatedCrash()]
    with pytest.raises(SimulatedCrash):
        runner.run(first)
    second = FakeDeps(cid, agent_id="a2")  # the task was reassigned meanwhile
    runner.run(second)
    assert names(second)[0] == "prepare"


def test_interrupts_wait_and_resume_with_the_decisions(runner, company):
    cid = company["id"]
    deps = FakeDeps(cid)
    deps.inspection = ActInspection(True, [], ["ap1"])
    out = runner.run(deps)
    assert out.waiting == "approval" and names(deps)[-2:] == ["act", "wait_approval"]
    deps2 = FakeDeps(cid)
    out2 = runner.run(deps2)
    assert out2.waiting is None
    assert names(deps2)[:3] == ["graph", "wait_approval", "act"]
    assert "Approval granted: Buy" in deps2.acts[0].carry


def test_a_task_never_parks_more_than_five_times(runner, company):
    cid = company["id"]
    deps = FakeDeps(cid)
    deps.inspection = ActInspection(True, [], ["ap1"])
    waits = 0
    for _ in range(8):
        out = runner.run(deps)
        if out.waiting is None:
            break
        waits += 1
        deps = FakeDeps(cid)
        deps.inspection = ActInspection(True, [], ["ap1"])
    assert waits == 5  # then the pending approval no longer parks the task


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ('{"pass": true, "feedback": ""}', "passed"),
        ('Sure!\n```json\n{"pass": false, "feedback": "Missing X"}\n```', "failed"),
        ('{"pass": false}', "failed"),
        ('{"pass": "yes"}', "inconclusive"),
        ("looks good", "inconclusive"),
        ("", "inconclusive"),
        (None, "inconclusive"),
    ],
)
def test_parse_verdict(text, status):
    verdict = parse_verdict(text)
    assert verdict["status"] == status
    if status == "failed":
        assert verdict["feedback"]
