"""Agent API for GitHub-backed projects (M14): the internal reviewer's verdict on a Foundry pull
request. Authenticated by the run token and gated like every ``/api/agent/v1`` route."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, RunCtx
from app.api.guard import agent_guard
from app.schemas.repository import PullReviewBody, PullReviewResult
from app.services import repo_reviews

router = APIRouter(prefix="/agent/v1/repo", tags=["agent-api"], dependencies=[Depends(agent_guard)])


@router.post("/pulls/{pull_id}/review", response_model=PullReviewResult)
def review_pull(pull_id: str, body: PullReviewBody, rc: RunCtx, db: DbSession):
    """Record the verdict (``accept`` | ``request_changes``) of the review task assigned to you.
    A verdict only counts for the head SHA that was reviewed."""
    return repo_reviews.record_verdict(db, rc, pull_id, body.decision, body.note)
