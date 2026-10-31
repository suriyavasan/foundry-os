"""Shared machinery of the coding-CLI adapters (Claude Code, Codex, OpenCode).

One ``CliAdapter`` per CLI. The base does everything that is the same: reading the run's context from
the database, an isolated per-run directory (token file, MCP configuration), a minimal child
environment, spawning in its own process group, JSON-lines parsing into run events, cancel
(SIGTERM then SIGKILL of the group), usage and cost accounting, completion detection through the task
state (the CLI reports back through the ``company`` MCP tools), and artifact collection when it did
not. Subclasses only build the command line and parse their CLI's JSON stream.

Isolation (docs/cli-runtimes.md):

* ``api_key`` mode: ``HOME`` is ``data/agents/<id>/cli-home``; the key is injected into the child's
  environment only. Strongest isolation.
* ``own_login`` mode: the real ``HOME`` (the CLI must find its own login). Weaker, shown as a warning.
  Foundry AI OS never reads, copies or logs a CLI's credential files.
* Never ``bypassPermissions``, ``--dangerously-skip-permissions``, ``--auto``, ``danger-full-access``
  (``catalog.assert_safe_argv`` runs before every spawn).
"""

from __future__ import annotations

import collections
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.core.errors import AppError
from app.core.keys import SecretKeyProvider
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.company import Company
from app.models.enums import CostKind, TaskStatus
from app.models.provider import ModelProvider
from app.models.run import TaskRun
from app.models.work import Project, Task, TaskDependency
from app.runtimes.base import (
    ArtifactRef,
    CancelToken,
    Capability,
    ConfigIssue,
    EventSink,
    ExecutionError,
    ExecutionEvent,
    ExecutionRequest,
    ExecutionResult,
    HealthStatus,
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeErrorKind,
    RuntimeHealth,
    UsageReport,
)
from app.runtimes.cli import prompt as prompt_builder
from app.runtimes.cli import role_brief as role_brief_module
from app.runtimes.cli import store, workspace
from app.runtimes.cli.catalog import CliSpec, assert_safe_argv
from app.runtimes.cli.env import CliEnvironment, base_env
from app.services import agent_roles, agent_toolkit, credentials, routing, standards, task_briefs
from app.services import meetings as meetings_service
from app.services import memory as memory_service
from app.storage.local import LocalFileStorage

logger = logging.getLogger("company_os.runtime.cli")

MAX_LINE_BYTES = 1_000_000
STDERR_TAIL_LINES = 30
TEST_TIMEOUT_SECONDS = 60
TEST_BUDGET_USD = 0.05
# The company tools a coding CLI gets over MCP (short names; the server enforces everything again).
CLI_MCP_TOOLS = (
    "get_context",
    "report_progress",
    "submit_artifact",
    "complete_task",
    "update_task",
    "request_help",
    "find_colleagues",
    "request_purchase",
    "record_decision",  # M18 decide-don't-ask
    "ask_founder",  # M18
    "request_human_work",  # M22
)
SUBSCRIPTION_NOTE = "subscription"
LOCAL_COST_NOTE = "Local model: no provider charge, recorded as $0 (verified)."


@dataclass
class ResolvedProvider:
    """A company model provider an OpenCode run points at (its endpoint, and its key as env)."""

    provider_id: str
    name: str
    kind: str
    base_url: str
    model: str
    is_local: bool
    api_key: str | None
    context_length: int


@dataclass
class RunPrep:
    cli: CliSpec
    mode: str  # "run" | "test"
    binary: str
    auth_mode: str  # own_login | api_key
    secret: str | None  # API key (api_key mode); never logged
    home: Path  # HOME for the child
    isolated_home: bool
    run_dir: Path  # per-run private directory (token file, config); removed afterwards
    workspace: Path
    prompt: str
    model: str | None
    budget_usd: float | None
    max_run_seconds: int
    provider: ResolvedProvider | None = None
    # MCP wiring (run mode only)
    api_url: str | None = None
    run_token: str | None = None
    run_id: str = ""
    task_id: str = ""
    task_kind: str = "work"
    mcp_python: Path | None = None
    runtime_dir: Path | None = None
    secrets: list[str] = field(default_factory=list)
    workspace_kind: str = "run_dir"  # "project" | "review" | "run_dir" (M12)
    # B9: the role's company tools (MCP short names; None = the fixed worker list) and, for a
    # management run, the role instructions
    mcp_tools: list[str] | None = None
    role_brief: str = ""
    # M17: a plan connection's private home. Claude Code: its ``CLAUDE_CONFIG_DIR`` (``home`` stays the
    # real home); Cursor: ``home`` itself is that isolated directory.
    config_dir: Path | None = None
    ai_connection_id: str | None = None
    # M18 Part B: "verify" = the work graph's tool-less self-check call (``mode`` is "test": no tools,
    # no company MCP); ``session_mode``/``session_id``: a persistent conversation (Claude Code).
    purpose: str = "work"
    session_mode: str | None = None  # "new" | "resume"
    session_id: str | None = None

    @property
    def local_model(self) -> bool:
        return bool(self.provider and self.provider.is_local)


@dataclass
class Invocation:
    argv: list[str]  # argv[prompt_index] is the prompt (excluded from the safety scan)
    env: dict[str, str]
    cwd: Path
    prompt_index: int | None = None
    config_text: str = ""  # generated config, scanned for forbidden settings too


@dataclass
class Parsed:
    """What the stream parser learned (single writer: the stdout reader thread)."""

    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    error: str | None = None
    finished: bool = False  # the CLI reported a clean end of its run
    error_category: str | None = (
        None  # the CLI's own error class (Claude Code: authentication_failed ...)
    )
    seq: int = 1
    dropped: collections.Counter = field(default_factory=collections.Counter)
    emitted: int = 0


class StreamParser:
    """Turns one decoded JSON line of a CLI into run events ``[(type, payload)]``."""

    def __init__(self, prep: RunPrep, parsed: Parsed) -> None:
        self.prep = prep
        self.parsed = parsed

    def feed(self, obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        raise NotImplementedError


def clip(value: Any, n: int = 300) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 6 and secret in text:
            text = text.replace(secret, "[redacted]")
    return text


class CliAdapter(RuntimeAdapter):
    kind = "agent_runtime"
    supports_execution = True
    supports_verify = True  # M18 Part B: a tool-less call (the "test" mode of every CLI)
    spec: CliSpec
    parser_class: type[StreamParser]
    tool_prefix = ""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        keys: SecretKeyProvider,
        cli_env: CliEnvironment | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._cipher = CredentialCipher(keys)
        self.cli_env = cli_env or CliEnvironment(
            extra_dirs=[Path(p) for p in getattr(settings, "cli_search_paths", [])]
        )
        self.id = self.spec.runtime_id
        self.display_name = self.spec.display_name

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    # ------------------------------------------------------------ RuntimeAdapter

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.STREAMING_EVENTS,
                Capability.CANCEL,
                Capability.USAGE_TOKENS,
                Capability.USAGE_COST,
                Capability.ARTIFACTS,
            }
        )

    config_hint = ""

    def validate_config(self, config: dict[str, Any]) -> list[ConfigIssue]:
        issues = []
        for key in config or {}:
            if key != "model":
                issues.append(ConfigIssue(key, "unknown runtime_config key", "warning"))
        model = (config or {}).get("model")
        if model is not None and not isinstance(model, str):
            issues.append(ConfigIssue("model", "must be a string"))
        return issues

    def check_health(self) -> RuntimeHealth:
        binary = self.cli_env.find_binary(self.spec)
        if binary is None:
            return RuntimeHealth(
                HealthStatus.NOT_CONFIGURED,
                f"{self.spec.display_name} is not installed ({self.spec.login_hint})",
            )
        with self._session_factory() as db:
            state = store.load(db, self.spec.id)
        if not state.get("auth_mode"):
            return RuntimeHealth(
                HealthStatus.NOT_CONFIGURED,
                f"Choose how {self.spec.display_name} signs in (own login or API key)",
            )
        return RuntimeHealth(HealthStatus.OK, f"{self.spec.display_name} at {binary}")

    # ------------------------------------------------------------ subclass hooks

    def build(self, prep: RunPrep) -> Invocation:
        raise NotImplementedError

    def classify_failure(self, parsed: Parsed) -> str:
        return parsed.error or "The CLI reported an error"

    # ------------------------------------------------------------ preparation

    def _isolated_home(self, agent_id: str) -> Path:
        home = self._settings.resolved_data_dir / "agents" / agent_id / "cli-home"
        home.mkdir(parents=True, exist_ok=True)
        return home

    def _real_home(self) -> Path:
        return Path(os.environ.get("HOME") or str(Path.home()))

    def _key_for(
        self, db: Session, company_id: str, state: dict[str, Any]
    ) -> tuple[str | None, ResolvedProvider | None]:
        """(API key, company model provider) for api_key mode; raises a CONFIG error when missing."""
        key: str | None = None
        cred = state.get("credential_id")
        if cred:
            try:
                key = credentials.reveal(db, self._cipher, company_id, cred)
            except CredentialDecryptError as exc:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.CONFIG,
                    "The CLI's API key cannot be decrypted (was the secret key replaced?)",
                ) from exc
            except AppError as exc:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.CONFIG,
                    f"The CLI's API key is not usable in this company: {exc.message}",
                ) from exc
        provider = self._provider_for(db, company_id, state)
        if provider is not None and provider.api_key and not key:
            key = provider.api_key
        return key, provider

    def _provider_for(
        self, db: Session, company_id: str, state: dict[str, Any]
    ) -> ResolvedProvider | None:
        provider_id = state.get("model_provider_id")
        if not provider_id:
            return None
        row = db.get(ModelProvider, provider_id)
        if row is None or row.company_id != company_id or not row.enabled:
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIG, "The model provider chosen for this CLI is not available"
            )
        from app.services import model_providers

        api_key = None
        if row.credential_id:
            try:
                api_key = credentials.reveal(db, self._cipher, company_id, row.credential_id)
            except (CredentialDecryptError, AppError) as exc:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.CONFIG, "The model provider's credential is not usable"
                ) from exc
        base = row.base_url or model_providers.KINDS[row.kind][0] or ""
        if row.kind == "ollama":
            base = base.rstrip("/") + "/v1"
        return ResolvedProvider(
            provider_id=row.id,
            name=row.name,
            kind=row.kind,
            base_url=base,
            model=state.get("model") or row.default_model,
            is_local=row.is_local,
            api_key=api_key,
            context_length=row.context_length,
        )

    def _prep_common(
        self, db: Session, state: dict[str, Any], company_id: str, agent_id: str, run_id: str
    ) -> tuple[str, str | None, ResolvedProvider | None, Path, bool]:
        auth_mode = state.get("auth_mode")
        if auth_mode not in ("own_login", "api_key"):
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIG,
                f"{self.spec.display_name} has no sign-in mode configured (Settings > AI)",
            )
        secret: str | None = None
        provider: ResolvedProvider | None = None
        if auth_mode == "api_key":
            secret, provider = self._key_for(db, company_id, state)
            if secret is None and provider is None:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.CONFIG,
                    f"{self.spec.display_name} is set to use an API key but none is configured",
                )
            home, isolated = self._isolated_home(agent_id), True
        else:
            provider = self._provider_for(db, company_id, state)  # OpenCode on a local model
            home, isolated = self._real_home(), False
            if state.get("plan_home") and self.spec.id != "claude_code":
                # a plan connection of a CLI that keeps its login under HOME (Cursor): its own home
                home, isolated = Path(state["plan_home"]), True
        return auth_mode, secret, provider, home, isolated

    def _prepare(self, request: ExecutionRequest) -> RunPrep:
        binary = self.cli_env.find_binary(self.spec)
        if binary is None:
            raise RuntimeAdapterError(
                RuntimeErrorKind.UNAVAILABLE, f"{self.spec.display_name} is not installed"
            )
        settings = self._settings
        run_scratch = settings.resolved_data_dir / "agents" / request.agent.id / "cli-run-brief"
        with self._session_factory() as db:
            agent = db.get(Agent, request.agent.id)
            company = db.get(Company, request.company.id)
            task = db.get(Task, request.task.id)
            if agent is None or company is None or task is None or agent.company_id != company.id:
                raise RuntimeAdapterError(RuntimeErrorKind.REJECTED, "Agent or company not found")
            state = self._state_for(db, company.id, request)
            auth_mode, secret, provider, home, isolated = self._prep_common(
                db, state, company.id, agent.id, request.run_id
            )
            model = (
                (agent.runtime_config or {}).get("model")
                or (provider.model if provider else None)
                or state.get("model")
            )
            standard_lines = standards.context_lines(db, company.id, agent)
            memory_lines = memory_service.context_lines(
                memory_service.relevant_for(db, company.id, agent)
            )
            description = request.task.description
            project = db.get(Project, task.project_id) if task.project_id else None
            project_name = project.name if project and project.company_id == company.id else None
            dependencies = self._dependency_briefs(db, task)
            prompt = prompt_builder.build(
                prompt_builder.PromptInputs(
                    company_name=company.name,
                    mission=company.mission,
                    strategic_goal=company.strategic_goal,
                    constraints=list(company.constraints or []),
                    agent_name=agent.name,
                    role_title=agent.role_title,
                    responsibilities=agent.responsibilities,
                    instructions=agent.instructions,
                    task_id=task.id,
                    task_title=request.task.title or task.title,
                    task_description=description,
                    acceptance_criteria=request.task.acceptance_criteria,
                    review_note=request.task.review_note,
                    carry_over=request.task.carry_over,
                    standards=standard_lines,
                    memory=memory_lines,
                    tool_prefix=self.tool_prefix,
                    workspace_kind=request.workspace_kind,
                    project_name=project_name,
                    workspace_files=list(request.workspace_files),
                    dependencies=dependencies,
                    meeting_brief=meetings_service.turn_brief(db, task),
                )
            )
            budget = request.limits.budget_micros
            mcp_tools, role_brief = self._role_wiring(db, agent, task, request, run_scratch)
            if role_brief:
                prompt = prompt_builder.with_role_brief(
                    prompt, role_brief, mcp_tools, self.tool_prefix
                )
            if request.session is not None and request.session.mode == "resume":
                prompt = f"{prompt_builder.RESUME_HEADER}\n\n{prompt}"
        run_dir = (
            settings.resolved_data_dir / "agents" / request.agent.id / "cli-run" / request.run_id
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(run_dir, 0o700)
        secrets = [
            s for s in (secret, request.run_token, provider.api_key if provider else None) if s
        ]
        return RunPrep(
            cli=self.spec,
            mode="run",
            binary=binary,
            auth_mode=auth_mode,
            secret=secret,
            home=home,
            isolated_home=isolated,
            run_dir=run_dir,
            workspace=Path(request.workspace_dir),
            prompt=prompt,
            model=model,
            budget_usd=(budget / 1_000_000) if budget else None,
            max_run_seconds=request.limits.max_run_seconds,
            provider=provider,
            api_url=request.agent_api_url,
            run_token=request.run_token,
            run_id=request.run_id,
            task_id=request.task.id,
            task_kind=request.task.kind,
            workspace_kind=request.workspace_kind,
            mcp_python=settings.resolved_runtime_python,
            runtime_dir=settings.resolved_runtime_dir,
            secrets=secrets,
            mcp_tools=mcp_tools,
            role_brief=role_brief,
            config_dir=self._config_dir(state),
            ai_connection_id=state.get("connection_id"),
            session_mode=request.session.mode if request.session else None,
            session_id=request.session.id if request.session else None,
        )

    def _prepare_verify(self, request: ExecutionRequest) -> RunPrep:
        """M18 Part B: the work graph's self-check. Same sign-in as the work run (the routed AI
        connection), but a tool-less read-only call: ``mode`` "test" gives every CLI no tools, no
        company MCP and no token file; the prompt is the verification prompt alone."""
        binary = self.cli_env.find_binary(self.spec)
        if binary is None:
            raise RuntimeAdapterError(
                RuntimeErrorKind.UNAVAILABLE, f"{self.spec.display_name} is not installed"
            )
        settings = self._settings
        with self._session_factory() as db:
            agent = db.get(Agent, request.agent.id)
            company = db.get(Company, request.company.id)
            if agent is None or company is None or agent.company_id != company.id:
                raise RuntimeAdapterError(RuntimeErrorKind.REJECTED, "Agent or company not found")
            state = self._state_for(db, company.id, request)
            auth_mode, secret, provider, home, isolated = self._prep_common(
                db, state, company.id, agent.id, request.run_id
            )
            model = (
                (agent.runtime_config or {}).get("model")
                or (provider.model if provider else None)
                or state.get("model")
            )
        run_dir = (
            settings.resolved_data_dir
            / "agents"
            / request.agent.id
            / "cli-run"
            / f"{request.run_id}-verify"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(run_dir, 0o700)
        budget = request.limits.budget_micros
        return RunPrep(
            cli=self.spec,
            mode="test",
            binary=binary,
            auth_mode=auth_mode,
            secret=secret,
            home=home,
            isolated_home=isolated,
            run_dir=run_dir,
            workspace=Path(request.workspace_dir),
            prompt=request.task.description or "",
            model=model,
            budget_usd=(budget / 1_000_000) if budget else None,
            max_run_seconds=request.limits.max_run_seconds,
            provider=provider,
            run_id=request.run_id,
            task_id=request.task.id,
            task_kind=request.task.kind,
            workspace_kind="review",  # read-only copy semantics: nothing it does is kept
            secrets=[
                s for s in (secret, request.run_token, provider.api_key if provider else None) if s
            ],
            config_dir=self._config_dir(state),
            ai_connection_id=state.get("connection_id"),
            purpose="verify",
        )

    def _state_for(self, db: Session, company_id: str, request: ExecutionRequest) -> dict[str, Any]:
        """The sign-in this run uses: its AI connection's (M17), else the installation-level setting."""
        connection_id = getattr(request, "ai_connection_id", None)
        if not connection_id:
            return store.load(db, self.spec.id)
        from app.models.ai_stack import AiConnection
        from app.services import ai_stack

        row = db.get(AiConnection, connection_id)
        if row is None or row.company_id != company_id or row.cli_id != self.spec.id:
            raise RuntimeAdapterError(
                RuntimeErrorKind.CONFIG, "The AI connection chosen for this run is not available"
            )
        return ai_stack.run_auth(db, row, data_dir=self._settings.resolved_data_dir).as_state()

    def _config_dir(self, state: dict[str, Any]) -> Path | None:
        """Claude Code's per-connection ``CLAUDE_CONFIG_DIR`` (a plan connection's home)."""
        home = state.get("plan_home")
        return Path(home) if home and self.spec.id == "claude_code" else None

    def _role_wiring(
        self, db: Session, agent: Agent, task: Task, request: ExecutionRequest, scratch: Path
    ) -> tuple[list[str], str]:
        """(MCP tools, role brief) of this run. The tools are what a ``company_agent`` run of the same
        agent and task would get (``agent_toolkit.run_company_tools``), by MCP name, except that a
        specialist keeps today's fixed worker list. A management run also gets the role's
        instructions."""
        runtime_config = dict(agent.runtime_config or {})
        override = runtime_config.get("company_tools")
        tools = agent_toolkit.run_company_tools(db, agent, task, runtime_config)
        names = agent_toolkit.mcp_names(tools)
        role = agent_roles.role_of(db, agent)
        if (
            role == agent_roles.SPECIALIST
            and "decide_peer_review" not in names
            and task.kind != "meeting"  # M21: a meeting turn offers its own tools
        ):
            # A specialist's CLI keeps today's fixed worker list (it creates no tasks and proposes no
            # memory); only the tools its task kind adds (pull request / subscription review) come on top.
            base = set(
                agent_toolkit.mcp_names(agent_toolkit.company_tools_for(db, agent, override))
            )
            extras = [n for n in names if n not in base]
            fixed = [n for n in CLI_MCP_TOOLS if not isinstance(override, list) or n in override]
            names = list(dict.fromkeys([*fixed, *extras]))
        if not routing.is_management_run(db, agent, task):
            return names, ""
        from app.guardrails import peer
        from app.services import reviews

        storage = LocalFileStorage(self._settings.artifacts_dir)
        brief_task = {
            "kind": task.kind,
            "title": request.task.title or task.title,
            "review_target": reviews.review_target(db, storage, task)
            if task.kind == "review"
            else None,
            "peer_review": peer.review_target(db, task) if task.kind == "review" else None,
        }
        brief = role_brief_module.fetch(
            self._settings.resolved_runtime_python,
            self._settings.resolved_runtime_dir,
            scratch,
            role=role,
            tools=[f"company_{n}" for n in names],
            task=brief_task,
            tool_prefix=self.tool_prefix,
        )
        return names, brief

    def _dependency_briefs(self, db: Session, task: Task) -> list[prompt_builder.DependencyBrief]:
        """Briefs of the (up to two) newest finished tasks this task depends on, so the CLI knows
        what was delivered before it starts (the repository holds their files)."""
        deps = db.scalars(
            select(Task)
            .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
            .where(
                TaskDependency.task_id == task.id,
                TaskDependency.company_id == task.company_id,
                Task.company_id == task.company_id,
                Task.status == TaskStatus.DONE,
            )
            .order_by(Task.completed_at.desc(), Task.id)
            .limit(prompt_builder.MAX_DEPENDENCY_BRIEFS)
        ).all()
        storage = LocalFileStorage(self._settings.artifacts_dir)
        briefs: list[prompt_builder.DependencyBrief] = []
        for dep in deps:
            lines = []
            for art in task_briefs.artifact_briefs(
                db, storage, task.company_id, dep.id, max_artifacts=3, chars=400
            ):
                excerpt = clip(art.get("excerpt"), 300)
                lines.append(f"'{art['title']}'" + (f": {excerpt}" if excerpt else ""))
            briefs.append(
                prompt_builder.DependencyBrief(
                    title=dep.title, summary=dep.result_summary, artifacts=lines
                )
            )
        return briefs

    def _run_has_artifact(self, run_id: str) -> bool:
        with self._session_factory() as db:
            return (
                db.scalar(select(Artifact.id).where(Artifact.run_id == run_id).limit(1)) is not None
            )

    # ------------------------------------------------------------ MCP wiring

    def write_token_file(self, prep: RunPrep) -> Path:
        path = prep.run_dir / "run-token"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(prep.run_token or "")
        return path

    def mcp_server_env(self, prep: RunPrep, token_file: Path) -> dict[str, str]:
        """Environment of the MCP server only. The token is in a 0600 file, not here."""
        assert prep.runtime_dir is not None
        env = {
            "COMPANY_OS_API_URL": prep.api_url or "",
            "COMPANY_OS_RUN_TOKEN_FILE": str(token_file),
            "COMPANY_OS_RUN_ID": prep.run_id,
            "COMPANY_OS_TASK_ID": prep.task_id,
            "COMPANY_OS_WORKSPACE": str(prep.workspace),
            "COMPANY_OS_TASK_KIND": prep.task_kind,
            "COMPANY_OS_MCP_TOOLS": ",".join(prep.mcp_tools or CLI_MCP_TOOLS),
            "PYTHONPATH": str(prep.runtime_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HERMES_HOME": str(prep.run_dir / "hermes"),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "HOME": str(prep.run_dir / "hermes-home"),
        }
        if "PATH" in os.environ:
            env["PATH"] = os.environ["PATH"]
        return env

    def mcp_command(self, prep: RunPrep) -> list[str]:
        assert prep.mcp_python is not None
        return [str(prep.mcp_python), "-m", "company_agent.mcp_server"]

    # ------------------------------------------------------------ process env

    def child_env(self, prep: RunPrep) -> dict[str, str]:
        env = base_env()
        env["PATH"] = self.cli_env.search_path(self.spec)
        env["HOME"] = str(prep.home)
        return env

    # ------------------------------------------------------------ execute

    def execute(
        self, request: ExecutionRequest, sink: EventSink, cancel: CancelToken
    ) -> ExecutionResult:
        prep = (
            self._prepare_verify(request) if request.purpose == "verify" else self._prepare(request)
        )
        try:
            return self._execute(prep, sink, cancel)
        finally:
            shutil.rmtree(prep.run_dir, ignore_errors=True)  # the token file goes with it

    def _execute(self, prep: RunPrep, sink: EventSink, cancel: CancelToken) -> ExecutionResult:
        prep.workspace.mkdir(parents=True, exist_ok=True)
        invocation = self.build(prep)
        scan = [a for i, a in enumerate(invocation.argv) if i != invocation.prompt_index]
        assert_safe_argv(scan)
        assert_safe_argv([invocation.config_text] if invocation.config_text else [])
        # Only a plain per-run directory needs a snapshot; a project worktree is diffed by git.
        before = (
            workspace.snapshot(prep.workspace)
            if prep.workspace_kind == "run_dir"
            else workspace.Snapshot({})
        )
        parsed = Parsed()
        stderr_tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        parser = self.parser_class(prep, parsed)
        emit_lock = threading.Lock()

        def emit(type_: str, payload: dict[str, Any]) -> None:
            with emit_lock:
                seq = parsed.seq
                parsed.seq += 1
            payload = _scrub(payload, prep.secrets)
            try:
                sink.emit(ExecutionEvent(seq=seq, type=type_, payload=payload))
                parsed.emitted += 1
            except Exception:
                logger.warning("could not persist CLI event seq=%s", seq, exc_info=False)

        emit(
            "run.started",
            {
                "runtime": self.id,
                "auth_mode": prep.auth_mode,
                "model": prep.model,
                "isolated_home": prep.isolated_home,
                "message": f"Started {self.spec.display_name}",
            },
        )
        try:
            proc = subprocess.Popen(
                invocation.argv,
                cwd=invocation.cwd,
                env=invocation.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeAdapterError(
                RuntimeErrorKind.UNAVAILABLE,
                f"Cannot start {self.spec.display_name} ({type(exc).__name__})",
            ) from exc
        threads = [
            threading.Thread(
                target=self._read_stdout, args=(proc, parser, parsed, emit, prep), daemon=True
            ),
            threading.Thread(
                target=self._read_stderr, args=(proc, stderr_tail, prep.secrets), daemon=True
            ),
        ]
        for t in threads:
            t.start()
        killed = False
        try:
            killed = self._supervise(proc, cancel)
        finally:
            self._reap(proc)
            for t in threads:
                t.join(timeout=5)
        return self._finish(
            prep, proc.returncode, parsed, stderr_tail, cancel, killed, before, emit
        )

    # -- process control (same contract as the company agent adapter)

    def _supervise(self, proc: subprocess.Popen, cancel: CancelToken) -> bool:
        term_at: float | None = None
        grace = self._settings.runtime_kill_grace_seconds
        while proc.poll() is None:
            if cancel.is_set() and term_at is None:
                term_at = time.monotonic()
                self._signal(proc, signal.SIGTERM, group=False)
            if term_at is not None and time.monotonic() - term_at >= grace:
                logger.warning("CLI did not stop %.0fs after SIGTERM; killing it", grace)
                self._signal(proc, signal.SIGKILL, group=True)
                proc.wait()
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int, *, group: bool) -> None:
        try:
            if group:
                os.killpg(proc.pid, sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _reap(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            self._signal(proc, signal.SIGKILL, group=True)
            proc.wait()
        else:
            self._signal(proc, signal.SIGKILL, group=True)  # grandchildren of a finished run

    @staticmethod
    def _read_stderr(proc: subprocess.Popen, tail: collections.deque, secrets: list[str]) -> None:
        assert proc.stderr is not None
        try:
            for raw in iter(lambda: proc.stderr.readline(MAX_LINE_BYTES), b""):  # type: ignore[union-attr]
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    tail.append(redact(text, secrets)[:500])
        except (OSError, ValueError):
            pass

    def _read_stdout(self, proc, parser: StreamParser, parsed: Parsed, emit, prep: RunPrep) -> None:
        assert proc.stdout is not None
        discarding = False
        try:
            while True:
                raw = proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    return
                complete = raw.endswith(b"\n")
                if discarding:
                    discarding = not complete
                    continue
                if len(raw) > MAX_LINE_BYTES:
                    parsed.dropped["oversized_line"] += 1
                    discarding = not complete
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    parsed.dropped["malformed"] += 1
                    continue
                if not isinstance(obj, dict):
                    parsed.dropped["malformed"] += 1
                    continue
                try:
                    events = parser.feed(obj)
                except Exception:  # an unexpected shape must not kill the reader
                    parsed.dropped["unparsable"] += 1
                    continue
                for type_, payload in events:
                    emit(type_, payload)
        except (OSError, ValueError):
            return

    # ------------------------------------------------------------ result mapping

    def billing(self, prep: RunPrep, parsed: Parsed) -> dict[str, Any]:
        if prep.local_model:
            return {
                "cost_micros": 0,
                "cost_kind": CostKind.VERIFIED.value,
                "note": LOCAL_COST_NOTE,
                "local": True,
            }
        if prep.auth_mode == "own_login":
            return {
                "cost_micros": 0,
                "cost_kind": CostKind.UNKNOWN.value,
                "note": "Used the person's own CLI login (subscription): no charge is recorded.",
                "local": False,
                "subscription": True,
            }
        if parsed.cost_usd is not None and parsed.cost_usd > 0:
            return {
                "cost_micros": int(round(parsed.cost_usd * 1_000_000)),
                "cost_kind": CostKind.ESTIMATED.value,
                "note": "Estimated by the CLI's own price table.",
                "local": False,
            }
        return {
            "cost_micros": None,
            "cost_kind": CostKind.UNKNOWN.value,
            "note": "The CLI reported no cost.",
            "local": False,
        }

    def usage_report(self, prep: RunPrep, parsed: Parsed) -> UsageReport | None:
        no_usage = not (parsed.input_tokens or parsed.output_tokens) and parsed.cost_usd is None
        if no_usage and not (prep.local_model or prep.auth_mode == "own_login"):
            return None
        bill = self.billing(prep, parsed)
        if prep.auth_mode == "own_login" and not prep.local_model:
            return UsageReport(
                input_tokens=parsed.input_tokens or None,
                output_tokens=parsed.output_tokens or None,
                cost_micros=0,
                cost_kind=CostKind.UNKNOWN,
                billing_note=SUBSCRIPTION_NOTE,
            )
        return UsageReport(
            input_tokens=parsed.input_tokens or None,
            output_tokens=parsed.output_tokens or None,
            cost_micros=bill["cost_micros"],
            cost_kind=CostKind(bill["cost_kind"]),
        )

    def _run_proposed(self, run_id: str) -> bool:
        """M18 Part B: in a work-graph run ``complete_task`` only *proposes* a result (the task stays
        in progress until the graph's finish node), which counts as the CLI having completed."""
        with self._session_factory() as db:
            row = db.get(TaskRun, run_id)
            return row is not None and row.proposed_result is not None

    def _task_open(self, task_id: str) -> bool:
        with self._session_factory() as db:
            task = db.get(Task, task_id)
            return task is not None and task.status == TaskStatus.IN_PROGRESS

    def _finish(
        self,
        prep: RunPrep,
        code: int | None,
        parsed: Parsed,
        stderr_tail: collections.deque[str],
        cancel: CancelToken,
        killed: bool,
        before: workspace.Snapshot,
        emit,
    ) -> ExecutionResult:
        usage = self.usage_report(prep, parsed)
        if parsed.dropped:
            logger.info("run %s: dropped CLI lines %s", prep.run_id, dict(parsed.dropped))
        if usage is not None:
            emit(
                "usage",
                {
                    "input_tokens": parsed.input_tokens,
                    "output_tokens": parsed.output_tokens,
                    "billing": self.billing(prep, parsed),
                    "final": True,
                },
            )
        if cancel.is_set():
            emit("run.cancelled", {"summary": "Cancelled"})
            return ExecutionResult(
                status="cancelled",
                error=ExecutionError(RuntimeErrorKind.CANCELLED, "Cancelled"),
                usage=usage,
            )
        if prep.purpose == "verify":
            text = parsed.final_text.strip()
            if parsed.error or code not in (0, None) or not text:
                message = redact(
                    self.classify_failure(parsed) if parsed.error else "The self-check failed",
                    prep.secrets,
                )[:300]
                emit("run.failed", {"summary": message, "error": message})
                return ExecutionResult(
                    status="failed",
                    error=ExecutionError(RuntimeErrorKind.FAILED, message, retryable=True),
                    usage=usage,
                )
            emit("run.completed", {"summary": clip(text, 300)})
            return ExecutionResult(status="succeeded", summary=text[:4000], usage=usage)
        completed = not self._task_open(prep.task_id) or self._run_proposed(prep.run_id)
        if completed:
            summary = clip(parsed.final_text, 1000) or None
            emit(
                "run.completed", {"summary": summary or "Task completed through the company tools"}
            )
            # A plain per-run directory is not a repository: files the CLI wrote but never
            # submitted would be lost with it, so they become artifacts (a project worktree gets
            # its "Changes" artifact from the control plane instead).
            kept: list[ArtifactRef] = []
            if prep.workspace_kind == "run_dir" and not self._run_has_artifact(prep.run_id):
                kept = workspace.changed_files(prep.workspace, before)
            return ExecutionResult(status="succeeded", summary=summary, usage=usage, artifacts=kept)

        # The CLI never called complete_task: keep what it made, then apply the M5 rules.
        artifacts: list[ArtifactRef] = (
            workspace.changed_files(prep.workspace, before)
            if prep.workspace_kind == "run_dir"
            else []  # a project worktree is committed (and kept on a branch) by the control plane
        )
        if parsed.final_text.strip():
            artifacts.append(
                ArtifactRef(
                    title="final-message.md",
                    content=parsed.final_text.strip()[:200_000],
                    kind="document",
                    mime_type="text/markdown",
                )
            )
        tail = " | ".join(list(stderr_tail)[-3:])
        if parsed.error:
            message = redact(self.classify_failure(parsed), prep.secrets)
        elif killed:
            message = f"{self.spec.display_name} was killed after it ignored SIGTERM"
        elif code not in (0, None):
            message = f"{self.spec.display_name} exited with code {code}" + (
                f": {tail}" if tail else ""
            )
        else:
            message = "task_not_completed: the CLI finished without calling complete_task" + (
                f" ({len(artifacts)} file(s) were kept as artifacts)" if artifacts else ""
            )
        message = redact(message, prep.secrets)[:1000]
        emit(
            "run.failed",
            {
                "summary": message,
                "error": message,
                "exit_reason": "task_not_completed"
                if not parsed.error and code in (0, None)
                else "failed",
            },
        )
        return ExecutionResult(
            status="failed",
            error=ExecutionError(RuntimeErrorKind.FAILED, message, retryable=True),
            usage=usage,
            artifacts=artifacts,
        )

    # ------------------------------------------------------------ connectivity test

    def prepare_test(
        self, db: Session, state: dict[str, Any], company_id: str | None = None
    ) -> tuple[RunPrep | None, str]:
        """The preparation of the connectivity test: (prep, "") or (None, why it cannot run)."""
        binary = self.cli_env.find_binary(self.spec)
        if binary is None:
            return None, f"{self.spec.display_name} is not installed"
        if not state.get("auth_mode"):
            return None, "Choose how it signs in first"
        company = company_id or _credential_company(db, state)
        try:
            auth_mode, secret, provider, home, isolated = self._prep_common(
                db, state, company or "", f"test-{self.spec.id}", "test"
            )
        except RuntimeAdapterError as exc:
            return None, exc.message
        tmp = Path(tempfile.mkdtemp(prefix=f"cos-cli-test-{self.spec.id}-"))
        (tmp / "ws").mkdir()
        (tmp / "run").mkdir(mode=0o700)
        return (
            RunPrep(
                cli=self.spec,
                mode="test",
                binary=binary,
                auth_mode=auth_mode,
                secret=secret,
                home=home,
                isolated_home=isolated,
                run_dir=tmp / "run",
                workspace=tmp / "ws",
                prompt="Reply with the single word OK.",
                model=(provider.model if provider else None) or state.get("model"),
                budget_usd=TEST_BUDGET_USD,
                max_run_seconds=TEST_TIMEOUT_SECONDS,
                provider=provider,
                secrets=[s for s in (secret, provider.api_key if provider else None) if s],
                config_dir=self._config_dir(state),
                ai_connection_id=state.get("connection_id"),
            ),
            "",
        )

    def run_test(
        self, db: Session, state: dict[str, Any], company_id: str | None = None
    ) -> tuple[bool, str]:
        """A tiny headless prompt in a temp dir with a hard timeout and a budget cap. Returns
        (ok, detail). Runs the real CLI (the person pressed "Test")."""
        prep, why = self.prepare_test(db, state, company_id)
        if prep is None:
            return False, why
        try:
            return self._run_test_process(prep)
        finally:
            shutil.rmtree(prep.run_dir.parent, ignore_errors=True)

    def _run_test_process(self, prep: RunPrep) -> tuple[bool, str]:
        invocation = self.build(prep)
        scan = [a for i, a in enumerate(invocation.argv) if i != invocation.prompt_index]
        assert_safe_argv(scan)
        assert_safe_argv([invocation.config_text] if invocation.config_text else [])
        parsed = Parsed()
        parser = self.parser_class(prep, parsed)
        try:
            proc = subprocess.Popen(
                invocation.argv,
                cwd=invocation.cwd,
                env=invocation.env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return False, f"Cannot start {self.spec.display_name} ({type(exc).__name__})"
        tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        threads = [
            threading.Thread(
                target=self._read_stdout,
                args=(proc, parser, parsed, lambda t, p: None, prep),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_stderr, args=(proc, tail, prep.secrets), daemon=True
            ),
        ]
        for t in threads:
            t.start()
        cancel = CancelToken()
        timer = threading.Timer(TEST_TIMEOUT_SECONDS, cancel.set)
        timer.start()
        try:
            killed = self._supervise(proc, cancel)
        finally:
            timer.cancel()
            self._reap(proc)
            for t in threads:
                t.join(timeout=5)
        if cancel.is_set():
            return False, f"No answer within {TEST_TIMEOUT_SECONDS} seconds"
        if parsed.error:
            return False, redact(self.classify_failure(parsed), prep.secrets)[:300]
        if proc.returncode not in (0, None):
            detail = " | ".join(list(tail)[-2:])
            return False, redact(f"Exited with code {proc.returncode}: {detail}", prep.secrets)[
                :300
            ]
        text = parsed.final_text.strip()
        if not text:
            return False, "The CLI ran but returned no answer" + (" (killed)" if killed else "")
        return True, f"Answered: {clip(text, 80)}"


def _credential_company(db: Session, state: dict[str, Any]) -> str | None:
    from app.models.provider import Credential

    cred = state.get("credential_id")
    if cred:
        row = db.get(Credential, cred)
        if row is not None:
            return row.company_id
    provider_id = state.get("model_provider_id")
    if provider_id:
        row = db.get(ModelProvider, provider_id)
        if row is not None:
            return row.company_id
    return None


def _scrub(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {str(k): _scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, secrets) for v in value]
    return value
