"""Import every model so Base.metadata is complete (Alembic autogenerate relies on this)."""

from app.models.agent import Agent
from app.models.artifact import Artifact, ArtifactVersion
from app.models.audit import AuditEvent
from app.models.company import Company, CompanyMember
from app.models.department import Department
from app.models.governance import Approval, Decision
from app.models.ledger import BudgetReservation, UsageRecord
from app.models.policy import CompanyPolicy
from app.models.provider import Credential, ModelProvider
from app.models.run import CompanyEvent, Job, RunEvent, TaskRun
from app.models.schedule import Schedule, ScheduleFire
from app.models.skill import AgentSkill, RoleTemplate, Skill, SkillBlob
from app.models.user import User
from app.models.work import Goal, Project, Task, TaskDependency

__all__ = [
    "Agent",
    "AgentSkill",
    "Approval",
    "Artifact",
    "ArtifactVersion",
    "AuditEvent",
    "BudgetReservation",
    "Company",
    "CompanyEvent",
    "CompanyMember",
    "CompanyPolicy",
    "Credential",
    "Decision",
    "Department",
    "Goal",
    "Job",
    "ModelProvider",
    "Project",
    "RunEvent",
    "RoleTemplate",
    "Schedule",
    "ScheduleFire",
    "Skill",
    "SkillBlob",
    "Task",
    "TaskDependency",
    "TaskRun",
    "UsageRecord",
    "User",
]
