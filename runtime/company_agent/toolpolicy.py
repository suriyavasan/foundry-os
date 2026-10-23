"""Which toolsets an employee actually gets (launcher-enforced, never prompt-enforced).

``terminal`` and ``code_execution`` run arbitrary code as the launcher's OS user, so they are
removed unless the run has a real sandbox (docker). The control plane applies the same rule when
it builds the spec; this is the second, independent check.
"""

from __future__ import annotations

from company_agent.spec import SANDBOX_ONLY_TOOLSETS, RunSpec

# Spec toolset name -> Hermes toolset name. Employees get read-only skills by default; authoring
# skills (skill_manage) is an explicit, separate grant for the HR / skill-author roles (M4).
# "web" -> "search": the only keyless backend (ddgs) cannot extract pages, and a web_extract tool that always
# errors just wastes small-model turns. Search-only is what actually works.
HERMES_TOOLSET = {"skills": "skills_read", "skills_manage": "skills", "web": "search"}


def resolve_toolsets(spec: RunSpec) -> tuple[list[str], list[str]]:
    """Returns ``(effective toolsets, warnings)``."""
    effective: list[str] = []
    warnings: list[str] = []
    for ts in spec.toolsets:
        if ts in SANDBOX_ONLY_TOOLSETS and spec.sandbox_backend != "docker":
            warnings.append(
                f"Toolset '{ts}' removed: it needs a docker sandbox and this run has none "
                f"(sandbox.backend={spec.sandbox_backend})."
            )
            continue
        effective.append(HERMES_TOOLSET.get(ts, ts))
    effective = list(dict.fromkeys(effective))
    if "company" not in effective:
        warnings.append("Toolset 'company' is not enabled: the agent cannot report progress, submit "
                        "artifacts or complete its task.")
    return effective, warnings
