"""Import every model so Base.metadata is complete (Alembic autogenerate relies on this)."""

from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.company import Company, CompanyMember
from app.models.department import Department
from app.models.user import User

__all__ = ["Agent", "AuditEvent", "Company", "CompanyMember", "Department", "User"]
