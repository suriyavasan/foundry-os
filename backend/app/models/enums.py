from enum import StrEnum


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    VIEWER = "viewer"


class CompanyStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class AgentKind(StrEnum):
    FOUNDER = "founder"
    LEAD = "lead"
    SPECIALIST = "specialist"
    REVIEWER = "reviewer"


class AgentStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRING = "retiring"
    RETIRED = "retired"
    REJECTED = "rejected"


# Statuses in which an agent still occupies its slot (e.g. "the" founder).
LIVE_AGENT_STATUSES = (
    AgentStatus.PROPOSED,
    AgentStatus.ACTIVE,
    AgentStatus.PAUSED,
    AgentStatus.RETIRING,
)


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
