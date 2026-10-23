"""Prepare the employee's isolated HERMES_HOME, SOUL.md, config.yaml and process environment.

Everything here runs BEFORE any Hermes module is imported (Hermes reads ``.env``, ``config.yaml``
and ``SOUL.md`` from ``$HERMES_HOME`` at import / first config load, and would otherwise seed its
own defaults). Only ``ruamel.yaml`` (already a Hermes dependency) is imported.

The generated ``config.yaml`` is the *enforced* part of the configuration: the keys below are
always overwritten from the run spec, whatever a previous run or an operator left in the file;
unrelated keys (for example provider tuning) are preserved.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from company_agent.spec import RunSpec

# Context floor used for company runs (spec A3); never above the model's own window.
COMPANY_MIN_CONTEXT_TOKENS = 16_384

# Environment variables that survive into the launcher process (everything else is dropped).
_ENV_KEEP = frozenset({
    "PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT", "COMSPEC", "PATHEXT",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "DOCKER_HOST",
})


def jail_enabled(spec: RunSpec) -> bool:
    """File tools are jailed to the workspace on the host only when there is no container boundary."""
    return spec.sandbox_backend == "none"


def minimum_context(spec: RunSpec) -> int:
    return min(COMPANY_MIN_CONTEXT_TOKENS, spec.model.context_length)


def build_process_env(spec: RunSpec, source: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The only environment the run is allowed: a small allow-list plus our own HERMES_* flags."""
    source = os.environ if source is None else source
    env = {k: v for k, v in source.items() if k in _ENV_KEEP}
    home = spec.hermes_home
    env.update({
        "HERMES_HOME": home,
        "HOME": os.path.join(home, "home"),
        "TMPDIR": os.path.join(home, "tmp"),
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
        "HERMES_MIN_CONTEXT_TOKENS": str(minimum_context(spec)),
        "TERMINAL_CWD": spec.workspace_dir,
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
    })
    if jail_enabled(spec):
        env["HERMES_COMPANY_JAIL"] = spec.workspace_dir
    return env


def apply_process_env(spec: RunSpec) -> dict[str, str]:
    env = build_process_env(spec)
    os.environ.clear()
    os.environ.update(env)
    return env


def ensure_dirs(spec: RunSpec) -> None:
    home = Path(spec.hermes_home)
    for d in (home, home / "home", home / "tmp", Path(spec.workspace_dir)):
        d.mkdir(parents=True, exist_ok=True)
    for d in (home, home / "home", home / "tmp"):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _yaml():
    from ruamel.yaml import YAML

    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    y.sort_base_mapping_type_on_output = False  # type: ignore[attr-defined]
    return y


def build_enforced_config(spec: RunSpec, toolsets: list[str]) -> dict[str, Any]:
    """The config keys the launcher always sets (see module docstring)."""
    m = spec.model
    model: dict[str, Any] = {
        "default": m.model,
        "provider": m.provider,
        "context_length": m.context_length,
        "min_context_tokens": minimum_context(spec),
    }
    if m.base_url:
        model["base_url"] = m.base_url
    if m.is_local:
        # Local OpenAI-compatible servers (Ollama) serve num_ctx, not the GGUF's advertised window.
        model["ollama_num_ctx"] = m.context_length

    cfg: dict[str, Any] = {
        "model": model,
        "platform_toolsets": {"company": list(toolsets)},
        "approvals": {"mode": "manual", "cron_mode": "deny", "single_query_mode": "deny",
                      "unattended_mode": "deny"},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "provider": ""},
        "skills": {"external_dirs": [], "trusted_project_dirs": [], "project_discovery": False,
                   "auto_load": [], "inline_shell": False},
        "curator": {"enabled": False},
        "auxiliary": {"background_review": {"enabled": False}, "title_generation": {"enabled": False}},
        "auth": {"adopt_external_logins": False},
        "checkpoints": {"enabled": False},
        "security": {"allow_lazy_installs": False},
        # Small local models do much better with the tools directly in the request than behind
        # Hermes' search/describe/call indirection.
        "tools": {"tool_search": {"enabled": "off"}},
        # Keyless web search through ddgs only; never fan queries out to third-party free tiers.
        "web": {"backend": "ddgs", "keyless_fallback": False},
        "agent": {"max_turns": spec.max_iterations},
        "display": {"streaming": False},
    }
    if spec.sandbox_backend == "docker":
        cfg["terminal"] = {
            "backend": "docker",
            "docker_image": spec.docker_image,
            "docker_network": bool(spec.docker_network),
            "container_persistent": False,
            "docker_volumes": [f"{spec.workspace_dir}:/workspace"],
            "docker_mount_cwd_to_workspace": False,
            "cwd": "/workspace",
            "home_mode": "profile",
        }
    else:
        cfg["terminal"] = {"backend": "local", "cwd": spec.workspace_dir, "home_mode": "profile"}
    return cfg


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def write_config(spec: RunSpec, toolsets: list[str]) -> Path:
    path = Path(spec.hermes_home) / "config.yaml"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = _yaml().load(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except Exception:
            existing = {}  # unreadable config is replaced, never trusted
    # model/platform_toolsets are replaced wholesale; everything else is merged key-by-key.
    merged = _deep_merge(existing, build_enforced_config(spec, toolsets))
    merged["platform_toolsets"] = {"company": list(toolsets)}
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        _yaml().dump(merged, fh)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def write_soul(spec: RunSpec, text: str) -> Path:
    path = Path(spec.hermes_home) / "SOUL.md"
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path


def remove_dotenv(spec: RunSpec) -> None:
    """A ``.env`` in the employee home would be loaded into the process at import; never allow one."""
    p = Path(spec.hermes_home) / ".env"
    try:
        if p.exists() or p.is_symlink():
            p.unlink()
    except OSError:
        pass
