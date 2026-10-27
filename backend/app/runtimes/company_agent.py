"""Company agent runtime adapter: spawns our Hermes fork (``runtime/``) once per run.

Protocol (runtime/README.md section 5): cwd = ``runtime/``, interpreter = the runtime's own venv,
ONE JSON run spec on stdin (the only place the model key and the run token ever travel -- never
argv, never env, never disk), JSONL events on stdout, redacted logs on stderr. Exit codes: 0
completed, 1 failed, 2 partial (iteration/time limit), 3 bad spec, 130 cancelled. Cancel =
SIGTERM, SIGKILL (whole process group) after ``runtime_kill_grace_seconds``.

The process sees a minimal environment: never the owner token, ``COMPANY_OS_*`` settings, the
secret key or any provider key. Before every run the agent's HERMES_HOME (``data/agents/<id>/home``)
gets exactly its assigned active skills (``skills.materialize_for_agent``).
"""

from __future__ import annotations

import collections
import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.crypto import CredentialCipher, CredentialDecryptError
from app.core.errors import AppError
from app.core.keys import SecretKeyProvider
from app.core.time import utcnow
from app.guardrails import peer
from app.guardrails import registry as guard_registry
from app.models.agent import Agent
from app.models.company import Company
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.runtimes.base import (
    CancelToken,
    Capability,
    ConfigIssue,
    CostKind,
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
from app.runtimes.sandbox import DockerProbe, resolve_backend
from app.services import (
    agent_roles,
    agent_toolkit,
    credentials,
    model_providers,
    policy,
    reviews,
    standards,
)
from app.services import memory as memory_service
from app.services import skills as skills_service
from app.skills.blobstore import BlobStore
from app.storage.local import LocalFileStorage

logger = logging.getLogger("company_os.runtime.company_agent")

PROTOCOL = 1
MAX_WORKSPACE_FILES = 60
# Events the runtime may emit. The server's own `progress` events (POST /progress) are numbered
# from SERVER_SEQ_FLOOR; a launcher seq at or above it is dropped so the two can never collide.
SERVER_SEQ_FLOOR = 1_000_000
KNOWN_EVENT_TYPES = frozenset(
    {
        "run.started",
        "message",
        "reasoning",
        "tool.started",
        "tool.completed",
        "progress",
        "usage",
        "artifact",
        "run.completed",
        "run.failed",
        "run.cancelled",
    }
)
TERMINAL_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})
MAX_LINE_BYTES = 1_000_000
STDERR_TAIL_LINES = 40
DEFAULT_MAX_ITERATIONS = 30
LOCAL_COST_NOTE = "Local model: no provider charge, recorded as $0 (verified)."

# Environment variables copied from the worker into the runtime process. Everything else
# (COMPANY_OS_*, HERMES_* keys, cloud credentials, ...) is dropped.
ENV_ALLOW = (
    "PATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SYSTEMROOT",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "DOCKER_HOST",
)
RUNTIME_CONFIG_KEYS = {
    "max_iterations": "integer 1-500 (default 30)",
    "toolsets": "list: restrict the role's toolsets further",
    "company_tools": "list: restrict the role's company tools further",
    # M10: stay on the company agent even when the company's work mode is "hybrid" (owner only)
    "pin_runtime": "true: never route this agent's work tasks to a coding CLI",
    # M12: keep this agent on its own model instead of the company's management/work model
    "pin_model": "true: keep this agent on its own model (owner only)",
}


@dataclass
class _Prepared:
    spec: dict[str, Any]
    secrets: list[str]
    model: model_providers.ResolvedModel
    hermes_home: Path


@dataclass
class _Stream:
    """What the stdout reader learned (single writer: the reader thread)."""

    last_seq: int = 0
    terminal_type: str | None = None
    terminal_payload: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None
    dropped: collections.Counter = field(default_factory=collections.Counter)
    emitted: int = 0


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def redact_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 6 and secret in text:
            text = text.replace(secret, "[redacted]")
    return text


def scrub_secrets(value: Any, secrets: list[str]) -> Any:
    """Deep copy of ``value`` with every known secret string replaced."""
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, dict):
        return {str(k): scrub_secrets(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_secrets(v, secrets) for v in value]
    return value


class CompanyAgentAdapter(RuntimeAdapter):
    id = "company_agent"
    display_name = "Company Agent (Hermes fork)"
    kind = "agent_runtime"
    supports_execution = True
    config_hint = (
        "Install the runtime venv (see runtime/README.md: `uv sync` in runtime/), register a model "
        "provider (POST /api/companies/{id}/model-providers/detect for local Ollama), then give an "
        "agent runtime 'company_agent'. Override the interpreter with COMPANY_OS_RUNTIME_PYTHON."
    )

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        keys: SecretKeyProvider,
        *,
        docker: DockerProbe | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._cipher = CredentialCipher(keys)
        self._docker = docker or DockerProbe()
        self._health_lock = threading.Lock()
        self._health_cache: tuple[float, RuntimeHealth] | None = None

    def __repr__(self) -> str:
        return "CompanyAgentAdapter()"

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

    # ------------------------------------------------------------ config + health

    def validate_config(self, config: dict[str, Any]) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        config = config or {}
        for key, value in config.items():
            if key not in RUNTIME_CONFIG_KEYS:
                issues.append(ConfigIssue(key, "unknown runtime_config key", "warning"))
            elif key == "max_iterations" and not (
                isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 500
            ):
                issues.append(ConfigIssue(key, "must be an integer between 1 and 500"))
            elif key in ("pin_runtime", "pin_model") and not isinstance(value, bool):
                issues.append(ConfigIssue(key, "must be true or false"))
            elif key in ("toolsets", "company_tools") and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)
            ):
                issues.append(ConfigIssue(key, "must be a list of strings"))
        return issues

    def _interpreter(self) -> Path:
        return self._settings.resolved_runtime_python

    def _runtime_dir(self) -> Path:
        return self._settings.resolved_runtime_dir

    def check_health(self) -> RuntimeHealth:
        with self._health_lock:
            if self._health_cache and time.monotonic() - self._health_cache[0] < 15.0:
                return self._health_cache[1]
        health = self._probe_health()
        with self._health_lock:
            self._health_cache = (time.monotonic(), health)
        return health

    def _probe_health(self) -> RuntimeHealth:
        python, runtime_dir = self._interpreter(), self._runtime_dir()
        if not python.exists():
            return RuntimeHealth(
                HealthStatus.NOT_CONFIGURED,
                f"Runtime interpreter not found at {python}. Create the runtime virtualenv "
                "(`uv sync` in runtime/) or set COMPANY_OS_RUNTIME_PYTHON.",
            )
        if not (runtime_dir / "company_agent" / "run.py").is_file():
            return RuntimeHealth(
                HealthStatus.NOT_CONFIGURED, f"company_agent package not found in {runtime_dir}"
            )
        try:
            done = subprocess.run(
                [str(python), "-m", "company_agent.run", "--version"],
                cwd=runtime_dir,
                env=self._health_env(),
                capture_output=True,
                text=True,
                timeout=self._settings.runtime_health_timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return RuntimeHealth(HealthStatus.UNAVAILABLE, "Runtime --version timed out")
        except OSError as exc:
            return RuntimeHealth(
                HealthStatus.UNAVAILABLE, f"Cannot start the runtime ({type(exc).__name__})"
            )
        if done.returncode != 0:
            return RuntimeHealth(
                HealthStatus.UNAVAILABLE, f"Runtime --version exited with code {done.returncode}"
            )
        try:
            info = json.loads(done.stdout.strip().splitlines()[-1])
            version = str(info.get("company_agent", "?"))
            protocol = info.get("protocol")
        except (ValueError, IndexError, AttributeError):
            return RuntimeHealth(HealthStatus.UNAVAILABLE, "Runtime --version printed no JSON")
        if protocol != PROTOCOL:
            return RuntimeHealth(
                HealthStatus.UNAVAILABLE,
                f"Runtime speaks protocol {protocol!r}; this backend needs {PROTOCOL}",
            )
        docker = self._docker.status()
        sandbox = (
            "sandbox: docker available (not exercised in M3b)"
            if docker.reachable
            else f"sandbox: none ({docker.detail}); terminal and code_execution are never granted"
        )
        return RuntimeHealth(
            HealthStatus.OK, f"company-agent {version}, protocol {protocol}; {sandbox}"
        )

    @staticmethod
    def _health_env() -> dict[str, str]:
        return {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ}

    # ------------------------------------------------------------ spec

    def _prepare(self, request: ExecutionRequest) -> _Prepared:
        settings = self._settings
        with self._session_factory() as db:
            agent = db.get(Agent, request.agent.id)
            company = db.get(Company, request.company.id)
            if agent is None or company is None or agent.company_id != company.id:
                raise RuntimeAdapterError(RuntimeErrorKind.REJECTED, "Agent or company not found")
            run_row = db.get(TaskRun, request.run_id)
            try:
                # M6 routing: the dispatcher may have routed this run to another provider/model.
                model = model_providers.resolve_for_agent(
                    db,
                    agent,
                    provider_id=run_row.model_provider_id if run_row else None,
                    model_name=run_row.model_used if run_row else None,
                )
            except model_providers.ModelResolutionError as exc:
                raise RuntimeAdapterError(RuntimeErrorKind.CONFIG, str(exc)) from exc
            api_key, key_is_secret = self._api_key(db, agent, model)

            runtime_config = dict(agent.runtime_config or {})
            for issue in self.validate_config(runtime_config):
                if issue.severity == "error":
                    raise RuntimeAdapterError(
                        RuntimeErrorKind.CONFIG, f"runtime_config.{issue.field}: {issue.message}"
                    )
            limits = policy.load_policy(db, company.id)
            sandbox = resolve_backend(limits.sandbox_backend, self._docker.status())
            if sandbox is None:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.UNAVAILABLE,
                    "Company policy requires a docker sandbox but the docker daemon is not "
                    "reachable",
                    retryable=True,
                )
            toolsets = agent_toolkit.toolsets_for(
                db, agent, sandbox=sandbox, runtime_config=runtime_config
            )
            company_tools = agent_toolkit.company_tools_for(
                db, agent, runtime_config.get("company_tools")
            )
            task_peek = db.get(Task, request.task.id)
            peer_target = (
                peer.review_target(db, task_peek)
                if task_peek is not None and task_peek.kind == "review"
                else None
            )
            if peer_target is not None:
                # A peer review run decides one thing; nothing else is on offer.
                company_tools = ["company_decide_peer_review", "company_complete_task"]
            manager = db.get(Agent, agent.manager_agent_id) if agent.manager_agent_id else None
            reports = agent_toolkit.direct_reports(db, agent)
            role = agent_roles.role_of(db, agent)
            memory_lines = memory_service.context_lines(
                memory_service.relevant_for(db, company.id, agent)
            )
            task_row = db.get(Task, request.task.id)
            project_name = None
            if task_row is not None and task_row.project_id:
                project_row = db.get(Project, task_row.project_id)
                if project_row is not None and project_row.company_id == company.id:
                    project_name = project_row.name
            review_target = (
                reviews.review_target(db, LocalFileStorage(settings.artifacts_dir), task_row)
                if task_row is not None and task_row.kind == "review"
                else None
            )

            hermes_home = settings.resolved_data_dir / "agents" / agent.id / "home"
            hermes_home.mkdir(parents=True, exist_ok=True)
            result = skills_service.materialize_for_agent(
                db,
                company.id,
                agent.id,
                hermes_home,
                blobs=BlobStore(settings.skills_blobs_dir),
            )
            if result.skipped:
                logger.warning(
                    "run %s: %d assigned skill(s) not materialized",
                    request.run_id,
                    len(result.skipped),
                )
            max_iterations = runtime_config.get("max_iterations", DEFAULT_MAX_ITERATIONS)
            task = request.task
            description = task.description or ""
            if task.review_note:
                feedback = f"REVISION REQUESTED. Address this feedback:\n{task.review_note}"
                description = f"{description}\n\n{feedback}".strip()
            spec: dict[str, Any] = {
                "protocol": PROTOCOL,
                "run_id": request.run_id,
                "company": {
                    "id": company.id,
                    "name": company.name,
                    "mission": company.mission,
                    "strategic_goal": company.strategic_goal,
                    "constraints": list(company.constraints or []),
                    # Verified company memory that applies to this agent.
                    "memory": memory_lines,
                    # M10: standards the Founder set; they bind every agent's work.
                    "standards": standards.context_lines(db, company.id, agent),
                },
                "agent": {
                    "id": agent.id,
                    "name": agent.name,
                    "role_title": agent.role_title,
                    "kind": agent.kind,
                    "role": role,
                    "role_family": agent.role_family,
                    "responsibilities": agent.responsibilities or "",
                    "instructions": agent.instructions or "",
                    "manager": (
                        {"id": manager.id, "name": manager.name, "role_title": manager.role_title}
                        if manager
                        else None
                    ),
                    "direct_reports": [
                        {"id": r.id, "name": r.name, "role_title": r.role_title} for r in reports
                    ],
                },
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "description": description,
                    "acceptance_criteria": task.acceptance_criteria or "",
                    "kind": task.kind,
                    "priority": task.priority,
                    "input_refs": list(task.input_refs),
                    "review_target": review_target,
                    "peer_review": peer_target,
                },
                "model": {
                    "provider": model.runtime_provider,
                    "base_url": model.runtime_base_url(),
                    "api_key": api_key,
                    "model": model.model,
                    "context_length": model.context_length,
                    "is_local": model.is_local,
                },
                "toolsets": toolsets,
                "company_tools": company_tools,
                # M6: the tier of every tool the run can reach (the runtime refuses any other),
                # which results are untrusted data, and which tiers it asks the control plane about.
                "guardrails": guard_registry.runtime_spec(toolsets, company_tools),
                "limits": {
                    "max_iterations": max_iterations,
                    "max_run_seconds": request.limits.max_run_seconds,
                },
                "agent_api": {"url": request.agent_api_url, "token": request.run_token},
                "workspace_dir": request.workspace_dir,
                "hermes_home": str(hermes_home),
                "sandbox": (
                    {
                        "backend": "docker",
                        "docker_image": settings.sandbox_docker_image,
                        "network": False,
                    }
                    if sandbox == "docker"
                    else {"backend": "none"}
                ),
            }
        if request.workspace_kind in ("project", "review"):
            # M12: the shared project repository. A plain per-run directory sends nothing.
            spec["workspace"] = {
                "kind": request.workspace_kind,
                "project_name": project_name,
                "files": list(request.workspace_files)[:MAX_WORKSPACE_FILES],
            }
        # The local placeholder key is a public constant, not a secret (scrubbing "ollama" would
        # mangle ordinary text).
        secrets = [s for s in (api_key if key_is_secret else None, request.run_token) if s]
        return _Prepared(spec, secrets, model, hermes_home)

    def _api_key(
        self, db: Session, agent: Agent, model: model_providers.ResolvedModel
    ) -> tuple[str, bool]:
        """(key for the run spec, whether it is a real secret to scrub from events)."""
        if model.credential_id:
            try:
                key = credentials.reveal(db, self._cipher, agent.company_id, model.credential_id)
                return key, True
            except CredentialDecryptError as exc:
                raise RuntimeAdapterError(
                    RuntimeErrorKind.CONFIG,
                    "The model credential cannot be decrypted (was the secret key replaced?)",
                ) from exc
            except AppError as exc:
                raise RuntimeAdapterError(RuntimeErrorKind.CONFIG, exc.message) from exc
        if model.is_local:
            return model_providers.LOCAL_API_KEY_PLACEHOLDER, False
        raise RuntimeAdapterError(
            RuntimeErrorKind.CONFIG, f"Model provider {model.provider_name!r} has no credential"
        )

    def _process_env(self, hermes_home: Path) -> dict[str, str]:
        env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}
        env.update(
            {
                "HERMES_HOME": str(hermes_home),
                "HOME": str(hermes_home / "home"),
                "HERMES_DISABLE_LAZY_INSTALLS": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return env

    # ------------------------------------------------------------ execute

    def execute(
        self, request: ExecutionRequest, sink: EventSink, cancel: CancelToken
    ) -> ExecutionResult:
        python, runtime_dir = self._interpreter(), self._runtime_dir()
        if not python.exists():
            raise RuntimeAdapterError(
                RuntimeErrorKind.UNAVAILABLE, f"Runtime interpreter not found at {python}"
            )
        prepared = self._prepare(request)
        env = self._process_env(prepared.hermes_home)
        try:
            proc = subprocess.Popen(
                [str(python), "-m", "company_agent.run"],
                cwd=runtime_dir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,  # own process group: terminal-tool children die with it
            )
        except OSError as exc:
            raise RuntimeAdapterError(
                RuntimeErrorKind.UNAVAILABLE, f"Cannot start the runtime ({type(exc).__name__})"
            ) from exc

        spec_bytes = json.dumps(prepared.spec).encode("utf-8")
        stream = _Stream()
        stderr_tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL_LINES)
        threads = [
            threading.Thread(
                target=self._feed_stdin, args=(proc, spec_bytes), name="ca-stdin", daemon=True
            ),
            threading.Thread(
                target=self._read_stdout,
                args=(proc, sink, stream, prepared),
                name="ca-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=self._read_stderr,
                args=(proc, stderr_tail, prepared.secrets),
                name="ca-stderr",
                daemon=True,
            ),
        ]
        for t in threads:
            t.start()
        del spec_bytes

        killed = False
        try:
            killed = self._supervise(proc, cancel)
        finally:
            self._reap(proc)
            for t in threads:
                t.join(timeout=5)
        return self._result(proc.returncode, stream, stderr_tail, prepared, cancel, killed)

    def _supervise(self, proc: subprocess.Popen, cancel: CancelToken) -> bool:
        """Wait for exit. On cancel: SIGTERM, then SIGKILL the process group after the grace
        period. Returns True if the process had to be killed."""
        term_at: float | None = None
        grace = self._settings.runtime_kill_grace_seconds
        while proc.poll() is None:
            if cancel.is_set() and term_at is None:
                term_at = time.monotonic()
                self._signal(proc, signal.SIGTERM, group=False)
            if term_at is not None and time.monotonic() - term_at >= grace:
                logger.warning("runtime did not stop %.0fs after SIGTERM; killing it", grace)
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
        """Never leave stragglers: kill whatever is still in the process group."""
        if proc.poll() is None:  # an exception escaped the supervisor
            self._signal(proc, signal.SIGKILL, group=True)
            proc.wait()
        else:
            self._signal(proc, signal.SIGKILL, group=True)  # grandchildren of a finished run

    @staticmethod
    def _feed_stdin(proc: subprocess.Popen, payload: bytes) -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(payload)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    @staticmethod
    def _read_stderr(proc: subprocess.Popen, tail: collections.deque, secrets: list[str]) -> None:
        assert proc.stderr is not None
        try:
            for raw in iter(lambda: proc.stderr.readline(MAX_LINE_BYTES), b""):  # type: ignore[union-attr]
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    tail.append(redact_text(text, secrets)[:500])
        except (OSError, ValueError):
            pass

    def _read_stdout(
        self, proc: subprocess.Popen, sink: EventSink, stream: _Stream, prepared: _Prepared
    ) -> None:
        assert proc.stdout is not None
        discarding = False
        try:
            while True:
                raw = proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    return
                complete = raw.endswith(b"\n")
                if discarding:  # the tail of an oversized line
                    discarding = not complete
                    continue
                if len(raw) > MAX_LINE_BYTES:
                    stream.dropped["oversized_line"] += 1
                    discarding = not complete
                    continue
                self._handle_line(raw, sink, stream, prepared)
        except (OSError, ValueError):
            return

    def _handle_line(
        self, raw: bytes, sink: EventSink, stream: _Stream, prepared: _Prepared
    ) -> None:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except ValueError:
            stream.dropped["malformed"] += 1
            return
        if not isinstance(obj, dict):
            stream.dropped["malformed"] += 1
            return
        seq, type_, payload = obj.get("seq"), obj.get("type"), obj.get("payload")
        if not _positive_int(seq) or seq < 1 or not isinstance(type_, str):
            stream.dropped["malformed"] += 1
            return
        if type_ not in KNOWN_EVENT_TYPES:
            stream.dropped["unknown_type"] += 1
            return
        if seq >= SERVER_SEQ_FLOOR:
            stream.dropped["reserved_seq"] += 1  # would collide with the server's progress events
            return
        if seq <= stream.last_seq:
            stream.dropped["duplicate_seq"] += 1  # replay / out of order: the first one wins
            return
        if stream.terminal_type is not None:
            stream.dropped["after_terminal"] += 1
            return
        if not isinstance(payload, dict):
            payload = {}
        payload = scrub_secrets(
            payload, prepared.secrets
        )  # belt and braces: the launcher redacts too
        stream.last_seq = seq

        if type_ == "usage":
            stream.usage = payload
        payload = dict(payload)
        if type_ == "usage" or type_ in TERMINAL_TYPES:
            usage = payload.get("usage") if type_ in TERMINAL_TYPES else payload
            if type_ in TERMINAL_TYPES and isinstance(usage, dict):
                stream.usage = usage
            payload["billing"] = self._billing(prepared.model, stream.usage)
        if type_ in TERMINAL_TYPES:
            stream.terminal_type, stream.terminal_payload = type_, payload
        try:
            ts = datetime.fromisoformat(str(obj.get("ts")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = utcnow()
        except ValueError:
            ts = utcnow()
        try:
            sink.emit(ExecutionEvent(seq=seq, type=type_, payload=payload, ts=ts))
            stream.emitted += 1
        except Exception:
            logger.warning("could not persist runtime event seq=%s", seq, exc_info=False)

    # ------------------------------------------------------------ result mapping

    @staticmethod
    def _tokens(usage: dict[str, Any] | None) -> tuple[int, int]:
        if not usage:
            return 0, 0

        def n(key: str) -> int:
            value = usage.get(key)
            return value if _positive_int(value) else 0

        prompt = n("input_tokens") + n("cache_read_tokens") + n("cache_write_tokens")
        return prompt, n("output_tokens")

    def _billing(
        self, model: model_providers.ResolvedModel, usage: dict[str, Any] | None
    ) -> dict[str, Any]:
        cost, kind, note = self._cost(model, usage)
        return {"cost_micros": cost, "cost_kind": kind.value, "note": note, "local": model.is_local}

    def _cost(
        self, model: model_providers.ResolvedModel, usage: dict[str, Any] | None
    ) -> tuple[int | None, CostKind, str]:
        if model.is_local:
            return 0, CostKind.VERIFIED, LOCAL_COST_NOTE
        prompt, completion = self._tokens(usage)
        status = str((usage or {}).get("cost_status") or "unknown")
        usd = (usage or {}).get("estimated_cost_usd")
        if isinstance(usd, int | float) and not isinstance(usd, bool) and usd >= 0:
            micros = int(round(usd * 1_000_000))
            if status == "actual":
                return micros, CostKind.VERIFIED, "Cost reported by the provider."
            if status in ("estimated", "included"):
                return micros, CostKind.ESTIMATED, "Estimated by the runtime's price table."
        micros, kind = model_providers.priced_cost(model, prompt, completion)
        if micros is not None:
            return micros, kind, "Estimated from the provider's configured per-token prices."
        return None, CostKind.UNKNOWN, "The provider reported no cost and no price is configured."

    def _usage_report(
        self, model: model_providers.ResolvedModel, usage: dict[str, Any] | None
    ) -> UsageReport | None:
        cost, kind, _ = self._cost(model, usage)
        if usage is None and cost is None:
            return None
        prompt, completion = self._tokens(usage)
        return UsageReport(
            input_tokens=prompt if usage else None,
            output_tokens=completion if usage else None,
            cost_micros=cost,
            cost_kind=kind,
        )

    def _result(
        self,
        code: int | None,
        stream: _Stream,
        stderr_tail: collections.deque[str],
        prepared: _Prepared,
        cancel: CancelToken,
        killed: bool,
    ) -> ExecutionResult:
        usage = self._usage_report(prepared.model, stream.usage)
        if stream.dropped:
            logger.info(
                "run %s: dropped runtime lines %s", prepared.spec["run_id"], dict(stream.dropped)
            )
        terminal, payload = stream.terminal_type, stream.terminal_payload
        summary = str(payload.get("summary") or "").strip() or None

        if terminal == "run.completed":
            return ExecutionResult(status="succeeded", summary=summary, usage=usage)
        if terminal == "run.cancelled" or (terminal is None and cancel.is_set()):
            return ExecutionResult(
                status="cancelled",
                error=ExecutionError(RuntimeErrorKind.CANCELLED, "Cancelled"),
                usage=usage,
            )
        if terminal == "run.failed":
            reason = str(payload.get("exit_reason") or "failed")
            message = redact_text(
                str(payload.get("error") or summary or f"The agent run failed ({reason})"),
                prepared.secrets,
            )[:1000]
            if reason == "time_limit":
                kind, retryable = RuntimeErrorKind.TIMEOUT, True
            elif reason in ("bad_spec", "setup_failed"):
                kind, retryable = RuntimeErrorKind.CONFIG, False
            else:  # max_iterations, task_not_completed, model_error, ...
                kind, retryable = RuntimeErrorKind.FAILED, True
            return ExecutionResult(
                status="failed", error=ExecutionError(kind, message, retryable), usage=usage
            )
        # No terminal event: crashed, killed or exited early.
        how = "was killed after it ignored SIGTERM" if killed else f"exited with code {code}"
        tail = " | ".join(list(stderr_tail)[-3:])
        message = f"The agent runtime {how} without a result" + (f": {tail}" if tail else "")
        return ExecutionResult(
            status="failed",
            error=ExecutionError(RuntimeErrorKind.FAILED, message[:1000], retryable=True),
            usage=usage,
        )


__all__ = ["CompanyAgentAdapter", "SERVER_SEQ_FLOOR"]
