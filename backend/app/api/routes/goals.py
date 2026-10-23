from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.work import Goal, GoalCreate, GoalStatusLiteral, GoalUpdate
from app.services import goals
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}/goals", tags=["goals"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("", response_model=Page[Goal])
def list_goals(
    ctx: CompanyCtx,
    db: DbSession,
    status: GoalStatusLiteral | None = None,
    owner_agent_id: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = goals.list_goals(
        db, ctx, limit=limit, cursor=cursor, status=status, owner_agent_id=owner_agent_id
    )
    return {"items": items, "next_cursor": nxt}


@router.post("", response_model=Goal, status_code=201)
def create_goal(body: GoalCreate, ctx: CompanyCtx, db: DbSession):
    return goals.create_goal(db, ctx, body)


@router.get("/{goal_id}", response_model=Goal)
def get_goal(goal_id: str, ctx: CompanyCtx, db: DbSession):
    return goals.get_goal(db, ctx, goal_id)


@router.patch("/{goal_id}", response_model=Goal)
def update_goal(goal_id: str, body: GoalUpdate, ctx: CompanyCtx, db: DbSession):
    return goals.update_goal(db, ctx, goal_id, body)
