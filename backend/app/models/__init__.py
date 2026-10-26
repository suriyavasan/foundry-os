"""Import every model so Base.metadata is complete (Alembic autogenerate relies on this)."""

from app.models.accounts import ApiToken, AuthSession, Invitation, LoginThrottle, Notification
from app.models.agent import Agent
from app.models.artifact import Artifact, ArtifactVersion
from app.models.audit import AuditEvent
from app.models.autonomy import (
    Alert,
    AutonomyIntervention,
    FounderNote,
    MemoryEntry,
    NoteReply,
    WakeRequest,
)
from app.models.commands import CommandExchange
from app.models.company import Company, CompanyMember
from app.models.department import Department
from app.models.governance import Approval, Decision
from app.models.guardrails import (
    BudgetAlertMark,
    ExternalExpense,
    GuardrailEvent,
    GuardrailRule,
    RateLimitHit,
    SystemState,
)
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.policy import CompanyPolicy
from app.models.provider import Credential, ModelProvider
from app.models.reachability import (
    CompanyDomain,
    ConnectedAccount,
    DomainRegistration,
    EmailIdentity,
    HostedUi,
    Mailbox,
    MailMessage,
    NotificationPreference,
)
from app.models.reflections import CompanyStandard, Reflection, ReflectionResponse
from app.models.run import CompanyEvent, Job, RunEvent, TaskRun
from app.models.schedule import Schedule, ScheduleFire
from app.models.skill import AgentSkill, RoleTemplate, Skill, SkillBlob
from app.models.user import User
from app.models.work import Goal, Project, Task, TaskDependency

__all__ = [
    "Agent",
    "Alert",
    "AgentSkill",
    "ApiToken",
    "AuthSession",
    "AutonomyIntervention",
    "Invitation",
    "LoginThrottle",
    "Notification",
    "NotificationPreference",
    "Approval",
    "Artifact",
    "ArtifactVersion",
    "AuditEvent",
    "BudgetAlertMark",
    "BudgetReservation",
    "Company",
    "CommandExchange",
    "CompanyStandard",
    "Reflection",
    "ReflectionResponse",
    "CompanyDomain",
    "CompanyEvent",
    "ConnectedAccount",
    "DomainRegistration",
    "EmailIdentity",
    "HostedUi",
    "MailMessage",
    "Mailbox",
    "CompanyMember",
    "CompanyPolicy",
    "Credential",
    "Decision",
    "ExternalExpense",
    "Department",
    "FounderNote",
    "Goal",
    "GuardrailEvent",
    "GuardrailRule",
    "Job",
    "MemoryEntry",
    "ModelProvider",
    "NoteReply",
    "Project",
    "RateLimitHit",
    "RunEvent",
    "RoleTemplate",
    "Schedule",
    "ScheduleFire",
    "Skill",
    "SkillBlob",
    "Task",
    "TaskDependency",
    "SystemState",
    "TaskRun",
    "UsageRecord",
    "User",
    "WakeRequest",
]
