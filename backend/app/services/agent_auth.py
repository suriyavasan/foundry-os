"""Agent run-token issuance and verification.

A token is valid only while its run is ``starting``/``running`` and unexpired, so finishing
(or cancelling) the run revokes it. It resolves to an agent ``Principal`` bound to the run's
company; writes made with it are attributed to that agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import Unauthenticated
from app.core.keys import SecretKeyProvider
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.core.time import utcnow
from app.core.tokens import RunTokenClaims, mint_run_token, verify_run_token
from app.models.agent import Agent
from app.models.enums import LIVE_RUN_STATUSES, AgentStatus
from app.models.run import TaskRun
from app.models.work import Task

TOKEN_GRACE_SECONDS = 120


@dataclass(frozen=True)
class AgentRunContext:
    ctx: CompanyContext
    run: TaskRun
    task: Task
    agent: Agent


def issue_token(
    keys: SecretKeyProvider, run: TaskRun, *, max_run_seconds: int, now: datetime | None = None
) -> str:
    now = now or utcnow()
    return mint_run_token(
        keys.get(),
        RunTokenClaims(
            run_id=run.id,
            agent_id=run.agent_id,
            company_id=run.company_id,
            exp=int(now.timestamp()) + max_run_seconds + TOKEN_GRACE_SECONDS,
        ),
    )


def authenticate(
    db: Session, keys: SecretKeyProvider, token: str, *, now: datetime | None = None
) -> AgentRunContext:
    """Resolve a bearer run token or raise ``Unauthenticated`` (always the same message)."""
    now = now or utcnow()
    claims = verify_run_token(keys.get(), token, now_epoch=int(now.timestamp()))
    invalid = Unauthenticated("Invalid, expired or revoked run token")
    if claims is None:
        raise invalid
    run = db.get(TaskRun, claims.run_id)
    if (
        run is None
        or run.company_id != claims.company_id
        or run.agent_id != claims.agent_id
        or run.status not in LIVE_RUN_STATUSES
    ):
        raise invalid
    agent = db.get(Agent, run.agent_id)
    task = db.get(Task, run.task_id)
    if (
        agent is None
        or task is None
        or agent.company_id != run.company_id
        or agent.status in (AgentStatus.RETIRED, AgentStatus.REJECTED)
    ):
        raise invalid
    principal = Principal(
        type=PrincipalType.AGENT, id=agent.id, label=agent.name, company_id=run.company_id
    )
    return AgentRunContext(
        ctx=CompanyContext(run.company_id, principal, Role.AGENT), run=run, task=task, agent=agent
    )
