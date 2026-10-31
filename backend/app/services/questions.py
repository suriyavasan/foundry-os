"""M18 founder questions: an agent asks the Founder something it cannot decide for itself.

* Every question carries the **default** the agent uses and a **deadline** (1-24 h, default 4 h).
  Nothing waits forever: when the deadline passes the question is ``defaulted`` and the default
  stands (``sweep_deadlines``, run by the scheduler every 60 s).
* ``blocking=False`` (the default): the agent keeps going with its default. If the Founder later
  answers with something else, the asking agent is woken (``founder_answered``).
* ``blocking=True`` (needs a task): when the run ends the task is parked
  (``blocked_reason = waiting_for_founder``) and returns to ``ready`` on answer, cancel or default;
  its next run finds the answer in its context (``context_lines``).
* Only owners and admins (human principals) answer or cancel. An agent never can.
* Limits (code, not prompt): 5 open questions per company, 2 per agent per 24 h.

The question text, options and default are written by an AI employee: they are stored and shown as
plain text and scanned for secrets before they are stored.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, InvalidTransition, NotFound, RateLimited, ValidationFailed
from app.core.principal import CompanyContext, Role, require_human_admin
from app.core.time import utcnow
from app.guardrails import dlp
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import WAITING_FOR_FOUNDER, CompanyStatus
from app.models.questions import ANSWER_VIAS, FounderQuestion, QuestionStatus
from app.models.run import TaskRun
from app.models.work import Task
from app.schemas.questions import MAX_ANSWER, QuestionAsk
from app.services import audit, events, notifications
from app.services.pagination import paginate

logger = logging.getLogger("company_os.questions")

MAX_OPEN_PER_COMPANY = 5
MAX_PER_AGENT_PER_DAY = 2
MIN_DEADLINE_HOURS = 1
MAX_DEADLINE_HOURS = 24
DEFAULT_DEADLINE_HOURS = 4
SWEEP_BATCH = 100
DECIDE_YOURSELF = "Decide yourself and record it with company_record_decision."


class QuestionLimit(RateLimited):
    """Too many questions for the Founder (429 ``question_limit``)."""

    code = "question_limit"
    default_message = f"Too many questions for the Founder. {DECIDE_YOURSELF}"


# ---------------------------------------------------------------- helpers


def get_question(db: Session, company_id: str, question_id: str) -> FounderQuestion:
    row = db.scalar(
        select(FounderQuestion).where(
            FounderQuestion.id == question_id, FounderQuestion.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Question not found")
    return row


def _reject_secrets(text: str, what: str) -> None:
    if any(f.kind in ("secret", "own_token") for f in dlp.scan_text(text)):
        raise ValidationFailed(
            f"The {what} looks like it contains a secret (key, token or password). Never put "
            "secrets in a question or an answer.",
            details=[{"loc": ["body", what], "msg": "looks like a secret"}],
        )


def _event_payload(q: FounderQuestion) -> dict:
    # Ids and states only: every member of the company can read the event stream.
    return {
        "question_id": q.id,
        "agent_id": q.agent_id,
        "task_id": q.task_id,
        "blocking": q.blocking,
        "status": q.status,
        "deadline": q.deadline.isoformat(),
        "answered_via": q.answered_via,
    }


def _same(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def open_blocking_for_task(db: Session, task: Task) -> list[FounderQuestion]:
    return list(
        db.scalars(
            select(FounderQuestion).where(
                FounderQuestion.company_id == task.company_id,
                FounderQuestion.task_id == task.id,
                FounderQuestion.blocking.is_(True),
                FounderQuestion.status == QuestionStatus.OPEN,
            )
        )
    )


# ---------------------------------------------------------------- asking


def ask(
    db: Session,
    ctx: CompanyContext,
    run: TaskRun | None,
    data: QuestionAsk,
    *,
    now: datetime | None = None,
) -> FounderQuestion:
    """An agent asks the Founder a question (with its default and a deadline). Commits."""
    if not ctx.principal.is_agent or ctx.role != Role.AGENT:
        raise Forbidden("Only AI employees ask the Founder questions")
    agent = db.scalar(
        select(Agent).where(Agent.id == ctx.principal.id, Agent.company_id == ctx.company_id)
    )
    if agent is None:
        raise NotFound("Agent not found")
    now = now or utcnow()
    task_id = run.task_id if run is not None else None
    if data.blocking and not task_id:
        raise ValidationFailed(
            "A blocking question needs a task: only a run on a task can wait for the Founder. "
            "Ask without blocking, or decide yourself.",
            details=[{"loc": ["body", "blocking"], "msg": "no task"}],
        )
    for text, what in ((data.question, "question"), (data.default_answer, "default_answer")):
        _reject_secrets(text, what)
    for option in data.options:
        _reject_secrets(option, "options")
    open_count = db.scalar(
        select(func.count(FounderQuestion.id)).where(
            FounderQuestion.company_id == ctx.company_id,
            FounderQuestion.status == QuestionStatus.OPEN,
        )
    )
    if (open_count or 0) >= MAX_OPEN_PER_COMPANY:
        raise QuestionLimit(
            f"The Founder already has {MAX_OPEN_PER_COMPANY} open questions. {DECIDE_YOURSELF}",
            details={"limit": "open_questions", "max": MAX_OPEN_PER_COMPANY},
        )
    recent = db.scalar(
        select(func.count(FounderQuestion.id)).where(
            FounderQuestion.company_id == ctx.company_id,
            FounderQuestion.agent_id == agent.id,
            FounderQuestion.created_at >= now - timedelta(hours=24),
        )
    )
    if (recent or 0) >= MAX_PER_AGENT_PER_DAY:
        raise QuestionLimit(
            f"You already asked {MAX_PER_AGENT_PER_DAY} questions in the last 24 hours. "
            f"{DECIDE_YOURSELF}",
            details={"limit": "questions_per_agent_per_day", "max": MAX_PER_AGENT_PER_DAY},
        )
    hours = max(MIN_DEADLINE_HOURS, min(MAX_DEADLINE_HOURS, int(data.deadline_hours)))
    q = FounderQuestion(
        company_id=ctx.company_id,
        agent_id=agent.id,
        task_id=task_id,
        run_id=run.id if run is not None else None,
        question=data.question,
        options=list(data.options),
        default_answer=data.default_answer,
        blocking=bool(data.blocking),
        status=QuestionStatus.OPEN,
        deadline=now + timedelta(hours=hours),
        created_at=now,
        updated_at=now,
    )
    db.add(q)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="question.asked",
        target_type="question",
        target_id=q.id,
        summary=f"{agent.name} asked the Founder: {q.question[:120]}",
        data={
            "agent_id": agent.id,
            "task_id": task_id,
            "blocking": q.blocking,
            "deadline": q.deadline.isoformat(),
        },
    )
    events.emit(db, ctx.company_id, "question.created", _event_payload(q))
    notifications.notify_admins(
        db,
        ctx.company_id,
        notifications.FOUNDER_QUESTION,
        f"{agent.name} has a question: {q.question[:200]}",
        related_type="question",
        related_id=q.id,
        body=f"If you do not answer, {agent.name} will use: {q.default_answer[:300]}",
    )
    db.commit()
    return q


# ---------------------------------------------------------------- reading


def list_questions(
    db: Session,
    ctx: CompanyContext,
    *,
    status: str | None = None,
    limit: int,
    cursor: str | None = None,
):
    require_human_admin(ctx, "see founder questions")
    stmt = select(FounderQuestion).where(FounderQuestion.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(FounderQuestion.status == status)
    return paginate(db, stmt, FounderQuestion, limit=limit, cursor=cursor, descending=True)


# ---------------------------------------------------------------- resolving


def _release_task(db: Session, q: FounderQuestion, now: datetime) -> None:
    """The task parked for this question returns to the queue (once no other blocking question of
    it is open). No commit."""
    if not q.task_id:
        return
    db.flush()  # autoflush is off: the question's new status must be visible to the query below
    task = db.scalar(select(Task).where(Task.id == q.task_id, Task.company_id == q.company_id))
    if task is None or task.blocked_reason != WAITING_FOR_FOUNDER:
        return
    if open_blocking_for_task(db, task):
        return
    from app.services import tasks

    task.blocked_reason = None
    task.updated_at = now
    task.next_attempt_at = None
    tasks.recompute_readiness(db, task.company_id, task_ids=[task.id], principal=None, now=now)


def _wake_asker(db: Session, q: FounderQuestion, now: datetime) -> None:
    from app.services import wakes

    wakes.wake_agent(
        db,
        q.company_id,
        q.agent_id,
        "founder_answered",
        f"The founder answered '{q.question[:80]}': {(q.answer or '')[:150]}. Your default was "
        f"'{q.default_answer[:60]}'. Adjust your work if it changes anything.",
        ref_type="question",
        ref_id=q.id,
        now=now,
    )


def answer(
    db: Session,
    ctx: CompanyContext,
    question_id: str,
    answer: str,
    *,
    via: str = "web",
    now: datetime | None = None,
) -> FounderQuestion:
    """A person (owner/admin) answers an open question. Never an agent. Commits."""
    require_human_admin(ctx, "answer founder questions")
    if via not in ANSWER_VIAS:
        raise ValidationFailed("Unknown answer channel", details=[{"loc": ["via"], "msg": via}])
    text = answer.strip()
    if not text:
        raise ValidationFailed("The answer must not be empty")
    if len(text) > MAX_ANSWER:
        raise ValidationFailed(f"The answer is at most {MAX_ANSWER} characters")
    _reject_secrets(text, "answer")
    q = get_question(db, ctx.company_id, question_id)
    if q.status != QuestionStatus.OPEN:
        raise InvalidTransition(f"This question is already {q.status}", details={"from": q.status})
    now = now or utcnow()
    q.status = QuestionStatus.ANSWERED
    q.answer = text
    q.answered_by_user_id = ctx.principal.id
    q.answered_via = via
    q.answered_at = now
    q.updated_at = now
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="question.answered",
        target_type="question",
        target_id=q.id,
        summary=f"The Founder answered a question from an AI employee (via {via})",
        data={
            "via": via,
            "agent_id": q.agent_id,
            "differs_from_default": not _same(text, q.default_answer),
        },
    )
    events.emit(db, ctx.company_id, "question.updated", _event_payload(q))
    parked = _is_parked(db, q)
    _release_task(db, q, now)
    if not parked and not _same(text, q.default_answer):
        _wake_asker(db, q, now)
    db.commit()
    return q


def _is_parked(db: Session, q: FounderQuestion) -> bool:
    if not (q.blocking and q.task_id):
        return False
    task = db.scalar(select(Task).where(Task.id == q.task_id, Task.company_id == q.company_id))
    return task is not None and task.blocked_reason == WAITING_FOR_FOUNDER


def cancel(
    db: Session, ctx: CompanyContext, question_id: str, *, now: datetime | None = None
) -> FounderQuestion:
    """A person withdraws an open question: the agent's default stands. Never an agent. Commits."""
    require_human_admin(ctx, "cancel founder questions")
    q = get_question(db, ctx.company_id, question_id)
    if q.status != QuestionStatus.OPEN:
        raise InvalidTransition(f"This question is already {q.status}", details={"from": q.status})
    now = now or utcnow()
    q.status = QuestionStatus.CANCELLED
    q.updated_at = now
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="question.cancelled",
        target_type="question",
        target_id=q.id,
        summary="The Founder dismissed a question from an AI employee (the default stands)",
        data={"agent_id": q.agent_id},
    )
    events.emit(db, ctx.company_id, "question.updated", _event_payload(q))
    _release_task(db, q, now)
    db.commit()
    return q


def sweep_deadlines(db: Session, now: datetime, *, limit: int = SWEEP_BATCH) -> int:
    """Scheduler pass (every 60 s): open questions of active companies past their deadline become
    ``defaulted`` (the agent's default stands); a parked task returns to the queue. Each question
    commits on its own. Returns how many were defaulted."""
    ids = list(
        db.execute(
            select(FounderQuestion.id, FounderQuestion.company_id)
            .join(Company, Company.id == FounderQuestion.company_id)
            .where(
                FounderQuestion.status == QuestionStatus.OPEN,
                FounderQuestion.deadline <= now,
                Company.status == CompanyStatus.ACTIVE,
            )
            .order_by(FounderQuestion.deadline, FounderQuestion.id)
            .limit(limit)
        ).all()
    )
    done = 0
    for question_id, company_id in ids:
        try:
            q = db.scalar(
                select(FounderQuestion).where(
                    FounderQuestion.id == question_id,
                    FounderQuestion.company_id == company_id,
                    FounderQuestion.status == QuestionStatus.OPEN,
                )
            )
            if q is None:
                continue
            q.status = QuestionStatus.DEFAULTED
            q.answer = q.default_answer
            q.answered_at = now
            q.updated_at = now
            audit.record(
                db,
                company_id=company_id,
                principal=None,
                action="question.defaulted",
                target_type="question",
                target_id=q.id,
                summary="No answer from the Founder in time: the agent's default stands",
                data={"agent_id": q.agent_id, "task_id": q.task_id},
            )
            events.emit(db, company_id, "question.updated", _event_payload(q))
            _release_task(db, q, now)
            db.commit()
            done += 1
        except Exception:
            db.rollback()
            logger.exception("question deadline sweep failed for %s", question_id)
    return done


# ---------------------------------------------------------------- context for the next run


def context_lines(db: Session, task: Task, *, limit: int = 5) -> list[str]:
    """What the Founder answered (or did not) about this task, for the next run's context. The
    answer is trusted Founder input; the question text is the agent's own."""
    rows = list(
        db.scalars(
            select(FounderQuestion)
            .where(
                FounderQuestion.company_id == task.company_id,
                FounderQuestion.task_id == task.id,
                FounderQuestion.status.in_([QuestionStatus.ANSWERED, QuestionStatus.DEFAULTED]),
            )
            .order_by(FounderQuestion.answered_at.desc(), FounderQuestion.id)
            .limit(limit)
        )
    )
    lines: list[str] = []
    for q in reversed(rows):
        if q.status == QuestionStatus.ANSWERED:
            lines.append(f"You asked: {q.question[:300]} -> The founder answered: {q.answer}")
        else:
            lines.append(
                f"You asked: {q.question[:300]} -> No answer from the founder; use your default: "
                f"{q.default_answer}"
            )
    return lines
