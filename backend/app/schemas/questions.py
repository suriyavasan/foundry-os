"""M18: founder questions (an agent asks, a person answers)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

QuestionStatusLiteral = Literal["open", "answered", "defaulted", "cancelled"]
AnswerViaLiteral = Literal["web", "telegram", "discord", "whatsapp", "slack", "api"]

MAX_QUESTION = 1000
MAX_ANSWER = 500
MAX_OPTIONS = 6
MAX_OPTION = 120


class QuestionAsk(RequestModel):
    """What an agent sends (``POST /agent/v1/questions``). ``deadline_hours`` is clamped to 1-24."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    default_answer: str = Field(min_length=1, max_length=MAX_ANSWER)
    options: list[str] = Field(default_factory=list, max_length=MAX_OPTIONS)
    blocking: bool = False
    deadline_hours: int = Field(default=4, ge=-1_000_000, le=1_000_000)

    @field_validator("options")
    @classmethod
    def _options(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for option in v:
            option = option.strip()
            if not option:
                continue
            if len(option) > MAX_OPTION:
                raise ValueError(f"each option is at most {MAX_OPTION} characters")
            if option not in out:
                out.append(option)
        return out


class QuestionAnswer(RequestModel):
    answer: str = Field(min_length=1, max_length=MAX_ANSWER)


class QuestionOut(ApiModel):
    """The question text, options and default are written by an AI employee: show them as such
    (plain text, never as HTML or markdown links)."""

    id: str
    company_id: str
    agent_id: str
    task_id: str | None
    run_id: str | None
    question: str
    options: list[str]
    default_answer: str
    blocking: bool
    status: QuestionStatusLiteral
    answer: str | None
    answered_by_user_id: str | None
    answered_via: AnswerViaLiteral | None
    deadline: Timestamp
    answered_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp
