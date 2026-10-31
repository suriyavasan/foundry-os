"""M18 A5: retries with memory.

When a run fails (any failure class: transient or permanent) the task remembers how the attempt
ended -- failure class, code, a redacted error message (<= 1000 characters), the agent's last
progress report and the run id (its ``run/<id>`` branch may hold commits) -- in
``tasks.last_attempt``.
The next attempt's context carries it (``carry_over_text``), together with what the Founder
answered about the task (``questions.context_lines``). A success clears it.

The text is data for the next attempt, never an instruction from a person: it is built from the
platform's own records and wrapped by the prompt builders accordingly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models.run import RunEvent, TaskRun
from app.models.work import Task
from app.services import secrets_scan

MAX_MESSAGE = 1000
MAX_PROGRESS = 500
MAX_CARRY_OVER = 3000


def _clip(text: str | None, limit: int) -> str:
    text = (secrets_scan.scrub(" ".join((text or "").split())) or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def last_progress(db: Session, company_id: str, run_id: str | None) -> str | None:
    if not run_id:
        return None
    payload = db.scalar(
        select(RunEvent.payload)
        .join(TaskRun, TaskRun.id == RunEvent.run_id)
        .where(
            RunEvent.run_id == run_id,
            RunEvent.company_id == company_id,
            TaskRun.company_id == company_id,
            RunEvent.type == "progress",
        )
        .order_by(RunEvent.seq.desc())
        .limit(1)
    )
    message = (payload or {}).get("message") if isinstance(payload, dict) else None
    return _clip(message, MAX_PROGRESS) or None


def record_failure(
    db: Session,
    task: Task,
    *,
    run_id: str | None,
    code: str,
    message: str,
    failure_class: str,
    now: datetime | None = None,
) -> dict:
    """Remember how this attempt ended (no commit). Called by ``tasks.finish_failure``."""
    task.last_attempt = {
        "run_id": run_id,
        "attempt": task.attempt_count,
        "failure_class": failure_class,
        "code": _clip(code, 80),
        "message": _clip(message, MAX_MESSAGE),
        "last_progress": last_progress(db, task.company_id, run_id),
        "at": (now or utcnow()).isoformat(),
    }
    return task.last_attempt


def _branch_line(db: Session, task: Task, run_id: str | None) -> str | None:
    if not run_id:
        return None
    run = db.scalar(
        select(TaskRun).where(TaskRun.id == run_id, TaskRun.company_id == task.company_id)
    )
    ref = (run.workspace_ref if run is not None else None) or {}
    if ref.get("branch_kept"):
        return (
            f"Its unmerged work was kept on the branch run/{run_id}; the project files you see "
            "now do not include it."
        )
    return None


def previous_attempt_lines(db: Session, task: Task) -> list[str]:
    last = task.last_attempt
    if not isinstance(last, dict) or not last:
        return []
    lines = [
        f"The previous attempt (number {last.get('attempt', '?')}) ended with a "
        f"{last.get('failure_class', 'failure')} failure ({last.get('code', 'unknown')})."
    ]
    if last.get("message"):
        lines.append(f"Error: {last['message']}")
    if last.get("last_progress"):
        lines.append(f"Its last progress report: {last['last_progress']}")
    branch = _branch_line(db, task, last.get("run_id"))
    if branch:
        lines.append(branch)
    lines.append(
        "Do not simply repeat what failed: continue from where it stopped, or change the approach."
    )
    return lines


def carry_over_text(db: Session, task: Task) -> str | None:
    """Everything the next attempt of ``task`` should know from before: the previous attempt's
    outcome and the Founder's answers. None when there is nothing."""
    from app.services import questions

    lines = previous_attempt_lines(db, task)
    founder = questions.context_lines(db, task)
    if founder:
        lines += ["", "Questions you asked the founder:", *founder]
    text = "\n".join(lines).strip()
    if not text:
        return None
    return text if len(text) <= MAX_CARRY_OVER else text[: MAX_CARRY_OVER - 3] + "..."
