"""Company memory: facts, requirements, decisions, lessons and research notes with provenance.

* A human's entry is **verified** on creation; an agent can only **propose** (``proposed``).
* Verification is maker-checker: the AI Co-founder verifies other agents' proposals, never its
  own (those wait for a human). Humans can verify or reject anything.
* Only ``verified`` entries reach agents (``GET /context``, the run prompt). Lessons may be
  scoped with ``applies_to`` (role families, role templates, agent ids; empty = everyone).
* Text that looks like a secret is rejected (422), and note-derived entries skip memory
  instead of storing it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, InvalidTransition, LimitExceeded, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, Role, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.autonomy import MemoryEntry
from app.models.enums import AgentKind, AgentStatus, MemoryKind, MemoryStatus
from app.schemas.autonomy import MemoryCreate
from app.services import audit, events, secrets_scan
from app.services.pagination import paginate

MAX_PROPOSED_PER_AGENT = 20
CONTEXT_MAX_ENTRIES = 12
CONTEXT_ENTRY_CHARS = 600
CONTEXT_TOTAL_CHARS = 4000


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def reject_secrets(*texts: str | None) -> None:
    for text in texts:
        found = secrets_scan.find_secret(text)
        if found:
            raise ValidationFailed(
                f"Memory must not contain secrets (found: {found})",
                details=[{"loc": ["body", "body"], "msg": f"looks like a secret: {found}"}],
            )


def _actor(principal: Principal) -> tuple[str, str | None]:
    return principal.type.value, principal.id


def _emit(db: Session, entry: MemoryEntry, type_: str) -> None:
    events.emit(
        db,
        entry.company_id,
        type_,
        {"memory_id": entry.id, "kind": entry.kind, "status": entry.status},
    )


def _find_duplicate(db: Session, company_id: str, kind: str, body: str) -> MemoryEntry | None:
    wanted = _normalize(body)
    for entry in db.scalars(
        select(MemoryEntry).where(
            MemoryEntry.company_id == company_id,
            MemoryEntry.kind == kind,
            MemoryEntry.status.in_([MemoryStatus.PROPOSED, MemoryStatus.VERIFIED]),
        )
    ):
        if _normalize(entry.body) == wanted:
            return entry
    return None


def create_entry(
    db: Session,
    company_id: str,
    principal: Principal,
    data: MemoryCreate,
    *,
    source: dict[str, Any] | None = None,
    verified: bool,
) -> tuple[MemoryEntry, bool]:
    """Insert an entry (no commit). Returns (entry, created); an identical live entry is reused."""
    reject_secrets(data.title, data.body)
    duplicate = _find_duplicate(db, company_id, data.kind, data.body)
    if duplicate is not None:
        return duplicate, False
    atype, aid = _actor(principal)
    now = utcnow()
    entry = MemoryEntry(
        company_id=company_id,
        kind=data.kind,
        title=data.title,
        body=data.body,
        status=MemoryStatus.VERIFIED if verified else MemoryStatus.PROPOSED,
        source=source or {"type": atype, "id": aid},
        tags=list(data.tags),
        applies_to=list(data.applies_to),
        proposed_by_type=atype,
        proposed_by_id=aid,
        verified_by_type=atype if verified else None,
        verified_by_id=aid if verified else None,
        verified_at=now if verified else None,
    )
    db.add(entry)
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=principal,
        action="memory.created",
        target_type="memory",
        target_id=entry.id,
        summary=f"{'Added' if verified else 'Proposed'} {entry.kind} memory: "
        f"{entry.title or entry.body[:60]}",
        data={"kind": entry.kind, "status": entry.status},
    )
    _emit(db, entry, "memory.created")
    return entry, True


def add_from_note(
    db: Session,
    note: Any,
    *,
    kind: str,
    title: str | None,
    author: Principal | None,
) -> MemoryEntry | None:
    """A requirement/context note becomes a verified entry (source = the note, verified by its
    human author). Skipped (None) when the note looks like it contains a secret."""
    if secrets_scan.find_secret(note.body) or secrets_scan.find_secret(title):
        return None
    for existing in db.scalars(
        select(MemoryEntry).where(
            MemoryEntry.company_id == note.company_id, MemoryEntry.status != MemoryStatus.REJECTED
        )
    ):
        src = existing.source or {}
        if src.get("type") == "note" and src.get("id") == note.id:
            return existing
    now = utcnow()
    entry = MemoryEntry(
        company_id=note.company_id,
        kind=kind,
        title=(title or note.body[:80])[:200],
        body=note.body,
        status=MemoryStatus.VERIFIED,
        source={"type": "note", "id": note.id, "label": "Founder's note"},
        tags=["founder-note"],
        applies_to=[],
        proposed_by_type="human",
        proposed_by_id=note.author_user_id,
        verified_by_type="human",
        verified_by_id=note.author_user_id,
        verified_at=now,
    )
    db.add(entry)
    db.flush()
    audit.record(
        db,
        company_id=note.company_id,
        principal=author,
        action="memory.created",
        target_type="memory",
        target_id=entry.id,
        summary=f"Founder's note became verified {kind} memory: {entry.title}",
        data={"kind": kind, "note_id": note.id},
    )
    _emit(db, entry, "memory.created")
    return entry


# ---------------------------------------------------------------- owner API


def _require_member(ctx: CompanyContext) -> None:
    if ctx.principal.is_agent:
        raise Forbidden("Agents use the agent API for memory")


def list_entries(
    db: Session,
    ctx: CompanyContext,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
):
    _require_member(ctx)
    stmt = select(MemoryEntry).where(MemoryEntry.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(MemoryEntry.status == status)
    if kind:
        stmt = stmt.where(MemoryEntry.kind == kind)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(MemoryEntry.body).like(like), func.lower(MemoryEntry.title).like(like))
        )
    rows, nxt = paginate(db, stmt, MemoryEntry, limit=limit, cursor=cursor, descending=True)
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    return rows, nxt


def get_entry(db: Session, ctx: CompanyContext, entry_id: str) -> MemoryEntry:
    entry = db.scalar(
        select(MemoryEntry).where(
            MemoryEntry.id == entry_id, MemoryEntry.company_id == ctx.company_id
        )
    )
    if entry is None:
        raise NotFound("Memory entry not found")
    return entry


def create_as_human(db: Session, ctx: CompanyContext, data: MemoryCreate) -> MemoryEntry:
    require_human_admin(ctx, "add memory")
    entry, _ = create_entry(db, ctx.company_id, ctx.principal, data, verified=True)
    db.commit()
    return entry


def create_as_agent(
    db: Session, ctx: CompanyContext, data: MemoryCreate, *, run_task_id: str | None, run_id: str
) -> tuple[MemoryEntry, bool]:
    """An agent proposes an entry: never verified by its own call."""
    proposed = db.scalar(
        select(func.count(MemoryEntry.id)).where(
            MemoryEntry.company_id == ctx.company_id,
            MemoryEntry.proposed_by_id == ctx.principal.id,
            MemoryEntry.status == MemoryStatus.PROPOSED,
        )
    )
    if (proposed or 0) >= MAX_PROPOSED_PER_AGENT:
        raise LimitExceeded(
            f"You already have {proposed} unverified proposals; wait for them to be reviewed",
            details={"limit": "memory_proposals", "max": MAX_PROPOSED_PER_AGENT},
        )
    source = {"type": "run", "id": run_id, "task_id": run_task_id}
    entry, created = create_entry(
        db, ctx.company_id, ctx.principal, data, source=source, verified=False
    )
    db.commit()
    return entry, created


def decide(
    db: Session,
    ctx: CompanyContext,
    entry_id: str,
    decision: str,
    reason: str | None = None,
) -> MemoryEntry:
    """verify / reject. Humans: any entry. The active AI Co-founder: other agents' proposals."""
    if ctx.role == Role.VIEWER:
        raise Forbidden("Viewers have read-only access")
    entry = get_entry(db, ctx, entry_id)
    principal = ctx.principal
    if principal.is_agent:
        agent = db.get(Agent, principal.id)
        if agent is None or agent.kind != AgentKind.FOUNDER or agent.status != AgentStatus.ACTIVE:
            raise Forbidden("Only the AI Co-founder can verify memory proposed by agents")
        if entry.proposed_by_type == "agent" and entry.proposed_by_id == principal.id:
            raise Forbidden("The AI Co-founder cannot verify its own proposal; a human must")
        if entry.status != MemoryStatus.PROPOSED:
            raise InvalidTransition(
                f"Only proposed entries can be decided (entry is {entry.status})"
            )
    elif not ctx.is_human_admin:
        raise Forbidden("Only company owners and admins can decide memory")
    if decision == "verify":
        if entry.status == MemoryStatus.VERIFIED:
            return entry
        if entry.status == MemoryStatus.SUPERSEDED:
            raise InvalidTransition("A superseded entry cannot be verified")
        reject_secrets(entry.title, entry.body)
        now = utcnow()
        entry.status = MemoryStatus.VERIFIED
        entry.verified_by_type, entry.verified_by_id = _actor(principal)
        entry.verified_at = now
        entry.rejection_reason = None
        action, verb = "memory.verified", "Verified"
    elif decision == "reject":
        if entry.status in (MemoryStatus.REJECTED, MemoryStatus.SUPERSEDED):
            return entry
        entry.status = MemoryStatus.REJECTED
        entry.rejection_reason = reason
        action, verb = "memory.rejected", "Rejected"
    else:
        raise ValidationFailed("decision must be 'verify' or 'reject'")
    entry.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=principal,
        action=action,
        target_type="memory",
        target_id=entry.id,
        summary=f"{verb} memory: {entry.title or entry.body[:60]}",
        data={"kind": entry.kind, "reason": reason},
    )
    _emit(db, entry, "memory.updated")
    db.commit()
    return entry


# ---------------------------------------------------------------- agent context


def _applies(entry: MemoryEntry, agent: Agent) -> bool:
    if not entry.applies_to:
        return True
    mine = {
        str(agent.id),
        str(agent.role_family),
        str(agent.role_template or ""),
        str(agent.kind),
        "all",
    }
    return bool(mine & {str(a) for a in entry.applies_to})


def relevant_for(
    db: Session, company_id: str, agent: Agent, *, now: datetime | None = None
) -> list[MemoryEntry]:
    """Verified entries an agent should see, newest first, within a context budget."""
    rows = db.scalars(
        select(MemoryEntry)
        .where(MemoryEntry.company_id == company_id, MemoryEntry.status == MemoryStatus.VERIFIED)
        .order_by(MemoryEntry.verified_at.desc(), MemoryEntry.id)
        .limit(100)
    )
    out: list[MemoryEntry] = []
    total = 0
    for entry in rows:
        if not _applies(entry, agent):
            continue
        size = min(len(entry.body), CONTEXT_ENTRY_CHARS) + len(entry.title or "")
        if out and total + size > CONTEXT_TOTAL_CHARS:
            break
        out.append(entry)
        total += size
        if len(out) >= CONTEXT_MAX_ENTRIES:
            break
    return out


def context_lines(entries: list[MemoryEntry]) -> list[str]:
    """One compact line per entry for the run prompt."""
    lines = []
    for e in entries:
        head = f"[{e.kind}] {e.title}: " if e.title else f"[{e.kind}] "
        body = " ".join(e.body.split())
        lines.append((head + body)[:CONTEXT_ENTRY_CHARS])
    return lines


def kinds() -> list[str]:
    return [k.value for k in MemoryKind]
