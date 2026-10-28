"""The Command Center (docs/specs/m10 section 5): every input produces an output.

* ``/commands`` are deterministic and instant, executed by the control plane with **the human's own
  permissions** -- they call the same services as the owner API, so the role matrix applies unchanged
  (a viewer's ``/pause`` is the same refusal as ``POST /pause``); a refusal is an ``error`` output, not
  silence.
* **Free text** is a Founder's note: the AI Co-founder is woken (5 s debounce, urgent) and the
  exchange moves ``pending -> processing -> answered``. The output is guaranteed: if its run fails,
  times out, finishes without answering, or nothing answers within 15 minutes, the system writes an
  output saying what happened and retries once automatically; ``POST .../retry`` retries by hand.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.principal import CompanyContext, require_human_admin
from app.core.time import utcnow
from app.models.agent import Agent
from app.models.autonomy import FounderNote, NoteReply
from app.models.commands import CommandExchange, ExchangeStatus
from app.models.company import Company
from app.models.enums import (
    AgentKind,
    AgentStatus,
    ApprovalStatus,
    CompanyStatus,
    NoteStatus,
    RoleFamily,
    TaskKind,
)
from app.models.governance import Approval
from app.models.reflections import StandardStatus
from app.models.run import TaskRun
from app.schemas.autonomy import NoteCreate
from app.schemas.work import GoalCreate, TaskCreate
from app.services import (
    ai_setup,
    approvals,
    audit,
    control,
    events,
    goals,
    notes,
    overview,
    role_templates,
    standards,
    tasks,
    wakes,
    wallet,
)
from app.services.pagination import paginate

logger = logging.getLogger("company_os.command_center")

ANSWER_DEADLINE = timedelta(minutes=15)
MAX_ATTEMPTS = 2  # the first try and one automatic retry
SPECIALS = ("@",)


# ---------------------------------------------------------------- output helpers


def _out(kind: str, title: str, text: str = "", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "title": title, "text": text, **extra}


def _table(title: str, columns: list[str], rows: list[list[str]], *, text: str = "", links=None):
    out = _out("table", title, text, columns=columns, rows=rows)
    if links:
        out["links"] = links
    return out


def _link(label: str, panel: str) -> dict[str, str]:
    return {"label": label, "panel": panel}


def _usd(micros: int) -> str:
    dollars = micros / 1_000_000
    return f"${dollars:,.2f}" if micros % 10_000 == 0 else f"${dollars:,.4f}"


def _split(split) -> str:
    return (
        f"verified {_usd(split.verified_micros)}, estimated {_usd(split.estimated_micros)}, "
        f"unknown {_usd(split.unknown_micros)}"
    )


def _clip(text: str | None, n: int = 80) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


# ---------------------------------------------------------------- slash commands

HELP: list[tuple[str, str]] = [
    ("/help", "List the commands"),
    ("/status", "Company, team, work, spend and approvals at a glance"),
    ("/goals", "Open goals"),
    ("/agents", "The team"),
    ("/tasks [status]", "Tasks (optionally one status, e.g. /tasks blocked)"),
    ("/spend [day|month]", "Spend, split into verified / estimated / unknown"),
    ("/approvals", "Approvals waiting for a decision"),
    ("/wallet", "Agent cards: balance, awaiting checkout, renewals"),
    ("/standards", "The company standards in force"),
    ("/pause", "Pause the company: cancel runs, stop dispatch"),
    ("/resume", "Resume a paused company"),
    ("/goal <text>", "Create a goal"),
    ("/task <title> [@agent]", "Create a task, optionally assigned to an agent"),
    ("/hire <template> [reason]", "Ask HR to hire from a role template"),
    ("/approve <id>", "Approve an approval request"),
    ("/reject <id> [note]", "Reject an approval request"),
]
COMMAND_NAMES = [h[0].split()[0].lstrip("/") for h in HELP]


class _Result:
    def __init__(self, output: dict[str, Any], actions: list[dict[str, str | None]] | None = None):
        self.output = output
        self.actions = actions or []


def _find_agent(db: Session, company_id: str, name: str) -> Agent | None:
    key = name.strip().lower()
    rows = list(
        db.scalars(
            select(Agent).where(
                Agent.company_id == company_id,
                Agent.status.in_([AgentStatus.ACTIVE, AgentStatus.PAUSED]),
            )
        )
    )
    exact = [a for a in rows if a.name.lower() == key]
    if len(exact) == 1:
        return exact[0]
    starts = [a for a in rows if a.name.lower().startswith(key)]
    return starts[0] if len(starts) == 1 else None


def _cmd_help(db, ctx, arg) -> _Result:
    return _Result(_table("Commands", ["Command", "What it does"], [[a, b] for a, b in HELP]))


def _cmd_status(db, ctx, arg) -> _Result:
    ov = overview.build_overview(db, ctx)
    state = ai_setup.evaluate(db, ctx.company_id)
    by_status = ov.agents.by_status
    spend = ov.spending
    rows = [
        f"Company: {ov.company.name} ({ov.company.status})",
        "AI: "
        + (
            f"ready ({state.stack_count} connection(s))"
            if state.ready
            else f"not ready ({state.blocking_reason})"
        ),
        f"Team: {ov.agents.total} agents ({by_status.active} active, {by_status.paused} paused, "
        f"{by_status.proposed} proposed)",
        f"Tasks: {ov.tasks.open} open of {ov.tasks.total} "
        f"(in progress {ov.tasks.by_status.get('in_progress', 0)}, blocked "
        f"{ov.tasks.by_status.get('blocked', 0)}, failed {ov.tasks.by_status.get('failed', 0)})",
        f"Spend today: {_usd(spend.today.spent_micros)} ({_split(spend.today.by_cost_kind)})",
        f"Spend this month: {_usd(spend.month.spent_micros)} ({_split(spend.month.by_cost_kind)})",
        f"Approvals waiting: {ov.approvals.pending_total}",
    ]
    return _Result(
        _out(
            "list",
            "Status",
            "",
            rows=[[r] for r in rows],
            links=[
                _link("Organization", "org"),
                _link("Tasks", "tasks"),
                _link("Approvals", "approvals"),
                _link("Finance", "finance"),
            ],
        )
    )


def _cmd_goals(db, ctx, arg) -> _Result:
    items, _ = goals.list_goals(db, ctx, limit=20, cursor=None)
    open_goals = [g for g in items if g.status in ("proposed", "active")]
    if not open_goals:
        return _Result(
            _out("text", "Goals", "There are no open goals yet.", links=[_link("Goals", "goals")])
        )
    return _Result(
        _table(
            "Goals",
            ["Goal", "Status", "Priority"],
            [[_clip(g.title), g.status, g.priority] for g in open_goals],
            links=[_link("Goals", "goals")],
        )
    )


def _cmd_agents(db, ctx, arg) -> _Result:
    rows = list(
        db.scalars(
            select(Agent)
            .where(
                Agent.company_id == ctx.company_id,
                Agent.status.notin_([AgentStatus.RETIRED, AgentStatus.REJECTED]),
            )
            .order_by(Agent.created_at, Agent.id)
            .limit(40)
        )
    )
    return _Result(
        _table(
            "Team",
            ["Name", "Role", "Status", "Runtime"],
            [[a.name, a.role_title, a.status, a.runtime] for a in rows],
            links=[_link("Organization", "org")],
        )
    )


def _cmd_tasks(db, ctx, arg) -> _Result:
    status = arg.strip().lower() or None
    valid = {
        "backlog",
        "ready",
        "in_progress",
        "in_review",
        "revision_requested",
        "blocked",
        "done",
        "failed",
        "cancelled",
    }
    if status and status not in valid:
        return _Result(
            _out(
                "error",
                "Tasks",
                f"Unknown status '{status}'. Use one of: {', '.join(sorted(valid))}.",
            )
        )
    items, _ = tasks.list_tasks(db, ctx, limit=15, cursor=None, status=status)
    names = dict(
        db.execute(select(Agent.id, Agent.name).where(Agent.company_id == ctx.company_id)).all()
    )
    if not items:
        return _Result(
            _out(
                "text",
                "Tasks",
                f"No {status + ' ' if status else ''}tasks.",
                links=[_link("Tasks", "tasks")],
            )
        )
    return _Result(
        _table(
            f"Tasks{' · ' + status if status else ''}",
            ["Task", "Status", "Assignee", "Priority"],
            [
                [_clip(t.title), t.status, names.get(t.assignee_agent_id or "", "—"), t.priority]
                for t in items
            ],
            links=[_link("Tasks", "tasks")],
        ),
        [{"type": "task", "ref_id": t.id, "label": _clip(t.title, 60)} for t in items[:5]],
    )


def _cmd_spend(db, ctx, arg) -> _Result:
    period = arg.strip().lower() or "day"
    if period not in ("day", "month"):
        return _Result(_out("error", "Spend", "Use /spend day or /spend month."))
    ov = overview.build_overview(db, ctx)
    p = ov.spending.today if period == "day" else ov.spending.month
    rows = [
        ["Verified (a charge)", _usd(p.by_cost_kind.verified_micros)],
        ["Estimated (not a charge)", _usd(p.by_cost_kind.estimated_micros)],
        ["Unknown (no price)", _usd(p.by_cost_kind.unknown_micros)],
        ["Held by running work", _usd(p.held_micros)],
        ["Budget", _usd(p.budget_micros) if p.budget_micros is not None else "none set"],
    ]
    return _Result(
        _table(
            f"Spend · {'today' if period == 'day' else 'this month'}",
            ["", "Amount"],
            rows,
            text=f"Total recorded: {_usd(p.spent_micros)}",
            links=[_link("Finance", "finance")],
        )
    )


def _cmd_wallet(db, ctx, arg) -> _Result:
    ov = wallet.overview(db, ctx)
    if not ov.enabled:
        return _Result(
            _out(
                "text",
                "Wallet",
                "Agent cards are not set up: agents cannot buy anything yet, and a purchase "
                "request becomes your task. Open the Wallet to set it up.",
                links=[_link("Wallet", "wallet")],
            )
        )
    bal = ov.balance
    if bal is None or bal.available_micros is None:
        balance = (
            "manual card (no balance to report)" if bal and bal.source == "manual" else "unknown"
        )
    else:
        balance = _usd(bal.available_micros) + (
            f" as of {bal.as_of:%Y-%m-%d %H:%M} UTC" if bal.as_of else ""
        )
    rows = [
        ["Balance (provider-reported)", balance],
        ["Settled this month (verified)", _usd(ov.month.settled_verified_micros)],
        ["Committed, not charged", _usd(ov.month.held_micros)],
        [
            "Purchase budget",
            _usd(ov.month.budget_micros) if ov.month.budget_micros is not None else "none set",
        ],
        ["Awaiting your checkout", str(len(ov.awaiting_checkout))],
        ["Purchases awaiting approval", str(ov.pending_approvals)],
        ["Renewals due soon", str(len(ov.renewals_due))],
    ]
    notes = []
    if ov.low_balance:
        notes.append("The balance may not cover what is committed and due.")
    if ov.attention:
        notes.append(ov.attention)
    return _Result(
        _table("Wallet", ["", ""], rows, text=" ".join(notes), links=[_link("Wallet", "wallet")])
    )


def _cmd_approvals(db, ctx, arg) -> _Result:
    rows = list(
        db.scalars(
            select(Approval)
            .where(Approval.company_id == ctx.company_id, Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at)
            .limit(15)
        )
    )
    if not rows:
        return _Result(
            _out(
                "text",
                "Approvals",
                "Nothing is waiting for a decision.",
                links=[_link("Approvals", "approvals")],
            )
        )
    return _Result(
        _table(
            "Approvals waiting",
            ["Id", "Request", "Kind", "Decides"],
            [[a.id, _clip(a.title, 60), a.kind, a.approver] for a in rows],
            text="Decide with /approve <id> or /reject <id> [note].",
            links=[_link("Approvals", "approvals")],
        )
    )


def _cmd_standards(db, ctx, arg) -> _Result:
    items, _ = standards.list_standards(
        db, ctx, limit=30, cursor=None, status=StandardStatus.ACTIVE
    )
    if not items:
        return _Result(
            _out(
                "text",
                "Standards",
                "No company standards yet. The AI Co-founder proposes them in Reflections; endorse one to adopt it.",
                links=[_link("Reflections", "reflections")],
            )
        )
    return _Result(
        _table(
            "Company standards",
            ["Standard", "Statement"],
            [[_clip(s.title, 60), _clip(s.statement, 160)] for s in items],
            links=[_link("Standards", "standards")],
        )
    )


def _cmd_pause(db, ctx, arg) -> _Result:
    control.pause_company(db, ctx)
    return _Result(
        _out(
            "text",
            "Paused",
            "The company is paused: runs were cancelled and nothing is dispatched. /resume to continue.",
        )
    )


def _cmd_resume(db, ctx, arg) -> _Result:
    control.resume_company(db, ctx)
    return _Result(_out("text", "Resumed", "The company is running again."))


def _cmd_goal(db, ctx, arg) -> _Result:
    text = arg.strip()
    if not text:
        return _Result(_out("error", "Goal", "Usage: /goal <what you want to achieve>"))
    title = text if len(text) <= 200 else _clip(text, 200)
    goal = goals.create_goal(
        db, ctx, GoalCreate(title=title, description=text if len(text) > 200 else None)
    )
    return _Result(
        _out(
            "text",
            "Goal created",
            f"“{_clip(goal.title, 120)}” is a new goal. The AI Co-founder will plan it.",
            links=[_link("Goals", "goals")],
        ),
        [{"type": "goal", "ref_id": goal.id, "label": _clip(goal.title, 60)}],
    )


def _cmd_task(db, ctx, arg) -> _Result:
    words = arg.strip().split()
    assignee = None
    if words and words[-1].startswith("@") and len(words[-1]) > 1:
        name = words.pop()[1:]
        assignee = _find_agent(db, ctx.company_id, name)
        if assignee is None:
            return _Result(
                _out("error", "Task", f"No single active agent matches @{name}. See /agents.")
            )
    title = " ".join(words)
    if not title:
        return _Result(_out("error", "Task", "Usage: /task <title> [@agent]"))
    task, _created = tasks.create_task(
        db, ctx, TaskCreate(title=title[:300], assignee_agent_id=assignee.id if assignee else None)
    )
    who = (
        f" for {assignee.name}"
        if assignee
        else " (unassigned: the system will find the right person)"
    )
    return _Result(
        _out(
            "text",
            "Task created",
            f"“{_clip(task.title, 100)}”{who}.",
            links=[_link("Task", f"task:{task.id}")],
        ),
        [{"type": "task", "ref_id": task.id, "label": _clip(task.title, 60)}],
    )


def _cmd_hire(db, ctx, arg) -> _Result:
    require_human_admin(ctx, "ask for a hire")
    parts = arg.strip().split(None, 1)
    if not parts:
        return _Result(
            _out(
                "error",
                "Hire",
                "Usage: /hire <role template> [reason]. Templates: see /help hire or the Team panel.",
            )
        )
    slug, reason = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    view = role_templates.find_template(db, ctx.company_id, slug)
    if view is None:
        known = ", ".join(
            sorted(v.spec.slug for v in role_templates.list_templates(db, ctx.company_id))[:25]
        )
        return _Result(_out("error", "Hire", f"Unknown role template '{slug}'. Known: {known}."))
    hr = db.scalar(
        select(Agent)
        .where(
            Agent.company_id == ctx.company_id,
            Agent.role_family == RoleFamily.HR,
            Agent.status == AgentStatus.ACTIVE,
        )
        .order_by(Agent.created_at)
    )
    if hr is None:
        return _Result(
            _out("error", "Hire", "The company has no active HR agent to take the request.")
        )
    description = (
        f"Role template: {view.spec.slug} ({view.spec.title})\n"
        + (f"Reason: {reason}\n" if reason else "")
        + "Requested by the Founder through the Command Center; the new hire reports to the AI Co-founder "
        "unless the reason says otherwise.\nFollow the hiring-playbook skill; complete this task with a short summary."
    )
    task, _created = tasks.create_task(
        db,
        ctx,
        TaskCreate(
            title=f"Hire: {view.spec.title}"[:300],
            description=description,
            kind=TaskKind.HIRING.value,
            assignee_agent_id=hr.id,
            priority="high",
        ),
    )
    return _Result(
        _out(
            "text",
            "Hiring request sent",
            f"{hr.name} (HR) will hire a {view.spec.title}.",
            links=[_link("Task", f"task:{task.id}")],
        ),
        [{"type": "hire", "ref_id": task.id, "label": _clip(task.title, 60)}],
    )


def _cmd_approve(db, ctx, arg) -> _Result:
    parts = arg.strip().split(None, 1)
    if not parts:
        return _Result(_out("error", "Approve", "Usage: /approve <id> (see /approvals)"))
    approval = approvals.decide(db, ctx, parts[0], "approve", parts[1] if len(parts) > 1 else None)
    return _Result(
        _out("text", "Approved", f"“{_clip(approval.title, 100)}” is approved."),
        [{"type": "approval", "ref_id": approval.id, "label": _clip(approval.title, 60)}],
    )


def _cmd_reject(db, ctx, arg) -> _Result:
    parts = arg.strip().split(None, 1)
    if not parts:
        return _Result(_out("error", "Reject", "Usage: /reject <id> [note] (see /approvals)"))
    approval = approvals.decide(db, ctx, parts[0], "reject", parts[1] if len(parts) > 1 else None)
    return _Result(
        _out("text", "Rejected", f"“{_clip(approval.title, 100)}” is rejected."),
        [{"type": "approval", "ref_id": approval.id, "label": _clip(approval.title, 60)}],
    )


_HANDLERS = {
    "help": _cmd_help,
    "status": _cmd_status,
    "goals": _cmd_goals,
    "agents": _cmd_agents,
    "tasks": _cmd_tasks,
    "spend": _cmd_spend,
    "approvals": _cmd_approvals,
    "wallet": _cmd_wallet,
    "standards": _cmd_standards,
    "pause": _cmd_pause,
    "resume": _cmd_resume,
    "goal": _cmd_goal,
    "task": _cmd_task,
    "hire": _cmd_hire,
    "approve": _cmd_approve,
    "reject": _cmd_reject,
}
READ_COMMANDS = frozenset(
    {"help", "status", "goals", "agents", "tasks", "spend", "approvals", "wallet", "standards"}
)


def parse(text: str) -> tuple[str, str]:
    """``/tasks blocked`` -> ("tasks", "blocked")."""
    body = text.strip()[1:]
    name, _, arg = body.partition(" ")
    return name.strip().lower(), arg.strip()


def _run_command(db: Session, ctx: CompanyContext, text: str) -> _Result:
    name, arg = parse(text)
    handler = _HANDLERS.get(name)
    if handler is None:
        near = [c for c in COMMAND_NAMES if c.startswith(name[:2])] if name else []
        hint = f" Did you mean /{near[0]}?" if near else ""
        return _Result(
            _out("error", "Unknown command", f"/{name} is not a command.{hint} Try /help.")
        )
    try:
        return handler(db, ctx, arg)
    except AppError as exc:
        db.rollback()
        return _Result(_out("error", f"/{name} was not done", exc.message))
    except Exception:  # never leak a traceback into the feed
        db.rollback()
        logger.exception("command /%s failed", name)
        return _Result(
            _out("error", f"/{name} failed", "Something went wrong; nothing was changed.")
        )


# ---------------------------------------------------------------- exchanges


def _emit(db: Session, ex: CommandExchange) -> None:
    events.emit(
        db,
        ex.company_id,
        "command.updated",
        {"exchange_id": ex.id, "status": ex.status, "note_id": ex.note_id, "run_id": ex.run_id},
    )


def _for_note(db: Session, note_id: str) -> CommandExchange | None:
    return db.scalar(select(CommandExchange).where(CommandExchange.note_id == note_id))


def register_note(db: Session, note: FounderNote) -> CommandExchange:
    """Every Founder's note is an exchange (called by ``notes.create_note``). No commit."""
    ex = CommandExchange(
        company_id=note.company_id,
        user_id=note.author_user_id,
        input_kind="note",
        input=None,
        status=ExchangeStatus.PENDING,
        note_id=note.id,
        actions=[],
        attempts=1,
        last_attempt_at=utcnow(),
    )
    ex.created_at = note.created_at or utcnow()
    db.add(ex)
    db.flush()
    _emit(db, ex)
    return ex


def on_note_reopened(db: Session, note: FounderNote) -> None:
    """A human reply (or an explicit re-open) put the note back in the Co-founder's queue."""
    ex = _for_note(db, note.id)
    if ex is None:
        return
    ex.status = ExchangeStatus.PENDING
    ex.output = None
    ex.run_id = None
    ex.attempts = 1
    ex.last_attempt_at = utcnow()
    ex.updated_at = utcnow()
    _emit(db, ex)


def _note_answer(db: Session, note: FounderNote) -> tuple[dict[str, Any], list[dict]]:
    reply = db.scalar(
        select(NoteReply)
        .where(NoteReply.note_id == note.id, NoteReply.author_type == "agent")
        .order_by(NoteReply.created_at.desc(), NoteReply.id.desc())
        .limit(1)
    )
    triage = note.triage or {}
    actions = [
        {"type": a.get("type"), "ref_id": a.get("ref_id"), "label": a.get("label")}
        for a in (triage.get("actions") or [])
        if a.get("type") and a.get("ref_id")
    ]
    if reply is not None and reply.body.strip():
        text = reply.body.strip()
    elif triage.get("summary"):
        text = f"Handled as {triage.get('category', 'a note')}: {triage['summary']}"
    else:
        text = "The AI Co-founder has handled your note."
    return _out("text", "AI Co-founder", text[:4000]), actions


def _has_agent_reply(db: Session, note: FounderNote) -> bool:
    return (
        db.scalar(
            select(NoteReply.id)
            .where(NoteReply.note_id == note.id, NoteReply.author_type == "agent")
            .limit(1)
        )
        is not None
    )


def _mark_answered(db: Session, ex: CommandExchange, note: FounderNote, run_id: str | None) -> None:
    output, actions = _note_answer(db, note)
    ex.status = ExchangeStatus.ANSWERED
    ex.output = output
    ex.actions = actions
    if run_id and not ex.run_id:
        ex.run_id = run_id
    ex.updated_at = utcnow()
    _emit(db, ex)


def on_note_answered(db: Session, note: FounderNote, run_id: str | None = None) -> None:
    """The AI Co-founder replied to / triaged the note. A reply answers it; a triage alone (the model
    may still write its reply in the same run) only shows its action chips until the run ends.
    No commit."""
    ex = _for_note(db, note.id)
    if ex is None:
        return
    if _has_agent_reply(db, note) or ex.status != ExchangeStatus.PROCESSING:
        _mark_answered(db, ex, note, run_id)
        return
    _, actions = _note_answer(db, note)
    ex.actions = actions
    ex.updated_at = utcnow()
    _emit(db, ex)


def on_run_started(db: Session, run: TaskRun, agent: Agent) -> None:
    """The AI Co-founder's run started: the notes waiting for it are being processed. No commit."""
    if agent.kind != AgentKind.FOUNDER:
        return
    db.flush()
    rows = list(
        db.scalars(
            select(CommandExchange).where(
                CommandExchange.company_id == run.company_id,
                CommandExchange.input_kind == "note",
                CommandExchange.status == ExchangeStatus.PENDING,
            )
        )
    )
    for ex in rows:
        ex.status = ExchangeStatus.PROCESSING
        ex.run_id = run.id
        ex.updated_at = utcnow()
        _emit(db, ex)


def _fallback(reason: str, *, retrying: bool, attempt: int) -> dict[str, Any]:
    if retrying:
        return _out(
            "text",
            "No answer yet",
            f"The AI Co-founder could not answer yet: {reason}. Your note is saved; I am trying once more automatically.",
        )
    return _out(
        "error",
        "No answer",
        f"The AI Co-founder could not answer after {attempt} tries: {reason}. Your note is saved and "
        "nothing was lost. Use Retry, or check Settings > AI and the Activity feed.",
    )


def _fail_attempt(db: Session, ex: CommandExchange, reason: str, now: datetime) -> None:
    """The attempt produced no answer: write the fallback output; retry once, then give up. No commit."""
    reason = reason.strip().rstrip(".")[:300]
    if ex.attempts < MAX_ATTEMPTS:
        ex.output = _fallback(reason, retrying=True, attempt=ex.attempts)
        ex.attempts += 1
        ex.status = ExchangeStatus.PENDING
        ex.run_id = None
        ex.last_attempt_at = now
        wakes.wake_founder(
            db,
            ex.company_id,
            "command",
            "Retry: the Founder's last Command Center message was not answered. Call company_list_notes "
            "and answer it with company_reply_to_note.",
            ref_type="note",
            ref_id=ex.note_id,
            delay_seconds=wakes.COMMAND_DEBOUNCE_SECONDS,
        )
        audit.record(
            db,
            company_id=ex.company_id,
            principal=None,
            action="command.retry_scheduled",
            target_type="command",
            target_id=ex.id,
            summary="A Command Center message got no answer; retrying once",
            data={"reason": reason, "attempt": ex.attempts},
        )
    else:
        ex.output = _fallback(reason, retrying=False, attempt=ex.attempts)
        ex.status = ExchangeStatus.FAILED
        audit.record(
            db,
            company_id=ex.company_id,
            principal=None,
            action="command.failed",
            target_type="command",
            target_id=ex.id,
            summary="A Command Center message could not be answered",
            data={"reason": reason, "attempts": ex.attempts},
        )
    ex.updated_at = now
    _emit(db, ex)


_NOT_ITS_FAULT = (
    "company_paused",
    "worker_shutdown",
    "emergency_stop",
    "task_blocked",
    "task_cancelled",
)


def on_run_finished(
    db: Session, run: TaskRun, status: str, exit_reason: str, error: dict | None, now: datetime
) -> None:
    """A run ended: notes it was processing and did not answer get a fallback output. No commit."""
    db.flush()  # sessions do not autoflush: see the answers the run just wrote
    rows = list(
        db.scalars(
            select(CommandExchange).where(
                CommandExchange.run_id == run.id,
                CommandExchange.status == ExchangeStatus.PROCESSING,
            )
        )
    )
    for ex in rows:
        note = db.get(FounderNote, ex.note_id) if ex.note_id else None
        if note is not None and note.status == NoteStatus.TRIAGED:
            _mark_answered(db, ex, note, run.id)  # recorded and acted on; the summary is its answer
            continue
        if status == "cancelled" and exit_reason in _NOT_ITS_FAULT:
            ex.status = ExchangeStatus.PENDING  # the run is re-queued; no attempt is used up
            ex.run_id = None
            ex.updated_at = now
            _emit(db, ex)
            continue
        if status == "succeeded":
            reason = "it finished its run without replying to your message"
        elif status == "timed_out":
            reason = "its run took longer than the time limit"
        elif status == "cancelled":
            reason = "its run was cancelled"
        else:
            reason = "its run failed" + (
                f" ({_clip((error or {}).get('message'), 160)})" if error else ""
            )
        _fail_attempt(db, ex, reason, now)


def sweep(factory: sessionmaker[Session], now: datetime) -> int:
    """Scheduler step: a message nobody answered within 15 minutes gets the fallback output."""
    done = 0
    with factory() as db:
        due = list(
            db.scalars(
                select(CommandExchange).where(
                    CommandExchange.input_kind == "note",
                    CommandExchange.status.in_([ExchangeStatus.PENDING, ExchangeStatus.PROCESSING]),
                    CommandExchange.last_attempt_at <= now - ANSWER_DEADLINE,
                )
            )
        )
        for ex in due:
            company = db.get(Company, ex.company_id)
            if company is None or company.status != CompanyStatus.ACTIVE:
                continue  # a paused company answers nothing; the clock does not run against it
            if not ai_setup.is_ready(db, ex.company_id):
                continue
            note = db.get(FounderNote, ex.note_id) if ex.note_id else None
            if note is None or note.status != NoteStatus.NEW:
                continue
            _fail_attempt(db, ex, "nothing answered within 15 minutes", now)
            done += 1
        if done:
            db.commit()
    return done


# ---------------------------------------------------------------- owner API


def serialize(db: Session, rows: list[CommandExchange]) -> list[dict[str, Any]]:
    note_ids = [r.note_id for r in rows if r.note_id]
    notes_by_id: dict[str, FounderNote] = {}
    if note_ids:
        notes_by_id = {
            n.id: n for n in db.scalars(select(FounderNote).where(FounderNote.id.in_(note_ids)))
        }
    out = []
    for r in rows:
        note = notes_by_id.get(r.note_id or "")
        output = r.output
        actions = list(r.actions or [])
        if note is not None and output is None and r.status == ExchangeStatus.ANSWERED:
            output, derived = _note_answer(db, note)  # notes answered before the Command Center
            actions = actions or derived
        out.append(
            {
                "id": r.id,
                "input": (note.body if note is not None else r.input) or "",
                "input_kind": r.input_kind,
                "created_at": r.created_at,
                "status": r.status,
                "output": output,
                "note_id": r.note_id,
                "run_id": r.run_id,
                "actions": actions,
                "updated_at": r.updated_at,
            }
        )
    return out


def submit(db: Session, ctx: CompanyContext, text: str) -> dict[str, Any]:
    """``POST /command``: a slash command is answered now; free text becomes a Founder's note."""
    if not ctx.principal.is_human:
        raise Forbidden("The Command Center is for people; agents use the agent API")
    ai_setup.require_ready(db, ctx.company_id)
    text = text.strip()
    if text.startswith("/"):
        result = _run_command(db, ctx, text)
        status = ExchangeStatus.DONE
        ex = CommandExchange(
            company_id=ctx.company_id,
            user_id=ctx.principal.id,
            input_kind="command",
            input=text,
            status=status,
            output=result.output,
            actions=result.actions,
            attempts=1,
            last_attempt_at=utcnow(),
        )
        db.add(ex)
        db.flush()
        audit.record(
            db,
            company_id=ctx.company_id,
            principal=ctx.principal,
            action="command.run",
            target_type="command",
            target_id=ex.id,
            summary=f"Ran {text.split()[0][:40]} in the Command Center",
            data={"command": text.split()[0][:40], "ok": result.output.get("kind") != "error"},
        )
        _emit(db, ex)
        db.commit()
        return serialize(db, [ex])[0]
    note = notes.create_note(
        db,
        ctx,
        NoteCreate(body=text),
        delay_seconds=wakes.COMMAND_DEBOUNCE_SECONDS,
        trigger="command",
    )
    ex = _for_note(db, note.id)
    assert ex is not None
    return serialize(db, [ex])[0]


def list_feed(db: Session, ctx: CompanyContext, *, limit: int, cursor: str | None):
    if not ctx.principal.is_human:
        raise Forbidden("The Command Center is for people")
    stmt = select(CommandExchange).where(CommandExchange.company_id == ctx.company_id)
    rows, nxt = paginate(db, stmt, CommandExchange, limit=limit, cursor=cursor, descending=True)
    return serialize(db, rows), nxt


def retry(db: Session, ctx: CompanyContext, exchange_id: str) -> dict[str, Any]:
    if not ctx.principal.is_human:
        raise Forbidden("The Command Center is for people")
    require_human_admin(ctx, "retry a message")
    ex = db.scalar(
        select(CommandExchange).where(
            CommandExchange.id == exchange_id, CommandExchange.company_id == ctx.company_id
        )
    )
    if ex is None:
        raise NotFound("Not found")
    if ex.input_kind != "note":
        raise Conflict("Commands run instantly; type the command again")
    if ex.status != ExchangeStatus.FAILED:
        raise Conflict(f"Only a failed message can be retried (this one is {ex.status})")
    ai_setup.require_ready(db, ctx.company_id)
    note = db.get(FounderNote, ex.note_id) if ex.note_id else None
    if note is None:
        raise NotFound("Not found")
    now = utcnow()
    if note.status != NoteStatus.NEW:
        note.status = NoteStatus.NEW
        note.updated_at = now
    ex.status = ExchangeStatus.PENDING
    ex.output = None
    ex.run_id = None
    ex.attempts = 1
    ex.last_attempt_at = now
    ex.updated_at = now
    wakes.wake_founder(
        db,
        ctx.company_id,
        "command",
        "The Founder asked to retry a Command Center message nobody answered: call company_list_notes "
        "and answer it with company_reply_to_note.",
        ref_type="note",
        ref_id=note.id,
        delay_seconds=wakes.COMMAND_DEBOUNCE_SECONDS,
    )
    audit.record(
        db,
        company_id=ctx.company_id,
        principal=ctx.principal,
        action="command.retried",
        target_type="command",
        target_id=ex.id,
        summary="Retried a Command Center message",
        data={"note_id": note.id},
    )
    _emit(db, ex)
    db.commit()
    return serialize(db, [ex])[0]
