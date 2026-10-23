"""Dispatcher: turns ``ready`` tasks into queued runs, within policy and budget.

For each active company: recompute readiness, then repeatedly (one transaction per dispatch)
take the next ready task by (priority desc, created_at), reserve budget, create a queued
``TaskRun`` and enqueue the ``execute_run`` job. The transaction starts with the budget lock
(``ledger.acquire_budget_lock``), so the concurrency cap and the budget limits are checked
against a consistent view even with several dispatchers running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import case, select

from app.core.errors import BudgetExceeded
from app.engine.context import EngineContext
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import (
    ACTIVE_RUN_STATUSES,
    AgentStatus,
    CompanyStatus,
    TaskStatus,
)
from app.models.run import TaskRun
from app.models.work import Task
from app.services import jobs, ledger, policy, runs, tasks

PRIORITY_RANK = case(
    (Task.priority == "urgent", 3),
    (Task.priority == "high", 2),
    (Task.priority == "normal", 1),
    else_=0,
)
CANDIDATE_WINDOW = 50


@dataclass
class DispatchReport:
    queued_run_ids: list[str] = field(default_factory=list)
    budget_blocked_task_ids: list[str] = field(default_factory=list)
    policy_blocked_task_ids: list[str] = field(default_factory=list)
    waiting_for_runtime: int = 0


def dispatch_tick(ctx: EngineContext, now: datetime | None = None) -> DispatchReport:
    now = now or ctx.clock()
    report = DispatchReport()
    with ctx.session_factory() as db:
        company_ids = list(
            db.scalars(select(Company.id).where(Company.status == CompanyStatus.ACTIVE))
        )
    for company_id in company_ids:
        _dispatch_company(ctx, company_id, now, report)
    return report


def _dispatch_company(
    ctx: EngineContext, company_id: str, now: datetime, report: DispatchReport
) -> None:
    with ctx.session_factory() as db:
        tasks.recompute_readiness(db, company_id, now=now)
        db.commit()

    skipped: set[str] = set()
    while _dispatch_one(ctx, company_id, now, report, skipped):
        pass


def _dispatch_one(
    ctx: EngineContext,
    company_id: str,
    now: datetime,
    report: DispatchReport,
    skipped: set[str],
) -> bool:
    """Dispatch at most one task. Returns True if progress was made (keep looping)."""
    with ctx.session_factory() as db:
        ledger.acquire_budget_lock(db, company_id)  # first statement of the transaction
        company = db.get(Company, company_id)
        if company is None or company.status != CompanyStatus.ACTIVE:
            db.rollback()
            return False
        limits = policy.load_policy(db, company_id)
        active = list(
            db.execute(
                select(TaskRun.agent_id).where(
                    TaskRun.company_id == company_id, TaskRun.status.in_(ACTIVE_RUN_STATUSES)
                )
            ).scalars()
        )
        if len(active) >= limits.max_concurrent_runs:
            db.rollback()
            return False
        busy_agents = set(active)

        candidate = None
        agent = None
        stmt = (
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.status == TaskStatus.READY,
                Task.id.notin_(skipped) if skipped else True,
            )
            .order_by(PRIORITY_RANK.desc(), Task.created_at, Task.id)
            .limit(CANDIDATE_WINDOW)
        )
        for task in db.scalars(stmt):
            assignee = db.get(Agent, task.assignee_agent_id) if task.assignee_agent_id else None
            if assignee is None or assignee.status != AgentStatus.ACTIVE:
                skipped.add(task.id)
                continue
            if assignee.id in busy_agents:  # one run per agent at a time
                skipped.add(task.id)
                continue
            allowed = limits.allowed_model_providers
            if allowed and (assignee.model_provider or "") not in allowed:
                tasks.transition(
                    db,
                    task,
                    TaskStatus.BLOCKED,
                    principal=None,
                    now=now,
                    reason="model provider not allowed by company policy",
                    error={
                        "code": "provider_not_allowed",
                        "message": f"Provider {assignee.model_provider!r} is not in the "
                        "company's allowed_model_providers",
                        "run_id": None,
                    },
                )
                db.commit()
                report.policy_blocked_task_ids.append(task.id)
                return True
            if not ctx.health.usable(assignee.runtime):
                skipped.add(task.id)
                report.waiting_for_runtime += 1
                continue
            candidate, agent = task, assignee
            break
        if candidate is None or agent is None:
            db.rollback()
            return False

        # Budget: reserve the task's remaining budget, else the policy default.
        try:
            if candidate.budget_micros is not None:
                remaining = candidate.budget_micros - ledger.committed_micros(
                    db, company_id, task_id=candidate.id
                )
                if remaining <= 0:
                    raise BudgetExceeded(
                        "Task budget is exhausted",
                        details={
                            "limit": "task_budget",
                            "limit_micros": candidate.budget_micros,
                            "retry_after": None,
                        },
                    )
                amount = remaining
            else:
                amount = limits.default_run_reservation_micros
            reservation = ledger.reserve(db, company_id, agent, candidate, amount, now=now)
        except BudgetExceeded as exc:
            details = exc.details or {}
            tasks.transition(
                db,
                candidate,
                TaskStatus.BLOCKED,
                principal=None,
                now=now,
                reason=f"budget exceeded ({details.get('limit')})",
                error={
                    "code": "budget_exceeded",
                    "message": exc.message,
                    "run_id": None,
                    "limit": details.get("limit"),
                    "retry_after": details.get("retry_after"),
                },
            )
            db.commit()
            report.budget_blocked_task_ids.append(candidate.id)
            return True

        run = runs.create_queued_run(db, candidate, agent, reservation, now=now)
        jobs.enqueue(
            db,
            kind="execute_run",
            payload={"run_id": run.id},
            company_id=company_id,
            run_after=now,
            dedupe_key=run.id,
        )
        db.commit()
        report.queued_run_ids.append(run.id)
        return True


__all__ = ["DispatchReport", "dispatch_tick"]
