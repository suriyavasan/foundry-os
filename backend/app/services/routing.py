"""Routing (docs/specs/m17-ai-stack.md section 4): which AI connection a run uses, and why.

The company's AI stack (``ai_connections``, owned by ``services.ai_stack``) is the only routing
input.
The legacy settings (``work_mode``, ``work_runtime``, ``work_model_*``, ``ai_engine``,
``default_model_*``, ``model_routing``, ``allow_paid_fallback``) stay in the schema but are never
read
here. For each run, :func:`decide`:

1. **Owner pins win.** An agent whose runtime is itself a CLI, or whose ``runtime_config`` has
   ``pin_runtime`` / ``pin_model``, runs on its own runtime (and, on ``company_agent``, its own
   provider and model): no stack connection is involved. Only a human sets a pin.
2. **Class.** *heavy* for a management run (:func:`is_management_run`: the task is not ``work``, or
the
   agent's role is Founder / HR / Lead / Manager), else *everyday*.
3. **Candidates**: enabled connections that cover the class (``use_for``), pass
:func:`ai_stack.usable`
   (passing test, CLI installed, plan signed in, not paused, API provider enabled) and, for an API
   connection, whose provider is allowed by ``policy.allowed_model_providers``; and are below their
   ``max_concurrency`` (a Cursor plan runs one process at a time).
4. **Weighted fair share.** Among candidates with ``share > 0`` the one with the smallest
   ``(runs dispatched on it in the last 24 h + 1) / share`` wins; ties go to the lower position.
   Backups (``share == 0``) are used only when no such candidate exists, first by position.
5. **No candidate**: the task waits (``waiting_for_runtime``) with a reason that names why
   ("Claude plan is paused until ...; no other AI can take heavy work right now").
6. The decision (runtime, provider/model, ``ai_connection_id``, ``routing_reason``) is stored on
the run.
7. An API connection runs on ``company_agent`` with the connection's provider and model (the
agent's own
   provider is not needed); a CLI connection runs on ``cli:<id>`` and takes its sign-in from the
   connection (``ai_stack.run_auth``).

There is no ``free_first`` / ``quality_first`` / paid-fallback approval for stack-routed runs: the
owner
chose every connection and its share explicitly. Budgets, approvals and guardrails apply unchanged.

Only agents on ``company_agent`` or ``unassigned`` are routed through the stack; an agent wired to
any
other runtime (a test fixture, a future runtime) keeps it.  A company with no connections at all
(only
possible while the AI gate is off, i.e. in tests) leaves every agent on its own runtime and model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.ai_stack import AiConnection
from app.models.enums import ACTIVE_RUN_STATUSES, TaskKind
from app.models.policy import CompanyPolicy
from app.models.provider import ModelProvider
from app.models.run import TaskRun
from app.models.work import Task
from app.runtimes.cli.env import CliEnvironment
from app.services import agent_roles, ai_stack, model_providers

COMPANY_AGENT = "company_agent"
CLI_PREFIX = "cli:"
# Runtimes whose agents are routed through the AI stack; any other runtime is kept as it is.
STACK_ROUTED_RUNTIMES = frozenset({COMPANY_AGENT, "unassigned"})
# Roles that always count as management (heavy) work (spec M10 section 2).
MANAGEMENT_ROLES = frozenset(
    {agent_roles.FOUNDER, agent_roles.HR, agent_roles.LEAD, agent_roles.MANAGER}
)
FAIR_SHARE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Route:
    provider_id: str | None = None
    model: str | None = None
    reason: str | None = None
    free: bool = False  # no provider charge (local model, plan subscription): nothing to reserve
    ai_connection_id: str | None = None

    @property
    def routed(self) -> bool:
        return self.provider_id is not None


@dataclass(frozen=True)
class Decision:
    """Where one run goes. ``runtime`` is empty and ``wait_reason`` set when nothing can take it
    now."""

    runtime: str = ""
    route: Route = Route()
    runtime_reason: str | None = None  # why this runtime, for pinned / override runs
    stacked: bool = False  # chosen from the stack (health is per connection, not per runtime)
    subscription: bool = False  # a plan connection: usage is a subscription, nothing is reserved
    wait_reason: str | None = None

    @property
    def waits(self) -> bool:
        return self.wait_reason is not None


def is_free(provider: ModelProvider) -> bool:
    if provider.is_local:
        return True
    return provider.input_price_per_mtok_micros == 0 and provider.output_price_per_mtok_micros == 0


def is_management_run(db: Session, agent: Agent, task: Task) -> bool:
    """Planning, review, hiring and routine tasks, and every task of a management role."""
    return task.kind != TaskKind.WORK or agent_roles.role_of(db, agent) in MANAGEMENT_ROLES


def is_pinned(agent: Agent) -> bool:
    """An owner decision takes the agent out of the stack: a CLI runtime of its own, or a pin."""
    config = agent.runtime_config or {}
    return (
        agent.runtime.startswith(CLI_PREFIX)
        or config.get("pin_runtime") is True
        or config.get("pin_model") is True
    )


def choose(
    db: Session,
    agent: Agent,
    limits: CompanyPolicy | None = None,
    runtime: str | None = None,
    task: Task | None = None,
) -> Route:
    """The agent's own provider and model on the company agent runtime (a pinned or unrouted run).
    Empty for any other runtime, or when the agent has no resolvable model (the adapter then reports
    the configuration error as it always did)."""
    runtime = runtime or agent.runtime
    if runtime != COMPANY_AGENT:
        return Route()
    try:
        own = model_providers.resolve_for_agent(db, agent)
    except model_providers.ModelResolutionError:
        return Route()
    row = db.get(ModelProvider, own.provider_id)
    pinned = (agent.runtime_config or {}).get("pin_model") is True
    return Route(
        own.provider_id,
        own.model,
        "pinned by the owner: the agent's own model" if pinned else None,
        free=row is not None and is_free(row),
    )


def _label_of(klass: str) -> str:
    return "heavy" if klass == ai_stack.HEAVY else "everyday"


def _share_text(row: AiConnection) -> str:
    return ai_stack.SHARE_LABELS.get(row.share, str(row.share))


def _in_flight(db: Session, company_id: str) -> dict[str, int]:
    rows = db.execute(
        select(TaskRun.ai_connection_id, func.count(TaskRun.id))
        .where(
            TaskRun.company_id == company_id,
            TaskRun.ai_connection_id.is_not(None),
            TaskRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .group_by(TaskRun.ai_connection_id)
    )
    return {cid: int(n) for cid, n in rows}


def recent_runs(db: Session, company_id: str, now: datetime) -> dict[str, int]:
    """Runs dispatched on each connection in the last 24 hours (queued counts: it is a dispatch)."""
    rows = db.execute(
        select(TaskRun.ai_connection_id, func.count(TaskRun.id))
        .where(
            TaskRun.company_id == company_id,
            TaskRun.ai_connection_id.is_not(None),
            TaskRun.queued_at >= now - FAIR_SHARE_WINDOW,
        )
        .group_by(TaskRun.ai_connection_id)
    )
    return {cid: int(n) for cid, n in rows}


def _unavailable(
    db: Session,
    row: AiConnection,
    limits: CompanyPolicy,
    *,
    cli_env: CliEnvironment | None,
    now: datetime,
    in_flight: Mapping[str, int],
    runtime_ok: Callable[[str], bool] | None,
    providers: Mapping[str, ModelProvider],
) -> str | None:
    """Why ``row`` cannot take a run now (None: it can)."""
    ok, why = ai_stack.usable(row, db=db, cli_env=cli_env, now=now)
    if not ok:
        return why
    runtime = COMPANY_AGENT if row.engine == "api" else f"{CLI_PREFIX}{row.cli_id}"
    if runtime_ok is not None and not runtime_ok(runtime):
        return f"{row.label} cannot run here: the {runtime} runtime is not available"
    if row.engine == "api":
        provider = providers.get(row.model_provider_id or "")
        allowed = list(limits.allowed_model_providers or [])
        if (
            provider is not None
            and allowed
            and provider.id not in allowed
            and provider.name not in allowed
        ):
            return f"{row.label} uses a model provider that company policy does not allow"
    cap = ai_stack.max_concurrency(row)
    if cap is not None and in_flight.get(row.id, 0) >= cap:
        return f"{row.label} is busy (it runs {cap} at a time)"
    return None


def decide(
    db: Session,
    agent: Agent,
    task: Task,
    limits: CompanyPolicy,
    *,
    now: datetime,
    cli_env: CliEnvironment | None = None,
    runtime_ok: Callable[[str], bool] | None = None,
    in_flight: Mapping[str, int] | None = None,
    recent: Mapping[str, int] | None = None,
) -> Decision:
    """The decision for one run of ``task`` by ``agent`` (module docstring). Read-only.
    ``in_flight`` / ``recent`` (runs per connection id) are looked up when not given."""
    if agent.runtime.startswith(CLI_PREFIX):
        return Decision(agent.runtime, Route(), "agent runtime override")
    if is_pinned(agent):
        if (agent.runtime_config or {}).get("pin_runtime") is True:
            reason = "agent runtime pinned by the owner"
        else:
            reason = None
        return Decision(agent.runtime, choose(db, agent, limits, agent.runtime, task), reason)
    if agent.runtime not in STACK_ROUTED_RUNTIMES:
        return Decision(agent.runtime, Route())  # not wired to the company agent: keep it

    rows = list(
        db.scalars(
            select(AiConnection)
            .where(AiConnection.company_id == agent.company_id)
            .order_by(AiConnection.position, AiConnection.created_at, AiConnection.id)
        )
    )
    if not rows:  # no stack (the AI gate is off): the agent's own runtime and model
        return Decision(agent.runtime, choose(db, agent, limits, agent.runtime, task))

    klass = ai_stack.HEAVY if is_management_run(db, agent, task) else ai_stack.EVERYDAY
    kind = _label_of(klass)
    in_flight = _in_flight(db, agent.company_id) if in_flight is None else in_flight
    providers = {
        p.id: p
        for p in db.scalars(
            select(ModelProvider).where(ModelProvider.company_id == agent.company_id)
        )
    }
    covering = [r for r in rows if ai_stack.covers(r, klass)]
    candidates: list[AiConnection] = []
    reasons: list[str] = []
    for row in covering:
        why = _unavailable(
            db,
            row,
            limits,
            cli_env=cli_env,
            now=now,
            in_flight=in_flight,
            runtime_ok=runtime_ok,
            providers=providers,
        )
        if why is None:
            candidates.append(row)
        else:
            reasons.append(why)
    if not candidates:
        if not covering:
            wait = f"No AI in the stack covers {kind} work"
        else:
            wait = "; ".join(reasons) + (
                f"; no other AI can take {kind} work right now" if len(covering) == 1 else ""
            )
        return Decision(wait_reason=wait)

    recent = recent_runs(db, agent.company_id, now) if recent is None else recent
    funded = [r for r in candidates if r.share > 0]
    backup = not funded
    if funded:
        pick = min(
            funded,
            key=lambda r: (
                Fraction(recent.get(r.id, 0) + 1, r.share),
                r.position,
                r.created_at,
                r.id,
            ),
        )
    else:
        pick = candidates[0]  # rows are ordered by position
    rank = ai_stack.rank_label(rows.index(pick)).lower()
    reason = (
        f"AI stack: {pick.label} ({rank}, {_share_text(pick)}) "
        f"{'as the backup for' if backup else 'for'} {kind} work"
    )
    if pick.engine == "api":
        provider = providers[pick.model_provider_id or ""]
        route = Route(
            provider.id,
            pick.model or provider.default_model,
            reason,
            free=is_free(provider),
            ai_connection_id=pick.id,
        )
        return Decision(COMPANY_AGENT, route, stacked=True)
    plan = ai_stack.is_plan(pick)
    cli_provider = providers.get(pick.cli_provider_id or "")
    free = plan or (cli_provider is not None and cli_provider.is_local)
    route = Route(None, pick.model, reason, free=free, ai_connection_id=pick.id)
    return Decision(f"{CLI_PREFIX}{pick.cli_id}", route, stacked=True, subscription=plan)
