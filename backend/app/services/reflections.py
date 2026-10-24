"""Reflections: the AI Co-founder's observations and the Founder's reactions (docs/specs/m10 section 4).

* The **AI Co-founder** (agent API) posts reflections, replies to reactions and revises a reflection.
  It can never adopt, retire or edit a company standard.
* The **Founder** (a human, owner/admin) endorses, challenges, refines or comments. Endorsing a
  ``standard_proposal`` (original or revised) creates the company standard -- the only way an agent's
  idea becomes a rule, and it takes a human. Every human response wakes the AI Co-founder (coalesced).
* Limits (code, not prompts): a few reflections a day, a cap on open ones, no duplicate titles, no
  secrets, evidence must point at records of this company.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, LimitExceeded, NotFound, ValidationFailed
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.enums import AgentKind, AgentStatus
from app.models.reflections import (
    Reflection,
    ReflectionKind,
    ReflectionReaction,
    ReflectionResponse,
    ReflectionStatus,
)
from app.models.run import TaskRun
from app.models.work import Goal, Project, Task
from app.schemas.reflections import (
    AgentReflection,
    AgentReflectionPost,
    AgentReflectionRevise,
    ReflectionResponseCreate,
)
from app.services import audit, events, secrets_scan, standards, wakes
from app.services.agent_auth import AgentRunContext
from app.services.pagination import paginate

MAX_PER_DAY = 5
MAX_OPEN = 20
DEDUPE_DAYS = 7
ATTENTION_LIMIT = 5

_EVIDENCE_MODELS: dict[str, tuple[Any, str]] = {
    "task": (Task, "title"),
    "artifact": (Artifact, "title"),
    "project": (Project, "name"),
    "agent": (Agent, "name"),
    "goal": (Goal, "title"),
    "run": (TaskRun, "id"),
}
_CLOSED = (ReflectionStatus.ARCHIVED, ReflectionStatus.ADOPTED)


# ---------------------------------------------------------------- reading


def _get(db: Session, company_id: str, reflection_id: str) -> Reflection:
    row = db.scalar(
        select(Reflection).where(
            Reflection.id == reflection_id, Reflection.company_id == company_id
        )
    )
    if row is None:
        raise NotFound("Reflection not found")
    return row


def _names(db: Session, agent_ids: set[str]) -> dict[str, str]:
    if not agent_ids:
        return {}
    return dict(db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))).all())


def _user_names(db: Session, user_ids: set[str]) -> dict[str, str]:
    from app.models.user import User

    if not user_ids:
        return {}
    return dict(db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids))).all())


def _response_out(r: ReflectionResponse, agents: dict[str, str], users: dict[str, str]) -> dict:
    name = (
        agents.get(r.author_id or "") if r.author_type == "agent" else users.get(r.author_id or "")
    )
    return {
        "id": r.id,
        "reflection_id": r.reflection_id,
        "author_type": r.author_type,
        "author_id": r.author_id,
        "author_name": name,
        "reaction": r.reaction,
        "body": r.body,
        "created_at": r.created_at,
    }


def serialize_response(db: Session, r: ReflectionResponse) -> dict:
    agents = _names(db, {r.author_id} if r.author_type == "agent" and r.author_id else set())
    users = _user_names(db, {r.author_id} if r.author_type == "human" and r.author_id else set())
    return _response_out(r, agents, users)


def _responses(db: Session, reflection_ids: list[str]) -> dict[str, list[ReflectionResponse]]:
    out: dict[str, list[ReflectionResponse]] = {rid: [] for rid in reflection_ids}
    if reflection_ids:
        for r in db.scalars(
            select(ReflectionResponse)
            .where(ReflectionResponse.reflection_id.in_(reflection_ids))
            .order_by(ReflectionResponse.created_at, ReflectionResponse.id)
        ):
            out[r.reflection_id].append(r)
    return out


def serialize(db: Session, rows: list[Reflection], *, detail: bool = False) -> list[dict]:
    if not rows:
        return []
    by_reflection = _responses(db, [r.id for r in rows])
    agents = _names(
        db,
        {r.author_agent_id for r in rows}
        | {
            resp.author_id
            for resps in by_reflection.values()
            for resp in resps
            if resp.author_type == "agent" and resp.author_id
        },
    )
    users = _user_names(
        db,
        {
            resp.author_id
            for resps in by_reflection.values()
            for resp in resps
            if resp.author_type == "human" and resp.author_id
        },
    )
    out = []
    for row in rows:
        resps = by_reflection[row.id]
        item = {
            "id": row.id,
            "company_id": row.company_id,
            "author_agent_id": row.author_agent_id,
            "author_name": agents.get(row.author_agent_id),
            "kind": row.kind,
            "title": row.title,
            "body": row.body,
            "evidence": list(row.evidence or []),
            "proposed_standard": row.proposed_standard,
            "status": row.status,
            "standard_id": row.standard_id,
            "response_count": len(resps),
            "last_response": _response_out(resps[-1], agents, users) if resps else None,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        if detail:
            item["responses"] = [_response_out(r, agents, users) for r in resps]
        out.append(item)
    return out


def list_reflections(
    db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None, status: str | None = None
):
    if not ctx.principal.is_human:
        raise Forbidden("Agents use the agent API for reflections")
    stmt = select(Reflection).where(Reflection.company_id == ctx.company_id)
    if status:
        stmt = stmt.where(Reflection.status == status)
    rows, nxt = paginate(db, stmt, Reflection, limit=limit, cursor=cursor, descending=True)
    return serialize(db, rows), nxt


def get_reflection(db: Session, ctx: CompanyContext, reflection_id: str) -> dict:
    if not ctx.principal.is_human:
        raise Forbidden("Agents use the agent API for reflections")
    return serialize(db, [_get(db, ctx.company_id, reflection_id)], detail=True)[0]


# ---------------------------------------------------------------- the Founder reacts


def _emit(db: Session, row: Reflection, type_: str, **extra: Any) -> None:
    events.emit(db, row.company_id, type_, {"reflection_id": row.id, "status": row.status, **extra})


def respond(
    db: Session, ctx: CompanyContext, reflection_id: str, data: ReflectionResponseCreate
) -> dict:
    """A human reacts. Endorsing a standard proposal adopts the standard (the human is its author)."""
    if not ctx.principal.is_human:
        raise Forbidden("Agents use the agent API for reflections")
    require_human_admin(ctx, "respond to reflections")
    row = _get(db, ctx.company_id, reflection_id)
    if row.status == ReflectionStatus.ARCHIVED:
        raise Conflict("This reflection is archived")
    body = (data.body or "").strip() or None
    response = ReflectionResponse(
        company_id=ctx.company_id,
        reflection_id=row.id,
        author_type="human",
        author_id=ctx.principal.id,
        reaction=data.reaction,
        body=body,
    )
    db.add(response)
    previous = row.status
    standard_id = None
    if data.reaction == ReflectionReaction.ENDORSE:
        if (
            row.kind == ReflectionKind.STANDARD_PROPOSAL
            and row.proposed_standard
            and row.status != ReflectionStatus.ADOPTED
        ):
            standard = standards.adopt(
                db,
                ctx.company_id,
                ctx.principal,
                title=row.title,
                statement=row.proposed_standard,
                source_reflection_id=row.id,
            )
            row.standard_id = standard.id
            row.status = ReflectionStatus.ADOPTED
            standard_id = standard.id
        elif row.status != ReflectionStatus.ADOPTED:
            row.status = ReflectionStatus.ENDORSED
    elif (
        data.reaction in (ReflectionReaction.CHALLENGE, ReflectionReaction.REFINE)
        and row.status != ReflectionStatus.ADOPTED
    ):
        row.status = ReflectionStatus.OPEN
    row.updated_at = utcnow()
    db.flush()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="reflection.responded",
        target_type="reflection",
        target_id=row.id,
        summary=f"The Founder {data.reaction}d reflection '{row.title}'"
        if data.reaction != "comment"
        else f"The Founder commented on reflection '{row.title}'",
        data={
            "reaction": data.reaction,
            "from": previous,
            "to": row.status,
            "standard_id": standard_id,
        },
    )
    _emit(db, row, "reflection.responded", response_id=response.id, reaction=data.reaction)
    if row.status != previous:
        _emit(db, row, "reflection.updated", previous_status=previous)
    excerpt = " ".join((secrets_scan.scrub(body) or "").split())[:200]
    verb = {
        "endorse": "endorsed",
        "challenge": "challenged",
        "refine": "asked to refine",
        "comment": "commented on",
    }[data.reaction]
    wakes.wake_founder(
        db,
        ctx.company_id,
        "reflection",
        f"The Founder {verb} your reflection '{row.title[:80]}' [id {row.id}]"
        + (f': "{excerpt}"' if excerpt else "")
        + ". Answer with company_reply_reflection"
        + (
            "; change it with company_revise_reflection if they asked for changes."
            if data.reaction in ("challenge", "refine")
            else " (brief; nothing else is needed)."
        ),
        ref_type="reflection",
        ref_id=row.id,
        delay_seconds=wakes.COMMAND_DEBOUNCE_SECONDS,
    )
    db.commit()
    out = _response_out(
        response,
        _names(db, set()),
        _user_names(db, {ctx.principal.id}),
    )
    out.update(reflection_status=row.status, standard_id=standard_id)
    return out


def archive(db: Session, ctx: CompanyContext, reflection_id: str) -> dict:
    if not ctx.principal.is_human:
        raise Forbidden("Agents use the agent API for reflections")
    require_human_admin(ctx, "archive reflections")
    row = _get(db, ctx.company_id, reflection_id)
    if row.status == ReflectionStatus.ARCHIVED:
        raise Conflict("This reflection is already archived")
    previous = row.status
    row.status = ReflectionStatus.ARCHIVED
    row.updated_at = utcnow()
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="reflection.archived",
        target_type="reflection",
        target_id=row.id,
        summary=f"Archived reflection '{row.title}'",
        data={"from": previous},
    )
    _emit(db, row, "reflection.updated", previous_status=previous)
    db.commit()
    return serialize(db, [row], detail=True)[0]


# ---------------------------------------------------------------- the AI Co-founder (agent API)


def _require_cofounder(rc: AgentRunContext) -> Agent:
    agent = rc.agent
    if agent.kind != AgentKind.FOUNDER or agent.status != AgentStatus.ACTIVE:
        raise Forbidden("Only the AI Co-founder writes reflections")
    return agent


def _resolve_evidence(db: Session, company_id: str, refs: list[Any]) -> list[dict[str, str | None]]:
    out: list[dict[str, str | None]] = []
    problems: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for i, ref in enumerate(refs):
        key = (ref.type, ref.id)
        if key in seen:
            continue
        seen.add(key)
        model, attr = _EVIDENCE_MODELS[ref.type]
        row = db.scalar(select(model).where(model.id == ref.id, model.company_id == company_id))
        if row is None:
            problems.append(
                {
                    "loc": ["body", "evidence", i, "id"],
                    "msg": f"no {ref.type} with this id exists in this company",
                }
            )
            continue
        label = str(getattr(row, attr, "") or "")[:200] or None
        if ref.type == "run":
            task = db.get(Task, row.task_id)
            label = f"Run of '{task.title}'"[:200] if task else label
        out.append({"type": ref.type, "id": ref.id, "label": label})
    if problems:
        raise ValidationFailed(
            "Evidence must reference records that exist in this company", details=problems
        )
    return out


def agent_post(
    db: Session, rc: AgentRunContext, data: AgentReflectionPost
) -> tuple[Reflection, bool]:
    cofounder = _require_cofounder(rc)
    company_id = rc.ctx.company_id
    if data.kind == ReflectionKind.STANDARD_PROPOSAL:
        if not (data.proposed_standard or "").strip():
            raise ValidationFailed(
                "A standard_proposal needs proposed_standard (one clear rule, up to 500 characters)",
                details=[{"loc": ["body", "proposed_standard"], "msg": "required for this kind"}],
            )
    elif data.proposed_standard:
        raise ValidationFailed(
            "proposed_standard belongs to kind standard_proposal",
            details=[{"loc": ["body", "proposed_standard"], "msg": "only for standard_proposal"}],
        )
    for text in (data.title, data.body, data.proposed_standard):
        if secrets_scan.find_secret(text):
            raise Conflict("A reflection must not contain secrets or credentials")
    title_key = data.title.strip().lower()
    cutoff = utcnow() - timedelta(days=DEDUPE_DAYS)
    existing = db.scalar(
        select(Reflection).where(
            Reflection.company_id == company_id,
            func.lower(Reflection.title) == title_key,
            Reflection.created_at >= cutoff,
            Reflection.status.notin_([ReflectionStatus.ARCHIVED]),
        )
    )
    if existing is not None:
        return existing, False
    since = utcnow() - timedelta(days=1)
    today = int(
        db.scalar(
            select(func.count(Reflection.id)).where(
                Reflection.company_id == company_id, Reflection.created_at >= since
            )
        )
        or 0
    )
    if today >= MAX_PER_DAY:
        raise LimitExceeded(
            f"At most {MAX_PER_DAY} reflections a day; pick the most important one",
            details={"limit": "reflections_per_day", "max": MAX_PER_DAY},
        )
    open_count = int(
        db.scalar(
            select(func.count(Reflection.id)).where(
                Reflection.company_id == company_id,
                Reflection.status.in_([ReflectionStatus.OPEN, ReflectionStatus.REVISED]),
            )
        )
        or 0
    )
    if open_count >= MAX_OPEN:
        raise LimitExceeded(
            f"{open_count} reflections still wait for the Founder; wait for reactions first",
            details={"limit": "open_reflections", "max": MAX_OPEN},
        )
    evidence = _resolve_evidence(db, company_id, data.evidence)
    row = Reflection(
        company_id=company_id,
        author_agent_id=cofounder.id,
        kind=data.kind,
        title=data.title.strip(),
        body=data.body.strip(),
        evidence=evidence,
        proposed_standard=(data.proposed_standard or "").strip() or None,
        status=ReflectionStatus.OPEN,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        company_id=company_id,
        principal=rc.ctx.principal,
        action="reflection.created",
        target_type="reflection",
        target_id=row.id,
        summary=f"{cofounder.name} posted a {row.kind.replace('_', ' ')}: {row.title}",
        data={"kind": row.kind, "evidence": len(evidence), "run_id": rc.run.id},
    )
    _emit(db, row, "reflection.created", kind=row.kind)
    db.commit()
    return row, True


def agent_reply(
    db: Session, rc: AgentRunContext, reflection_id: str, body: str
) -> ReflectionResponse:
    cofounder = _require_cofounder(rc)
    row = _get(db, rc.ctx.company_id, reflection_id)
    if row.status == ReflectionStatus.ARCHIVED:
        raise Conflict("This reflection is archived")
    if secrets_scan.find_secret(body):
        raise Conflict("A reply must not contain secrets or credentials")
    last = db.scalar(
        select(ReflectionResponse)
        .where(ReflectionResponse.reflection_id == row.id)
        .order_by(ReflectionResponse.created_at.desc(), ReflectionResponse.id.desc())
        .limit(1)
    )
    if (
        last is not None
        and last.author_type == "agent"
        and (last.body or "").strip() == body.strip()
    ):
        return last  # small models repeat themselves: one reply per thought
    response = ReflectionResponse(
        company_id=row.company_id,
        reflection_id=row.id,
        author_type="agent",
        author_id=cofounder.id,
        reaction=ReflectionReaction.COMMENT,
        body=body.strip(),
    )
    db.add(response)
    row.updated_at = utcnow()
    db.flush()
    audit.record(
        db,
        company_id=row.company_id,
        principal=rc.ctx.principal,
        action="reflection.replied",
        target_type="reflection",
        target_id=row.id,
        summary=f"{cofounder.name} replied on reflection '{row.title}'",
        data={"response_id": response.id},
    )
    _emit(db, row, "reflection.responded", response_id=response.id, reaction="comment")
    db.commit()
    return response


def agent_revise(
    db: Session, rc: AgentRunContext, reflection_id: str, data: AgentReflectionRevise
) -> Reflection:
    cofounder = _require_cofounder(rc)
    row = _get(db, rc.ctx.company_id, reflection_id)
    if row.author_agent_id != cofounder.id:
        raise Forbidden("Only the author can revise a reflection")
    if row.status in _CLOSED:
        raise Conflict(
            "An adopted standard is changed by people (Standards); an archived reflection is closed"
        )
    if data.body is None and data.title is None and data.proposed_standard is None:
        raise ValidationFailed("Nothing to revise: give a new body, title or proposed_standard")
    if data.proposed_standard is not None and row.kind != ReflectionKind.STANDARD_PROPOSAL:
        raise ValidationFailed("proposed_standard belongs to kind standard_proposal")
    for text in (data.title, data.body, data.proposed_standard, data.note):
        if secrets_scan.find_secret(text):
            raise Conflict("A reflection must not contain secrets or credentials")
    changed = []
    if data.title is not None and data.title.strip() != row.title:
        row.title = data.title.strip()
        changed.append("title")
    if data.body is not None and data.body.strip() != row.body:
        row.body = data.body.strip()
        changed.append("body")
    if (
        data.proposed_standard is not None
        and data.proposed_standard.strip() != row.proposed_standard
    ):
        row.proposed_standard = data.proposed_standard.strip()
        changed.append("proposed_standard")
    if not changed:
        raise Conflict("That is already what the reflection says")
    previous = row.status
    row.status = ReflectionStatus.REVISED
    row.updated_at = utcnow()
    note = (data.note or "").strip() or f"Revised: {', '.join(changed)}"
    response = ReflectionResponse(
        company_id=row.company_id,
        reflection_id=row.id,
        author_type="agent",
        author_id=cofounder.id,
        reaction=ReflectionReaction.COMMENT,
        body=note,
    )
    db.add(response)
    db.flush()
    audit.record(
        db,
        company_id=row.company_id,
        principal=rc.ctx.principal,
        action="reflection.revised",
        target_type="reflection",
        target_id=row.id,
        summary=f"{cofounder.name} revised reflection '{row.title}'",
        data={"changed": changed, "from": previous},
    )
    _emit(db, row, "reflection.updated", previous_status=previous, changed=changed)
    _emit(db, row, "reflection.responded", response_id=response.id, reaction="comment")
    db.commit()
    return row


def attention(db: Session, company_id: str) -> list[AgentReflection]:
    """Reflections where the Founder spoke last: what the AI Co-founder still has to answer."""
    rows = list(
        db.scalars(
            select(Reflection)
            .where(
                Reflection.company_id == company_id,
                Reflection.status.notin_([ReflectionStatus.ARCHIVED]),
            )
            .order_by(Reflection.updated_at.desc())
            .limit(30)
        )
    )
    by_reflection = _responses(db, [r.id for r in rows])
    out: list[AgentReflection] = []
    for row in rows:
        resps = by_reflection[row.id]
        if not resps or resps[-1].author_type != "human":
            continue
        last = resps[-1]
        out.append(
            AgentReflection(
                id=row.id,
                kind=row.kind,  # type: ignore[arg-type]
                title=row.title,
                status=row.status,  # type: ignore[arg-type]
                proposed_standard=row.proposed_standard,
                latest_response=" ".join((secrets_scan.scrub(last.body) or "").split())[:400]
                or None,
                latest_reaction=last.reaction,  # type: ignore[arg-type]
            )
        )
        if len(out) >= ATTENTION_LIMIT:
            break
    return out


def recent_for_state(db: Session, company_id: str, limit: int = 5) -> list[dict]:
    rows = list(
        db.scalars(
            select(Reflection)
            .where(Reflection.company_id == company_id)
            .order_by(Reflection.created_at.desc())
            .limit(limit)
        )
    )
    return [{"id": r.id, "kind": r.kind, "title": r.title[:120], "status": r.status} for r in rows]
