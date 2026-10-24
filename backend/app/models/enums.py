from enum import StrEnum


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"  # M7: a "human agent": reads everything, works on what is assigned to them
    VIEWER = "viewer"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REMOVED = "removed"  # history is kept; the row is never deleted


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class TokenScope(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


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
    PEER = "peer"  # M6: another agent checks the exact outbound content (maker-checker)


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


# ---------------------------------------------------------------- Milestone 4: skills & HR


class SkillSourceKind(StrEnum):
    HERMES_BUNDLED = "hermes_bundled"
    HERMES_OPTIONAL = "hermes_optional"
    GITHUB = "github"
    COMPANY_AUTHORED = "company_authored"
    UPLOADED = "uploaded"


class SkillTrust(StrEnum):
    """Mirrors the trust levels of the skills guard (runtime/tools/skills_guard.py)."""

    BUILTIN = "builtin"
    TRUSTED = "trusted"
    COMMUNITY = "community"


class ScanVerdict(StrEnum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    UNSCANNED = "unscanned"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    ARCHIVED = "archived"


class RoleFamily(StrEnum):
    """Avatar type of an employee (the canvas UI draws one per family)."""

    FOUNDER = "founder"
    HR = "hr"
    ENGINEERING = "engineering"
    DESIGN = "design"
    MARKETING = "marketing"
    SALES = "sales"
    CUSTOMER_SUCCESS = "customer_success"
    RESEARCH = "research"
    QA = "qa"
    OPERATIONS = "operations"
    FINANCE = "finance"
    GENERIC = "generic"


# ------------------------------------------------------ Milestone 5: autonomous operating model


class NoteStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    ARCHIVED = "archived"


class NoteCategory(StrEnum):
    DIRECTIVE = "directive"
    GOAL = "goal"
    REQUIREMENT = "requirement"
    IDEA = "idea"
    FEEDBACK = "feedback"
    QUESTION = "question"
    CONTEXT = "context"
    NOISE = "noise"


class MemoryKind(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    REQUIREMENT = "requirement"
    LESSON = "lesson"
    RESEARCH = "research"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


# Task ``blocked_reason`` values that park a backlog task without any human involvement.
WAITING_FOR_HIRE = "waiting_for_hire"
WAITING_ON_SUBTASKS = "waiting_on_subtasks"
AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"


# ------------------------------------------------------------ Milestone 6: guardrails & governance


class ActionTier(StrEnum):
    """Risk tier of an agent action (docs/specs/m6 section 1)."""

    READ = "read"
    INTERNAL_WRITE = "internal_write"
    ORG_CHANGE = "org_change"
    EXTERNAL_READ = "external_read"
    EXTERNAL_COMMUNICATION = "external_communication"
    EXTERNAL_COMMITMENT = "external_commitment"
    SPEND = "spend"
    IDENTITY = "identity"
    DESTRUCTIVE = "destructive"


class GuardrailDecision(StrEnum):
    ALLOW = "allow"
    PEER_REVIEW = "peer_review"
    COFOUNDER_APPROVAL = "cofounder_approval"
    HUMAN_APPROVAL = "human_approval"
    DENY = "deny"


# Decisions that park the action behind an Approval.
APPROVAL_DECISIONS = (
    GuardrailDecision.PEER_REVIEW,
    GuardrailDecision.COFOUNDER_APPROVAL,
    GuardrailDecision.HUMAN_APPROVAL,
)


class GuardrailScope(StrEnum):
    ALL = "all"
    FAMILY = "family"
    AGENT = "agent"


class ModelRouting(StrEnum):
    FREE_FIRST = "free_first"
    PINNED = "pinned"
    QUALITY_FIRST = "quality_first"
