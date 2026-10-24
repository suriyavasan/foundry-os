"""The runtime's tools against the control plane's guardrail registry (backend/app/guardrails/tools.json).

The registry is the single source of truth for which tool has which risk tier; the control plane sends
it in the run spec and the runtime refuses anything not in it. These tests keep the file and the tools
Hermes really resolves in step: a new tool without a tier fails here before it can ship."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from company_agent import toolpolicy
from company_agent.spec import ALLOWED_TOOLSETS, COMPANY_TOOL_NAMES

REGISTRY_FILE = Path(__file__).resolve().parents[3] / "backend" / "app" / "guardrails" / "tools.json"
TIERS = {
    "read", "internal_write", "org_change", "external_read", "external_communication",
    "external_commitment", "spend", "identity", "destructive",
}


@pytest.fixture(scope="module")
def registry() -> dict:
    if not REGISTRY_FILE.is_file():
        pytest.skip("backend/app/guardrails/tools.json is not part of this checkout")
    return json.loads(REGISTRY_FILE.read_text())


def test_every_tool_has_a_valid_tier(registry):
    assert registry["tools"]
    assert all(t["tier"] in TIERS for t in registry["tools"].values())
    for info in registry["company_tools"].values():
        assert isinstance(info["action"], str) and info["action"]


def test_every_company_tool_the_runtime_knows_is_classified_and_vice_versa(registry):
    from tools import company_tools

    assert set(COMPANY_TOOL_NAMES) == set(registry["company_tools"])
    assert set(company_tools._TOOLS) == set(registry["company_tools"])


def test_every_hermes_tool_of_every_grantable_toolset_is_classified(registry):
    """What each control-plane toolset resolves to in Hermes is exactly what the registry lists."""
    from toolsets import resolve_toolset

    grantable = set(ALLOWED_TOOLSETS) - {"company", "skills_manage"}
    listed_toolsets = set(registry["toolsets"])
    assert grantable <= listed_toolsets, f"toolsets without a registry entry: {grantable - listed_toolsets}"
    for name in grantable:
        hermes_name = toolpolicy.HERMES_TOOLSET.get(name, name)
        resolved = set(resolve_toolset(hermes_name))
        assert resolved == set(registry["toolsets"][name]), (name, resolved)
        assert resolved <= set(registry["tools"]), f"unclassified tools in {name}: {resolved - set(registry['tools'])}"
    # the skill author toolset is never granted but is classified in case it ever is
    assert set(resolve_toolset("skills")) - set(registry["toolsets"]["skills"]) <= set(registry["tools"])


def test_tools_in_a_real_agent_build_are_all_classified(registry):
    """The strongest check: ask Hermes for the tool definitions of a full company run."""
    import model_tools

    enabled = [toolpolicy.HERMES_TOOLSET.get(t, t) for t in ("company", "file", "web", "skills", "todo",
                                                             "vision", "terminal", "code_execution")]
    defs = model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)
    names = {d["function"]["name"] for d in defs}
    known = set(registry["tools"]) | set(registry["company_tools"])
    assert names <= known, f"tools Hermes hands an agent that have no guardrail tier: {sorted(names - known)}"
    assert set(registry["always"]) <= set(registry["tools"])
    assert set(registry["never_granted"]) <= set(registry["tools"])


def test_untrusted_sources_name_known_tools(registry):
    for name, info in registry["tools"].items():
        if info.get("untrusted"):
            assert isinstance(info["untrusted"], str)
    outside = {n for n, i in registry["tools"].items() if i.get("untrusted")}
    assert {"web_search", "read_file", "search_files"} <= outside
