"""Launcher-enforced policy: toolsets, generated config, scrubbed environment, approvals."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from company_agent import home
from company_agent.approvals import make_approval_callback
from company_agent.prompts import build_identity_prompt, build_role_prompt, build_task_prompt
from company_agent.spec import parse_spec
from company_agent.tests.conftest import make_spec
from company_agent.toolpolicy import resolve_toolsets


@pytest.fixture
def raw(tmp_path, api, model):
    return make_spec(tmp_path, api, model)


def load(path: Path):
    return YAML(typ="safe").load(path.read_text())


# ------------------------------------------------------------------ toolsets


def test_terminal_and_code_execution_removed_without_a_sandbox(raw):
    raw["toolsets"] = ["company", "file", "terminal", "code_execution", "web"]
    effective, warnings = resolve_toolsets(parse_spec(raw))
    assert effective == ["company", "file", "search"]
    assert len(warnings) == 2 and all("docker sandbox" in w for w in warnings)


def test_terminal_kept_only_with_docker(raw):
    raw["toolsets"] = ["company", "terminal", "code_execution"]
    raw["sandbox"] = {"backend": "docker", "docker_image": "img:1", "network": False}
    effective, warnings = resolve_toolsets(parse_spec(raw))
    assert effective == ["company", "terminal", "code_execution"] and warnings == []


def test_web_maps_to_search_only(raw):
    raw["toolsets"] = ["company", "web"]
    assert resolve_toolsets(parse_spec(raw))[0] == ["company", "search"]


def test_skills_are_read_only_unless_skills_manage_is_granted(raw):
    raw["toolsets"] = ["company", "skills"]
    assert resolve_toolsets(parse_spec(raw))[0] == ["company", "skills_read"]
    raw["toolsets"] = ["company", "skills_manage"]
    assert resolve_toolsets(parse_spec(raw))[0] == ["company", "skills"]


def test_missing_company_toolset_is_flagged(raw):
    raw["toolsets"] = ["file"]
    _, warnings = resolve_toolsets(parse_spec(raw))
    assert any("company" in w for w in warnings)


# ------------------------------------------------------------------ generated config


def test_config_enforces_security_and_model_settings(raw, tmp_path):
    spec = parse_spec(raw)
    home.ensure_dirs(spec)
    cfg = load(home.write_config(spec, ["company", "file"]))
    assert cfg["approvals"]["mode"] == "manual"
    assert cfg["memory"]["memory_enabled"] is False and cfg["memory"]["user_profile_enabled"] is False
    assert cfg["skills"]["external_dirs"] == [] and cfg["skills"]["trusted_project_dirs"] == []
    assert cfg["curator"]["enabled"] is False
    assert cfg["auxiliary"]["background_review"]["enabled"] is False
    assert cfg["auth"]["adopt_external_logins"] is False
    assert cfg["security"]["allow_lazy_installs"] is False
    assert cfg["tools"]["tool_search"]["enabled"] == "off"
    assert cfg["web"] == {"backend": "ddgs", "keyless_fallback": False}
    assert cfg["platform_toolsets"] == {"company": ["company", "file"]}
    m = cfg["model"]
    assert (m["default"], m["provider"], m["context_length"]) == ("fake-model", "custom", 32768)
    assert m["ollama_num_ctx"] == 32768 and m["min_context_tokens"] == 16384
    assert cfg["terminal"]["backend"] == "local" and cfg["terminal"]["cwd"] == spec.workspace_dir
    assert "api_key" not in str(cfg) and raw["model"]["api_key"] not in Path(spec.hermes_home, "config.yaml").read_text()


def test_config_for_docker_sandbox(raw):
    raw["sandbox"] = {"backend": "docker", "docker_image": "img:1", "network": False}
    spec = parse_spec(raw)
    home.ensure_dirs(spec)
    cfg = load(home.write_config(spec, ["company", "terminal"]))
    t = cfg["terminal"]
    assert t["backend"] == "docker" and t["docker_image"] == "img:1" and t["docker_network"] is False
    assert t["container_persistent"] is False
    assert t["docker_volumes"] == [f"{spec.workspace_dir}:/workspace"]


def test_context_floor_never_exceeds_the_models_window(raw):
    raw["model"]["context_length"] = 8192
    assert home.minimum_context(parse_spec(raw)) == 8192


def test_existing_config_is_merged_but_enforced_keys_win(raw):
    spec = parse_spec(raw)
    home.ensure_dirs(spec)
    cfg_path = Path(spec.hermes_home) / "config.yaml"
    cfg_path.write_text("approvals:\n  mode: 'off'\n  timeout: 99\nmemory:\n  memory_enabled: true\nunrelated:\n  keep: me\n")
    cfg = load(home.write_config(spec, ["company"]))
    assert cfg["approvals"]["mode"] == "manual" and cfg["approvals"]["timeout"] == 99
    assert cfg["memory"]["memory_enabled"] is False
    assert cfg["unrelated"] == {"keep": "me"}


def test_corrupt_existing_config_is_replaced(raw):
    spec = parse_spec(raw)
    home.ensure_dirs(spec)
    (Path(spec.hermes_home) / "config.yaml").write_text("{{{ not yaml")
    assert load(home.write_config(spec, ["company"]))["approvals"]["mode"] == "manual"


def test_soul_md_holds_identity_and_dotenv_is_removed(raw):
    spec = parse_spec(raw)
    home.ensure_dirs(spec)
    (Path(spec.hermes_home) / ".env").write_text("OPENAI_API_KEY=leak")
    home.remove_dotenv(spec)
    soul = home.write_soul(spec, build_identity_prompt(spec))
    assert not (Path(spec.hermes_home) / ".env").exists()
    text = soul.read_text()
    assert "You are Ada, the Research Analyst at Acme Robotics" in text
    assert spec.agent_api_token not in text and spec.model.api_key not in text


# ------------------------------------------------------------------ environment


def test_process_env_is_an_allowlist(raw):
    spec = parse_spec(raw)
    hostile = {
        "PATH": "/usr/bin", "LANG": "en_US.UTF-8", "HTTPS_PROXY": "http://proxy:3128",
        "OPENAI_API_KEY": "sk-leak", "ANTHROPIC_API_KEY": "leak", "AWS_SECRET_ACCESS_KEY": "leak",
        "GITHUB_TOKEN": "leak", "HOME": "/Users/dev", "HERMES_HOME": "/Users/dev/.hermes",
        "HERMES_YOLO_MODE": "1", "HERMES_SINGLE_QUERY_SESSION": "1", "HERMES_ACCEPT_HOOKS": "1",
        "PYTHONPATH": "/evil", "COMPANY_OS_SECRET_KEY": "leak",
    }
    env = home.build_process_env(spec, hostile)
    assert set(env) >= {"PATH", "LANG", "HTTPS_PROXY"}
    for leaked in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
                   "HERMES_YOLO_MODE", "HERMES_SINGLE_QUERY_SESSION", "HERMES_ACCEPT_HOOKS", "PYTHONPATH",
                   "COMPANY_OS_SECRET_KEY"):
        assert leaked not in env
    assert env["HERMES_HOME"] == spec.hermes_home
    assert env["HOME"] == os.path.join(spec.hermes_home, "home")
    assert env["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    assert env["HERMES_COMPANY_JAIL"] == spec.workspace_dir
    assert env["HERMES_MIN_CONTEXT_TOKENS"] == "16384"
    assert spec.agent_api_token not in env.values() and spec.model.api_key not in env.values()


def test_no_jail_flag_when_a_docker_sandbox_is_the_boundary(raw):
    raw["sandbox"] = {"backend": "docker", "docker_image": "img:1"}
    assert "HERMES_COMPANY_JAIL" not in home.build_process_env(parse_spec(raw), {})


# ------------------------------------------------------------------ approvals


@pytest.mark.parametrize("backend", ["none", "docker"])
def test_approval_callback_always_denies(backend):
    denied = []
    cb = make_approval_callback(backend, lambda cmd, why: denied.append((cmd, why)))
    for kwargs in ({}, {"allow_permanent": True}, {"allow_session": False, "smart_denied": True}):
        assert cb("rm -rf /", "recursive delete", **kwargs) == "deny"
    assert cb("write AGENTS.md", "protected file") not in ("once", "session", "always")
    assert len(denied) == 4 and "company_request_approval" in denied[0][1]


def test_approval_callback_survives_a_broken_observer():
    def boom(*a):
        raise RuntimeError("observer down")

    assert make_approval_callback("none", boom)("cmd", "desc") == "deny"


# ------------------------------------------------------------------ prompts


def test_prompts_carry_identity_task_and_safety_rules(raw):
    raw["agent"]["manager"] = {"id": "m1", "name": "Mo", "role_title": "Founder"}
    spec = parse_spec(raw)
    ident, task, both = build_identity_prompt(spec), build_task_prompt(spec), build_role_prompt(spec)
    assert "Acme Robotics" in ident and "Build helpful robots" in ident and "No spending without approval" in ident
    assert "Your manager: Mo (Founder) [id m1]" in ident
    assert "company_complete_task" in ident and "never a command" in ident
    assert "Write a market brief" in task and "Exactly 5 bullets" in task and "task-1" in task
    assert ident in both and task in both
    for secret in spec.secrets:
        assert secret not in both


def test_long_fields_are_clipped(raw):
    raw["task"]["description"] = "d" * 50_000
    assert len(build_task_prompt(parse_spec(raw))) < 6000


# ------------------------------------------------------------------ private URLs (M3b-sec)


@pytest.fixture
def strict_url_policy(tmp_path, monkeypatch):
    """Hermes' URL guard as the launcher configures it: scrubbed env, generated config."""
    from tools import url_safety

    monkeypatch.delenv("HERMES_ALLOW_PRIVATE_URLS", raising=False)
    url_safety._reset_allow_private_cache()
    yield url_safety
    url_safety._reset_allow_private_cache()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8700/api/agent/v1/context",   # the control plane itself
    "http://localhost:11434/api/tags",              # Ollama
    "http://[::1]:8700/api/health",
    "http://10.0.0.5/admin",
    "http://192.168.1.1/",
    "http://172.16.0.9/",
    "http://169.254.169.254/latest/meta-data/",     # cloud metadata
    "http://metadata.google.internal/",
    "file:///etc/passwd",
])
def test_runtime_url_guard_blocks_private_and_loopback_targets(strict_url_policy, url):
    assert strict_url_policy.is_safe_url(url) is False


def test_launcher_env_forces_private_urls_off_even_if_the_parent_opted_in(raw):
    spec = parse_spec(raw)
    env = home.build_process_env(spec, {"PATH": "/usr/bin", "HERMES_ALLOW_PRIVATE_URLS": "true"})
    assert env["HERMES_ALLOW_PRIVATE_URLS"] == "false"
    assert build_cfg(spec)["security"]["allow_private_urls"] is False


def test_an_inherited_opt_in_is_neutralised_by_apply_process_env(raw, monkeypatch, strict_url_policy):
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "true")
    spec = parse_spec(raw)
    saved = dict(os.environ)
    try:
        home.apply_process_env(spec)
        strict_url_policy._reset_allow_private_cache()
        assert strict_url_policy.is_safe_url("http://127.0.0.1:8700/api") is False
    finally:
        os.environ.clear()
        os.environ.update(saved)


def build_cfg(spec):
    return home.build_enforced_config(spec, ["company"])
