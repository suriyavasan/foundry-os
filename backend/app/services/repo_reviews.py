"""Internal review of Foundry pull requests (M14 step 0: contracts; WP-C implements).

Before a pull request is merged an agent reviews it (docs/specs/m14-github-backed-projects.md 3.5).
``ensure_pr_review_task`` creates the review task; the reviewer records ONE verdict with the
company tool ``company_review_pull_request`` -> agent route
``POST /api/agent/v1/repo/pulls/{pull_id}/review`` -> ``record_verdict``. The verdict only writes
the ``verdict*`` columns; the stage is moved by the sync loop alone.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.repository import ProjectRepository, RepoPullRequest
from app.models.work import Task
from app.schemas.repository import PullReviewResult
from app.services.agent_auth import AgentRunContext
from app.services.repo_errors import not_built


def reviewer_for(db: Session, binding: ProjectRepository) -> Agent:
    """An active QA-family agent among the project lead's reports, else the lead, else the
    Co-founder (never the agent that did most of the merged runs, when an alternative exists)."""
    raise not_built()


def ensure_pr_review_task(
    db: Session, binding: ProjectRepository, pr: RepoPullRequest, now: datetime
) -> Task:
    """The review task of ``pr`` at its current revision (kind ``review``, project, title
    ``Review pull request #<n>: <title>``, ``idempotency_key="repo-pr-review:<pr_id>:<revision>"``,
    ``input_refs=["github:pr:<pr_id>"]``, the diffstat in the description, the ``PR #<n> diff``
    artifact attached). Idempotent. Does not commit."""
    raise not_built()


def record_verdict(
    db: Session, rc: AgentRunContext, pull_id: str, decision: str, note: str
) -> PullReviewResult:
    """Record the assignee's verdict (``accept`` | ``request_changes``).

    Only the assignee of the open review task of that PR (else 403); ``pr.head_sha !=
    pr.review_sha`` -> 409 ``pull_request_changed``; writes ``verdict``, ``verdict_note``,
    ``verdict_sha``, ``verdict_agent_id`` (never ``stage``); completes the review task;
    ``request_changes`` also creates the fix task ``Address the review of PR #<n>``."""
    raise not_built()
