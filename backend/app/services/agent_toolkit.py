"""What tools an AI employee gets for a run. Enforcement is code, never a prompt.

* **Toolsets** (Hermes toolsets): role template defaults  ∩  the agent's ``tool_permissions``
  (when it has any)  ∩  ``runtime_config.toolsets`` (owner restriction), always plus ``company``
  (the channel back to the control plane, itself server-authorized), minus ``terminal`` and
  ``code_execution`` unless the sandbox is docker. ``skills_manage`` (Hermes' skill_manage, which
  writes into the ephemeral HERMES_HOME) is never granted: HR authors skills through
  ``company_author_skill``, which scans, versions and audits them.
* **Company tools** (the ``company`` toolset's members): chosen by role. Small models degrade with
  many tools, so workers get a lean set; managers, the founder and HR get what their job needs.
  The server enforces the same roles again (a non-HR token calling an HR endpoint is 403).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.enums import LIVE_AGENT_STATUSES, AgentKind, AgentStatus, RoleFamily
from app.services import role_templates

DEFAULT_TOOLSETS = ("company", "file", "web", "skills", "todo")
ALLOWED_TOOLSETS = frozenset(
    {"company", "file", "web", "search", "skills", "todo", "vision", "terminal", "code_execution"}
)
SANDBOX_ONLY = frozenset({"terminal", "code_execution"})

WORKER_TOOLS = (
    "company_get_context",
    "company_report_progress",
    "company_submit_artifact",
    "company_complete_task",
    "company_update_task",
    "company_create_task",
)
MANAGER_EXTRA = (
    "company_review_task",
    "company_record_decision",
    "company_request_approval",
    "company_find_colleagues",
    "company_request_hire",
)
FOUNDER_EXTRA = ("company_decide_approval",)
HR_TOOLS = (
    "company_get_context",
    "company_report_progress",
    "company_submit_artifact",
    "company_complete_task",
    "company_update_task",
    "company_find_colleagues",
    "company_search_skills",
    "company_install_skill",
    "company_author_skill",
    "company_assign_skills",
    "company_list_role_templates",
    "company_create_role_template",
    "company_hire_agent",
)


def has_active_reports(db: Session, agent: Agent) -> bool:
    return bool(
        db.scalar(
            select(func.count(Agent.id)).where(
                Agent.company_id == agent.company_id,
                Agent.manager_agent_id == agent.id,
                Agent.status.in_(LIVE_AGENT_STATUSES),
            )
        )
    )


def company_tools_for(db: Session, agent: Agent, override: Any = None) -> list[str]:
    """Company tools for the agent's role, optionally narrowed by ``runtime_config``."""
    if agent.role_family == RoleFamily.HR:
        tools = list(HR_TOOLS)
    else:
        tools = list(WORKER_TOOLS)
        manager = agent.kind in (
            AgentKind.FOUNDER,
            AgentKind.LEAD,
            AgentKind.REVIEWER,
        ) or has_active_reports(db, agent)
        if manager:
            tools += MANAGER_EXTRA
        if agent.kind == AgentKind.FOUNDER:
            tools += FOUNDER_EXTRA
    tools = list(dict.fromkeys(tools))
    if isinstance(override, list) and override:
        keep = {str(t) for t in override}
        narrowed = [t for t in tools if t in keep]
        # An owner can only remove tools here, never add ones the role does not get.
        tools = narrowed or tools
    return tools


def toolsets_for(db: Session, agent: Agent, *, sandbox: str, runtime_config: dict) -> list[str]:
    template = (
        role_templates.find_template(db, agent.company_id, agent.role_template)
        if agent.role_template
        else None
    )
    base = list(template.spec.default_toolsets) if template else list(DEFAULT_TOOLSETS)
    allowed = ALLOWED_TOOLSETS if sandbox == "docker" else ALLOWED_TOOLSETS - SANDBOX_ONLY
    granted = [t for t in base if t in allowed]
    permissions = [t for t in agent.tool_permissions if isinstance(t, str)]
    if permissions:
        granted = [t for t in granted if t in permissions]
    restrict = runtime_config.get("toolsets")
    if isinstance(restrict, list) and restrict:
        granted = [t for t in granted if t in {str(x) for x in restrict}]
    if "company" not in granted:
        granted.insert(0, "company")
    return list(dict.fromkeys(granted))


def direct_reports(db: Session, agent: Agent, limit: int = 20) -> list[Agent]:
    return list(
        db.scalars(
            select(Agent)
            .where(
                Agent.company_id == agent.company_id,
                Agent.manager_agent_id == agent.id,
                Agent.status == AgentStatus.ACTIVE,
            )
            .order_by(Agent.created_at, Agent.id)
            .limit(limit)
        )
    )
