"""M18: questions AI employees ask the Founder (owners/admins list, answer or dismiss them).

The question text is written by an AI employee: clients show it as plain text."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.questions import QuestionAnswer, QuestionOut, QuestionStatusLiteral
from app.services import questions
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["questions"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/questions", response_model=Page[QuestionOut])
def list_questions(
    ctx: CompanyCtx,
    db: DbSession,
    status: QuestionStatusLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = questions.list_questions(db, ctx, status=status, limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": nxt}


@router.post("/questions/{question_id}/answer", response_model=QuestionOut)
def answer_question(question_id: str, body: QuestionAnswer, ctx: CompanyCtx, db: DbSession):
    return questions.answer(db, ctx, question_id, body.answer, via="web")


@router.post("/questions/{question_id}/cancel", response_model=QuestionOut)
def cancel_question(question_id: str, ctx: CompanyCtx, db: DbSession):
    return questions.cancel(db, ctx, question_id)
