"""Polymorphic org references: ``agent:<id>`` | ``member:<id>``.

``manager_ref`` (agents and members), ``assignee_ref`` / ``reviewer_ref`` (tasks) and
``approver_ref`` (approvals) all use this form; a ``member`` id is a ``CompanyMember`` id (not a
user id), so a person who belongs to two companies has a different ref in each.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.errors import ValidationFailed

_REF = re.compile(r"^(agent|member):([0-9a-fA-F-]{8,64})$")


@dataclass(frozen=True)
class Ref:
    kind: str  # "agent" | "member"
    id: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    @property
    def is_member(self) -> bool:
        return self.kind == "member"


def agent_ref(agent_id: str | None) -> str | None:
    return f"agent:{agent_id}" if agent_id else None


def member_ref(member_id: str | None) -> str | None:
    return f"member:{member_id}" if member_id else None


def parse_ref(value: str, *, field: str = "ref") -> Ref:
    match = _REF.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise ValidationFailed(
            f"{field} must look like 'agent:<id>' or 'member:<id>'",
            details=[{"loc": ["body", field], "msg": "invalid reference"}],
        )
    return Ref(match.group(1), match.group(2))


def ref_of(agent_id: str | None, member_id: str | None) -> str | None:
    """The single ref a pair of mutually exclusive columns stands for."""
    return agent_ref(agent_id) or member_ref(member_id)
