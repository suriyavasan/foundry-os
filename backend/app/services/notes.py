"""The Founder's notes: a free-form thought stream the AI Co-founder triages and answers.

Owner side (humans): write notes, reply in the thread, pin / archive. Agent side (the AI
Co-founder only): list the unprocessed notes, triage them (category, summary and **validated**
actions - every ``ref_id`` must be a real record of this company) and reply.

* A new note, or a human reply that re-opens a triaged one, wakes the AI Co-founder through the
  debounced wake queue (60 s), so a burst of notes becomes one planning run.
* ``requirement`` and ``context`` notes become **verified** company memory automatically.
* Note text is the Founder's intent, but it is shown to the agent with secret-looking spans
  redacted, and never copied into memory when it contains one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.audit import AuditEvent
from app.models.autonomy import FounderNote, MemoryEntry, NoteReply
from app.models.enums import AgentKind, AgentStatus, MemoryKind, NoteCategory, NoteStatus
from app.models.governance import Approval, Decision
from app.models.schedule import Schedule
from app.models.work import Goal, Project, Task
from app.schemas.autonomy import (
    AgentNote,
    AgentNoteReply,
    NoteCreate,
    NoteDetail,
    NoteTriage,
    NoteTriageIn,
    NoteUpdate,
)
from app.schemas.autonomy import (
    FounderNote as FounderNoteOut,
)
from app.schemas.autonomy import (
    NoteReply as NoteReplyOut,
)
from app.services import audit, events, memory, notifications, secrets_scan, wakes
from app.services.agent_auth import AgentRunContext
from app.services.pagination import paginate

MEMORY_CATEGORIES = {
    NoteCategory.REQUIREMENT.value: MemoryKind.REQUIREMENT.value,
    NoteCategory.CONTEXT.value: MemoryKind.FACT.value,
}
# Triage action type -> (model, label attribute). ``hire`` is checked separately.
_ACTION_MODELS: dict[str, tuple[Any, str]] = {
    "goal": (Goal, "title"),
    "project": (Project, "name"),
    "task": (Task, "title"),
    "schedule": (Schedule, "name"),
    "agent": (Agent, "name"),
    "decision": (Decision, "title"),
    "memory": (MemoryEntry, "title"),
    "approval": (Approval, "title"),
}
AGENT_REPLIES_SHOWN = 6


# ---------------------------------------------------------------- helpers


def _get(db: Session, company_id: str, note_id: str) -> FounderNote:
    note = db.scalar(
        select(FounderNote).where(FounderNote.id == note_id, FounderNote.company_id == company_id)
    )
    if note is None:
        raise NotFound("Note not found")
    return note


def _replies(db: Session, note: FounderNote) -> list[NoteReply]:
    return list(
        db.scalars(
            select(NoteReply)
            .where(NoteReply.note_id == note.id, NoteReply.company_id == note.company_id)
            .order_by(NoteReply.created_at, NoteReply.id)
        )
    )


def serialize(db: Session, notes: list[FounderNote], *, detail: bool = False):
    """FounderNote rows (list) or NoteDetail (one, with the whole thread)."""
    if not notes:
        return []
    ids = [n.id for n in notes]
    counts = dict(
        db.execute(
            select(NoteReply.note_id, func.count(NoteReply.id))
            .where(NoteReply.note_id.in_(ids))
            .group_by(NoteReply.note_id)
        ).all()
    )
    last: dict[str, NoteReply] = {}
    for reply in db.scalars(
        select(NoteReply)
        .where(NoteReply.note_id.in_(ids))
        .order_by(NoteReply.created_at, NoteReply.id)
    ):
        last[reply.note_id] = reply
    out = []
    for note in notes:
        base = FounderNoteOut.model_validate(note)
        base.reply_count = int(counts.get(note.id, 0))
        base.last_reply = NoteReplyOut.model_validate(last[note.id]) if note.id in last else None
        if detail:
            replies = [NoteReplyOut.model_validate(r) for r in _replies(db, note)]
            full = NoteDetail(**base.model_dump(), replies=replies)
            out.append(full)
        else:
            out.append(base)
    return out


def _check_attachments(db: Session, company_id: str, ids: list[str]) -> None:
    if not ids:
        return
    found = set(
        db.scalars(
            select(Artifact.id).where(Artifact.company_id == company_id, Artifact.id.in_(ids))
        )
    )
    missing = [i for i in ids if i not in found]
    if missing:
        raise NotFound("Attachment not found")


def _emit(db: Session, note: FounderNote, type_: str, **extra: Any) -> None:
    events.emit(
        db,
        note.company_id,
        type_,
        {"note_id": note.id, "status": note.status, **extra},
    )


def _preview(text: str, n: int = 90) -> str:
    """A short, secret-scrubbed excerpt for wake reasons."""
    flat = " ".join((secrets_scan.scrub(text) or "").split())
    return flat if len(flat) <= n else flat[: n - 3] + "..."


def _no_agents(ctx: CompanyContext) -> None:
    if ctx.principal.is_agent:
        raise Forbidden("Agents use the agent API for the Founder's notes")


# ---------------------------------------------------------------- owner API


def create_note(db: Session, ctx: CompanyContext, data: NoteCreate) -> FounderNote:
    _no_agents(ctx)
    require_human_admin(ctx, "write notes")
    _check_attachments(db, ctx.company_id, data.attachments)
    note = FounderNote(
        company_id=ctx.company_id,
        author_user_id=ctx.principal.id,
        body=data.body,
        attachments=list(data.attachments),
        status=NoteStatus.NEW,
        pinned=False,
    )
    db.add(note)
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="note.created",
        target_type="note",
        target_id=note.id,
        summary="The Founder wrote a note",
        data={"chars": len(note.body)},  # never the text: it can be long and personal
    )
    _emit(db, note, "note.created")
    wakes.wake_founder(
        db,
        ctx.company_id,
        "note",
        f'New Founder\'s note: "{_preview(note.body)}" - call company_list_notes and triage it.',
        ref_type="note",
        ref_id=note.id,
        delay_seconds=wakes.NOTE_DEBOUNCE_SECONDS,
    )
    db.commit()
    return note


def list_notes(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    category: str | None = None,
    pinned: bool | None = None,
):
    _no_agents(ctx)
    stmt = select(FounderNote).where(FounderNote.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(FounderNote.status == status)
    if pinned is not None:
        stmt = stmt.where(FounderNote.pinned == pinned)
    rows, nxt = paginate(db, stmt, FounderNote, limit=limit, cursor=cursor, descending=True)
    if category:  # category lives inside the triage json: filter after the page query
        rows = [n for n in rows if (n.triage or {}).get("category") == category]
    return rows, nxt


def get_note(db: Session, ctx: CompanyContext, note_id: str) -> FounderNote:
    _no_agents(ctx)
    return _get(db, ctx.company_id, note_id)


def add_human_reply(db: Session, ctx: CompanyContext, note_id: str, body: str) -> NoteReply:
    _no_agents(ctx)
    require_human_admin(ctx, "reply to notes")
    note = _get(db, ctx.company_id, note_id)
    reply = NoteReply(
        company_id=ctx.company_id,
        note_id=note.id,
        author_type="human",
        author_id=ctx.principal.id,
        body=body,
    )
    db.add(reply)
    now = utcnow()
    reopened = note.status == NoteStatus.TRIAGED
    if reopened:  # a reply re-opens triage: the AI Co-founder reads the thread again
        note.status = NoteStatus.NEW
    note.updated_at = now
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="note.replied",
        target_type="note",
        target_id=note.id,
        summary="The Founder replied on a note",
        data={"reply_id": reply.id, "reopened": reopened},
    )
    _emit(db, note, "note.replied", reply_id=reply.id, author_type="human")
    if note.status == NoteStatus.NEW:
        wakes.wake_founder(
            db,
            ctx.company_id,
            "note",
            f"The Founder replied on a note ({_preview(note.body, 40)}): call company_list_notes "
            "and answer it.",
            ref_type="note",
            ref_id=note.id,
            delay_seconds=wakes.NOTE_DEBOUNCE_SECONDS,
        )
    db.commit()
    return reply


def update_note(db: Session, ctx: CompanyContext, note_id: str, data: NoteUpdate) -> FounderNote:
    _no_agents(ctx)
    require_human_admin(ctx, "edit notes")
    note = _get(db, ctx.company_id, note_id)
    changed: list[str] = []
    fields = data.model_fields_set
    if "pinned" in fields and data.pinned is not None and note.pinned != data.pinned:
        note.pinned = data.pinned
        changed.append("pinned")
    if "status" in fields and data.status is not None and note.status != data.status:
        if data.status == NoteStatus.TRIAGED and note.triage is None:
            raise Conflict("A note without a triage cannot be marked triaged")
        note.status = data.status
        changed.append("status")
    if changed:
        note.updated_at = utcnow()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="note.updated",
            target_type="note",
            target_id=note.id,
            summary=f"Updated a note: {', '.join(changed)}",
            data={"changed": changed, "status": note.status},
        )
        _emit(db, note, "note.updated", changed=changed)
        if note.status == NoteStatus.NEW and "status" in changed:  # explicit re-open
            wakes.wake_founder(
                db,
                ctx.company_id,
                "note",
                "The Founder re-opened a note: call company_list_notes and triage it again.",
                ref_type="note",
                ref_id=note.id,
                delay_seconds=wakes.NOTE_DEBOUNCE_SECONDS,
            )
        db.commit()
    return note


def unprocessed_count(db: Session, company_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(FounderNote.id)).where(
                FounderNote.company_id == company_id, FounderNote.status == NoteStatus.NEW
            )
        )
        or 0
    )


# ---------------------------------------------------------------- agent API (AI Co-founder)


def _require_cofounder(rc: AgentRunContext) -> Agent:
    agent = rc.agent
    if agent.kind != AgentKind.FOUNDER or agent.status != AgentStatus.ACTIVE:
        raise Forbidden("Only the AI Co-founder handles the Founder's notes")
    return agent


def agent_view(db: Session, note: FounderNote) -> AgentNote:
    replies = _replies(db, note)[-AGENT_REPLIES_SHOWN:]
    return AgentNote(
        id=note.id,
        status=note.status,  # type: ignore[arg-type]
        body=secrets_scan.scrub(note.body) or "",
        created_at=note.created_at,
        pinned=note.pinned,
        previous_triage=NoteTriage.model_validate(note.triage) if note.triage else None,
        replies=[
            AgentNoteReply(
                author_type=r.author_type,  # type: ignore[arg-type]
                body=secrets_scan.scrub(r.body) or "",
                created_at=r.created_at,
            )
            for r in replies
        ],
    )


def agent_list(
    db: Session, rc: AgentRunContext, *, status: str | None = "new", limit: int = 20
) -> list[AgentNote]:
    _require_cofounder(rc)
    stmt = select(FounderNote).where(FounderNote.company_id == rc.ctx.company_id)
    if status:
        stmt = stmt.where(FounderNote.status == status)
    notes = list(
        db.scalars(
            stmt.order_by(FounderNote.created_at, FounderNote.id).limit(max(1, min(limit, 50)))
        )
    )
    return [agent_view(db, n) for n in notes]


def _resolve_actions(
    db: Session, company_id: str, actions: list[Any]
) -> list[dict[str, str | None]]:
    """Every action must point at a real record of THIS company; the label is read from it."""
    resolved: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    problems: list[dict[str, Any]] = []
    for i, action in enumerate(actions):
        key = (action.type, action.ref_id)
        if key in seen:
            continue
        seen.add(key)
        label: str | None = None
        found = False
        if action.type == "hire":
            task = db.scalar(
                select(Task).where(Task.id == action.ref_id, Task.company_id == company_id)
            )
            if task is not None:
                found, label = True, task.title
            else:
                agent = db.scalar(
                    select(Agent).where(Agent.id == action.ref_id, Agent.company_id == company_id)
                )
                if agent is not None:
                    found, label = True, agent.name
        else:
            model, attr = _ACTION_MODELS[action.type]
            row = db.scalar(
                select(model).where(model.id == action.ref_id, model.company_id == company_id)
            )
            if row is not None:
                found = True
                label = getattr(row, attr, None) or getattr(row, "body", None)
        if not found:
            problems.append(
                {
                    "loc": ["body", "actions", i, "ref_id"],
                    "msg": f"no {action.type} with this id exists in this company",
                }
            )
            continue
        resolved.append(
            {"type": action.type, "ref_id": action.ref_id, "label": (label or "")[:200] or None}
        )
    if problems:
        raise ValidationFailed(
            "Triage actions must reference records that exist in this company", details=problems
        )
    return resolved


ACT_FIRST_CATEGORIES = ("directive", "goal")
ACTION_AUDITS = (
    "goal.created",
    "project.created",
    "task.created",
    "schedule.created",
    "decision.recorded",
    "hire.requested",
)


def _require_acted(db: Session, rc: AgentRunContext, category: str) -> None:
    """A directive or a goal is not handled by describing it: enforced in code, because small models
    like to triage first and then forget to act. Something must have been created in this run."""
    if category not in ACT_FIRST_CATEGORIES:
        return
    since = rc.run.created_at  # row creation time (wall clock), not the engine clock
    acted = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.company_id == rc.ctx.company_id,
            AuditEvent.actor_id == rc.agent.id,
            AuditEvent.created_at >= since,
            AuditEvent.action.in_(ACTION_AUDITS),
        )
    )
    if not acted:
        raise Conflict(
            f"Act before you triage a {category}: create the goal (company_manage_goal) and the "
            "project (company_manage_project), or request a hire, then triage the note with the "
            "records you created. If nothing is needed, use category idea, question or noise.",
            details={"code": "act_first"},
        )


def agent_triage(db: Session, rc: AgentRunContext, note_id: str, data: NoteTriageIn) -> FounderNote:
    cofounder = _require_cofounder(rc)
    company_id = rc.ctx.company_id
    note = _get(db, company_id, note_id)
    if note.status == NoteStatus.ARCHIVED:
        raise Conflict("This note is archived; the Founder must re-open it first")
    actions = _resolve_actions(db, company_id, data.actions)
    _require_acted(db, rc, data.category)
    now = utcnow()
    note.triage = {
        "category": data.category,
        "summary": data.summary,
        "actions": actions,
        "confidence": data.confidence,
    }
    note.status = NoteStatus.TRIAGED
    note.triaged_by_run_id = rc.run.id
    note.triaged_at = now
    note.updated_at = now
    kind = MEMORY_CATEGORIES.get(data.category)
    memory_entry = None
    skipped_memory = False
    if kind is not None:
        memory_entry = memory.add_from_note(
            db, note, kind=kind, title=data.summary, author=rc.ctx.principal
        )
        skipped_memory = memory_entry is None
    audit.record(
        db,
        company_id=company_id,
        principal=rc.ctx.principal,
        action="note.triaged",
        target_type="note",
        target_id=note.id,
        summary=f"{cofounder.name} triaged a note as {data.category}",
        data={
            "category": data.category,
            "actions": [{"type": a["type"], "ref_id": a["ref_id"]} for a in actions],
            "memory_id": memory_entry.id if memory_entry else None,
            "memory_skipped_secret": skipped_memory,
        },
    )
    _emit(
        db,
        note,
        "note.triaged",
        category=data.category,
        triage={"category": data.category},
        run_id=rc.run.id,
    )
    if data.reply:
        _add_agent_reply(db, rc, note, data.reply)
    db.commit()
    return note


def _add_agent_reply(db: Session, rc: AgentRunContext, note: FounderNote, body: str) -> NoteReply:
    last = db.scalar(
        select(NoteReply)
        .where(NoteReply.note_id == note.id)
        .order_by(NoteReply.created_at.desc(), NoteReply.id.desc())
        .limit(1)
    )
    if last is not None and last.author_type == "agent" and last.body.strip() == body.strip():
        return last  # small models repeat themselves: one reply per thought
    reply = NoteReply(
        company_id=note.company_id,
        note_id=note.id,
        author_type="agent",
        author_id=rc.agent.id,
        body=body,
    )
    db.add(reply)
    note.updated_at = utcnow()
    db.flush()
    audit.record(
        db,
        company_id=note.company_id,
        principal=rc.ctx.principal,
        action="note.replied",
        target_type="note",
        target_id=note.id,
        summary=f"{rc.agent.name} replied to the Founder's note",
        data={"reply_id": reply.id},
    )
    _emit(db, note, "note.replied", reply_id=reply.id, author_type="agent", agent_id=rc.agent.id)
    if note.author_user_id:  # M7: the person who wrote the note hears the AI Co-founder's answer
        notifications.notify_user(
            db,
            note.author_user_id,
            notifications.NOTE_REPLIED,
            "The AI Co-founder replied to your note",
            company_id=note.company_id,
            body=_preview(body, 200),
            related_type="note",
            related_id=note.id,
        )
    return reply


def agent_reply(db: Session, rc: AgentRunContext, note_id: str, body: str) -> NoteReply:
    _require_cofounder(rc)
    note = _get(db, rc.ctx.company_id, note_id)
    reply = _add_agent_reply(db, rc, note, body)
    db.commit()
    return reply


def link_created_records(db: Session, company_id: str, run, agent: Agent) -> None:
    """When the AI Co-founder finishes a planning run that triaged exactly one note, the goals,
    projects, hiring tasks, tasks and decisions it created during that run are linked to the note's
    triage (models often triage first and act afterwards). No commit."""
    handled = list(
        db.scalars(
            select(FounderNote).where(
                FounderNote.company_id == company_id, FounderNote.triaged_by_run_id == run.id
            )
        )
    )
    if len(handled) != 1 or not handled[0].triage:
        return
    note = handled[0]
    since = run.created_at  # row creation time (wall clock), not the engine clock
    found: list[dict[str, str | None]] = []
    for goal in db.scalars(
        select(Goal).where(
            Goal.company_id == company_id,
            Goal.created_by_id == agent.id,
            Goal.created_at >= since,
        )
    ):
        found.append({"type": "goal", "ref_id": goal.id, "label": goal.title[:200]})
    for project in db.scalars(
        select(Project).where(
            Project.company_id == company_id,
            Project.created_by_id == agent.id,
            Project.created_at >= since,
        )
    ):
        found.append({"type": "project", "ref_id": project.id, "label": project.name[:200]})
    for task in db.scalars(
        select(Task).where(Task.company_id == company_id, Task.created_by_run_id == run.id)
    ):
        kind = "hire" if task.kind == "hiring" else "task"
        found.append({"type": kind, "ref_id": task.id, "label": task.title[:200]})
    for decision in db.scalars(
        select(Decision).where(
            Decision.company_id == company_id,
            Decision.made_by_id == agent.id,
            Decision.created_at >= since,
        )
    ):
        found.append({"type": "decision", "ref_id": decision.id, "label": decision.title[:200]})
    triage = dict(note.triage)
    actions = list(triage.get("actions") or [])
    known = {(a.get("type"), a.get("ref_id")) for a in actions}
    added = [f for f in found if (f["type"], f["ref_id"]) not in known]
    if not added:
        return
    triage["actions"] = [*actions, *added][:20]
    note.triage = triage
    note.updated_at = utcnow()
    _emit(db, note, "note.updated", changed=["triage"])
