"""Dispatcher: turns ``ready`` tasks into queued runs, within policy and budget.

For each active company: recompute readiness, then repeatedly (one transaction per dispatch)
take the next ready task by (priority desc, created_at), reserve budget, create a queued
``TaskRun`` and enqueue the ``execute_run`` job. The transaction starts with the budget lock
(``ledger.acquire_budget_lock``), so the concurrency cap and the budget limits are checked
against a consistent view even with several dispatchers running.

M12 rules, all enforced here (code, not prompts):

* a task waiting for its retry backoff (``next_attempt_at`` in the future) is not dispatched;
* **reserved management slots**: work runs may use at most ``max_concurrent_runs`` minus the
  reserved slots (``management_reserved_slots``, clamped to ``max_concurrent_runs - 1``), so a
  planning, review or lead run is never starved by long work runs;
* one run per agent, except CLI work tasks of a project: up to ``max_parallel_runs_per_agent``;
* **routing** (M17): every run of a company-agent / unassigned employee goes through the
  company's AI stack (``routing.decide``): a connection is chosen per run and recorded on it
  (``ai_connection_id``); a task for which no connection is usable waits with a reason
  (``waiting_for_runtime``). Pinned runs keep the runtime-level health check;
* a non-free CLI run reserves ``cli_run_reservation_micros`` (the CLI's own spend cap derives from
  its reservation), not the small API default;
* an agent / task / project budget block wakes the project lead, else the agent's manager, else
  the Co-founder (trigger ``budget``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import case, or_, select

from app.core.errors import BudgetExceeded
from app.engine.context import EngineContext
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import (
    ACTIVE_RUN_STATUSES,
    AgentStatus,
    CompanyStatus,
    TaskKind,
    TaskStatus,
)
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.services import (
    agent_roles,
    ai_setup,
    cli_runtimes,
    emergency,
    hiring,
    jobs,
    ledger,
    policy,
    repo_sync,
    routing,
    runs,
    tasks,
    wakes,
)

PRIORITY_RANK = case(
    (Task.priority == "urgent", 3),
    (Task.priority == "high", 2),
    (Task.priority == "normal", 1),
    else_=0,
)
CANDIDATE_WINDOW = 50
# Roles whose runs are "management": they keep the reserved slots (same set routing uses).
MANAGEMENT_ROLES = routing.MANAGEMENT_ROLES
# Budget limits an agent's lead can act on; company-wide limits block everyone alike (no wake).
COMPANY_WIDE_LIMITS = frozenset({"company_daily", "company_monthly"})


@dataclass
class DispatchReport:
    queued_run_ids: list[str] = field(default_factory=list)
    budget_blocked_task_ids: list[str] = field(default_factory=list)
    policy_blocked_task_ids: list[str] = field(default_factory=list)
    waiting_for_runtime: int = 0
    # M17: task id -> why no AI connection / runtime can take it now
    waiting_reasons: dict[str, str] = field(default_factory=dict)
    # M14: task id -> why it waits (repository_not_ready, codebase_onboarding)
    held: dict[str, str] = field(default_factory=dict)


def is_management_run(db, task: Task, agent: Agent) -> bool:
    """A planning / review / hiring / routine task, or any task of a management role."""
    return task.kind != TaskKind.WORK or agent_roles.role_of(db, agent) in MANAGEMENT_ROLES


def effective_reserved_slots(limits) -> int:
    """Slots only management runs may use: never all of them, never negative."""
    return max(0, min(limits.management_reserved_slots, limits.max_concurrent_runs - 1))


def _agent_run_cap(limits, runtime_id: str, task: Task) -> int:
    """Parallel runs of one agent: only CLI work tasks of a project may overlap."""
    if runtime_id.startswith(routing.CLI_PREFIX) and task.kind == TaskKind.WORK and task.project_id:
        return max(1, limits.max_parallel_runs_per_agent)
    return 1


def _wake_for_budget_block(
    db, company_id: str, agent: Agent, task: Task, details: dict, now: datetime
) -> None:
    """A task is stuck on an agent / task / project budget: tell someone who can reassign or
    escalate it - the project lead, else the agent's manager, else the Co-founder. They cannot
    raise a budget (only a human can), and a planning task never wakes anyone (no wake loops)."""
    limit = str(details.get("limit") or "budget")
    if limit in COMPANY_WIDE_LIMITS or task.kind == TaskKind.PLANNING:
        return
    candidates: list[str | None] = []
    if task.project_id:
        candidates.append(
            db.scalar(
                select(Project.lead_agent_id).where(
                    Project.id == task.project_id, Project.company_id == company_id
                )
            )
        )
    candidates.append(agent.manager_agent_id)
    text = (
        f"Task '{task.title}' [id {task.id}] cannot start: the {limit} budget would be exceeded. "
        "You cannot raise budgets: reassign the task, shrink or split it, or escalate it for a "
        "human decision."
    )
    for target_id in candidates:
        if not target_id or target_id == agent.id:
            continue
        target = db.scalar(
            select(Agent).where(Agent.id == target_id, Agent.company_id == company_id)
        )
        if target is None or target.status != AgentStatus.ACTIVE:
            continue
        wakes.wake_agent(db, company_id, target.id, "budget", text, ref_type="task", ref_id=task.id)
        return
    wakes.wake_founder(
        db, company_id, "budget", text, ref_type="task", ref_id=task.id, actor_agent_id=agent.id
    )


def dispatch_tick(ctx: EngineContext, now: datetime | None = None) -> DispatchReport:
    now = now or ctx.clock()
    report = DispatchReport()
    with ctx.session_factory() as db:
        if emergency.is_active(db):  # nothing is dispatched until the owner resumes
            return report
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
        # Skill-gap detection first: unassigned tasks needing skills get an employee (or a
        # hiring request), then readiness sees the new assignees.
        hiring.resolve_skill_gaps(db, company_id, now=now)
        hiring.resolve_project_leads(db, company_id, now=now)
        wakes.nudge_goals_without_projects(db, company_id, now=now)
        tasks.recompute_readiness(db, company_id, now=now)
        ready = ai_setup.is_ready(db, company_id)
        db.commit()
    if not ready:  # no working AI model: tasks wait (blocked_reason=ai_setup_required)
        return

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
        if company is None or company.status != CompanyStatus.ACTIVE or emergency.is_active(db):
            db.rollback()
            return False
        if not ai_setup.is_ready(db, company_id):
            db.rollback()
            return False
        limits = policy.load_policy(db, company_id)
        active = list(
            db.execute(
                select(TaskRun.agent_id, Task.kind, TaskRun.ai_connection_id)
                .join(Task, Task.id == TaskRun.task_id)
                .where(TaskRun.company_id == company_id, TaskRun.status.in_(ACTIVE_RUN_STATUSES))
            ).all()
        )
        if len(active) >= limits.max_concurrent_runs:
            db.rollback()
            return False
        agent_runs = Counter(agent_id for agent_id, _, _ in active)
        connection_runs = Counter(conn for _, _, conn in active if conn)
        cli_env = cli_runtimes.bound_environment()
        recent: dict[str, int] | None = None  # runs per connection in 24 h: loaded on first use
        # Work runs may not take the slots reserved for management runs.
        roles: dict[str, bool] = {}
        work_in_flight = 0
        for agent_id, kind, _ in active:
            if kind == TaskKind.WORK:
                if agent_id not in roles:
                    holder = db.get(Agent, agent_id)
                    roles[agent_id] = holder is not None and (
                        agent_roles.role_of(db, holder) in MANAGEMENT_ROLES
                    )
                work_in_flight += 0 if roles[agent_id] else 1
        work_slots = limits.max_concurrent_runs - effective_reserved_slots(limits)

        candidate = None
        agent = None
        stmt = (
            select(Task)
            .where(
                Task.company_id == company_id,
                Task.status == TaskStatus.READY,
                # M7: a person's task is never dispatched to a runtime (they are notified instead)
                Task.assignee_member_id.is_(None),
                # waiting for a retry backoff (a 429, a failed attempt): not before it is due
                or_(Task.next_attempt_at.is_(None), Task.next_attempt_at <= now),
                Task.id.notin_(skipped) if skipped else True,
            )
            .order_by(PRIORITY_RANK.desc(), Task.created_at, Task.id)
            .limit(CANDIDATE_WINDOW)
        )
        for task in db.scalars(stmt):
            held = repo_sync.dispatch_hold(db, task)
            if held:  # M14: the repository is not cloned yet / the codebase is being learned
                skipped.add(task.id)
                report.held[task.id] = held
                continue
            assignee = db.get(Agent, task.assignee_agent_id) if task.assignee_agent_id else None
            if assignee is None or assignee.status != AgentStatus.ACTIVE:
                skipped.add(task.id)
                continue
            if recent is None:
                recent = routing.recent_runs(db, company_id, now)
            decision = routing.decide(
                db,
                assignee,
                task,
                limits,
                now=now,
                cli_env=cli_env,
                runtime_ok=_runtime_registered(ctx),
                in_flight=connection_runs,
                recent=recent,
            )
            if decision.waits:
                skipped.add(task.id)
                report.waiting_for_runtime += 1
                report.waiting_reasons[task.id] = decision.wait_reason or ""
                continue
            runtime_id = decision.runtime
            in_flight = agent_runs.get(assignee.id, 0)
            if in_flight and in_flight >= _agent_run_cap(limits, runtime_id, task):
                skipped.add(task.id)  # one run per agent (CLI project work: several)
                continue
            if not is_management_run(db, task, assignee) and work_in_flight >= work_slots:
                skipped.add(task.id)  # the remaining slots are reserved for management
                continue
            allowed = limits.allowed_model_providers
            if (
                allowed
                and not decision.stacked  # a stack connection's provider was checked by routing
                and (assignee.model_provider or "") not in allowed
            ):
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
            if not decision.stacked and not ctx.health.usable(runtime_id):
                skipped.add(task.id)  # a pinned / unrouted runtime that cannot execute yet
                report.waiting_for_runtime += 1
                report.waiting_reasons[task.id] = f"The {runtime_id} runtime is not available"
                continue
            candidate, agent = task, assignee
            break
        if candidate is None or agent is None:
            db.rollback()
            return False

        route = decision.route
        pinned_cli = not decision.stacked and runtime_id.startswith(routing.CLI_PREFIX)
        if pinned_cli and cli_runtimes.runs_free(db, runtime_id):  # local model: nothing to hold
            route = routing.Route(reason=route.reason, free=True)

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
                if runtime_id.startswith(routing.CLI_PREFIX):
                    # the CLI's spend cap (--max-budget-usd) derives from its reservation
                    amount = limits.cli_run_reservation_micros
                if route.free:
                    amount = 0  # a free model has no provider charge: nothing to hold
            if decision.subscription:
                amount = 0  # a plan connection is a subscription: nothing to hold
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
            _wake_for_budget_block(db, company_id, agent, candidate, details, now)
            db.commit()
            report.budget_blocked_task_ids.append(candidate.id)
            return True

        run = runs.create_queued_run(
            db,
            candidate,
            agent,
            reservation,
            now=now,
            route=route,
            runtime=runtime_id,
            runtime_reason=decision.runtime_reason,
            ai_connection_id=route.ai_connection_id,
        )
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


def _runtime_registered(ctx: EngineContext):
    """Stack-routed runs are checked per connection (``ai_stack.usable``); the runtime only has to
    be registered and able to execute."""

    def ok(runtime_id: str) -> bool:
        adapter = ctx.registry.get(runtime_id)
        return adapter is not None and adapter.supports_execution

    return ok


__all__ = ["DispatchReport", "dispatch_tick"]
