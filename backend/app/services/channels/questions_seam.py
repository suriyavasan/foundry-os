"""The one place where chat channels meet founder questions (M18 A2).

Reading uses the ``FounderQuestion`` model directly (company-filtered); answering goes through
``app.services.questions.answer(db, ctx, question_id, answer, *, via)`` so the same rules apply as
on the web (human admin only, still open, length limits, the asking agent is woken). When that
module is not present (an older checkout) chat shows "not available" instead of faking anything.
"""

from __future__ import annotations

import importlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotAvailable
from app.core.principal import CompanyContext
from app.models.questions import FounderQuestion, QuestionStatus


def _service() -> Any | None:
    try:
        return importlib.import_module("app.services.questions")
    except ImportError:
        return None


def available() -> bool:
    mod = _service()
    return mod is not None and hasattr(mod, "answer")


def get_question(db: Session, company_id: str, question_id: str) -> FounderQuestion | None:
    return db.scalar(
        select(FounderQuestion).where(
            FounderQuestion.id == question_id, FounderQuestion.company_id == company_id
        )
    )


def list_open(db: Session, company_id: str, limit: int = 10) -> list[FounderQuestion]:
    return list(
        db.scalars(
            select(FounderQuestion)
            .where(
                FounderQuestion.company_id == company_id,
                FounderQuestion.status == QuestionStatus.OPEN,
            )
            .order_by(FounderQuestion.created_at, FounderQuestion.id)
            .limit(limit)
        )
    )


def answer(
    db: Session, ctx: CompanyContext, question_id: str, text: str, *, via: str
) -> FounderQuestion:
    """Answer as the linked human (raises the service's own errors for stale/forbidden/invalid)."""
    mod = _service()
    if mod is None or not hasattr(mod, "answer"):
        raise NotAvailable("Founder questions are not available in this version", milestone="M18")
    mod.answer(db, ctx, question_id, text, via=via)
    row = get_question(db, ctx.company_id, question_id)
    assert row is not None
    return row
