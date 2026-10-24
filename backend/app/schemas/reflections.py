"""M10 reflections and company standards (docs/specs/m10 section 4)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

ReflectionKindLiteral = Literal["observation", "standard_proposal", "retro", "risk", "praise"]
ReflectionStatusLiteral = Literal["open", "endorsed", "revised", "rejected", "adopted", "archived"]
ReactionLiteral = Literal["endorse", "challenge", "refine", "comment"]
StandardStatusLiteral = Literal["active", "retired"]
EvidenceTypeLiteral = Literal["task", "artifact", "run", "project", "agent", "goal"]
MAX_BODY_CHARS = 6144
MAX_STANDARD_CHARS = 500


class EvidenceRef(ApiModel):
    type: EvidenceTypeLiteral
    id: str
    label: str | None = None


class ReflectionResponse(ApiModel):
    id: str
    reflection_id: str
    author_type: Literal["human", "agent"]
    author_id: str | None
    author_name: str | None = None
    reaction: ReactionLiteral
    body: str | None
    created_at: Timestamp
    # What this response did to the reflection (the status after it, the standard it adopted).
    reflection_status: ReflectionStatusLiteral | None = None
    standard_id: str | None = None


class Reflection(ApiModel):
    id: str
    company_id: str
    author_agent_id: str
    author_name: str | None = None
    kind: ReflectionKindLiteral
    title: str
    body: str
    evidence: list[EvidenceRef]
    proposed_standard: str | None
    status: ReflectionStatusLiteral
    standard_id: str | None = None
    response_count: int = 0
    last_response: ReflectionResponse | None = None
    created_at: Timestamp
    updated_at: Timestamp


class ReflectionDetail(Reflection):
    responses: list[ReflectionResponse]


class ReflectionResponseCreate(RequestModel):
    reaction: ReactionLiteral
    body: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _text_for_pushback(self):
        if self.reaction != "endorse" and not (self.body or "").strip():
            raise ValueError("a challenge, refinement or comment needs text")
        return self


class CompanyStandard(ApiModel):
    id: str
    company_id: str
    title: str
    statement: str
    source_reflection_id: str | None
    adopted_by_user_id: str | None
    adopted_at: Timestamp
    status: StandardStatusLiteral
    applies_to: list[str]
    created_at: Timestamp
    updated_at: Timestamp


def _tags(value: list[str]) -> list[str]:
    cleaned = [v.strip() for v in value if v and v.strip()]
    if len(cleaned) > 12 or any(len(v) > 60 for v in cleaned):
        raise ValueError("at most 12 entries of up to 60 characters")
    return list(dict.fromkeys(cleaned))


class CompanyStandardCreate(RequestModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=MAX_STANDARD_CHARS)
    applies_to: list[str] = Field(default_factory=list)

    @field_validator("applies_to")
    @classmethod
    def _clean_tags(cls, v):
        return _tags(v)


class CompanyStandardUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    statement: str | None = Field(default=None, min_length=1, max_length=MAX_STANDARD_CHARS)
    status: StandardStatusLiteral | None = None
    applies_to: list[str] | None = None

    @field_validator("title", "statement", "status", "applies_to")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("applies_to")
    @classmethod
    def _clean_tags(cls, v):
        return _tags(v)


# --- agent API (AI Co-founder)


class EvidenceIn(RequestModel):
    type: EvidenceTypeLiteral
    id: str = Field(min_length=1, max_length=64)


class AgentReflectionPost(RequestModel):
    kind: ReflectionKindLiteral
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=12)
    proposed_standard: str | None = Field(default=None, max_length=MAX_STANDARD_CHARS)


class AgentReflectionReply(RequestModel):
    body: str = Field(min_length=1, max_length=4000)


class AgentReflectionRevise(RequestModel):
    body: str | None = Field(default=None, min_length=1, max_length=MAX_BODY_CHARS)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    proposed_standard: str | None = Field(default=None, min_length=1, max_length=MAX_STANDARD_CHARS)
    note: str | None = Field(default=None, max_length=2000)  # what changed and why (a reply)


class AgentReflection(ApiModel):
    """A reflection as the AI Co-founder reads it back (state block)."""

    id: str
    kind: ReflectionKindLiteral
    title: str
    status: ReflectionStatusLiteral
    proposed_standard: str | None
    latest_response: str | None = None
    latest_reaction: ReactionLiteral | None = None
