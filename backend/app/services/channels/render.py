"""Turn records into chat text (plain text; adapters escape for their platform). Everything that
leaves goes through ``secrets_scan.scrub``. Agent-written text is labelled with the agent's name and
rendered as text only: never as markup or links that could phish."""

from __future__ import annotations

from typing import Any

from app.models.governance import Approval
from app.models.questions import FounderQuestion
from app.services.channels.common import Button, OutMessage
from app.services.secrets_scan import scrub

MAX_BODY = 3500


def clean(text: str | None, limit: int = MAX_BODY) -> str:
    text = scrub(text or "") or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


MAX_REPLY = 12_000  # long answers are chunked per platform, not cut


def agent_text(agent_name: str | None, body: str) -> str:
    return f"{agent_name or 'An agent'} (AI):\n{clean(body, MAX_REPLY)}"


def render_output(output: dict[str, Any] | None) -> str:
    """A Command Center output (text / list / table / error) as plain text."""
    if not output:
        return "Done."
    parts: list[str] = []
    title = str(output.get("title") or "").strip()
    if title:
        parts.append(title)
    text = str(output.get("text") or "").strip()
    if text:
        parts.append(text)
    rows = output.get("rows") or []
    columns = output.get("columns") or []
    if rows:
        lines = []
        for row in rows[:25]:
            cells = [str(c) for c in (row if isinstance(row, list) else [row])]
            lines.append(" | ".join(c for c in cells if c) if len(columns) > 1 else cells[0])
        if len(rows) > 25:
            lines.append(f"… and {len(rows) - 25} more")
        parts.append("\n".join(lines))
    return clean("\n\n".join(parts)) or "Done."


# ------------------------------------------------------------------ founder questions


def wa_uses_buttons(q: FounderQuestion) -> bool:
    """WhatsApp allows 3 reply buttons: options + 'Use default' must fit, else a numbered list."""
    return 0 < len(q.options or []) + 1 <= 3


def question_message(q: FounderQuestion, agent_name: str | None, kind: str) -> OutMessage:
    options = [str(o) for o in (q.options or [])][:6]
    lines = [
        f"{agent_name or 'An agent'} asks (AI):",
        clean(q.question, 1000),
        "",
        f"If you do not answer, it will use: {clean(q.default_answer, 500)}",
    ]
    buttons: list[Button] = []
    numbered = kind == "whatsapp" and not wa_uses_buttons(q) and bool(options)
    if numbered:
        lines.append("")
        lines.append("Reply with a number (or reply with your own words):")
        lines.extend(f"{i + 1}. {clean(o, 120)}" for i, o in enumerate(options))
        lines.append("0. Use the default")
    else:
        for i, option in enumerate(options):
            buttons.append(Button(clean(option, 40) or f"Option {i + 1}", f"q:{q.id}:{i}"))
        buttons.append(Button("Use default", f"q:{q.id}:d"))
        lines.append("")
        lines.append("Tap an option, or reply to this message with your own answer.")
    msg = OutMessage("\n".join(lines), buttons)
    msg.ref_type, msg.ref_id = "question", q.id
    return msg


# ------------------------------------------------------------------ human work (M22)


def human_work_message(title: str, task: Any, base_url: str | None = None) -> OutMessage:
    """An AI employee needs a person: the title, what to do and when. No buttons: open Foundry to
    take it and to complete it with a comment and files."""
    lines = [clean(title, 300)]
    if task.human_instructions:
        lines += ["", clean(task.human_instructions, 1200)]
    if task.due_at is not None:
        lines += ["", f"Due: {task.due_at.strftime('%Y-%m-%d %H:%M')} UTC"]
    lines += [
        "",
        "Open Foundry to take it or to mark it done." + (f" {base_url}" if base_url else ""),
    ]
    msg = OutMessage("\n".join(lines))
    msg.ref_type, msg.ref_id = "human_work", task.id
    return msg


# ------------------------------------------------------------------ approvals


def approval_is_chat_decidable(a: Approval) -> bool:
    # Purchase checkout (card details, spending) stays in the web UI, and so does an outsourcing
    # approval (M22: outward-facing and money; the person reads the brief on the job's page).
    return a.subject_type not in ("purchase_request", "outsource_job") and a.approver == "human"


def approval_message(a: Approval, base_url: str | None = None) -> OutMessage:
    lines = ["Approval needed:", clean(a.title, 300)]
    if a.reason:
        lines += ["", clean(a.reason, 600)]
    facts = []
    if a.amount_micros:
        facts.append(f"Amount: ${a.amount_micros / 1_000_000:,.2f} (requested, not charged)")
    if a.vendor:
        facts.append(f"Vendor: {clean(a.vendor, 80)}")
    if a.risk:
        facts.append(f"Risk: {a.risk}")
    if facts:
        lines += ["", *facts]
    buttons: list[Button] = []
    if approval_is_chat_decidable(a):
        buttons = [Button("Approve", f"a:{a.id}:approve"), Button("Decline", f"a:{a.id}:decline")]
    else:
        lines += ["", "Open Foundry to decide this one." + (f" {base_url}" if base_url else "")]
    msg = OutMessage("\n".join(lines), buttons)
    msg.ref_type, msg.ref_id = "approval", a.id
    return msg
