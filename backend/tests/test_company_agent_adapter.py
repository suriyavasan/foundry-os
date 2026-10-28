"""CompanyAgentAdapter against a fake launcher (tests/fake_launcher.py) that reads the real spec
from stdin and emits scripted JSONL: success, failure, partial, cancel, timeouts, malformed
lines, seq collisions, environment hygiene, cost mapping and the full engine pipeline."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.owner_token import OWNER_TOKEN_FILENAME
from app.core.time import utcnow
from app.engine.context import build_context
from app.engine.executor import DbEventSink
from app.engine.scheduler import Scheduler
from app.engine.worker import Worker
from app.models.run import RunEvent, TaskRun
from app.runtimes.base import (
    AgentSpec,
    CancelToken,
    CompanySpec,
    CostKind,
    ExecutionEvent,
    ExecutionRequest,
    HealthStatus,
    Limits,
    RuntimeAdapterError,
    RuntimeErrorKind,
    TaskSpec,
)
from app.runtimes.company_agent import SERVER_SEQ_FLOOR, CompanyAgentAdapter
from app.runtimes.registry import RuntimeRegistry
from app.runtimes.sandbox import DockerStatus
from tests.helpers import api, get_task, new_task, seed_library, set_policy, settle, wait_until

RUN_TOKEN = "cos_run.fake-run-token-0123456789abcdef.signature"
CLOUD_KEY = "sk-cloud-secret-key-0123456789abcdef"


@pytest.fixture
def skills_env_override(fake_skills_env):
    return fake_skills_env


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)

    @property
    def seqs(self) -> list[int]:
        return [e.seq for e in self.events]

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.events]


@pytest.fixture
def fake_runtime_dir(tmp_path: Path) -> Path:
    root = tmp_path / "fake-launcher"
    pkg = root / "company_agent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    shutil.copy(Path(__file__).parent / "fake_launcher.py", pkg / "run.py")
    return root


class Env(SimpleNamespace):
    def request(self, title: str, **overrides) -> ExecutionRequest:
        workspace = self.tmp / "ws" / f"{abs(hash(title)) % 10**8}"
        workspace.mkdir(parents=True, exist_ok=True)
        base = dict(
            run_id="run-" + title.replace(" ", "-")[:30],
            attempt_number=1,
            task=TaskSpec("task-1", title, "desc", "criteria", "work", "normal", ["ref-1"]),
            agent=AgentSpec(self.agent["id"], "A", "R", None, None, None, None, {}, []),
            company=CompanySpec(self.cid, "Acme", None, []),
            limits=Limits(max_run_seconds=30, budget_micros=None),
            run_token=RUN_TOKEN,
            agent_api_url="http://127.0.0.1:1/api/agent/v1",
            workspace_dir=str(workspace),
        )
        base.update(overrides)
        return ExecutionRequest(**base)

    def execute(self, title: str, cancel: CancelToken | None = None, **kw):
        sink = RecordingSink()
        result = self.adapter.execute(self.request(title, **kw), sink, cancel or CancelToken())
        return result, sink


@pytest.fixture
def env(make_settings, fake_runtime_dir, tmp_path, skills_env_override):
    settings = make_settings(
        runtime_dir=fake_runtime_dir,
        runtime_python=Path(sys.executable),
        runtime_kill_grace_seconds=0.6,
    )
    # The shared `app` fixture was built with default settings: rebuild around ours.
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.helpers import login

    registry = RuntimeRegistry()
    the_app = create_app(settings, runtime_registry=registry, skills_env=skills_env_override)
    adapter = CompanyAgentAdapter(settings, the_app.state.session_factory, the_app.state.keys)
    adapter._docker = SimpleNamespace(
        status=lambda: DockerStatus(False, "docker daemon not reachable")
    )
    registry.register(adapter)
    with TestClient(the_app) as c:
        login(c)
        cid = c.post("/api/companies", json={"name": "Acme", "mission": "Ship"}).json()["id"]
        provider = c.post(
            api(cid, "/model-providers"),
            json={"name": "Local", "kind": "ollama", "default_model": "qwen2.5:7b"},
        ).json()
        agents = {a["name"]: a for a in c.get(api(cid, "/agents")).json()["items"]}
        yield Env(
            app=the_app,
            client=c,
            adapter=adapter,
            settings=settings,
            cid=cid,
            provider=provider,
            agent=agents["Harper"],  # founder/HR got company_agent + the provider (see test below)
            agents=agents,
            tmp=tmp_path,
            registry=registry,
        )


# ------------------------------------------------------------------ health + config


def test_health_ok_reports_sandbox_none_when_docker_is_down(env):
    health = env.adapter.check_health()
    assert health.status == HealthStatus.OK
    assert "protocol 1" in health.detail
    assert "sandbox: none" in health.detail and "docker daemon not reachable" in health.detail
    assert "terminal and code_execution are never granted" in health.detail


def test_health_not_configured_without_interpreter(make_settings, app, tmp_path):
    settings = make_settings(runtime_dir=tmp_path / "x", runtime_python=tmp_path / "missing/python")
    adapter = CompanyAgentAdapter(settings, app.state.session_factory, app.state.keys)
    health = adapter.check_health()
    assert (
        health.status == HealthStatus.NOT_CONFIGURED
        and "COMPANY_OS_RUNTIME_PYTHON" in health.detail
    )


def test_health_unavailable_on_failing_or_mismatched_launcher(env, fake_runtime_dir):
    run = fake_runtime_dir / "company_agent" / "run.py"
    run.write_text("import sys\nsys.exit(1)\n")
    env.adapter._health_cache = None
    assert env.adapter.check_health().status == HealthStatus.UNAVAILABLE
    run.write_text('import json\nprint(json.dumps({"company_agent": "x", "protocol": 99}))\n')
    env.adapter._health_cache = None
    health = env.adapter.check_health()
    assert health.status == HealthStatus.UNAVAILABLE and "protocol" in health.detail


def test_capabilities_are_declared_honestly(env):
    caps = {c.value for c in env.adapter.capabilities}
    assert caps == {"streaming_events", "cancel", "usage_tokens", "usage_cost", "artifacts"}
    assert env.adapter.supports_execution is True


def test_validate_config(env):
    assert env.adapter.validate_config({}) == []
    bad = env.adapter.validate_config({"max_iterations": 0, "toolsets": "file", "mystery": 1})
    fields = {(i.field, i.severity) for i in bad}
    assert fields == {("max_iterations", "error"), ("toolsets", "error"), ("mystery", "warning")}


# ------------------------------------------------------------------ outcomes


def test_success_streams_events_with_the_launchers_seq_and_maps_usage(env):
    result, sink = env.execute("t scenario:success")
    assert result.status == "succeeded" and result.summary == "all done"
    assert sink.seqs == [1, 2, 3, 4, 5, 6]
    assert sink.types == [
        "run.started",
        "message",
        "tool.started",
        "tool.completed",
        "usage",
        "run.completed",
    ]
    # tokens: uncached input + cache reads = prompt tokens
    assert (result.usage.input_tokens, result.usage.output_tokens) == (500, 50)
    # local provider: no provider charge -> $0 *verified*, and the payload says so
    assert (result.usage.cost_micros, result.usage.cost_kind) == (0, CostKind.VERIFIED)
    billing = sink.events[-1].payload["billing"]
    assert billing["cost_micros"] == 0 and billing["cost_kind"] == "verified"
    assert "no provider charge" in billing["note"].lower() and billing["local"] is True
    assert sink.events[4].payload["billing"]["cost_kind"] == "verified"  # the usage event too


def test_failure_maps_to_failed_and_scrubs_secrets(env):
    result, sink = env.execute("t scenario:failure")
    assert result.status == "failed"
    assert result.error.kind == RuntimeErrorKind.FAILED and result.error.retryable
    assert "boom" in result.error.message
    assert result.usage.cost_kind == CostKind.VERIFIED  # tokens were burned locally: still $0


@pytest.mark.parametrize(
    "scenario, kind",
    [
        ("partial", RuntimeErrorKind.FAILED),
        ("not_completed", RuntimeErrorKind.FAILED),
        ("time_limit", RuntimeErrorKind.TIMEOUT),
    ],
)
def test_partial_and_limit_outcomes_are_retryable_failures(env, scenario, kind):
    result, sink = env.execute(f"t scenario:{scenario}")
    assert result.status == "failed" and result.error.kind == kind and result.error.retryable
    assert sink.types[-1] == "run.failed"


def test_no_terminal_event_is_a_failure_with_redacted_stderr(env):
    result, sink = env.execute("t scenario:no_terminal")
    assert result.status == "failed"
    assert (
        "exited with code 7" in result.error.message and "without a result" in result.error.message
    )
    assert "Traceback" in result.error.message
    assert "no_terminal" not in result.error.message


def test_cancel_sends_sigterm_and_the_launcher_stops_cleanly(env):
    cancel = CancelToken()
    holder = {}

    def run():
        holder["out"] = env.execute("t scenario:cancel", cancel)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(1.0)  # let the fake start and install its handler
    started = time.monotonic()
    cancel.set()
    t.join(timeout=10)
    assert not t.is_alive()
    result, sink = holder["out"]
    assert result.status == "cancelled"
    assert sink.types[-1] == "run.cancelled"
    assert time.monotonic() - started < 5  # SIGTERM honoured, no SIGKILL grace needed


def test_a_launcher_that_ignores_sigterm_is_killed_after_the_grace_period(env):
    cancel = CancelToken()
    holder = {}
    t = threading.Thread(
        target=lambda: holder.update(out=env.execute("t scenario:ignore_term", cancel))
    )
    t.start()
    time.sleep(1.0)
    started = time.monotonic()
    cancel.set()
    t.join(timeout=15)
    assert not t.is_alive()
    elapsed = time.monotonic() - started
    assert 0.5 <= elapsed < 8  # grace is 0.6 s in this fixture
    result, _ = holder["out"]
    assert result.status == "cancelled"


def test_grandchildren_die_with_the_process_group(env):
    cancel = CancelToken()
    request = env.request("t scenario:child")
    holder = {}
    sink = RecordingSink()
    t = threading.Thread(
        target=lambda: holder.update(out=env.adapter.execute(request, sink, cancel))
    )
    t.start()
    pid_file = Path(request.workspace_dir) / "child.pid"
    wait_until(
        lambda: pid_file.exists() and pid_file.read_text().strip(),
        timeout=10,
        what="the fake to spawn its child",
    )
    child = int(pid_file.read_text())
    os.kill(child, 0)  # alive
    cancel.set()
    t.join(timeout=15)
    wait_until(lambda: not _alive(child), timeout=5, what="the grandchild to be killed")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill(0); check the state
    try:
        import subprocess

        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        return bool(state) and not state.startswith("Z")
    except OSError:
        return True


def test_malformed_lines_are_dropped_and_the_run_still_succeeds(env):
    result, sink = env.execute("t scenario:malformed")
    assert result.status == "succeeded" and result.summary == "survived garbage"
    # garbage, non-objects, missing/odd seqs, unknown types and the 1.1 MB line are skipped; a valid
    # envelope with a non-object payload is kept with an empty payload.
    assert sink.seqs == [1, 2, 3, 4]
    assert sink.types == ["run.started", "message", "message", "run.completed"]
    assert sink.events[2].payload == {}
    assert "after terminal" not in json.dumps([e.payload for e in sink.events])


def test_seq_collisions_never_reach_the_event_log(env):
    result, sink = env.execute("t scenario:collide")
    assert result.status == "succeeded"
    assert sink.seqs == [1, 2, 3, 4]  # duplicates, replays and server-range seqs dropped
    assert sink.events[1].payload["text"] == "second"  # first one wins
    assert max(sink.seqs) < SERVER_SEQ_FLOOR


def test_secrets_are_scrubbed_from_every_event(env):
    result, sink = env.execute("t scenario:leak")
    blob = json.dumps([e.payload for e in sink.events]) + (result.summary or "")
    assert RUN_TOKEN not in blob
    assert "[redacted]" in blob


# ------------------------------------------------------------------ what the process is given


def _inspect(env, title="t scenario:inspect", **kw):
    result, sink = env.execute(title, **kw)
    assert result.status == "succeeded", result
    return next(e.payload for e in sink.events if e.type == "progress")


def test_process_environment_is_minimal_and_has_no_owner_credentials(env, monkeypatch):
    owner_token = env.app.state.owner_token.get()
    secret_key = env.app.state.keys.get().decode()
    monkeypatch.setenv("COMPANY_OS_SECRET_KEY", secret_key)
    monkeypatch.setenv("COMPANY_OS_DATABASE_URL", "sqlite:///leak.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-0123456789")
    monkeypatch.setenv("HERMES_API_KEY", "hermes-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-should-not-leak")
    seen = _inspect(env)
    keys = set(seen["env_keys"])
    assert not {k for k in keys if k.startswith(("COMPANY_OS_", "HERMES_API", "OPENAI", "AWS_"))}
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "HOME",
        "HERMES_HOME",
        "HERMES_DISABLE_LAZY_INSTALLS",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "__CF_USER_TEXT_ENCODING",  # macOS adds this one itself
    }
    assert keys <= allowed | {
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }, keys
    # nothing secret anywhere in the process arguments / environment values
    assert seen["argv"][1:] == []  # `-m company_agent.run` -> argv has no secrets or spec
    blob = json.dumps(seen)
    for secret in (owner_token, secret_key, RUN_TOKEN, "should-not-leak"):
        assert secret not in blob
    assert seen["has_api_key"] is True  # the key travels in the stdin spec only
    assert seen["cwd"] == str(env.settings.resolved_runtime_dir)


def test_home_is_per_agent_and_outside_the_workspace_and_the_data_root_files(env):
    seen = _inspect(env)
    data = env.settings.resolved_data_dir
    assert seen["hermes_home"] == str(data / "agents" / env.agent["id"] / "home")
    assert seen["home_env"] == str(Path(seen["hermes_home"]) / "home")
    assert seen["hermes_home_env"] == seen["hermes_home"]
    assert not Path(seen["workspace_dir"]).is_relative_to(seen["hermes_home"])
    # the control plane's secrets sit directly in the data dir, above both
    assert (data / OWNER_TOKEN_FILENAME).exists()
    assert not (data / OWNER_TOKEN_FILENAME).is_relative_to(seen["workspace_dir"])


def test_spec_carries_the_project_workspace_only_for_a_project_or_review_run(env):
    plain = _inspect(env)
    assert plain["workspace"] is None  # a plain per-run directory sends nothing
    files = [f"src/f{i}.py" for i in range(75)]
    seen = _inspect(env, "t scenario:inspect p", workspace_kind="project", workspace_files=files)
    assert seen["workspace"]["kind"] == "project" and seen["workspace"]["project_name"] is None
    assert seen["workspace"]["files"] == files[:60]  # at most 60 paths
    review = _inspect(
        env, "t scenario:inspect r", workspace_kind="review", workspace_files=["index.html"]
    )
    assert review["workspace"] == {"kind": "review", "project_name": None, "files": ["index.html"]}


def test_the_workspace_names_the_project_of_the_task(env, engine):
    """Through the engine, the spec holds the real project name and the files of the shared repo."""
    import shutil as _shutil

    if _shutil.which("git") is None:
        pytest.skip("git is not installed")
    project = env.client.post(
        api(env.cid, "/projects"), json={"name": "Landing page", "status": "active"}
    ).json()
    task = make_task(env, "inspect", project_id=project["id"])
    settle(engine.scheduler, engine.worker)
    run = run_of(env, task["id"])
    progress = next(e for e in events_of(env, run["id"]) if e["type"] == "progress")
    assert progress["payload"]["workspace"] == {
        "kind": "project",
        "project_name": "Landing page",
        "files": [],
    }
    assert run["id"] in progress["payload"]["workspace_dir"]
    assert get_task(env.client, env.cid, task["id"])["status"] == "done"


def test_spec_carries_identity_model_limits_and_org(env):
    seen = _inspect(env)
    assert seen["model"] == {
        "provider": "custom",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "context_length": 32768,
        "is_local": True,
    }
    assert seen["limits"] == {"max_iterations": 30, "max_run_seconds": 30}
    assert seen["agent"]["manager"]["role_title"] == "Founder & CEO" or seen["agent"]["manager"]
    assert seen["task"]["input_refs"] == ["ref-1"]
    assert seen["sandbox"] == {"backend": "none"}


def test_revision_feedback_is_part_of_the_task_description(env):
    request = env.request(
        "t scenario:inspect",
        task=TaskSpec(
            "task-1", "t scenario:inspect", "orig", None, "work", "normal", [], "Add sources"
        ),
    )
    sink = RecordingSink()
    env.adapter.execute(request, sink, CancelToken())
    desc = next(e.payload for e in sink.events if e.type == "progress")["task"]["description"]
    assert "orig" in desc and "REVISION REQUESTED" in desc and "Add sources" in desc


def test_hr_gets_the_hiring_tools_and_workers_get_a_lean_set(env):
    hr = _inspect(env)
    assert (
        "company_hire_agent" in hr["company_tools"]
        and "company_install_skill" in hr["company_tools"]
    )
    assert "company_decide_approval" not in hr["company_tools"]
    # a plain worker
    worker = env.client.post(
        api(env.cid, "/agents"),
        json={"name": "Ada", "role_title": "Analyst", "role_template": "research-analyst"},
    ).json()
    env.client.post(api(env.cid, f"/agents/{worker['id']}/activate"))
    assert worker["runtime"] == "company_agent"  # company default model -> new agents run on it
    request = env.request("t scenario:inspect")
    request = ExecutionRequest(
        **{
            **request.__dict__,
            "agent": AgentSpec(worker["id"], "Ada", "R", None, None, None, None, {}, []),
        }
    )
    sink = RecordingSink()
    env.adapter.execute(request, sink, CancelToken())
    seen = next(e.payload for e in sink.events if e.type == "progress")
    assert seen["company_tools"] == [
        "company_get_context",
        "company_report_progress",
        "company_submit_artifact",
        "company_complete_task",
        "company_update_task",
        "company_create_task",
        "company_request_help",
        "company_propose_memory",
        "company_request_purchase",
        # M16: a research-family employee gets the radar tools (app/radar/access.py)
        "company_radar_digest",
        "company_radar_read",
        "company_radar_brief",
        "company_radar_propose",
        "company_radar_keywords",
        "company_radar_add_source",
        "company_radar_ui_search",
    ]
    assert seen["toolsets"] == ["company", "file", "web", "search", "skills", "todo"]
    assert "skills_manage" not in seen["toolsets"]


def test_terminal_and_code_execution_are_never_granted_without_a_sandbox(env):
    devops = env.client.post(
        api(env.cid, "/agents"),
        json={"name": "Dev", "role_title": "DevOps", "role_template": "devops-engineer"},
    ).json()
    assert {"terminal", "code_execution"} <= set(
        devops["tool_permissions"]
    )  # the role *wants* them
    request = env.request("t scenario:inspect")
    request = ExecutionRequest(
        **{
            **request.__dict__,
            "agent": AgentSpec(devops["id"], "Dev", "R", None, None, None, None, {}, []),
        }
    )
    sink = RecordingSink()
    env.adapter.execute(request, sink, CancelToken())
    seen = next(e.payload for e in sink.events if e.type == "progress")
    assert "terminal" not in seen["toolsets"] and "code_execution" not in seen["toolsets"]
    assert seen["sandbox"] == {"backend": "none"}


def test_docker_sandbox_is_used_when_the_daemon_is_up_and_required_policy_fails_closed(env):
    env.adapter._docker = SimpleNamespace(status=lambda: DockerStatus(True, "docker daemon 27"))
    devops = env.client.post(
        api(env.cid, "/agents"),
        json={"name": "Dev", "role_title": "DevOps", "role_template": "devops-engineer"},
    ).json()
    request = env.request("t scenario:inspect")
    request = ExecutionRequest(
        **{
            **request.__dict__,
            "agent": AgentSpec(devops["id"], "Dev", "R", None, None, None, None, {}, []),
        }
    )
    sink = RecordingSink()
    env.adapter.execute(request, sink, CancelToken())
    seen = next(e.payload for e in sink.events if e.type == "progress")
    assert {"terminal", "code_execution"} <= set(seen["toolsets"])
    assert seen["sandbox"]["backend"] == "docker" and seen["sandbox"]["network"] is False
    # policy "docker" + daemon down = refuse, never silently run unsandboxed
    set_policy(env.client, env.cid, sandbox_backend="docker")
    env.adapter._docker = SimpleNamespace(status=lambda: DockerStatus(False, "down"))
    with pytest.raises(RuntimeAdapterError) as exc:
        env.execute("t scenario:inspect")
    assert exc.value.kind == RuntimeErrorKind.UNAVAILABLE


def test_agent_tool_permissions_restrict_the_toolsets(env):
    worker = env.client.post(
        api(env.cid, "/agents"),
        json={
            "name": "Ada",
            "role_title": "Analyst",
            "role_template": "research-analyst",
            "tool_permissions": ["file", "todo"],
        },
    ).json()
    request = env.request("t scenario:inspect")
    request = ExecutionRequest(
        **{
            **request.__dict__,
            "agent": AgentSpec(worker["id"], "Ada", "R", None, None, None, None, {}, []),
        }
    )
    sink = RecordingSink()
    env.adapter.execute(request, sink, CancelToken())
    seen = next(e.payload for e in sink.events if e.type == "progress")
    assert seen["toolsets"] == ["company", "file", "todo"]  # `company` is always the way home


# ------------------------------------------------------------------ model resolution + cost


def test_no_model_provider_is_a_config_error(env):
    env.client.patch(api(env.cid, f"/agents/{env.agent['id']}"), json={"model_provider": None})
    env.client.patch(
        api(env.cid, f"/model-providers/{env.provider['id']}"), json={"enabled": False}
    )
    with pytest.raises(RuntimeAdapterError) as exc:
        env.execute("t scenario:success")
    assert exc.value.kind == RuntimeErrorKind.CONFIG
    assert "model provider" in exc.value.message.lower()


def _cloud_agent(env, *, prices=True, scenario_key=CLOUD_KEY):
    cred = env.client.post(
        api(env.cid, "/credentials"),
        json={"name": "cloud-key", "kind": "api_key", "value": scenario_key},
    ).json()
    body = {
        "name": "Cloud",
        "kind": "openai_compatible",
        "base_url": "https://llm.example.com/v1",
        "credential_id": cred["id"],
        "default_model": "big-model",
        "context_length": 65536,
    }
    if prices:
        body.update(input_price_per_mtok_micros=3_000_000, output_price_per_mtok_micros=15_000_000)
    provider = env.client.post(api(env.cid, "/model-providers"), json=body).json()
    agent = env.client.post(
        api(env.cid, "/agents"),
        json={
            "name": "Cloudy",
            "role_title": "Analyst",
            "model_provider": provider["id"],
            "runtime": "company_agent",
        },
    ).json()
    return agent


def _as(env, agent, title):
    request = env.request(title)
    request = ExecutionRequest(
        **{
            **request.__dict__,
            "agent": AgentSpec(agent["id"], "A", "R", None, None, None, None, {}, []),
        }
    )
    sink = RecordingSink()
    result = env.adapter.execute(request, sink, CancelToken())
    return result, sink


def test_cloud_cost_provider_reported_is_verified(env):
    result, sink = _as(env, _cloud_agent(env), "t scenario:cloud_actual")
    assert (result.usage.cost_micros, result.usage.cost_kind) == (12_300, CostKind.VERIFIED)


def test_cloud_cost_runtime_estimate_is_estimated_never_verified(env):
    result, _ = _as(env, _cloud_agent(env), "t scenario:cloud_estimated")
    assert (result.usage.cost_micros, result.usage.cost_kind) == (500_000, CostKind.ESTIMATED)


def test_cloud_cost_from_configured_prices_is_an_estimate(env):
    result, _ = _as(env, _cloud_agent(env), "t scenario:cloud_unknown")
    # 500 prompt tokens * $3/M + 50 output * $15/M = 1500 + 750 micros
    assert (result.usage.cost_micros, result.usage.cost_kind) == (2_250, CostKind.ESTIMATED)


def test_cloud_cost_without_any_price_is_unknown(env):
    result, sink = _as(env, _cloud_agent(env, prices=False), "t scenario:cloud_unknown")
    assert result.usage.cost_micros is None and result.usage.cost_kind == CostKind.UNKNOWN
    assert sink.events[-1].payload["billing"]["cost_kind"] == "unknown"


def test_credential_reaches_the_process_through_stdin_and_is_scrubbed_from_events(env):
    agent = _cloud_agent(env)
    result, sink = _as(env, agent, "t scenario:leak")
    assert result.status == "succeeded"
    blob = json.dumps([e.payload for e in sink.events]) + (result.summary or "")
    assert CLOUD_KEY not in blob and RUN_TOKEN not in blob


# ------------------------------------------------------------------ the whole pipeline


@pytest.fixture
def engine(env):
    ctx = build_context(
        env.settings,
        session_factory=env.app.state.session_factory,
        registry=env.registry,
        keys=env.app.state.keys,
        storage=env.app.state.storage,
        skills=env.app.state.skills,
    )
    return SimpleNamespace(
        ctx=ctx,
        scheduler=Scheduler(ctx),
        worker=Worker(ctx, worker_id="t", monitor_interval=0.02, kinds=("execute_run",)),
    )


def make_task(env, scenario: str, **kw):
    return new_task(
        env.client,
        env.cid,
        title=f"job scenario:{scenario}",
        assignee_agent_id=env.agent["id"],
        **kw,
    )


def run_of(env, task_id) -> dict:
    return env.client.get(api(env.cid, f"/tasks/{task_id}/runs")).json()["items"][0]


def events_of(env, run_id) -> list[dict]:
    return env.client.get(api(env.cid, f"/runs/{run_id}/events?limit=200")).json()["items"]


def test_engine_success_end_to_end(env, engine):
    task = make_task(env, "success")
    settle(engine.scheduler, engine.worker)
    done = get_task(env.client, env.cid, task["id"])
    assert done["status"] == "done" and done["result_summary"] == "all done"
    run = run_of(env, task["id"])
    assert run["status"] == "succeeded" and run["exit_reason"] == "completed"
    assert (run["input_tokens"], run["output_tokens"]) == (500, 50)
    assert (run["cost_micros"], run["cost_kind"]) == (0, "verified")
    evs = events_of(env, run["id"])
    assert [e["seq"] for e in evs] == [1, 2, 3, 4, 5, 6]
    assert evs[-1]["type"] == "run.completed" and evs[-1]["payload"]["billing"]["local"] is True
    usage = env.client.get(api(env.cid, "/usage")).json()["totals"]
    assert usage["cost_micros"] == 0 and usage["by_cost_kind"]["verified_micros"] == 0
    assert usage["by_cost_kind"]["estimated_micros"] == 0  # a local run is never an "estimate"
    assert (usage["input_tokens"], usage["output_tokens"], usage["records"]) == (500, 50, 1)


def test_a_zero_project_budget_does_not_block_a_local_model(env, engine):
    """Found live in M5: the Co-founder gave a project budget_usd 0 ("keep spend at zero") and every
    task in it was blocked by the 50 000 micro-USD default reservation - for a model that costs nothing."""
    project = env.client.post(
        api(env.cid, "/projects"), json={"name": "Zero", "budget_micros": 0, "status": "active"}
    ).json()
    task = make_task(env, "success", project_id=project["id"])
    settle(engine.scheduler, engine.worker)
    assert get_task(env.client, env.cid, task["id"])["status"] == "done"
    run = run_of(env, task["id"])
    assert (run["cost_micros"], run["cost_kind"]) == (0, "verified")


def test_engine_failure_retries_then_fails_the_task(env, engine):
    from sqlalchemy import update

    from app.models.work import Task

    task = make_task(env, "failure")
    for _ in range(6):
        settle(engine.scheduler, engine.worker, rounds=30)
        with env.app.state.session_factory() as db:  # skip the retry backoff (M12), not the retries
            db.execute(update(Task).where(Task.id == task["id"]).values(next_attempt_at=None))
            db.commit()
    final = get_task(env.client, env.cid, task["id"])
    assert final["status"] == "failed"  # max_attempts_per_task (3) used up, each a real failed run
    runs = env.client.get(api(env.cid, f"/tasks/{task['id']}/runs")).json()["items"]
    assert len(runs) == 3 and {r["status"] for r in runs} == {"failed"}
    assert all(r["cost_kind"] == "verified" and r["cost_micros"] == 0 for r in runs)


def test_engine_partial_is_a_retryable_failure(env, engine):
    task = make_task(env, "partial")
    engine.scheduler.tick()
    engine.worker.drain()
    t = get_task(env.client, env.cid, task["id"])
    assert (
        t["status"] == "ready" and t["attempt_count"] == 1
    )  # retry queued by the task state machine
    assert run_of(env, task["id"])["status"] == "failed"


def test_engine_no_model_provider_fails_the_run_with_a_config_error(env, engine):
    env.client.patch(api(env.cid, f"/agents/{env.agent['id']}"), json={"model_provider": None})
    env.client.patch(
        api(env.cid, f"/model-providers/{env.provider['id']}"), json={"enabled": False}
    )
    task = make_task(env, "success")
    engine.scheduler.tick()
    engine.worker.drain()
    run = run_of(env, task["id"])
    assert run["status"] == "failed" and run["error"]["code"] == "config"
    assert "model provider" in run["error"]["message"].lower()


def test_engine_cancel_through_the_api(env, engine):
    task = make_task(env, "cancel")
    engine.scheduler.tick()
    t = threading.Thread(target=engine.worker.drain, daemon=True)
    t.start()
    run = wait_until(
        lambda: (r := run_of(env, task["id"]))["status"] == "running" and r,
        timeout=10,
        what="running",
    )
    wait_until(lambda: events_of(env, run["id"]), timeout=10, what="the first runtime event")
    time.sleep(0.5)  # the fake installs its SIGTERM handler right after its first events
    resp = env.client.post(api(env.cid, f"/runs/{run['id']}/cancel"), json={"reason": "stop"})
    assert resp.status_code == 200
    t.join(timeout=15)
    assert not t.is_alive()
    final = run_of(env, task["id"])
    assert final["status"] == "cancelled" and final["cancel_reason"] == "cancelled_by_user"
    assert get_task(env.client, env.cid, task["id"])["status"] == "blocked"
    assert events_of(env, run["id"])[-1]["type"] == "run.cancelled"


def test_engine_enforces_the_worker_timeout_by_killing_the_runtime(env, engine):
    set_policy(env.client, env.cid, max_run_seconds=1)
    task = make_task(env, "ignore_term")
    engine.scheduler.tick()
    started = time.monotonic()
    engine.worker.drain()
    elapsed = time.monotonic() - started
    run = run_of(env, task["id"])
    assert run["status"] == "timed_out" and run["exit_reason"] == "timeout"
    assert elapsed < 12  # 1 s deadline + 0.6 s SIGTERM grace + startup, never the fake's 60 s sleep
    assert get_task(env.client, env.cid, task["id"])["status"] == "ready"  # retry


def test_engine_event_log_has_no_seq_collisions_with_server_progress(env, engine):
    task = make_task(env, "collide")
    engine.scheduler.tick()
    engine.worker.drain()
    run = run_of(env, task["id"])
    # a server-side progress event (what POST /progress writes) takes seq >= 1,000,000
    with env.app.state.session_factory() as db:
        row = db.get(TaskRun, run["id"])
        db.add(
            RunEvent(
                company_id=row.company_id,
                run_id=row.id,
                seq=1_000_000,
                type="progress",
                payload={"message": "server"},
                occurred_at=row.created_at,
            )
        )
        db.commit()
    seqs = [e["seq"] for e in events_of(env, run["id"])]
    assert seqs == [1, 2, 3, 4, 1_000_000]


def test_engine_materializes_exactly_the_assigned_skills_before_each_run(env, engine):
    seed_library(env.client, env.cid)
    home = env.settings.resolved_data_dir / "agents" / env.agent["id"] / "home" / "skills"
    (home / "stale-skill").mkdir(parents=True)
    (home / "stale-skill" / "SKILL.md").write_text("---\nname: stale-skill\n---\nold")
    make_task(env, "inspect")
    engine.scheduler.tick()
    engine.worker.drain()
    run = env.client.get(api(env.cid, f"/agents/{env.agent['id']}/skills")).json()["items"]
    assigned = sorted(i["skill"]["slug"] for i in run)
    assert assigned  # Harper has her seed skills
    with env.app.state.session_factory() as db:
        events = [e for e in db.query(RunEvent).all() if e.type == "progress"]
    skills_seen = events[0].payload["skills"]
    assert skills_seen == assigned and "stale-skill" not in skills_seen


def test_db_event_sink_redacts_the_run_token(env, engine):
    task = make_task(env, "leak")
    engine.scheduler.tick()
    engine.worker.drain()
    run = run_of(env, task["id"])
    blob = json.dumps(events_of(env, run["id"]))
    assert "cos_run." not in blob
    assert DbEventSink  # imported: the persistence path under test


# ------------------------------------------------------------------ docker probe


@pytest.mark.parametrize(
    "stdout, code, reachable",
    [
        ("27.3.1\n", 0, True),
        (
            "Cannot connect to the Docker daemon at unix:///x.sock. Is the docker daemon running?\n",
            0,
            False,
        ),
        ("", 0, False),
        ("27.3.1", 1, False),
        ("Server: error", 0, False),
    ],
)
def test_docker_probe_requires_a_real_version_answer(monkeypatch, stdout, code, reachable):
    """`docker info --format` exits 0 with the error text on stdout when the daemon is down (seen
    live): an exit code alone must never enable the sandbox."""
    import subprocess

    from app.runtimes import sandbox

    monkeypatch.setattr(sandbox.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=stdout, stderr=""),
    )
    assert sandbox.DockerProbe(ttl=0).status().reachable is reachable


def test_sandbox_resolution_policy():
    from app.runtimes.sandbox import DockerStatus, resolve_backend

    up, down = DockerStatus(True, "27"), DockerStatus(False, "down")
    assert resolve_backend("auto", up) == "docker" and resolve_backend("auto", down) == "none"
    assert resolve_backend("none", up) == "none"
    assert resolve_backend("docker", up) == "docker" and resolve_backend("docker", down) is None


# ------------------------------------------------------------------ registration


def test_default_registry_registers_the_company_agent_with_database_access(make_settings, app):
    from app.runtimes.registry import build_default_registry

    settings = make_settings()
    assert build_default_registry(settings).get("company_agent") is None  # needs the database
    registry = build_default_registry(
        settings, session_factory=app.state.session_factory, keys=app.state.keys
    )
    adapter = registry.get("company_agent")
    assert adapter.display_name == "Company Agent (Hermes fork)" and adapter.supports_execution


def test_the_real_app_lists_the_runtime_and_reports_it_unconfigured_without_a_venv(make_settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.helpers import login

    settings = make_settings(runtime_python=Path("/nonexistent/venv/bin/python"))
    with TestClient(create_app(settings)) as c:
        items = {i["id"]: i for i in login(c).get("/api/runtimes").json()["items"]}
    assert items["company_agent"]["health"]["status"] == "not_configured"
    assert "COMPANY_OS_RUNTIME_PYTHON" in items["company_agent"]["health"]["detail"]
    assert items["company_agent"]["kind"] == "agent_runtime"
    assert set(items["company_agent"]["capabilities"]) == {
        "streaming_events",
        "cancel",
        "usage_tokens",
        "usage_cost",
        "artifacts",
    }


def test_a_missing_runtime_never_dispatches_the_task(make_settings):
    """Health gates dispatch: with no venv the task waits instead of failing runs."""
    from fastapi.testclient import TestClient

    from app.engine.context import build_context
    from app.main import create_app
    from tests.helpers import login

    settings = make_settings(runtime_python=Path("/nonexistent/venv/bin/python"))
    the_app = create_app(settings)
    with TestClient(the_app) as c:
        login(c)
        cid = c.post("/api/companies", json={"name": "NoVenv"}).json()["id"]
        c.post(
            api(cid, "/model-providers"), json={"name": "L", "kind": "ollama", "default_model": "m"}
        )
        harper = next(
            a for a in c.get(api(cid, "/agents")).json()["items"] if a["name"] == "Harper"
        )
        task = new_task(c, cid, title="x", assignee_agent_id=harper["id"])
        ctx = build_context(
            settings,
            session_factory=the_app.state.session_factory,
            registry=the_app.state.runtimes,
            keys=the_app.state.keys,
            storage=the_app.state.storage,
            skills=the_app.state.skills,
        )
        report = Scheduler(ctx).tick()
        assert report.dispatch.queued_run_ids == [] and report.dispatch.waiting_for_runtime >= 1
        assert get_task(c, cid, task["id"])["status"] == "ready"


# ------------------------------------------------------------------ Milestone 6: guardrails in the spec


def test_spec_carries_the_guardrail_registry_for_exactly_the_tools_of_the_run(env):
    seen = _inspect(env)
    guard = seen["guardrails"]
    tiers = guard["tool_tiers"]
    for tool in seen["company_tools"]:
        assert tool in tiers, f"{tool} reaches the agent without a guardrail tier"
    assert tiers["web_search"] == "external_read" and tiers["read_file"] == "read"
    assert "terminal" not in tiers  # no docker sandbox: the shell is not on offer
    assert guard["untrusted_tools"]["web_search"] == "web" and guard["flag_injection"] is True
    assert "external_read" in guard["server_checked_tiers"]


def test_a_peer_review_run_sees_the_exact_outbound_content_and_nothing_else(env):
    from tests.guardrail_helpers import connect_mail, make_team, send
    from tests.test_agent_api import start_run

    team = make_team(env.client, env.cid)
    mail = connect_mail(env.app, env.client, env.cid)
    worker = start_run(env.app, env.client, env.cid, team.worker["id"])
    pending = send(worker, mail, body="Draft for Jane: our price is $40.").json()
    from sqlalchemy import select

    from app.models.work import Task

    with env.app.state.session_factory() as db:
        review = db.scalar(
            select(Task).where(Task.idempotency_key == f"peerreview:{pending['approval_id']}")
        )
    assert review is not None and review.assignee_agent_id == team.manager["id"]
    request = env.request(
        "t scenario:inspect",
        task=TaskSpec(
            review.id, "t scenario:inspect", "d", None, "review", "high", list(review.input_refs)
        ),
        agent=AgentSpec(team.manager["id"], "Mia", "Lead", None, None, None, None, {}, []),
    )
    sink = RecordingSink()
    result = env.adapter.execute(request, sink, CancelToken())
    assert result.status == "succeeded"
    seen = next(e.payload for e in sink.events if e.type == "progress")
    peer = seen["task"]["peer_review"]
    assert peer["approval_id"] == pending["approval_id"] and peer["author"] == "Wes"
    assert peer["outbound"]["body"] == "Draft for Jane: our price is $40."
    assert peer["outbound"]["to"] == "jane@customer.example" and peer["dlp_findings"] == []
    assert len(peer["checklist"]) >= 4 and isinstance(peer["constraints"], list)
    assert seen["company_tools"] == ["company_decide_peer_review", "company_complete_task"]
    assert seen["guardrails"]["tool_tiers"]["company_decide_peer_review"] == "internal_write"
    # an ordinary run has no peer review block
    assert _inspect(env)["task"]["peer_review"] is None


def test_the_spec_uses_the_model_the_dispatcher_routed_the_run_to(env):
    from sqlalchemy import update

    other = env.client.post(
        api(env.cid, "/model-providers"),
        json={
            "name": "Second",
            "kind": "ollama",
            "default_model": "llama3:8b",
            "base_url": "http://127.0.0.1:11435",
        },
    ).json()
    request = env.request("t scenario:inspect", run_id="routed-run")
    with env.app.state.session_factory() as db:
        from app.models.run import TaskRun
        from app.models.work import Task

        task = Task(
            company_id=env.cid,
            title="t",
            kind="work",
            status="in_progress",
            created_by_type="system",
        )
        db.add(task)
        db.flush()
        db.add(
            TaskRun(
                id="routed-run",
                company_id=env.cid,
                task_id=task.id,
                agent_id=env.agent["id"],
                attempt_number=1,
                status="running",
                runtime="company_agent",
                idempotency_key="routed-key",
                queued_at=utcnow(),
                model_provider_id=other["id"],
                model_used="llama3:8b",
                routing_reason="free_first: test",
            )
        )
        db.commit()
        db.execute(update(TaskRun).where(TaskRun.id == "routed-run").values(cost_kind="unknown"))
        db.commit()
    sink = RecordingSink()
    result = env.adapter.execute(request, sink, CancelToken())
    assert result.status == "succeeded"
    seen = next(e.payload for e in sink.events if e.type == "progress")
    assert seen["model"]["model"] == "llama3:8b"
    assert seen["model"]["base_url"] == "http://127.0.0.1:11435/v1"


def test_active_company_standards_are_in_the_spec_of_every_run(env):
    """M10: the standards the Founder set bind every agent; retired ones are gone."""
    created = env.client.post(
        api(env.cid, "/standards"),
        json={"title": "Plain language", "statement": "Write plainly, no jargon."},
    ).json()
    sink = RecordingSink()
    env.adapter.execute(env.request("t scenario:inspect", run_id="std-run"), sink, CancelToken())
    seen = next(e.payload for e in sink.events if e.type == "progress")
    assert seen["company"]["standards"] == ["Plain language: Write plainly, no jargon."]
    env.client.patch(api(env.cid, f"/standards/{created['id']}"), json={"status": "retired"})
    sink = RecordingSink()
    env.adapter.execute(env.request("t scenario:inspect", run_id="std-run2"), sink, CancelToken())
    assert (
        next(e.payload for e in sink.events if e.type == "progress")["company"]["standards"] == []
    )
