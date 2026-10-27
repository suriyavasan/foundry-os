"""What tools an AI employee gets for a run. Enforcement is code, never a prompt.

* **Toolsets** (Hermes toolsets): role template defaults  ∩  the agent's ``tool_permissions``
  (when it has any)  ∩  ``runtime_config.toolsets`` (owner restriction), always plus ``company``
  (the channel back to the control plane, itself server-authorized), minus ``terminal`` and
  ``code_execution`` unless the sandbox is docker. ``skills_manage`` (Hermes' skill_manage, which
  writes into the ephemeral HERMES_HOME) is never granted: HR authors skills through
  ``company_author_skill``, which scans, versions and audits them.
* **Company tools** (the ``company`` toolset's members): chosen by role (``agent_roles``). Small
  models degrade with many tools, so specialists get a lean set; the AI Co-founder gets strategic
  tools, project leads management tools, HR the hiring tools.
  The server enforces the same roles again (a non-HR token calling an HR endpoint is 403).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.enums import LIVE_AGENT_STATUSES, AgentStatus
from app.services import agent_roles, role_templates

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
    "company_request_help",
    "company_propose_memory",
)
MANAGER_EXTRA = (
    "company_plan_project",
    "company_review_task",
    "company_record_decision",
    "company_request_approval",
    "company_find_colleagues",
    "company_request_hire",
)
# A project lead: manages the projects it leads (state, decomposition, reviews, hiring requests).
LEAD_TOOLS = (
    "company_get_state",
    "company_get_context",
    "company_report_progress",
    "company_submit_artifact",
    "company_complete_task",
    "company_update_task",
    "company_create_task",
    "company_plan_project",
    "company_review_task",
    "company_record_decision",
    "company_find_colleagues",
    "company_request_hire",
    "company_manage_project",
    "company_escalate",
    "company_propose_memory",
)
# The AI Co-founder: strategy only. It reads the live state, triages the Founder's notes,
# shapes goals/projects/schedules, requests hires and decides; it does not do the work itself.
FOUNDER_TOOLS = (
    "company_get_state",
    "company_list_notes",
    "company_triage_note",
    "company_reply_to_note",
    "company_manage_goal",
    "company_manage_project",
    "company_manage_schedule",
    "company_create_task",
    "company_update_task",
    "company_complete_task",
    "company_review_task",
    "company_record_decision",
    "company_request_hire",
    "company_request_approval",
    "company_decide_approval",
    "company_verify_memory",
    "company_propose_memory",
    "company_submit_artifact",
    # M10: reflections for the Founder (only people adopt a standard)
    "company_post_reflection",
    "company_reply_reflection",
    "company_revise_reflection",
)
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
    "company_propose_memory",
)


def can_use_provider_actions(db: Session, agent: Agent) -> bool:
    """The agent gets ``company_provider_action`` only when some live credential is scoped for
    actions its role family may use (nothing to call otherwise: fewer tools suit small models)."""
    from app.models.provider import Credential

    rows = db.scalars(
        select(Credential).where(
            Credential.company_id == agent.company_id, Credential.revoked_at.is_(None)
        )
    )
    return any(
        r.provider
        and r.allowed_actions
        and (not r.allowed_families or agent.role_family in r.allowed_families)
        for r in rows
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
    role = agent_roles.role_of(db, agent)
    if role == agent_roles.HR:
        tools = list(HR_TOOLS)
    elif role == agent_roles.FOUNDER:
        tools = list(FOUNDER_TOOLS)
    elif role == agent_roles.LEAD:
        tools = list(LEAD_TOOLS)
    elif role == agent_roles.MANAGER:
        tools = [*WORKER_TOOLS, *MANAGER_EXTRA]
    else:
        tools = list(WORKER_TOOLS)
    if can_use_provider_actions(db, agent):
        tools.append("company_provider_action")
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
