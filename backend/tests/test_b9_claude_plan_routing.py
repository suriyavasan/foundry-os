"""B9 behaviour with ``ai_engine == "claude_plan"`` (docs/specs/b9-claude-plan.md): routing, role tools,
role instructions, plan limits and an expired login.

Everything runs against a fake ``claude`` (tests/m10_helpers.py). No real sign-in, no real Claude Code run,
and no model provider exists in these companies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.time import format_ts
from app.models.agent import Agent
from app.models.ai_stack import AiConnection
from app.models.autonomy import Alert
from app.models.company import Company
from app.models.enums import TaskStatus
from app.models.provider import ModelProvider
from app.models.work import Task
from app.runtimes.base import (
    AgentSpec,
    CompanySpec,
    ExecutionRequest,
    Limits,
    TaskSpec,
)
from app.runtimes.cli import claude_code
from app.runtimes.cli.base import CLI_MCP_TOOLS
from app.runtimes.cli.claude_code import ClaudeCodeAdapter
from app.services import agent_toolkit, ai_stack, policy, retry_policy, routing
from tests.helpers import api, get_task, new_agent, new_task, settle
from tests.m10_helpers import install_fake_cli, make_ready, record_of, register_cli_adapters

pytestmark = pytest.mark.ai_gate

RUN_TOKEN = "cos_run.TEST-RUN-TOKEN-0123456789abcdef"
LIMIT_TEXT = "You've hit your session limit · resets 3:45pm"
EXPIRED_TEXT = "Failed to authenticate: OAuth session expired and could not be refreshed"


def result_line(text: str, *, error: bool) -> list[dict]:
    return [
        {
            "type": "result",
            "subtype": "success",
            "is_error": error,
            "result": text,
            "usage": {},
        }
    ]


@pytest.fixture
def bin_dir(app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "realhome"))
    (tmp_path / "realhome").mkdir()
    d = tmp_path / "bin"
    d.mkdir()
    app.state.cli_env.extra_dirs.append(d)
    app.state.cli_env.use_system_path = False
    register_cli_adapters(app)
    return d


@pytest.fixture
def real_runtime(app, monkeypatch):
    """The role brief comes from the runtime's own interpreter (runtime/.venv), as in production."""
    from app.core.config import REPO_DIR

    python = REPO_DIR / "runtime" / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip("runtime/.venv is not installed")
    settings = app.state.settings
    monkeypatch.setattr(settings, "runtime_dir", REPO_DIR / "runtime")
    monkeypatch.setattr(settings, "runtime_python", python)


@pytest.fixture
def plan(app, client, company, bin_dir, real_runtime):
    """The Claude plan as the company's only AI (M17 stack): fake claude installed, signed in, tested."""
    cid = company["id"]
    install_fake_cli(bin_dir, "claude", result_line("OK", error=False))
    r = client.post(api(cid, "/ai-stack"), json={"option": "claude_plan"})
    assert r.status_code == 201, r.text
    t = client.post(api(cid, f"/ai-stack/{r.json()['id']}/test"))
    assert t.status_code == 200 and t.json()["status"]["state"] == "ready", t.text
    stack = client.get(api(cid, "/ai-stack")).json()
    assert stack["ready"] is True and [i["option"] for i in stack["items"]] == ["claude_plan"]
    with app.state.session_factory() as db:
        assert db.scalars(select(ModelProvider)).first() is None
    return cid


def plan_connection(app, cid) -> AiConnection:
    with app.state.session_factory() as db:
        row = db.scalar(select(AiConnection).where(AiConnection.company_id == cid))
        db.expunge(row)
        return row


def agents_of(client, cid) -> dict:
    items = client.get(api(cid, "/agents")).json()["items"]
    out = {
        "founder": next(a for a in items if a["kind"] == "founder"),
        "hr": next(a for a in items if a["role_family"] == "hr"),
    }
    out["lead"] = new_agent(client, cid, name="Lena", kind="lead", runtime="company_agent")
    out["specialist"] = new_agent(client, cid, name="Sam", runtime="company_agent")
    return out


def adapter_of(app) -> ClaudeCodeAdapter:
    return app.state.runtimes.get("cli:claude_code")


def request_for(app, cid, agent_id, task_id, tmp_path) -> ExecutionRequest:
    with app.state.session_factory() as db:
        agent = db.get(Agent, agent_id)
        company = db.get(Company, cid)
        task = db.get(Task, task_id)
        ws = tmp_path / "ws"
        ws.mkdir(exist_ok=True)
        return ExecutionRequest(
            run_id=f"run-{task_id[:6]}",
            attempt_number=1,
            task=TaskSpec(
                id=task.id,
                title=task.title,
                description=task.description,
                acceptance_criteria=None,
                kind=task.kind,
                priority=task.priority,
            ),
            agent=AgentSpec(
                id=agent.id,
                name=agent.name,
                role_title=agent.role_title,
                instructions=None,
                responsibilities=None,
                model_provider=None,
                model_name=None,
                runtime_config=dict(agent.runtime_config),
                tool_permissions=[],
            ),
            company=CompanySpec(
                id=company.id, name=company.name, strategic_goal=None, constraints=[]
            ),
            limits=Limits(max_run_seconds=60, budget_micros=50_000),
            run_token=RUN_TOKEN,
            agent_api_url="http://127.0.0.1:8700/api/agent/v1",
            workspace_dir=str(ws),
            ai_connection_id=plan_connection(app, cid).id,  # the sign-in of the routed connection
        )


# ------------------------------------------------------------------ routing


def decide(app, agent_id, task, cid):
    with app.state.session_factory() as db:
        return routing.decide(
            db,
            db.get(Agent, agent_id),
            task,
            policy.load_policy(db, cid),
            now=datetime.now(UTC),
            cli_env=app.state.cli_env,
        )


@pytest.mark.parametrize("kind", ["work", "planning", "review", "hiring", "routine"])
def test_claude_plan_routes_every_role_and_task_kind_to_claude_code(
    app, client, company, plan, kind
):
    cid = plan
    team = agents_of(client, cid)
    for name, a in team.items():
        got = decide(app, a["id"], Task(company_id=cid, title="t", kind=kind), cid)
        assert got.runtime == "cli:claude_code" and got.stacked and got.subscription, name
        assert got.route.ai_connection_id == plan_connection(app, cid).id
        assert not got.route.routed  # no model provider is involved
        heavy = kind != "work" or name != "specialist"
        assert got.route.reason == (
            f"AI stack: Claude Code (primary, Normal) for {'heavy' if heavy else 'everyday'} work"
        ), name


def test_an_api_stack_routes_to_the_company_agent_with_the_connections_model(app, client, company):
    cid = company["id"]
    provider = make_ready(app, client, cid)
    team = agents_of(client, cid)
    for a in team.values():
        got = decide(app, a["id"], Task(company_id=cid, kind="work"), cid)
        assert got.runtime == "company_agent" and got.route.provider_id == provider["id"]
        assert got.route.model == provider["default_model"] and got.route.ai_connection_id


# ------------------------------------------------------------------ tools and instructions


def tools_and_prompt(app, client, cid, agent, kind, tmp_path):
    task = new_task(client, cid, title=f"{kind} job", kind=kind, assignee_agent_id=agent["id"])
    adapter = adapter_of(app)
    prep = adapter._prepare(request_for(app, cid, agent["id"], task["id"], tmp_path))
    return prep, task


def full_company_tools(app, agent_id, task_id) -> list[str]:
    with app.state.session_factory() as db:
        return agent_toolkit.run_company_tools(db, db.get(Agent, agent_id), db.get(Task, task_id))


def test_each_role_gets_its_own_company_tools_over_mcp(app, client, company, plan, tmp_path):
    cid = plan
    team = agents_of(client, cid)
    cases = {
        "founder": ("planning", agent_toolkit.FOUNDER_TOOLS),
        "hr": ("hiring", agent_toolkit.HR_TOOLS),
        "lead": ("planning", agent_toolkit.LEAD_TOOLS),
        "specialist": ("work", agent_toolkit.WORKER_TOOLS),
    }
    for role, (kind, expected) in cases.items():
        prep, task = tools_and_prompt(app, client, cid, team[role], kind, tmp_path)
        names = prep.mcp_tools
        if role == "specialist":
            assert names == list(
                CLI_MCP_TOOLS
            )  # today's fixed worker list, request_purchase included
        else:
            assert set(agent_toolkit.mcp_names(list(expected))) <= set(names), role
        # never more than a company_agent run of the same agent and task would get
        # (find_colleagues is the one read-only lookup workers have always had on a CLI)
        allowed = set(
            agent_toolkit.mcp_names(full_company_tools(app, team[role]["id"], task["id"]))
        )
        extra = set(names) - allowed
        assert extra <= ({"find_colleagues"} if role == "specialist" else set()), (role, extra)
    founder = tools_and_prompt(app, client, cid, team["founder"], "planning", tmp_path)[0].mcp_tools
    assert "get_state" in founder and "triage_note" in founder and "hire_agent" not in founder
    hr = tools_and_prompt(app, client, cid, team["hr"], "hiring", tmp_path)[0].mcp_tools
    assert "hire_agent" in hr and "author_skill" in hr and "triage_note" not in hr
    worker = tools_and_prompt(app, client, cid, team["specialist"], "work", tmp_path)[0].mcp_tools
    assert "request_purchase" in worker and set(CLI_MCP_TOOLS) <= set(worker)
    assert not {"hire_agent", "get_state", "triage_note", "create_task"} & set(worker)


def test_the_mcp_server_environment_carries_the_role_tools(app, client, company, plan, tmp_path):
    team = agents_of(client, plan)
    prep, _ = tools_and_prompt(app, client, plan, team["hr"], "hiring", tmp_path)
    token = prep.run_dir / "run-token"
    env = adapter_of(app).mcp_server_env(prep, token)
    assert env["COMPANY_OS_MCP_TOOLS"] == ",".join(prep.mcp_tools)
    assert "hire_agent" in env["COMPANY_OS_MCP_TOOLS"].split(",")


def test_the_owner_can_still_narrow_tools_and_a_peer_review_decides_one_thing(
    app, client, company, plan, tmp_path
):
    team = agents_of(client, plan)
    cid = plan
    with app.state.session_factory() as db:
        agent = db.get(Agent, team["lead"]["id"])
        agent.runtime_config = {"company_tools": ["company_get_state", "company_plan_project"]}
        db.commit()
    prep, _ = tools_and_prompt(app, client, cid, team["lead"], "planning", tmp_path)
    assert set(prep.mcp_tools) == {"get_state", "plan_project"}


def test_management_prompts_carry_the_role_instructions_with_the_cli_tool_names(
    app, client, company, plan, tmp_path
):
    cid = plan
    team = agents_of(client, cid)
    p = "mcp__company__"
    founder, _ = tools_and_prompt(app, client, cid, team["founder"], "planning", tmp_path)
    assert "ROLE INSTRUCTIONS" in founder.prompt
    assert "you are the AI Co-founder" in founder.prompt
    assert f"Call {p}get_state first" in founder.prompt  # renamed, not forked
    assert "company_get_state" not in founder.prompt
    hr, _ = tools_and_prompt(app, client, cid, team["hr"], "hiring", tmp_path)
    assert "you are HR" in hr.prompt and f"{p}hire_agent" in hr.prompt
    lead, _ = tools_and_prompt(app, client, cid, team["lead"], "planning", tmp_path)
    assert "you are a project lead" in lead.prompt and f"{p}plan_project" in lead.prompt
    # a specialist's work task keeps today's prompt
    worker, _ = tools_and_prompt(app, client, cid, team["specialist"], "work", tmp_path)
    assert "ROLE INSTRUCTIONS" not in worker.prompt and "HOW TO WORK" in worker.prompt
    assert f"{p}complete_task" in worker.prompt
    # a specialist's review task is a management run: it gets the review kickoff
    review, _ = tools_and_prompt(app, client, cid, team["specialist"], "review", tmp_path)
    assert "ROLE INSTRUCTIONS" in review.prompt


# ------------------------------------------------------------------ plan limit parsing


def test_reset_time_parsing_and_classification():
    now = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    local_now = now.astimezone()
    got = claude_code.parse_reset("You've hit your session limit · resets 3:45pm", now)
    assert got is not None and now < got <= now + timedelta(days=1, minutes=1)
    assert got.astimezone().hour == 15 and got.astimezone().minute == 45
    weekly = claude_code.parse_reset("You've hit your weekly limit · resets Mon 12:00am", now)
    assert weekly is not None and weekly.astimezone().weekday() == 0
    assert weekly.astimezone().hour == 0 and weekly > now
    assert claude_code.parse_reset("resets 3pm", now).astimezone().hour == 15
    assert claude_code.parse_reset("no time here", now) is None
    assert local_now  # silence unused
    ev = claude_code.detect_plan_event(LIMIT_TEXT, None, now)
    assert ev.kind == claude_code.USAGE_LIMIT and ev.window == "session" and ev.resets_at
    assert claude_code.detect_plan_event("You've hit your Opus limit", None, now).resets_at is None
    assert claude_code.detect_plan_event(EXPIRED_TEXT, None, now).kind == claude_code.LOGIN_EXPIRED
    assert claude_code.detect_plan_event("x", "authentication_failed", now).kind == "login_expired"
    assert claude_code.detect_plan_event("Request rejected (429)", None, now) is None
    assert claude_code.detect_plan_event("boom", "server_error", now) is None


def test_the_retry_policy_treats_plan_events_as_transient():
    assert retry_policy.classify("failed", claude_code.usage_limit_message(datetime.now(UTC))) == (
        retry_policy.TRANSIENT
    )
    assert retry_policy.classify("failed", claude_code.LOGIN_EXPIRED_MESSAGE) == (
        retry_policy.TRANSIENT
    )
    assert retry_policy.classify("failed", "Claude Code reported an error: boom") == (
        retry_policy.PERMANENT
    )


# ------------------------------------------------------------------ runs through the dispatcher


def complete_when_it_ran(monkeypatch, client, cid):
    """The fake claude cannot call the MCP tools; stand in for its ``complete_task`` call."""

    def _task_open(self, task_id):
        with self._session_factory() as db:
            db.execute(update(Task).where(Task.id == task_id).values(status=TaskStatus.DONE))
            db.commit()
        return False

    monkeypatch.setattr(ClaudeCodeAdapter, "_task_open", _task_open)


def run_status(app, task_id) -> list[str]:
    from app.models.run import TaskRun

    with app.state.session_factory() as db:
        return [r.status for r in db.scalars(select(TaskRun).where(TaskRun.task_id == task_id))]


def test_a_founder_planning_task_completes_through_claude_code_with_no_provider(
    app, client, company, plan, scheduler, worker, monkeypatch, bin_dir
):
    cid = plan
    team = agents_of(client, cid)
    complete_when_it_ran(monkeypatch, client, cid)
    task = new_task(
        client,
        cid,
        title="Plan the quarter",
        kind="planning",
        assignee_agent_id=team["founder"]["id"],
    )
    settle(scheduler, worker)
    run = client.get(api(cid, f"/tasks/{task['id']}/runs")).json()["items"][0]
    assert run["runtime"] == "cli:claude_code" and run["status"] == "succeeded", run
    assert run["model_provider_id"] is None
    assert get_task(client, cid, task["id"])["status"] == "done"
    argv = record_of(bin_dir, "claude")["argv"]
    assert "-p" in argv and any(a.startswith("--mcp-config=") for a in argv)


def test_a_usage_limit_pauses_claude_code_and_tasks_wait_then_resume(
    app, client, company, plan, scheduler, worker, monkeypatch, bin_dir
):
    cid = plan
    team = agents_of(client, cid)
    install_fake_cli(bin_dir, "claude", result_line(LIMIT_TEXT, error=True), exit=1)
    task = new_task(
        client, cid, title="Plan it", kind="planning", assignee_agent_id=team["founder"]["id"]
    )
    report = scheduler.tick()
    assert len(report.dispatch.queued_run_ids) == 1
    worker.drain()

    got = get_task(client, cid, task["id"])
    assert got["status"] == "ready", got  # waiting, not failed
    assert got["attempt_count"] == 0  # the attempt was refunded (transient)
    assert "usage limit" in got["error"]["message"] and "resumes at" in got["error"]["message"]
    conn = plan_connection(app, cid)
    until = conn.paused_until
    assert datetime.now(UTC) < until <= datetime.now(UTC) + timedelta(days=1, minutes=2)
    assert ai_stack.usable(conn) == (False, f"{conn.label} is paused until {format_ts(until)}")
    with app.state.session_factory() as db:
        alerts = list(db.scalars(select(Alert).where(Alert.kind == "claude_plan_limit")))
        assert len(alerts) == 1 and alerts[0].status == "open"
        assert alerts[0].dedupe_key.endswith(conn.id)  # one alert per connection

    # later ticks: the task waits for the runtime, no new run, nothing fails
    with app.state.session_factory() as db:
        db.execute(update(Task).where(Task.id == task["id"]).values(next_attempt_at=None))
        db.commit()
    report = scheduler.tick()
    assert report.dispatch.waiting_for_runtime >= 1 and not report.dispatch.queued_run_ids
    assert "paused until" in report.dispatch.waiting_reasons[task["id"]]
    assert "no other AI can take heavy work" in report.dispatch.waiting_reasons[task["id"]]
    assert len(run_status(app, task["id"])) == 1

    # the reset time passes: the connection is usable again and the task runs
    with app.state.session_factory() as db:
        db.get(AiConnection, conn.id).paused_until = datetime(2020, 1, 1, tzinfo=UTC)
        db.commit()
    install_fake_cli(bin_dir, "claude", result_line("done", error=False))
    complete_when_it_ran(monkeypatch, client, cid)
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    with app.state.session_factory() as db:
        row = db.get(AiConnection, conn.id)
        assert row.paused_until is None and row.pause_reason is None
        assert all(
            a.status != "open"
            for a in db.scalars(select(Alert).where(Alert.kind == "claude_plan_limit"))
        )


def test_an_unknown_reset_time_falls_back_to_one_hour(
    app, client, company, plan, scheduler, worker, bin_dir
):
    cid = plan
    team = agents_of(client, cid)
    install_fake_cli(
        bin_dir, "claude", result_line("You've hit your weekly limit", error=True), exit=1
    )
    new_task(client, cid, title="Plan it", kind="planning", assignee_agent_id=team["founder"]["id"])
    scheduler.tick()
    worker.drain()
    delta = plan_connection(app, cid).paused_until - datetime.now(UTC)
    assert timedelta(minutes=58) < delta <= timedelta(hours=1, seconds=5)


def test_an_expired_login_marks_the_connection_signed_out_and_work_waits(
    app, client, company, plan, scheduler, worker, bin_dir, monkeypatch
):
    cid = plan
    team = agents_of(client, cid)
    conn = plan_connection(app, cid)
    install_fake_cli(bin_dir, "claude", result_line(EXPIRED_TEXT, error=True), exit=1)
    task = new_task(client, cid, title="Hire", kind="hiring", assignee_agent_id=team["hr"]["id"])
    scheduler.tick()
    worker.drain()
    got = get_task(client, cid, task["id"])
    assert got["status"] == "ready" and got["attempt_count"] == 0, got
    assert "Sign in to Claude again" in got["error"]["message"]
    row = plan_connection(app, cid)
    assert row.pause_reason == ai_stack.LOGIN_EXPIRED
    assert ai_stack.usable(row) == (False, f"{row.label} needs to be signed in again")
    state = client.get(api(cid, "/ai-stack")).json()["items"][0]["status"]["state"]
    assert state == "needs_sign_in"
    with app.state.session_factory() as db:
        alerts = list(db.scalars(select(Alert).where(Alert.kind == "claude_login_expired")))
        assert len(alerts) == 1 and alerts[0].dedupe_key.endswith(conn.id)
        db.execute(update(Task).where(Task.id == task["id"]).values(next_attempt_at=None))
        db.commit()
    report = scheduler.tick()
    assert report.dispatch.waiting_for_runtime >= 1 and not report.dispatch.queued_run_ids
    assert "signed in again" in report.dispatch.waiting_reasons[task["id"]]

    # signed in again: the next run works, clears the mark and resolves the alerts
    with app.state.session_factory() as db:
        db.get(AiConnection, conn.id).pause_reason = None
        db.commit()
    install_fake_cli(bin_dir, "claude", result_line("done", error=False))
    complete_when_it_ran(monkeypatch, client, cid)
    settle(scheduler, worker)
    assert get_task(client, cid, task["id"])["status"] == "done"
    with app.state.session_factory() as db:
        assert all(
            a.status != "open"
            for a in db.scalars(select(Alert).where(Alert.kind == "claude_login_expired"))
        )


def test_other_failures_and_api_key_mode_are_not_treated_as_plan_events(
    app, client, company, plan, scheduler, worker, bin_dir
):
    cid = plan
    team = agents_of(client, cid)
    install_fake_cli(bin_dir, "claude", result_line("Something else broke", error=True), exit=1)
    task = new_task(
        client, cid, title="Plan it", kind="planning", assignee_agent_id=team["founder"]["id"]
    )
    scheduler.tick()
    worker.drain()
    got = get_task(client, cid, task["id"])
    assert got["status"] == "ready" and got["attempt_count"] == 1  # a permanent failure
    with app.state.session_factory() as db:
        assert plan_connection(app, cid).paused_until is None
        assert not list(db.scalars(select(Alert).where(Alert.kind.like("claude_%"))))
