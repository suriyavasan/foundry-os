"""tasks, runs tail, approvals."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.text import Text

from app.cli import render
from app.cli.client import CliError
from app.cli.commands.company import parse_usd
from app.cli.context import CliState, JsonOpt, command, state
from app.cli.render import console, short, usd
from app.cli.resolve import resolve

tasks_app = typer.Typer(help="Tasks.", no_args_is_help=True)
runs_app = typer.Typer(help="Agent runs.", no_args_is_help=True)
approvals_app = typer.Typer(help="Approvals waiting for a decision.", no_args_is_help=True)


def event_summary(event: dict) -> str:
    payload = event.get("payload") or {}
    if payload.get("name"):  # tool.started / tool.completed
        detail = payload.get("args_preview") or payload.get("result_preview") or ""
        flag = " (error)" if payload.get("is_error") else ""
        return f"{payload['name']}{flag} {detail}".strip().replace("\n", " ")
    if "input_tokens" in payload:
        billing = payload.get("billing") or {}
        return f"in {payload.get('input_tokens')} out {payload.get('output_tokens')} tokens" + (
            f", {billing.get('cost_kind')} {billing.get('cost_micros')} micro-USD"
            if billing
            else ""
        )
    for key in ("summary", "text", "message", "content", "tool", "status", "reason"):
        value = payload.get(key)
        if value:
            label = f"{payload.get('tool')}: " if key != "tool" and payload.get("tool") else ""
            return label + str(value).replace("\n", " ")
    return ""


_STYLES = {
    "error": "red",
    "failed": "red",
    "tool": "cyan",
    "usage": "dim",
    "finished": "green",
    "done": "green",
}


def print_event(stamp: str, kind: str, text: str) -> None:
    style = next((s for key, s in _STYLES.items() if key in kind), "")
    line = Text(f"{stamp}  ", style="dim")
    line.append(f"{kind:<14}", style=style or "bold")
    line.append(" " + short(text, 140))
    console().print(line)


def _clock(ts: str | None) -> str:
    return (ts or "")[11:19] or "--:--:--"


def find_run(st: CliState, run: str | None, task: str | None, agent: str | None) -> dict | None:
    client = st.client()
    if run:
        return client.get(st.cpath(f"/runs/{run}"))
    if task:
        found = resolve(st, "tasks", task)
        runs = client.get(st.cpath(f"/tasks/{found['id']}/runs"), limit=1).get("items", [])
        return runs[0] if runs else None
    if agent:
        found = resolve(st, "agents", agent)
        current = ((found.get("live") or {}).get("current_run") or {}).get("id")
        if current:
            return client.get(st.cpath(f"/runs/{current}"))
        tasks = client.get(st.cpath("/tasks"), assignee_agent_id=found["id"], limit=5).get(
            "items", []
        )
        for t in tasks:
            runs = client.get(st.cpath(f"/tasks/{t['id']}/runs"), limit=1).get("items", [])
            if runs:
                return runs[0]
    return None


def register(app: typer.Typer) -> None:
    app.add_typer(tasks_app, name="tasks")
    app.add_typer(runs_app, name="runs")
    app.add_typer(approvals_app, name="approvals")

    # ------------------------------------------------------------------ tasks

    @command(tasks_app, "ls")
    def tasks_ls(
        ctx: typer.Context,
        status: Annotated[
            str | None,
            typer.Option(
                help="backlog, ready, in_progress, in_review, blocked, done, failed, cancelled"
            ),
        ] = None,
        assignee: Annotated[str | None, typer.Option(help="Agent name or id")] = None,
        project: Annotated[str | None, typer.Option(help="Project id")] = None,
        limit: Annotated[int, typer.Option(min=1, max=200)] = 50,
        json_out: JsonOpt = False,
    ) -> None:
        """Tasks, newest first."""
        st = state(ctx, json_out)
        params = {"status": status, "project_id": project, "limit": limit}
        if assignee:
            params["assignee_agent_id"] = resolve(st, "agents", assignee)["id"]
        page = st.client().get(st.cpath("/tasks"), **params)
        items = page.get("items", [])
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda t: t["id"][:8]),
                ("status", "status"),
                ("pri", "priority"),
                (
                    "assignee",
                    lambda t: (t.get("assignee_agent_id") or t.get("assignee_ref") or "-")[:14],
                ),
                ("title", lambda t: short(t["title"], 60)),
            ],
            empty="No tasks match.",
        )

    @command(tasks_app, "show")
    def tasks_show(
        ctx: typer.Context,
        task: Annotated[str, typer.Argument(help="Task id (or its first characters)")],
        json_out: JsonOpt = False,
    ) -> None:
        """A task, its result and its runs."""
        st = state(ctx, json_out)
        found = resolve(st, "tasks", task)
        runs = st.client().get(st.cpath(f"/tasks/{found['id']}/runs"), limit=10).get("items", [])
        data = {**found, "runs": runs}
        if st.json:
            render.print_json(data)
            return
        render.render_kv(
            found,
            [
                ("id", "id"),
                ("title", "title"),
                ("status", "status"),
                ("priority", "priority"),
                ("kind", "kind"),
                ("assignee", lambda t: t.get("assignee_agent_id") or t.get("assignee_ref")),
                ("attempts", "attempt_count"),
                ("result", lambda t: short(t.get("result_summary"), 100)),
                ("error", lambda t: (t.get("error") or {}).get("message")),
            ],
            title=found["title"],
        )
        render.render_table(
            runs,
            [
                ("run", lambda r: r["id"][:8]),
                ("#", "attempt_number"),
                ("status", "status"),
                ("exit", "exit_reason"),
                ("cost", lambda r: f"{usd(r.get('cost_micros'))} ({r.get('cost_kind')})"),
                ("started", lambda r: _clock(r.get("started_at"))),
            ],
            title="Runs",
            empty="No runs yet.",
        )

    @command(tasks_app, "create")
    def tasks_create(
        ctx: typer.Context,
        title: Annotated[str, typer.Argument()],
        description: Annotated[str | None, typer.Option("--description", "-d")] = None,
        assignee: Annotated[str | None, typer.Option(help="Agent name or id")] = None,
        priority: Annotated[str, typer.Option(help="low, normal, high or urgent")] = "normal",
        criteria: Annotated[
            str | None, typer.Option(help="Acceptance criteria (enables review)")
        ] = None,
        project: Annotated[str | None, typer.Option(help="Project id")] = None,
        budget: Annotated[str | None, typer.Option(help="Task budget in USD")] = None,
        depends_on: Annotated[
            list[str] | None, typer.Option("--depends-on", help="Task id (repeatable)")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Create a task."""
        st = state(ctx, json_out)
        body: dict = {"title": title, "priority": priority}
        if description:
            body["description"] = description
        if criteria:
            body["acceptance_criteria"] = criteria
        if project:
            body["project_id"] = project
        if budget is not None:
            body["budget_micros"] = parse_usd(budget)
        if assignee:
            body["assignee_agent_id"] = resolve(st, "agents", assignee)["id"]
        if depends_on:
            body["depends_on"] = [resolve(st, "tasks", t)["id"] for t in depends_on]
        created = st.client().post(st.cpath("/tasks"), body)
        if st.json:
            render.print_json(created)
        else:
            render.say(f"Task {created['id'][:8]} created ({created['status']}).")

    @command(tasks_app, "cancel")
    def tasks_cancel(
        ctx: typer.Context,
        task: Annotated[str, typer.Argument()],
        reason: Annotated[str | None, typer.Option()] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Cancel a task (and its live runs)."""
        st = state(ctx, json_out)
        found = resolve(st, "tasks", task)
        result = st.client().post(
            st.cpath(f"/tasks/{found['id']}/cancel"), {"reason": reason} if reason else {}
        )
        if st.json:
            render.print_json(result)
        else:
            render.say(f"Task {result['id'][:8]} is {result['status']}.")

    @command(tasks_app, "retry")
    def tasks_retry(
        ctx: typer.Context, task: Annotated[str, typer.Argument()], json_out: JsonOpt = False
    ) -> None:
        """Retry a failed task."""
        st = state(ctx, json_out)
        found = resolve(st, "tasks", task)
        result = st.client().post(st.cpath(f"/tasks/{found['id']}/retry"))
        if st.json:
            render.print_json(result)
        else:
            render.say(f"Task {result['id'][:8]} is back in the queue ({result['status']}).")

    # ------------------------------------------------------------------ runs

    @command(runs_app, "tail")
    def runs_tail(
        ctx: typer.Context,
        agent: Annotated[str | None, typer.Option(help="Agent name or id")] = None,
        task: Annotated[str | None, typer.Option(help="Task id")] = None,
        run: Annotated[str | None, typer.Option(help="Run id")] = None,
        follow: Annotated[
            bool, typer.Option("--follow", "-f", help="Keep streaming live events.")
        ] = False,
        lines: Annotated[int, typer.Option("--lines", "-n", min=1, max=500)] = 30,
        json_out: JsonOpt = False,
    ) -> None:
        """Show (and with --follow, stream) the events of a run."""
        st = state(ctx, json_out)
        client = st.client()
        agent_id = resolve(st, "agents", agent)["id"] if agent else None
        target = find_run(st, run, task, agent) if (run or task or agent) else None
        if not target and not follow:
            raise CliError(
                "No run to show. Give --agent, --task or --run (or use --follow to watch everything)."
                if not (run or task or agent)
                else "No runs found for that selection yet.",
                exit_code=5,
                code="not_found",
            )
        printed = 0
        last_seq = 0
        if target:
            events: list = []
            cursor = 0
            while True:
                page = client.get(
                    st.cpath(f"/runs/{target['id']}/events"), after_seq=cursor, limit=200
                )
                events.extend(page.get("items", []))
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            events = events[-lines:]
            for ev in events:
                last_seq = max(last_seq, ev["seq"])
                if st.json:
                    render.print_json({"source": "history", **ev})
                else:
                    print_event(_clock(ev.get("occurred_at")), ev["type"], event_summary(ev))
                printed += 1
            if not st.json and not events:
                render.say(
                    f"Run {target['id'][:8]} ({target['status']}) has no events yet.", style="dim"
                )
        if not follow:
            return
        done_states = {"succeeded", "failed", "cancelled", "timed_out"}
        if target and target["status"] in done_states:
            return
        if not st.json:
            render.say("-- following (Ctrl-C to stop) --", style="dim")
        try:
            for event, data in client.sse("/companies/" + st.company_id() + "/stream"):
                p = data.get("payload") or {}
                if event not in ("run.event", "run.started", "run.finished"):
                    continue
                if target and p.get("run_id") != target["id"]:
                    continue
                if agent_id and p.get("agent_id") != agent_id:
                    continue
                if event == "run.event" and p.get("seq", 0) and p["seq"] <= last_seq and target:
                    continue
                if st.json:
                    render.print_json({"source": "live", "event": event, **data})
                else:
                    text = (
                        event_summary({"payload": p})
                        if event == "run.event"
                        else p.get("status") or ""
                    )
                    kind = p.get("type") if event == "run.event" else event
                    print_event(_clock(data.get("created_at")), str(kind), str(text))
                if event == "run.finished" and target:
                    break
        except KeyboardInterrupt:
            pass

    # ------------------------------------------------------------------ approvals

    @command(approvals_app, "ls")
    def approvals_ls(
        ctx: typer.Context,
        status: Annotated[
            str,
            typer.Option(
                help="pending (default), approved, rejected, changes_requested, expired, or all"
            ),
        ] = "pending",
        json_out: JsonOpt = False,
    ) -> None:
        """Approvals, newest first (pending by default)."""
        st = state(ctx, json_out)
        items = st.client().paged(
            st.cpath("/approvals"), status=None if status == "all" else status
        )
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda a: a["id"][:8]),
                ("status", "status"),
                ("approver", "approver"),
                ("kind", "kind"),
                ("risk", "risk"),
                (
                    "amount",
                    lambda a: (
                        usd(a["amount_micros"]) if a.get("amount_micros") is not None else None
                    ),
                ),
                ("title", lambda a: short(a["title"], 55)),
            ],
            empty="Nothing is waiting for you.",
        )

    def _decide(
        ctx: typer.Context, approval: str, action: str, note: str | None, json_out: bool
    ) -> None:
        st = state(ctx, json_out)
        found = resolve(st, "approvals", approval)
        result = st.client().post(
            st.cpath(f"/approvals/{found['id']}/{action}"), {"note": note} if note else {}
        )
        if st.json:
            render.print_json(result)
        else:
            render.say(f"Approval {result['id'][:8]} is {result['status']}.")

    @command(approvals_app, "approve")
    def approvals_approve(
        ctx: typer.Context,
        approval: Annotated[str, typer.Argument()],
        note: Annotated[str | None, typer.Option()] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Approve (one action only: the approval is consumed when used)."""
        _decide(ctx, approval, "approve", note, json_out)

    @command(approvals_app, "reject")
    def approvals_reject(
        ctx: typer.Context,
        approval: Annotated[str, typer.Argument()],
        note: Annotated[str | None, typer.Option()] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Reject."""
        _decide(ctx, approval, "reject", note, json_out)
