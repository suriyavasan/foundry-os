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

# M15 purchasing. Every working role may ASK the company to buy something (the tool commits
# nothing; the server decides, rate limits and requires the agent to own the task). The AI
# Co-founder, who runs the strategic loop and not the work, answers subscription renewal reviews.
PURCHASE_REQUEST_TOOL = "company_request_purchase"
# M18 decide-don't-ask: every role records the choices it makes and may ask the Founder (the server
# limits how often: 5 open questions per company, 2 per agent per day).
ASK_FOUNDER_TOOL = "company_ask_founder"
RECORD_DECISION_TOOL = "company_record_decision"
# M22: every role may ask a person to do something only a person can do (the server limits it:
# 3 open per agent, 20 per company); only HR drafts freelance jobs (a person approves each one).
HUMAN_WORK_TOOL = "company_request_human_work"
OUTSOURCE_TOOL = "company_draft_outsource_job"
SUBSCRIPTION_REVIEW_TOOL = "company_review_subscription"
# M21 agent meetings: every role may call a meeting and cancel one it organizes (the server limits
# how often). ``company_meeting_say`` exists only inside a meeting-turn run (``run_company_tools``).
MEETING_TOOLS = ("company_schedule_meeting", "company_cancel_meeting")
MEETING_SAY_TOOL = "company_meeting_say"
# What a meeting-turn run offers: speak, read the context, look up colleagues, call a follow-up.
MEETING_TURN_TOOLS = (
    MEETING_SAY_TOOL,
    "company_get_context",
    "company_find_colleagues",
    "company_schedule_meeting",
)

WORKER_TOOLS = (
    "company_get_context",
    "company_report_progress",
    "company_submit_artifact",
    "company_complete_task",
    "company_update_task",
    "company_create_task",
    "company_request_help",
    "company_propose_memory",
    PURCHASE_REQUEST_TOOL,
    RECORD_DECISION_TOOL,
    ASK_FOUNDER_TOOL,
    HUMAN_WORK_TOOL,  # M22
)
MANAGER_EXTRA = (
    "company_plan_project",
    "company_review_task",
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
    PURCHASE_REQUEST_TOOL,
    ASK_FOUNDER_TOOL,
    HUMAN_WORK_TOOL,  # M22
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
    # M15: answers subscription renewal reviews (it does not request purchases itself)
    SUBSCRIPTION_REVIEW_TOOL,
    ASK_FOUNDER_TOOL,  # M18
    HUMAN_WORK_TOOL,  # M22
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
    PURCHASE_REQUEST_TOOL,
    RECORD_DECISION_TOOL,
    ASK_FOUNDER_TOOL,
    HUMAN_WORK_TOOL,  # M22
    OUTSOURCE_TOOL,  # M22: HR only; a person approves every job
)


def radar_tools_for(db: Session, agent: Agent) -> list[str]:
    """M16 market radar tools by role (``app.radar.access``; the server checks the same table
    again). With the radar switched off only the page reader remains: it does not depend on it."""
    from app.radar import access as radar_access
    from app.services import policy

    tools = radar_access.tools_for(db, agent)
    if not policy.load_policy(db, agent.company_id).radar_enabled:
        return [t for t in tools if t == radar_access.TOOL_OF[radar_access.PAGE]]
    return tools


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
    tools.extend(radar_tools_for(db, agent))  # M16 radar
    from app.services import policy

    if policy.load_policy(db, agent.company_id).meetings_enabled:
        tools.extend(MEETING_TOOLS)  # M21
    if can_use_provider_actions(db, agent):
        tools.append("company_provider_action")
    tools = list(dict.fromkeys(tools))
    if isinstance(override, list) and override:
        keep = {str(t) for t in override}
        narrowed = [t for t in tools if t in keep]
        # An owner can only remove tools here, never add ones the role does not get.
        tools = narrowed or tools
    return tools


def run_company_tools(
    db: Session, agent: Agent, task: Any, runtime_config: dict | None = None
) -> list[str]:
    """The company tools of ONE run: the role's list (``company_tools_for``) plus the task-kind
    special cases (a peer review decides one thing; the reviewer of a Foundry pull request records
    its verdict; the subscription renewal review). The single source for ``company_agent`` runs and
    for the MCP tools of coding-CLI runs, so a runtime never offers more than the role would get."""
    from app.guardrails import peer
    from app.services import repo_reviews

    tools = company_tools_for(db, agent, (runtime_config or {}).get("company_tools"))
    if task is None:
        return tools
    if task.kind == "meeting":
        # M21: a meeting turn offers the meeting tools and read-only context, nothing else.
        return list(MEETING_TURN_TOOLS)
    if task.kind == "review" and peer.review_target(db, task) is not None:
        # A peer review run decides one thing; nothing else is on offer.
        return ["company_decide_peer_review", "company_complete_task"]
    if repo_reviews.pull_id_of(task) is not None:
        # M14: the server allows only the assignee of that review task.
        return [*dict.fromkeys([*tools, "company_review_pull_request"])]
    if task.idempotency_key and task.idempotency_key.startswith("subrev:"):
        # M15: ``subscriptions.review`` accepts the assignee of that task.
        return [*dict.fromkeys([*tools, SUBSCRIPTION_REVIEW_TOOL])]
    return tools


def mcp_names(tools: list[str]) -> list[str]:
    """Company tool names as the MCP server exposes them (without the ``company_`` prefix)."""
    return [t.removeprefix("company_") for t in tools]


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
