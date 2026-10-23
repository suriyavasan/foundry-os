from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.agent import (
    Agent,
    AgentCreate,
    AgentLive,
    AgentStatusLiteral,
    AgentUpdate,
    LifecycleAction,
)
from app.schemas.common import Page
from app.services import agents, live
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/agents", tags=["agents"])

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _one(db, ctx, agent) -> Agent:
    return live.serialize_agents(db, ctx.company_id, [agent])[0]


@router.get("", response_model=Page[Agent])
def list_agents(
    ctx: CompanyCtx,
    db: DbSession,
    status: AgentStatusLiteral | None = None,
    department_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, next_cursor = agents.list_agents(
        db, ctx, limit=limit, cursor=cursor, status=status, department_id=department_id
    )
    return {"items": live.serialize_agents(db, ctx.company_id, items), "next_cursor": next_cursor}


@router.post("", response_model=Agent, status_code=201)
def create_agent(body: AgentCreate, ctx: CompanyCtx, db: DbSession):
    return _one(db, ctx, agents.create_agent(db, ctx, body))


@router.get("/{agent_id}", response_model=Agent)
def get_agent(agent_id: str, ctx: CompanyCtx, db: DbSession):
    return _one(db, ctx, agents.get_agent(db, ctx, agent_id))


@router.get("/{agent_id}/activity-now", response_model=AgentLive)
def activity_now(agent_id: str, ctx: CompanyCtx, db: DbSession):
    return live.get_activity_now(db, ctx, agent_id)


@router.patch("/{agent_id}", response_model=Agent)
def update_agent(agent_id: str, body: AgentUpdate, ctx: CompanyCtx, db: DbSession):
    return _one(db, ctx, agents.update_agent(db, ctx, agent_id, body))


@router.post("/{agent_id}/{action}", response_model=Agent)
def lifecycle(
    agent_id: str,
    action: Literal["activate", "reject", "pause", "resume", "retire"],
    ctx: CompanyCtx,
    db: DbSession,
    body: LifecycleAction | None = None,
):
    return _one(db, ctx, agents.transition_agent(db, ctx, agent_id, action, body))
