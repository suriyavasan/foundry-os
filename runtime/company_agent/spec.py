"""Run spec: the one JSON document the control plane writes to the launcher's stdin.

Pure stdlib. Validation collects every problem so a bad spec produces one actionable error
(exit code 3) instead of a fix-one-retry loop. ``api_key`` and the agent-API ``token`` are
secrets: they are kept out of ``repr`` and out of :meth:`RunSpec.public_dict`.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from company_agent import PROTOCOL_VERSION

PROVIDERS = ("custom", "openrouter", "anthropic")
SANDBOX_BACKENDS = ("none", "docker")

# Toolsets an employee may be given. Everything else in Hermes (browser, delegation, cronjob,
# memory, kanban, messaging, MCP, clarify, tts, image/video generation, computer use, ...) is
# intentionally unreachable: employees collaborate through the `company` toolset only.
ALLOWED_TOOLSETS = (
    "company", "file", "web", "search", "skills", "skills_manage", "todo", "vision", "terminal", "code_execution",
)
# Need a real sandbox (docker): they execute arbitrary code on the host otherwise.
SANDBOX_ONLY_TOOLSETS = ("terminal", "code_execution")

COMPANY_TOOL_NAMES = (
    "company_get_context", "company_create_task", "company_update_task", "company_complete_task",
    "company_review_task", "company_submit_artifact", "company_report_progress", "company_record_decision",
    "company_request_approval", "company_decide_approval",
    # M4: colleagues + hiring (server-enforced roles; HR tools are HR-only on the API side).
    "company_find_colleagues", "company_request_hire",
    "company_search_skills", "company_install_skill", "company_author_skill", "company_assign_skills",
    "company_list_role_templates", "company_create_role_template", "company_hire_agent",
    # M5: autonomous operating model (live state, Founder's notes, goals/projects/schedules, escalation, memory).
    "company_get_state", "company_list_notes", "company_triage_note", "company_reply_to_note",
    "company_manage_goal", "company_manage_project", "company_manage_schedule",
    "company_escalate", "company_request_help", "company_propose_memory", "company_verify_memory",
    # M6: maker-checker review decisions and control-plane provider actions (credentials never reach the agent).
    "company_decide_peer_review", "company_provider_action",
    # M10: reflections for the Founder (AI Co-founder only on the server).
    "company_post_reflection", "company_reply_reflection", "company_revise_reflection",
    # M12: plan a project's first task list in one call (leads and managers).
    "company_plan_project",
    # M14: the internal reviewer's verdict on a Foundry pull request.
    "company_review_pull_request",
    # M15: ask the company to buy something (no card data ever); answer a subscription renewal review.
    "company_request_purchase", "company_review_subscription",
    # M16: market radar (outside text comes back wrapped as untrusted; the server enforces roles and caps).
    "company_radar_digest", "company_radar_read", "company_radar_brief", "company_radar_propose",
    "company_radar_decide", "company_radar_keywords", "company_radar_ui_search", "company_radar_add_source",
)

MIN_CONTEXT_LENGTH = 8192
MAX_ITERATIONS_CAP = 500
MAX_RUN_SECONDS_CAP = 24 * 3600
READ_TIMEOUT_SECONDS = 30.0
MAX_SPEC_BYTES = 2 * 1024 * 1024


class SpecError(ValueError):
    """Run spec is missing, malformed or invalid. ``problems`` lists every issue found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class ModelSpec:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    context_length: int
    is_local: bool = False


@dataclass
class RunSpec:
    run_id: str
    company: dict[str, Any]
    agent: dict[str, Any]
    task: dict[str, Any]
    model: ModelSpec
    toolsets: list[str]
    max_iterations: int
    max_run_seconds: int
    agent_api_url: str
    agent_api_token: str = field(repr=False)
    workspace_dir: str
    hermes_home: str
    sandbox_backend: str = "none"
    docker_image: str = ""
    docker_network: bool = False
    # Optional subset of the company tools (None = all). Small models do better with fewer tools.
    company_tools: Optional[list[str]] = None
    # M6 guardrails block (docs/specs/m6): tool tiers, which tiers the control plane is asked about,
    # which tools return untrusted content. None = a control plane older than Milestone 6.
    guardrails: Optional[dict[str, Any]] = None
    # M12: where the work lives, when the control plane gives the run a shared project repository:
    # {"kind": "project_repo" | "review" | "run_dir", "project_name": str, "files": [relative paths]}.
    # None = a plain per-run directory (or a control plane older than M12).
    workspace: Optional[dict[str, Any]] = None

    @property
    def secrets(self) -> list[str]:
        return [s for s in (self.agent_api_token, self.model.api_key) if s]

    def public_dict(self) -> dict[str, Any]:
        """Spec summary that is safe to log/emit (no api key, no token)."""
        return {
            "run_id": self.run_id,
            "company": {"id": self.company.get("id"), "name": self.company.get("name")},
            "agent": {k: self.agent.get(k) for k in ("id", "name", "role_title", "kind")},
            "task": {k: self.task.get(k) for k in ("id", "title", "kind", "priority")},
            "model": {
                "provider": self.model.provider,
                "model": self.model.model,
                "base_url": _strip_credentials(self.model.base_url),
                "context_length": self.model.context_length,
                "is_local": self.model.is_local,
            },
            "toolsets": list(self.toolsets),
            "limits": {"max_iterations": self.max_iterations, "max_run_seconds": self.max_run_seconds},
            "sandbox": {"backend": self.sandbox_backend, "network": self.docker_network},
        }


def _strip_credentials(url: str) -> str:
    try:
        p = urlparse(url)
        if p.username or p.password:
            netloc = p.hostname or ""
            if p.port:
                netloc += f":{p.port}"
            return p._replace(netloc=netloc).geturl()
    except ValueError:
        pass
    return url


def _str(d: dict, key: str, problems: list[str], where: str, *, required: bool = True) -> str:
    v = d.get(key)
    if v is None or v == "":
        if required:
            problems.append(f"{where}.{key} is required")
        return ""
    if not isinstance(v, str):
        problems.append(f"{where}.{key} must be a string")
        return ""
    return v


def _int(d: dict, key: str, problems: list[str], where: str, *, lo: int, hi: int, default: Optional[int] = None) -> int:
    v = d.get(key, default)
    if v is None:
        problems.append(f"{where}.{key} is required")
        return 0
    if isinstance(v, bool) or not isinstance(v, int):
        problems.append(f"{where}.{key} must be an integer")
        return 0
    if not lo <= v <= hi:
        problems.append(f"{where}.{key} must be between {lo} and {hi}")
        return 0
    return v


def _obj(d: dict, key: str, problems: list[str]) -> dict:
    v = d.get(key)
    if not isinstance(v, dict):
        problems.append(f"{key} is required and must be an object")
        return {}
    return v


def parse_spec(raw: Any) -> RunSpec:
    """Validate a decoded JSON document. Raises :class:`SpecError`."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise SpecError(["run spec must be a JSON object"])

    if raw.get("protocol") != PROTOCOL_VERSION:
        problems.append(f"protocol must be {PROTOCOL_VERSION} (got {raw.get('protocol')!r})")
    run_id = _str(raw, "run_id", problems, "spec")

    company = _obj(raw, "company", problems)
    _str(company, "id", problems, "company")
    _str(company, "name", problems, "company")
    constraints = company.get("constraints", [])
    if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
        problems.append("company.constraints must be a list of strings")

    agent = _obj(raw, "agent", problems)
    for key in ("id", "name", "role_title"):
        _str(agent, key, problems, "agent")

    task = _obj(raw, "task", problems)
    _str(task, "id", problems, "task")
    _str(task, "title", problems, "task")
    if not isinstance(task.get("input_refs", []), list):
        problems.append("task.input_refs must be a list")

    m = _obj(raw, "model", problems)
    provider = _str(m, "provider", problems, "model")
    if provider and provider not in PROVIDERS:
        problems.append(f"model.provider must be one of {', '.join(PROVIDERS)}")
    base_url = _str(m, "base_url", problems, "model", required=provider == "custom")
    if base_url and urlparse(base_url).scheme not in ("http", "https"):
        problems.append("model.base_url must be an http(s) URL")
    api_key = _str(m, "api_key", problems, "model")
    model_name = _str(m, "model", problems, "model")
    ctx_len = _int(m, "context_length", problems, "model", lo=MIN_CONTEXT_LENGTH, hi=10_000_000)
    is_local = m.get("is_local", False)
    if not isinstance(is_local, bool):
        problems.append("model.is_local must be a boolean")
        is_local = False

    toolsets = raw.get("toolsets")
    if not isinstance(toolsets, list) or not toolsets or not all(isinstance(t, str) and t for t in toolsets):
        problems.append("toolsets must be a non-empty list of toolset names")
        toolsets = []
    else:
        unknown = sorted({t for t in toolsets if t not in ALLOWED_TOOLSETS})
        if unknown:
            problems.append(f"toolsets not allowed for company agents: {', '.join(unknown)} "
                            f"(allowed: {', '.join(ALLOWED_TOOLSETS)})")

    company_tools = raw.get("company_tools")
    if company_tools is not None:
        if not isinstance(company_tools, list) or not all(isinstance(t, str) for t in company_tools):
            problems.append("company_tools must be a list of tool names")
            company_tools = None
        else:
            bad = sorted({t for t in company_tools if t not in COMPANY_TOOL_NAMES})
            if bad:
                problems.append(f"company_tools has unknown tools: {', '.join(bad)}")

    guardrails = raw.get("guardrails")
    if guardrails is not None:
        if not isinstance(guardrails, dict):
            problems.append("guardrails must be an object")
            guardrails = None
        else:
            tiers = guardrails.get("tool_tiers")
            checked = guardrails.get("server_checked_tiers", [])
            untrusted = guardrails.get("untrusted_tools", {})
            if not (isinstance(tiers, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in tiers.items())):
                problems.append("guardrails.tool_tiers must map tool names to tiers")
            if not (isinstance(checked, list) and all(isinstance(t, str) for t in checked)):
                problems.append("guardrails.server_checked_tiers must be a list of tiers")
            if not (isinstance(untrusted, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in untrusted.items())):
                problems.append("guardrails.untrusted_tools must map tool names to sources")

    workspace_info = raw.get("workspace")
    if workspace_info is not None:
        if not isinstance(workspace_info, dict):
            problems.append("workspace must be an object")
            workspace_info = None
        else:
            files = workspace_info.get("files", [])
            if not (isinstance(files, list) and all(isinstance(f, str) for f in files)):
                problems.append("workspace.files must be a list of paths")
            for key in ("kind", "project_name"):
                if workspace_info.get(key) is not None and not isinstance(workspace_info.get(key), str):
                    problems.append(f"workspace.{key} must be a string")

    limits = raw.get("limits")
    if not isinstance(limits, dict):
        problems.append("limits is required and must be an object")
        limits = {}
    max_iter = _int(limits, "max_iterations", problems, "limits", lo=1, hi=MAX_ITERATIONS_CAP)
    max_secs = _int(limits, "max_run_seconds", problems, "limits", lo=1, hi=MAX_RUN_SECONDS_CAP)

    api = _obj(raw, "agent_api", problems)
    api_url = _str(api, "url", problems, "agent_api")
    if api_url and urlparse(api_url).scheme not in ("http", "https"):
        problems.append("agent_api.url must be an http(s) URL")
    api_token = _str(api, "token", problems, "agent_api")

    workspace = _str(raw, "workspace_dir", problems, "spec")
    home = _str(raw, "hermes_home", problems, "spec")
    for label, p in (("workspace_dir", workspace), ("hermes_home", home)):
        if p and not os.path.isabs(p):
            problems.append(f"{label} must be an absolute path")
    if workspace and home and os.path.isabs(workspace) and os.path.isabs(home):
        ws, hm = os.path.realpath(workspace), os.path.realpath(home)
        if ws == hm or ws.startswith(hm + os.sep) or hm.startswith(ws + os.sep):
            problems.append("workspace_dir and hermes_home must not contain each other")

    sandbox = raw.get("sandbox") or {}
    if not isinstance(sandbox, dict):
        problems.append("sandbox must be an object")
        sandbox = {}
    backend = sandbox.get("backend", "none")
    if backend not in SANDBOX_BACKENDS:
        problems.append(f"sandbox.backend must be one of {', '.join(SANDBOX_BACKENDS)}")
        backend = "none"
    docker_image = sandbox.get("docker_image") or ""
    if backend == "docker" and not (isinstance(docker_image, str) and docker_image):
        problems.append("sandbox.docker_image is required when sandbox.backend is docker")
    network = sandbox.get("network", False)
    if not isinstance(network, bool):
        problems.append("sandbox.network must be a boolean")
        network = False

    if problems:
        raise SpecError(problems)

    return RunSpec(
        run_id=run_id,
        company=company,
        agent=agent,
        task=task,
        model=ModelSpec(provider=provider, model=model_name, base_url=base_url, api_key=api_key,
                        context_length=ctx_len, is_local=is_local),
        toolsets=list(dict.fromkeys(toolsets)),
        max_iterations=max_iter,
        max_run_seconds=max_secs,
        agent_api_url=api_url.rstrip("/"),
        agent_api_token=api_token,
        workspace_dir=os.path.abspath(workspace),
        hermes_home=os.path.abspath(home),
        sandbox_backend=backend,
        docker_image=docker_image if isinstance(docker_image, str) else "",
        docker_network=network,
        company_tools=list(dict.fromkeys(company_tools)) if company_tools is not None else None,
        guardrails=guardrails if isinstance(guardrails, dict) else None,
        workspace=workspace_info if isinstance(workspace_info, dict) else None,
    )


def read_spec_from_fd(fd: int = 0, timeout: float = READ_TIMEOUT_SECONDS) -> Any:
    """Read ONE JSON document from ``fd``: returns as soon as it parses (no EOF needed) or at EOF.

    Raises SpecError on timeout, oversize, empty input or malformed JSON. Trailing data after the
    document is ignored."""
    decoder = json.JSONDecoder()
    buf = b""
    deadline = time.monotonic() + timeout
    while True:
        if buf.strip():
            try:
                text = buf.decode("utf-8")
            except UnicodeDecodeError:
                text = None  # incomplete multibyte sequence: read more
            if text is not None:
                try:
                    obj, _ = decoder.raw_decode(text.lstrip())
                    return obj
                except json.JSONDecodeError:
                    pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SpecError(["timed out waiting for the run spec on stdin"])
        ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:  # EOF
            if not buf.strip():
                raise SpecError(["no run spec received on stdin"])
            try:
                return json.loads(buf.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SpecError([f"run spec is not valid JSON: {exc}"]) from None
        buf += chunk
        if len(buf) > MAX_SPEC_BYTES:
            raise SpecError(["run spec is too large"])


def load_spec_from_stdin() -> RunSpec:
    return parse_spec(read_spec_from_fd(sys.stdin.fileno()))
