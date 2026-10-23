"""Company toolset against an in-process stub agent API: requests, auth, error mapping, secrecy."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from company_agent import context as ctx
from company_agent.tests.conftest import RUNTIME_ROOT
from company_agent.tests.stub_agent_api import StubApi
from tools import company_tools as ct
from tools.registry import registry

TOOLS = [
    "company_get_context", "company_create_task", "company_update_task", "company_complete_task",
    "company_review_task", "company_submit_artifact", "company_report_progress",
    "company_record_decision", "company_request_approval", "company_decide_approval",
    "company_find_colleagues", "company_request_hire", "company_search_skills", "company_install_skill",
    "company_author_skill", "company_assign_skills", "company_list_role_templates",
    "company_create_role_template", "company_hire_agent",
]


@pytest.fixture
def configured(api: StubApi, tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    artifacts: list[tuple[str, str]] = []
    ctx.configure(api.url, api.token, run_id=api.run_id, task_id=api.task_id, workspace_dir=str(ws),
                  on_artifact=lambda i, t: artifacts.append((i, t)), task_kind="routine")
    yield ws, artifacts
    ctx.reset()


def call(tool_name: str, /, **args):
    out = registry.dispatch(tool_name, args)
    return json.loads(out) if isinstance(out, str) else out


def test_all_tools_registered_in_company_toolset():
    from company_agent.spec import COMPANY_TOOL_NAMES

    for name in TOOLS:
        assert registry.get_toolset_for_tool(name) == "company"
    assert len(TOOLS) == 19
    assert sorted(TOOLS) == sorted(COMPANY_TOOL_NAMES)  # spec validation and registry cannot drift


def test_tools_hidden_until_configured(tmp_path: Path):
    """check_fn gates the toolset: no company tools in the request before the launcher configures it."""
    code = textwrap.dedent("""
        import json, os, sys
        sys.path.insert(0, %r)
        import model_tools
        from company_agent import context as ctx
        names = lambda: sorted(d["function"]["name"] for d in model_tools.get_tool_definitions(enabled_toolsets=["company"], quiet_mode=True))
        before = names()
        ctx.configure("http://127.0.0.1:1/x", "tok-123456789", run_id="r", task_id="t")
        model_tools.registry._generation += 1  # invalidate memo
        print(json.dumps({"before": before, "after": names()}))
    """) % str(RUNTIME_ROOT)
    home = tmp_path / "hh"
    home.mkdir()
    (home / "config.yaml").write_text('tools:\n  tool_search:\n    enabled: "off"\n')
    env = {"PATH": "/usr/bin:/bin", "HERMES_HOME": str(home), "HOME": str(tmp_path), "HERMES_DISABLE_LAZY_INSTALLS": "1"}
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=RUNTIME_ROOT, timeout=120)
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert data["before"] == []
    assert data["after"] == sorted(TOOLS)


def test_requests_carry_bearer_token_and_correct_paths(configured, api: StubApi):
    call("company_get_context")
    call("company_report_progress", message="hello")
    call("company_record_decision", title="Use Postgres", rationale="Scale")
    call("company_complete_task", result_summary="done", output_artifact_ids=["a1", "a2"])
    assert [(r.method, r.path) for r in api.requests] == [
        ("GET", "/api/agent/v1/context"),
        ("POST", "/api/agent/v1/progress"),
        ("POST", "/api/agent/v1/decisions"),
        ("POST", "/api/agent/v1/tasks/task-1/complete"),
    ]
    assert all(r.authorization == f"Bearer {api.token}" for r in api.requests)
    assert api.requests[1].body == {"message": "hello"}
    assert api.requests[2].body == {"title": "Use Postgres", "rationale": "Scale", "related_task_id": "task-1"}
    assert api.requests[3].body == {"result_summary": "done", "output_refs": ["a1", "a2"]}


def test_create_task_converts_budget_and_is_idempotent_per_run_and_title(configured, api: StubApi):
    call("company_create_task", title="Draft  the Plan", budget_usd=1.25, assignee_agent_id="a9",
         depends_on="t1, t2", priority="high")
    call("company_create_task", title="draft the plan")
    first, second = (r.body for r in api.calls("POST", "/tasks"))
    assert first["budget_micros"] == 1_250_000
    assert first["depends_on"] == ["t1", "t2"]
    assert first["assignee_agent_id"] == "a9" and first["priority"] == "high"
    assert first["idempotency_key"] == second["idempotency_key"]  # same run + normalized title
    assert first["idempotency_key"].startswith("run-1:")
    call("company_create_task", title="Other title")
    assert api.calls("POST", "/tasks")[2].body["idempotency_key"] != first["idempotency_key"]


def test_create_task_rejects_negative_budget_without_calling_api(configured, api: StubApi):
    out = call("company_create_task", title="x", budget_usd=-1)
    assert out["error"] == "invalid_arguments"
    assert api.requests == []


def test_update_review_approval_tools(configured, api: StubApi):
    call("company_update_task", action="block", reason="waiting on data")
    call("company_update_task", action="reassign", task_id="t7", assignee_agent_id="a2")
    call("company_review_task", task_id="t7", decision="request_revision", note="more detail")
    call("company_request_approval", kind="external_purchase", title="Buy ads", amount_usd=20, risk="medium",
         scope={"vendor": "x"})
    call("company_decide_approval", approval_id="ap1", decision="approve", note="ok")
    bodies = [(r.method, r.path.replace("/api/agent/v1", ""), r.body) for r in api.requests]
    assert bodies[0] == ("PATCH", "/tasks/task-1", {"status": "blocked", "reason": "waiting on data"})
    assert bodies[1] == ("PATCH", "/tasks/t7", {"assignee_agent_id": "a2"})
    assert bodies[2] == ("POST", "/tasks/t7/review", {"decision": "request_revision", "note": "more detail"})
    assert bodies[3][2]["amount_micros"] == 20_000_000 and bodies[3][2]["kind"] == "external_purchase"
    assert bodies[4] == ("POST", "/approvals/ap1/decide", {"decision": "approve", "note": "ok"})


def test_invalid_arguments_never_reach_the_api(configured, api: StubApi):
    assert call("company_update_task", action="explode")["error"] == "invalid_arguments"
    assert call("company_update_task", action="block")["error"] == "invalid_arguments"
    assert call("company_review_task", task_id="t", decision="maybe")["error"] == "invalid_arguments"
    assert call("company_request_approval", kind="bitcoin", title="x")["error"] == "invalid_arguments"
    assert call("company_complete_task")["error"] == "invalid_arguments"
    assert call("company_report_progress")["error"] == "invalid_arguments"
    assert api.requests == []


def test_http_errors_become_readable_tool_errors(configured, api: StubApi):
    api.forced["/tasks/task-1/complete"] = (409, {"error": {"code": "invalid_transition", "message": "Task is not in progress", "details": {"status": "done"}}})
    out = call("company_complete_task", result_summary="x")
    assert out["error"] == "invalid_transition"
    assert "not in progress" in out["message"] and out["status"] == 409
    assert ctx.dispositions() == {}  # failed completion is not recorded as completed
    api.forced["/progress"] = (403, {"error": {"code": "forbidden", "message": "nope", "details": None}})
    assert call("company_report_progress", message="m")["error"] == "forbidden"


def test_non_json_and_unreachable_errors(configured, api: StubApi):
    api.forced["/context"] = (502, "Bad gateway")
    assert call("company_get_context")["error"] == "http_502"
    ctx.configure("http://127.0.0.1:1/api/agent/v1", "tok-123456789", run_id="r", task_id="t")
    out = call("company_get_context")
    assert out["error"] == "agent_api_unreachable"


def test_wrong_token_is_reported_not_leaked(configured, api: StubApi):
    ctx.configure(api.url, "wrong-token-123456", run_id="r", task_id="t")
    out = call("company_get_context")
    assert out["error"] == "unauthenticated"
    assert "wrong-token-123456" not in json.dumps(out)


def test_token_never_appears_in_results(configured, api: StubApi):
    api.forced["/progress"] = (400, {"error": {"code": "bad", "message": f"echo {api.token} here", "details": None}})
    out = registry.dispatch("company_report_progress", {"message": "m"})
    assert api.token not in out and "[REDACTED]" in out


def test_inline_artifact_emits_event_and_defaults_to_current_task(configured, api: StubApi):
    ws, artifacts = configured
    out = call("company_submit_artifact", title="Brief", content="- one\n- two", kind="report")
    assert out["id"] == "art-1"
    body = api.calls("POST", "/artifacts")[0].body
    assert body == {"title": "Brief", "kind": "report", "task_id": "task-1", "content": "- one\n- two"}
    assert artifacts == [("art-1", "Brief")]


def test_file_artifact_uploaded_multipart_from_workspace(configured, api: StubApi):
    ws, artifacts = configured
    (ws / "out").mkdir()
    (ws / "out" / "report.md").write_text("# Report\nhello")
    out = call("company_submit_artifact", title="Report", path="out/report.md", kind="document", note="v1")
    assert "error" not in out, out
    rec = api.calls("POST", "/artifacts")[0]
    assert rec.content_type.startswith("multipart/form-data")
    assert rec.body["fields"] == {"title": "Report", "kind": "document", "task_id": "task-1", "note": "v1"}
    assert rec.body["files"]["file"]["filename"] == "report.md"
    assert rec.body["files"]["file"]["content"] == "# Report\nhello"
    assert artifacts[0][1] == "Report"


def test_artifact_path_must_be_inside_workspace(configured, api: StubApi, tmp_path: Path):
    ws, artifacts = configured
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")
    (ws / "link").symlink_to(outside)
    for bad in (str(outside), "../secret.txt", "link", "missing.txt"):
        out = call("company_submit_artifact", title="x", path=bad)
        assert out["error"] == "invalid_arguments", (bad, out)
    assert api.requests == [] and artifacts == []


def test_artifact_requires_exactly_one_source(configured, api: StubApi):
    assert call("company_submit_artifact", title="x")["error"] == "invalid_arguments"
    assert call("company_submit_artifact", content="b")["error"] == "invalid_arguments"  # title required for new artifact
    ws, _ = configured
    (ws / "real.md").write_text("file text")
    assert call("company_submit_artifact", title="x", path="real.md", content="b")["error"] == "invalid_arguments"  # ambiguous


def test_invented_path_with_inline_content_falls_back_to_content(configured, api: StubApi):
    """Small models send both and make the path up: the inline content is what they meant."""
    out = call("company_submit_artifact", title="x", path="/path/to/nowhere.md", content="body")
    assert "error" not in out
    assert api.calls("POST", "/artifacts")[0].body["content"] == "body"


def test_new_artifact_version_needs_no_title(configured, api: StubApi):
    out = call("company_submit_artifact", content="v2", artifact_id="art-9", note="rev")
    assert "error" not in out
    assert api.calls("POST", "/artifacts")[0].body["artifact_id"] == "art-9"


def test_complete_and_block_dispositions_tracked(configured, api: StubApi):
    call("company_update_task", action="block", reason="stuck")
    assert ctx.dispositions() == {"task-1": "blocked"}
    call("company_complete_task", result_summary="finished")
    assert ctx.dispositions() == {"task-1": "completed"}


@pytest.mark.parametrize("task_error", [None, {"code": "failed", "message": "boom", "run_id": "r0"}])
def test_successful_task_bodies_with_an_error_field_are_not_tool_errors(configured, api: StubApi, task_error):
    """The real API's Task has `error: null` (or the last failed attempt's object). Treating the mere
    presence of the key as failure made every real complete_task look broken: the agent was never marked
    done and the launcher kept prompting it (found by the first live run against the backend)."""
    from company_agent.runner import tool_result_is_error

    api.task_error = task_error
    out = call("company_complete_task", result_summary="done")
    assert out["status"] == "done"
    assert ctx.dispositions() == {"task-1": "completed"}
    assert tool_result_is_error("company_complete_task", json.dumps(out)) is False
    call("company_update_task", action="block", reason="stuck", task_id="task-9")
    assert tool_result_is_error("company_update_task", json.dumps({"id": "t", "error": task_error})) is False
    # real failures are still failures
    failure = json.dumps({"error": "invalid_transition", "message": "Only in-progress tasks can be completed"})
    assert tool_result_is_error("company_complete_task", failure) is True
    assert tool_result_is_error("read_file", json.dumps({"error": {"code": 1}})) is True  # other tools unchanged


def test_blocking_marks_the_disposition_even_when_the_task_body_has_an_error_object(configured, api: StubApi):
    api.task_error = {"code": "failed", "message": "old failure", "run_id": "r0"}
    call("company_update_task", action="block", reason="stuck")
    assert ctx.dispositions() == {"task-1": "blocked"}


def test_allowed_tools_subset_gates_availability(api: StubApi):
    ctx.configure(api.url, api.token, run_id="r", task_id="t", allowed_tools=["company_report_progress", "company_complete_task"])
    try:
        assert ctx.tool_enabled("company_report_progress") and ctx.tool_enabled("company_complete_task")
        assert not ctx.tool_enabled("company_decide_approval")
    finally:
        ctx.reset()
    assert not ctx.tool_enabled("company_report_progress")  # unconfigured


def test_work_task_completion_without_deliverable_is_challenged_once(api: StubApi):
    ctx.configure(api.url, api.token, run_id="r", task_id="task-1", task_kind="work")
    try:
        first = call("company_complete_task", result_summary="did research")
        assert first["error"] == "no_deliverable" and "company_submit_artifact" in first["message"]
        assert api.requests == []  # never reached the server
        second = call("company_complete_task", result_summary="no deliverable needed because X")
        assert not isinstance(second.get("error"), str) and second["status"] == "done"
    finally:
        ctx.reset()


def test_completion_with_artifact_or_non_work_task_is_not_challenged(api: StubApi):
    ctx.configure(api.url, api.token, run_id="r", task_id="task-1", task_kind="work")
    try:
        call("company_submit_artifact", title="T", content="c")
        assert not isinstance(call("company_complete_task", result_summary="done").get("error"), str)
    finally:
        ctx.reset()
    ctx.configure(api.url, api.token, run_id="r", task_id="task-1", task_kind="planning")
    try:
        assert not isinstance(call("company_complete_task", result_summary="planned").get("error"), str)
    finally:
        ctx.reset()
    ctx.configure(api.url, api.token, run_id="r", task_id="task-1", task_kind="work")
    try:  # explicit output refs count as a deliverable
        assert not isinstance(call("company_complete_task", result_summary="done", output_artifact_ids=["a1"]).get("error"), str)
    finally:
        ctx.reset()


def test_completion_refs_default_to_this_runs_artifacts_and_accept_alias_names(api: StubApi):
    ctx.configure(api.url, api.token, run_id="r", task_id="task-1", task_kind="work")
    try:
        call("company_submit_artifact", title="A", content="a")
        call("company_submit_artifact", title="B", content="b")
        call("company_complete_task", result_summary="done")  # nothing named: use what was submitted
        assert api.calls("POST", "/tasks/task-1/complete")[0].body["output_refs"] == ["art-1", "art-2"]
    finally:
        ctx.reset()
    ctx.configure(api.url, api.token, run_id="r", task_id="task-1", task_kind="work")
    try:
        call("company_submit_artifact", title="C", content="c")
        call("company_complete_task", result_summary="done", artifact_ids={"art-9": "title"})  # wrong name, dict
        assert api.calls("POST", "/tasks/task-1/complete")[1].body["output_refs"] == ["art-9"]
    finally:
        ctx.reset()


def test_stringified_dict_or_list_refs_are_parsed(configured, api: StubApi):
    """Observed live: Hermes coerces the model's dict for an array parameter into its repr string."""
    call("company_complete_task", result_summary="d", output_artifact_ids="{'art-3': 'Artifact id for the brief'}")
    call("company_complete_task", result_summary="d", output_artifact_ids='["a1", "a2"]')
    bodies = [r.body["output_refs"] for r in api.calls("POST", "/tasks/task-1/complete")]
    assert bodies == [["art-3"], ["a1", "a2"]]


def test_unconfigured_returns_error_not_exception():
    ctx.reset()
    out = call("company_get_context")
    assert out["error"] == "not_configured"


# ---------------------------------------------------------------- M4: colleagues and hiring


def test_find_colleagues_sends_filters_as_query(configured, api: StubApi):
    out = call("company_find_colleagues", skill_tags=["Python", "TDD"], family="engineering")
    assert out["items"][0]["name"] == "Bo"
    (rec,) = api.calls("GET", "/agents")
    assert rec.path.startswith("/api/agent/v1/agents?")
    query = rec.path.split("?", 1)[1]
    assert "skill_tags=python%2Ctdd" in query and "family=engineering" in query
    call("company_find_colleagues")
    assert api.calls("GET", "/agents")[1].path == "/api/agent/v1/agents"


def test_request_hire_defaults_task_and_validates(configured, api: StubApi):
    out = call("company_request_hire", required_skills=["design", "figma"], reason="Need UI work", role_hint="designer")
    assert out["created"] is True
    (rec,) = api.calls("POST", "/hiring-requests")
    assert rec.body == {"required_skills": ["design", "figma"], "role_hint": "designer",
                        "reason": "Need UI work", "task_id": "task-1"}
    assert call("company_request_hire", reason="x")["error"] == "invalid_arguments"
    assert call("company_request_hire", required_skills=["a"])["error"] == "invalid_arguments"
    assert len(api.calls("POST", "/hiring-requests")) == 1


def test_search_skills_trims_results_and_validates_source(configured, api: StubApi):
    out = call("company_search_skills", query="research", source="hermes", limit=500)
    assert out["items"][0]["slug"] == "arxiv"
    assert len(out["items"][0]["description"]) <= 243  # clipped for small models
    assert "stars" not in out["items"][0] and "skill_id" not in out["items"][0]
    (rec,) = api.calls("GET", "/skills/catalog")
    assert "q=research" in rec.path and "source=hermes" in rec.path and "limit=50" in rec.path  # limit capped
    assert call("company_search_skills", source="pirate-bay")["error"] == "invalid_arguments"


def test_install_and_author_skill_bodies(configured, api: StubApi):
    call("company_install_skill", source_ref="hermes:arxiv")
    call("company_author_skill", slug="triage", name="Triage", description="Sort inbound requests",
         tags="support, triage", body_markdown="# Triage\n1. Read\n2. Label")
    assert api.calls("POST", "/skills/install")[0].body == {"source_ref": "hermes:arxiv"}
    assert api.calls("POST", "/skills/author")[0].body == {
        "slug": "triage", "name": "Triage", "description": "Sort inbound requests",
        "tags": ["support", "triage"], "body_markdown": "# Triage\n1. Read\n2. Label"}
    assert call("company_install_skill")["error"] == "invalid_arguments"
    assert call("company_author_skill", slug="x")["error"] == "invalid_arguments"


def test_assign_skills_targets_the_agent(configured, api: StubApi):
    call("company_assign_skills", agent_id="agent-9", skill_slugs='["arxiv", "llm-wiki"]')  # stringified list
    (rec,) = api.calls("POST", "/agents/agent-9/skills")
    assert rec.body == {"slugs": ["arxiv", "llm-wiki"]}
    assert call("company_assign_skills", agent_id="agent-9")["error"] == "invalid_arguments"


def test_role_templates_are_listed_without_playbooks_and_created(configured, api: StubApi):
    listed = call("company_list_role_templates")
    row = listed["items"][0]
    assert row["slug"] == "software-engineer" and "instructions" not in row
    assert len(row["description"]) <= 243
    call("company_create_role_template", slug="data-analyst", title="Data Analyst", family="research",
         default_toolsets="company,file,skills", required_skill_tags=["sql"])
    assert api.calls("POST", "/role-templates")[0].body == {
        "slug": "data-analyst", "title": "Data Analyst", "family": "research",
        "default_toolsets": ["company", "file", "skills"], "required_skill_tags": ["sql"]}


def test_hire_agent_converts_budget_and_defaults(configured, api: StubApi):
    out = call("company_hire_agent", role_template="software-engineer", name="Quinn", manager_agent_id="agent-1",
               skill_slugs=["tdd"], monthly_budget_usd=2.5)
    assert out["auto_activated"] is True
    (rec,) = api.calls("POST", "/hires")
    assert rec.body["monthly_budget_micros"] == 2_500_000
    assert rec.body["task_id"] == "task-1" and rec.body["skill_slugs"] == ["tdd"]
    assert rec.body["reason"] == "Hire requested by task task-1"
    assert call("company_hire_agent", name="Q", manager_agent_id="m")["error"] == "invalid_arguments"
    assert call("company_hire_agent", role_template="x", name="Q", manager_agent_id="m",
                monthly_budget_usd=-1)["error"] == "invalid_arguments"


def test_hr_only_server_denial_reaches_the_model(configured, api: StubApi):
    """The server (not this client) enforces HR-only; its 403 is surfaced as a readable error."""
    api.forced["/hires"] = (403, {"error": {"code": "forbidden", "message": "Only the HR agent can hire",
                                            "details": None}})
    out = call("company_hire_agent", role_template="software-engineer", name="Quinn", manager_agent_id="a")
    assert out["error"] == "forbidden" and out["status"] == 403 and "HR" in out["message"]


def test_new_tools_respect_the_company_tools_subset(api: StubApi, tmp_path: Path):
    ctx.configure(api.url, api.token, run_id="run-1", task_id="task-1", workspace_dir=str(tmp_path),
                  allowed_tools=["company_get_context", "company_find_colleagues"])
    try:
        assert ctx.tool_enabled("company_find_colleagues")
        assert not ctx.tool_enabled("company_hire_agent")
        assert not ctx.tool_enabled("company_install_skill")
    finally:
        ctx.reset()
