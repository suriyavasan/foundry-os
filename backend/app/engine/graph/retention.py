"""M18 Part B: a task's graph thread is deleted 30 days after the task is done or cancelled."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import literal, select

from app.engine.context import EngineContext
from app.engine.graph.checkpointer import purge_thread
from app.engine.graph.runner import thread_id_for
from app.models.enums import TaskStatus
from app.models.graph import GraphCheckpoint
from app.models.work import Task

RETENTION = timedelta(days=30)
BATCH = 100


def purge_finished_threads(ctx: EngineContext, now: datetime | None = None) -> int:
    """Delete the checkpoints of tasks that ended (done / cancelled) at least 30 days ago. Bounded
    per call; returns how many threads were purged."""
    cutoff = (now or ctx.clock()) - RETENTION
    with ctx.session_factory() as db:
        rows = db.execute(
            select(Task.company_id, Task.id)
            .where(
                Task.status.in_([TaskStatus.DONE, TaskStatus.CANCELLED]),
                Task.completed_at.is_not(None),
                Task.completed_at <= cutoff,
                select(GraphCheckpoint.thread_id)
                .where(
                    GraphCheckpoint.company_id == Task.company_id,
                    GraphCheckpoint.thread_id == literal("task:").concat(Task.id),
                )
                .exists(),
            )
            .order_by(Task.completed_at)
            .limit(BATCH)
        ).all()
    for company_id, task_id in rows:
        purge_thread(ctx.session_factory, company_id, thread_id_for(task_id))
    return len(rows)
