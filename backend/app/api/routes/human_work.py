"""M22: human work (things an AI employee needs a person for) and outsourcing (freelance jobs).

Text written by AI employees (titles, instructions, briefs) is plain text for clients."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, DbSession
from app.schemas.common import Page
from app.schemas.human_work import (
    HumanWorkDecline,
    HumanWorkItem,
    OutsourceDelivered,
    OutsourceHired,
    OutsourceJobOut,
    OutsourcePaid,
    OutsourcePosted,
    OutsourceRaiseBudget,
    OutsourceReason,
    OutsourceStatusLiteral,
)
from app.services import human_work, outsourcing
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT

router = APIRouter(prefix="/companies/{company_id}", tags=["human-work"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def _item(db, task) -> dict:
    return human_work.serialize(db, [task])[0]


@router.get("/human-work", response_model=Page[HumanWorkItem])
def list_human_work(
    ctx: CompanyCtx,
    db: DbSession,
    status: Literal["open", "done"] = "open",
    mine: bool = False,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = human_work.list_human_work(
        db, ctx, status=status, mine=mine, limit=limit, cursor=cursor
    )
    return {"items": human_work.serialize(db, rows), "next_cursor": nxt}


@router.get("/human-work/{task_id}", response_model=HumanWorkItem)
def get_human_work(task_id: str, ctx: CompanyCtx, db: DbSession):
    return _item(db, human_work.get_one(db, ctx, task_id))


@router.post("/human-work/{task_id}/claim", response_model=HumanWorkItem)
def claim_human_work(task_id: str, ctx: CompanyCtx, db: DbSession):
    """Take a task from the human pool (a second claim is a 409). Complete it afterwards with
    ``POST /tasks/{id}/complete`` (comment and artifacts)."""
    return _item(db, human_work.claim(db, ctx, task_id))


@router.post("/human-work/{task_id}/decline", response_model=HumanWorkItem)
def decline_human_work(task_id: str, body: HumanWorkDecline, ctx: CompanyCtx, db: DbSession):
    """Owners/admins: a person will not do this. The AI employee that asked is woken with the
    reason and its blocked task returns to the queue."""
    return _item(db, human_work.decline(db, ctx, task_id, body.reason))


# ---------------------------------------------------------------- outsourcing


def _job(db, job) -> dict:
    return outsourcing.serialize(db, [job])[0]


@router.get("/outsource-jobs", response_model=Page[OutsourceJobOut])
def list_outsource_jobs(
    ctx: CompanyCtx,
    db: DbSession,
    status: OutsourceStatusLiteral | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    rows, nxt = outsourcing.list_jobs(db, ctx, status=status, limit=limit, cursor=cursor)
    return {"items": outsourcing.serialize(db, rows), "next_cursor": nxt}


@router.get("/outsource-jobs/{job_id}", response_model=OutsourceJobOut)
def get_outsource_job(job_id: str, ctx: CompanyCtx, db: DbSession):
    if not ctx.principal.is_human:
        from app.core.errors import Forbidden

        raise Forbidden("Outsourcing is for people")
    return _job(db, outsourcing.get_job(db, ctx.company_id, job_id))


@router.post("/outsource-jobs/{job_id}/posted", response_model=OutsourceJobOut)
def job_posted(job_id: str, body: OutsourcePosted, ctx: CompanyCtx, db: DbSession):
    """Record that you posted the job on the marketplace's own site (the link must be on it)."""
    return _job(db, outsourcing.posted(db, ctx, job_id, body.listing_url))


@router.post("/outsource-jobs/{job_id}/hired", response_model=OutsourceJobOut)
def job_hired(job_id: str, body: OutsourceHired, ctx: CompanyCtx, db: DbSession):
    """Record who you hired and the agreed price (never above the approved budget)."""
    return _job(
        db,
        outsourcing.hired(
            db,
            ctx,
            job_id,
            freelancer_name=body.freelancer_name,
            freelancer_profile_url=body.freelancer_profile_url,
            agreed_price_usd=body.agreed_price_usd,
        ),
    )


@router.post("/outsource-jobs/{job_id}/delivered", response_model=OutsourceJobOut)
def job_delivered(job_id: str, body: OutsourceDelivered, ctx: CompanyCtx, db: DbSession):
    return _job(
        db,
        outsourcing.delivered(db, ctx, job_id, artifact_ids=body.artifact_ids, notes=body.notes),
    )


@router.post("/outsource-jobs/{job_id}/paid", response_model=OutsourceJobOut)
def job_paid(job_id: str, body: OutsourcePaid, ctx: CompanyCtx, db: DbSession):
    """Record a payment YOU made on the marketplace as a verified expense (Foundry moves none)."""
    return _job(db, outsourcing.paid(db, ctx, job_id, body.amount_usd))


@router.post("/outsource-jobs/{job_id}/close", response_model=OutsourceJobOut)
def job_close(job_id: str, ctx: CompanyCtx, db: DbSession):
    return _job(db, outsourcing.close(db, ctx, job_id))


@router.post("/outsource-jobs/{job_id}/cancel", response_model=OutsourceJobOut)
def job_cancel(job_id: str, body: OutsourceReason, ctx: CompanyCtx, db: DbSession):
    return _job(db, outsourcing.cancel(db, ctx, job_id, body.reason))


@router.post("/outsource-jobs/{job_id}/raise-budget", response_model=OutsourceJobOut)
def job_raise_budget(job_id: str, body: OutsourceRaiseBudget, ctx: CompanyCtx, db: DbSession):
    """Ask for a higher budget: a NEW approval decides it (the job keeps its budget until then)."""
    job, _ = outsourcing.raise_budget(db, ctx, job_id, body.new_budget_usd, body.reason)
    return _job(db, job)
