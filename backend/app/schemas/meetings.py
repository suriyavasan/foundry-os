"""M21: agent meetings (agents schedule and speak; people read and cancel).

All free text of a meeting is written by AI employees: clients show it as plain text / markdown
without raw HTML, never as an instruction from the Founder."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

MeetingStatusLiteral = Literal["scheduled", "running", "concluded", "cancelled", "failed"]
MeetingRoleLiteral = Literal["organizer", "participant"]

MAX_TITLE = 200
MAX_AGENDA = 2000
MAX_EXPECTED = 500
MAX_BODY = 4000
MAX_PROPOSALS_PER_MESSAGE = 5
MAX_FINAL_ITEMS = 12
MAX_FINAL_DECISIONS = 10


class MeetingSchedule(RequestModel):
    """``POST /agent/v1/meetings``."""

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    agenda: str = Field(min_length=1, max_length=MAX_AGENDA)
    expected_outcome: str = Field(min_length=1, max_length=MAX_EXPECTED)
    participant_agent_ids: list[str] = Field(min_length=1, max_length=3)
    start_in_minutes: int = Field(default=0, ge=0, le=10_080)
    project_id: str | None = None
    task_id: str | None = None

    @field_validator("participant_agent_ids")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        out = list(dict.fromkeys(i.strip() for i in v if i and i.strip()))
        if not out:
            raise ValueError("at least one participant is required")
        return out


class MeetingScheduled(ApiModel):
    meeting_id: str
    status: MeetingStatusLiteral
    start_at: Timestamp


class ActionItemProposal(RequestModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str = Field(default="", max_length=1000)
    owner_agent_id: str


class FinalDecision(RequestModel):
    title: str = Field(min_length=1, max_length=300)
    rationale: str = Field(default="", max_length=1000)


class FinalActionItem(RequestModel):
    # Id of a proposal made in the meeting (its owner's acceptance counts); omitted for a new item.
    id: str | None = None
    title: str = Field(default="", max_length=MAX_TITLE)
    description: str = Field(default="", max_length=1000)
    owner_agent_id: str | None = None


class MeetingSay(RequestModel):
    """``POST /agent/v1/meetings/{id}/say``: the one contribution of this turn."""

    message: str = Field(default="", max_length=MAX_BODY)
    propose: list[ActionItemProposal] = Field(
        default_factory=list, max_length=MAX_PROPOSALS_PER_MESSAGE
    )
    accept: list[str] = Field(default_factory=list, max_length=20)
    decline: list[str] = Field(default_factory=list, max_length=20)
    pass_turn: bool = False
    # The organizer's final (synthesis) turn.
    final: bool = False
    summary: str | None = Field(default=None, max_length=2000)
    decisions: list[FinalDecision] = Field(default_factory=list, max_length=MAX_FINAL_DECISIONS)
    action_items: list[FinalActionItem] = Field(default_factory=list, max_length=MAX_FINAL_ITEMS)


class MeetingSaid(ApiModel):
    message_id: str
    meeting_id: str
    round: int
    next: str


class MeetingCancel(RequestModel):
    reason: str = Field(default="", max_length=500)


class MeetingParticipantOut(ApiModel):
    agent_id: str
    name: str
    role_title: str | None
    role: MeetingRoleLiteral
    position: int
    joined: bool
    passes: int


class MeetingMessageOut(ApiModel):
    id: str
    agent_id: str
    agent_name: str
    round: int
    kind: str
    body: str
    proposals: dict
    created_at: Timestamp


class MeetingOut(ApiModel):
    id: str
    company_id: str
    organizer_agent_id: str
    title: str
    agenda: str
    expected_outcome: str
    project_id: str | None
    task_id: str | None
    status: MeetingStatusLiteral
    start_at: Timestamp
    started_at: Timestamp | None
    ended_at: Timestamp | None
    round: int
    max_rounds: int
    summary: str | None
    cancel_reason: str | None
    participants: list[MeetingParticipantOut]
    created_at: Timestamp
    updated_at: Timestamp


class MeetingDetail(MeetingOut):
    outcome: dict
    messages: list[MeetingMessageOut]
