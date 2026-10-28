"""Who may use which radar capability (enforced in the services; the tool lists in
``agent_toolkit`` are derived from the same table, so a tool is never offered that the server would
refuse, and never refused after being offered).

Capabilities map one to one onto the agent tools:

=========  ======================================================  =============================
READ       ``company_radar_digest``     (``radar.read``)          every radar role
PAGE       ``company_radar_read``       (``radar.page_read``)     any agent with web; the Co-founder
BRIEF      ``company_radar_brief``      (``radar.brief``)         research, design, QA, Co-founder
PROPOSE    ``company_radar_propose``    (``radar.idea.create``)   research, design, QA, Co-founder
DECIDE     ``company_radar_decide``     (``radar.idea.decide``)   Co-founder; leads in their project
KEYWORDS   ``company_radar_keywords``   (``radar.keyword.manage``) research, Co-founder
SOURCE     ``company_radar_add_source`` (``radar.source.add``)    research, Co-founder
UI         ``company_radar_ui_search``  (``radar.ui_search``)     research, design
=========  ======================================================  =============================
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import Forbidden
from app.models.agent import Agent
from app.models.enums import AgentKind, RoleFamily

READ = "read"
PAGE = "page"
BRIEF = "brief"
PROPOSE = "propose"
DECIDE = "decide"
KEYWORDS = "keywords"
SOURCE = "source"
UI = "ui"

CAPABILITIES = (READ, PAGE, BRIEF, PROPOSE, DECIDE, KEYWORDS, SOURCE, UI)

TOOL_OF = {
    READ: "company_radar_digest",
    PAGE: "company_radar_read",
    BRIEF: "company_radar_brief",
    PROPOSE: "company_radar_propose",
    DECIDE: "company_radar_decide",
    KEYWORDS: "company_radar_keywords",
    SOURCE: "company_radar_add_source",
    UI: "company_radar_ui_search",
}
RADAR_TOOL_NAMES = tuple(TOOL_OF.values())
ACTION_OF = {
    READ: "radar.read",
    PAGE: "radar.page_read",
    BRIEF: "radar.brief",
    PROPOSE: "radar.idea.create",
    DECIDE: "radar.idea.decide",
    KEYWORDS: "radar.keyword.manage",
    SOURCE: "radar.source.add",
    UI: "radar.ui_search",
}

_RESEARCH = frozenset({READ, BRIEF, PROPOSE, KEYWORDS, SOURCE, UI})
_DESIGN = frozenset({READ, BRIEF, PROPOSE, UI})
_QA = frozenset({READ, BRIEF, PROPOSE})
# The Co-founder runs the radar until a Market Researcher is hired and has no web toolset: the
# control plane reads pages for it (it never searches the web itself).
_FOUNDER = frozenset({READ, PAGE, BRIEF, PROPOSE, DECIDE, KEYWORDS, SOURCE})
_LEAD = frozenset({READ, DECIDE})


def role_capabilities(agent: Agent) -> frozenset[str]:
    """The capabilities an agent's role grants (``PAGE`` is separate: it needs the web toolset)."""
    if agent.kind == AgentKind.FOUNDER:
        return _FOUNDER
    family = agent.role_family
    if family == RoleFamily.RESEARCH:
        return _RESEARCH
    if family == RoleFamily.DESIGN:
        return _DESIGN
    if family == RoleFamily.QA:
        return _QA
    if agent.kind == AgentKind.LEAD:
        return _LEAD
    return frozenset()


def has_web(db: Session, agent: Agent) -> bool:
    """The agent's run gets the ``web`` toolset (role template, permissions, owner restriction)."""
    from app.services import agent_toolkit  # local: agent_toolkit imports this module

    toolsets = agent_toolkit.toolsets_for(
        db, agent, sandbox="none", runtime_config=dict(agent.runtime_config or {})
    )
    return "web" in toolsets


def capabilities(db: Session, agent: Agent) -> frozenset[str]:
    caps = set(role_capabilities(agent))
    if has_web(db, agent):
        caps.add(PAGE)
    return frozenset(caps)


def allowed(db: Session, agent: Agent, capability: str) -> bool:
    if capability in role_capabilities(agent):
        return True
    return capability == PAGE and has_web(db, agent)


def require(db: Session, agent: Agent, capability: str, what: str) -> None:
    if not allowed(db, agent, capability):
        raise Forbidden(f"Your role cannot {what}")


def tools_for(db: Session, agent: Agent) -> list[str]:
    """Radar tool names this agent gets, in a stable order."""
    caps = capabilities(db, agent)
    return [TOOL_OF[c] for c in CAPABILITIES if c in caps]
