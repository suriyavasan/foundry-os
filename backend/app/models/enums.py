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


# ---------------------------------------------------------------- Milestone 2: work engine


class AutonomyMode(StrEnum):
    AUTONOMOUS = "autonomous"
    SUPERVISED = "supervised"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


PRIORITY_RANK = {Priority.LOW: 0, Priority.NORMAL: 1, Priority.HIGH: 2, Priority.URGENT: 3}


class GoalStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskKind(StrEnum):
    WORK = "work"
    REVIEW = "review"
    PLANNING = "planning"
    HIRING = "hiring"
    ROUTINE = "routine"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    REVISION_REQUESTED = "revision_requested"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED})


class RunStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


ACTIVE_RUN_STATUSES = (RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING)
LIVE_RUN_STATUSES = (RunStatus.STARTING, RunStatus.RUNNING)


class CostKind(StrEnum):
    """How trustworthy a cost figure is. An estimate is never presented as a charge."""

    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class ArtifactKind(StrEnum):
    DOCUMENT = "document"
    CODE = "code"
    DESIGN = "design"
    REPORT = "report"
    DATA = "data"
    LINK = "link"
    OTHER = "other"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ReservationStatus(StrEnum):
    HELD = "held"
    SETTLED = "settled"
    RELEASED = "released"


class ApprovalKind(StrEnum):
    EXTERNAL_PURCHASE = "external_purchase"
    BUDGET_INCREASE = "budget_increase"
    EXTERNAL_COMMUNICATION = "external_communication"
    PRODUCTION_DEPLOY = "production_deploy"
    SENSITIVE_ACTION = "sensitive_action"
    HIRE = "hire"
    OTHER = "other"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXPIRED = "expired"


class Approver(StrEnum):
    HUMAN = "human"
    FOUNDER = "founder"


class Recurrence(StrEnum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ScheduleKind(StrEnum):
    HEARTBEAT = "heartbeat"
    ROUTINE = "routine"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"
