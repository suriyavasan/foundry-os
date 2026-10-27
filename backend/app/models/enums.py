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


# ---------------------------------------------------------- Milestone 15: agent cards & purchasing


class CardProviderId(StrEnum):
    """Which adapter issues (or, for ``manual``, merely tracks) the company's cards. ``fake`` exists
    for the test suite only and is never registered in production."""

    MANUAL = "manual"
    STRIPE_ISSUING = "stripe_issuing"
    MERCURY = "mercury"
    FAKE = "fake"


class CardAccountStatus(StrEnum):
    SETUP = "setup"
    ACTIVE = "active"
    ATTENTION = "attention"  # the last provider check failed, or cards were frozen
    DISABLED = "disabled"


class CardMode(StrEnum):
    LIVE = "live"
    TEST = "test"


class CardKind(StrEnum):
    SINGLE_USE = "single_use"
    SUBSCRIPTION = "subscription"


class CardStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"
    EXPIRED = "expired"


class PurchaseStatus(StrEnum):
    REQUESTED = "requested"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ISSUING = "issuing"
    ISSUE_FAILED = "issue_failed"
    CARD_ISSUED = "card_issued"
    AWAITING_CHECKOUT = "awaiting_checkout"
    AUTHORIZED = "authorized"
    SETTLED = "settled"
    HUMAN_TASK = "human_task"  # tier D: purchasing is off, the request became a human task
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# Statuses whose ``held_micros`` still counts toward the purchase totals (a hold, not a charge).
PURCHASE_HOLD_STATUSES = (
    PurchaseStatus.APPROVED,
    PurchaseStatus.ISSUING,
    PurchaseStatus.ISSUE_FAILED,
    PurchaseStatus.CARD_ISSUED,
    PurchaseStatus.AWAITING_CHECKOUT,
    PurchaseStatus.AUTHORIZED,
)
TERMINAL_PURCHASE_STATUSES = frozenset(
    {
        PurchaseStatus.SETTLED,
        PurchaseStatus.HUMAN_TASK,
        PurchaseStatus.REJECTED,
        PurchaseStatus.EXPIRED,
        PurchaseStatus.CANCELLED,
    }
)


class PurchaseDecision(StrEnum):
    """Who approved a purchase (the band the deterministic policy picked)."""

    AUTO = "auto"
    COFOUNDER = "cofounder"
    HUMAN = "human"
    REPORT_ONLY = "report_only"


class CheckoutPath(StrEnum):
    PROGRAMMATIC = (
        "programmatic"  # M16: token rails; answers {"available": false, "milestone": "M16"}
    )
    HUMAN_CHECKOUT = "human_checkout"  # a system-issued card; a human clicks Pay
    HUMAN_BUYS = "human_buys"  # the owner's own card (manual mode) or purchasing disabled


class CardTxnKind(StrEnum):
    AUTHORIZATION = "authorization"
    CAPTURE = "capture"
    REFUND = "refund"
    DECLINE = "decline"
    REVERSAL = "reversal"


class CardTxnStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    REVERSED = "reversed"
    DECLINED = "declined"


class CardTxnMatch(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    MISMATCH = "mismatch"


class CardTxnSource(StrEnum):
    PROVIDER = "provider"
    HUMAN = "human"  # manual mode: "confirmed by you"


class ExpenseSource(StrEnum):
    HUMAN = "human"
    CARD = "card"
    PROVIDER_ACTION = "provider_action"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    REVIEW_DUE = "review_due"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    LAPSED = "lapsed"
