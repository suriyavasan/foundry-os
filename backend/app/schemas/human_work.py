"""M22: human work (an AI employee needs a person to do something) and outsourcing (freelance jobs
HR drafts, a person approves, posts and records).

Titles, instructions, briefs and notes are written by AI employees (and edited by people): clients
show them as plain text, never as HTML or markdown links."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

HumanCategoryLiteral = Literal[
    "signature", "call", "identity", "payment", "physical", "account", "legal", "other"
]
MarketplaceLiteral = Literal["fiverr", "upwork", "other"]
OutsourceStatusLiteral = Literal[
    "draft",
    "pending_approval",
    "approved",
    "posted",
    "hired",
    "delivered",
    "closed",
    "cancelled",
    "declined",
]

MAX_TITLE = 300
MAX_INSTRUCTIONS = 4000
MAX_WHY = 300
MAX_JOB_TITLE = 200
MAX_BRIEF = 6000
MAX_SKILLS = 10
MAX_SKILL = 60
MAX_NOTE = 2000
MAX_REASON = 500
MAX_URL = 500
MAX_NAME = 200


# ------------------------------------------------------------------ human work


class HumanWorkRequest(RequestModel):
    """``POST /agent/v1/human-work`` (``company_request_human_work``)."""

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    instructions: str = Field(min_length=1, max_length=MAX_INSTRUCTIONS)
    category: HumanCategoryLiteral
    why_human: str = Field(min_length=1, max_length=MAX_WHY)
    due_in_hours: int = Field(default=48, ge=-1_000_000, le=1_000_000)  # clamped to 1-720
    assignee_member_id: str | None = Field(default=None, max_length=36)
    blocks_my_task: bool = True


class HumanWorkRequested(ApiModel):
    task_id: str
    status: str
    assignee_ref: str | None
    pool: bool
    blocks_my_task: bool
    due_at: Timestamp
    next: str


class HumanWorkItem(ApiModel):
    id: str
    company_id: str
    title: str
    description: str | None
    instructions: str | None
    category: HumanCategoryLiteral | None
    status: str
    priority: str
    due_at: Timestamp | None
    overdue: bool
    assignee_ref: str | None
    assignee_name: str | None
    pool: bool  # nobody has claimed it yet
    requested_by_agent_id: str | None
    requested_by_agent_name: str | None
    project_id: str | None
    blocking_task_ids: list[str]  # tasks that wait for this one
    result_summary: str | None
    output_refs: list[str]
    created_at: Timestamp
    completed_at: Timestamp | None


class HumanWorkDecline(RequestModel):
    reason: str = Field(min_length=1, max_length=MAX_REASON)


# ------------------------------------------------------------------ outsourcing


class OutsourceDraft(RequestModel):
    """``POST /agent/v1/outsource-jobs`` (``company_draft_outsource_job``, HR only)."""

    title: str = Field(min_length=1, max_length=MAX_JOB_TITLE)
    brief: str = Field(min_length=1, max_length=MAX_BRIEF)
    skills: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)
    marketplace: MarketplaceLiteral
    budget_usd: float = Field(gt=0, le=10_000_000)
    task_id: str | None = Field(default=None, max_length=36)

    @field_validator("skills")
    @classmethod
    def _skills(cls, v: list[str]) -> list[str]:
        out = list(dict.fromkeys(s.strip() for s in v if s and s.strip()))
        if any(len(s) > MAX_SKILL for s in out):
            raise ValueError(f"each skill is at most {MAX_SKILL} characters")
        return out


class OutsourceDrafted(ApiModel):
    job_id: str
    # Not called ``status``: the runtime reads ``status: pending_approval`` as a guardrail hold.
    job_status: OutsourceStatusLiteral
    approval_id: str | None
    budget_micros: int
    next: str


class OutsourceJobOut(ApiModel):
    id: str
    company_id: str
    created_by_agent_id: str | None
    created_by_agent_name: str | None
    task_id: str | None
    task_title: str | None
    title: str
    brief: str
    skills: list[str]
    marketplace: MarketplaceLiteral
    budget_micros: int
    status: OutsourceStatusLiteral
    approval_id: str | None
    approval_status: str | None
    listing_url: str | None
    freelancer_name: str | None
    freelancer_profile_url: str | None
    agreed_price_micros: int | None
    paid_micros: int | None
    cost_kind: Literal["verified"] | None  # only a recorded payment has a cost, and it is verified
    ledger_entry_id: str | None
    delivered_artifact_ids: list[str]
    notes: str | None
    # Helper links only (no fetching, no scraping): the marketplace's own public pages.
    open_url: str | None
    created_at: Timestamp
    updated_at: Timestamp


class OutsourcePosted(RequestModel):
    listing_url: str = Field(min_length=1, max_length=MAX_URL)


class OutsourceHired(RequestModel):
    freelancer_name: str = Field(min_length=1, max_length=MAX_NAME)
    freelancer_profile_url: str | None = Field(default=None, max_length=MAX_URL)
    agreed_price_usd: float = Field(ge=0, le=10_000_000)


class OutsourceDelivered(RequestModel):
    artifact_ids: list[str] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=MAX_NOTE)


class OutsourcePaid(RequestModel):
    amount_usd: float = Field(gt=0, le=10_000_000)


class OutsourceReason(RequestModel):
    reason: str | None = Field(default=None, max_length=MAX_REASON)


class OutsourceRaiseBudget(RequestModel):
    new_budget_usd: float = Field(gt=0, le=10_000_000)
    reason: str | None = Field(default=None, max_length=MAX_REASON)
