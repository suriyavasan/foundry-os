"""Configuration + HTTP client for the company toolset (``tools/company_tools.py``).

The launcher calls :func:`configure` once per run with the agent-API URL and the run token.
The token lives only in this module's memory (and in the ``Authorization`` header of one
``httpx.Client``): never in the environment, on disk, in events or in tool results.

Stdlib + httpx only, so it can be imported before/without Hermes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


@dataclass
class ToolClientConfig:
    url: str
    token: str = field(repr=False)
    run_id: str = ""
    task_id: str = ""
    workspace_dir: str = ""
    # Called as on_artifact(artifact_id, title) after an artifact was accepted by the API.
    on_artifact: Optional[Callable[[str, str], None]] = None
    # Restrict the company toolset to these tool names (None = all ten). Fewer tools = better small models.
    allowed_tools: Optional[frozenset] = None
    task_kind: str = ""


_lock = threading.Lock()
_config: Optional[ToolClientConfig] = None
_client: Optional[httpx.Client] = None
_dispositions: dict[str, str] = {}
_artifacts_submitted = 0
_artifact_ids: list[str] = []
_completion_warned = False


def configure(
    url: str,
    token: str,
    *,
    run_id: str = "",
    task_id: str = "",
    workspace_dir: str = "",
    on_artifact: Optional[Callable[[str, str], None]] = None,
    allowed_tools: Optional[Any] = None,
    task_kind: str = "",
) -> None:
    """Install the agent-API connection for this process (replaces any previous one)."""
    global _config, _client, _artifacts_submitted, _completion_warned
    if not url or not token:
        raise ValueError("agent API url and token are required")
    with _lock:
        if _client is not None:
            _client.close()
        _config = ToolClientConfig(
            url=url.rstrip("/"),
            token=token,
            run_id=run_id,
            task_id=task_id,
            workspace_dir=workspace_dir,
            on_artifact=on_artifact,
            allowed_tools=frozenset(allowed_tools) if allowed_tools is not None else None,
            task_kind=task_kind or "",
        )
        # trust_env=False: the control plane is local; never route it through an env proxy.
        _client = httpx.Client(
            timeout=REQUEST_TIMEOUT,
            trust_env=False,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "company-agent/1"},
        )
        _dispositions.clear()
        _artifacts_submitted = 0
        _artifact_ids.clear()
        _completion_warned = False


def reset() -> None:
    """Drop the connection (tests, shutdown)."""
    global _config, _client, _artifacts_submitted, _completion_warned
    with _lock:
        if _client is not None:
            _client.close()
        _config = None
        _client = None
        _dispositions.clear()
        _artifacts_submitted = 0
        _artifact_ids.clear()
        _completion_warned = False


def is_configured() -> bool:
    return _config is not None and _client is not None


def tool_enabled(name: str) -> bool:
    """check_fn for one company tool: configured, and permitted by the run's tool subset."""
    cfg = _config
    if cfg is None or _client is None:
        return False
    return cfg.allowed_tools is None or name in cfg.allowed_tools


def config() -> ToolClientConfig:
    if _config is None:
        raise RuntimeError("company toolset is not configured")
    return _config


def client() -> httpx.Client:
    if _client is None:
        raise RuntimeError("company toolset is not configured")
    return _client


def api_url(path: str) -> str:
    return f"{config().url}/{path.lstrip('/')}"


def secrets() -> list[str]:
    """Secret strings tool code must scrub from anything it returns."""
    return [_config.token] if _config is not None and _config.token else []


def mark_task_completed(task_id: str) -> None:
    with _lock:
        _dispositions[task_id] = "completed"


def mark_task_blocked(task_id: str) -> None:
    with _lock:
        _dispositions.setdefault(task_id, "blocked")


def dispositions() -> dict[str, str]:
    """task_id -> "completed" | "blocked" for tasks the agent successfully disposed of this run."""
    with _lock:
        return dict(_dispositions)


def note_artifact_submitted(artifact_id: str = "") -> None:
    global _artifacts_submitted
    with _lock:
        _artifacts_submitted += 1
        if artifact_id and artifact_id not in _artifact_ids:
            _artifact_ids.append(artifact_id)


def submitted_artifact_ids() -> list[str]:
    """Artifact ids this run created, in order."""
    with _lock:
        return list(_artifact_ids)


def artifacts_submitted() -> int:
    with _lock:
        return _artifacts_submitted


def should_warn_no_deliverable() -> bool:
    """True exactly once per run, when a "work" task is being completed with no artifact submitted."""
    global _completion_warned
    with _lock:
        if _completion_warned or _config is None or _config.task_kind not in ("", "work"):
            return False
        if _artifacts_submitted > 0:
            return False
        _completion_warned = True
        return True
