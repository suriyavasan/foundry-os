"""status, note(s), goal, budget, policy, pause/resume/stop, reach."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Annotated

import typer

from app.cli import render, tunnel_flow
from app.cli.client import EXIT_UNREACHABLE, CliError
from app.cli.context import CliState, JsonOpt, command, guarded, state
from app.cli.render import short, usd
from app.cli.resolve import resolve

notes_app = typer.Typer(
    help="Founder's notes and the AI Co-founder's replies.", no_args_is_help=True
)
goal_app = typer.Typer(help="Company goals.", no_args_is_help=True)
company_app = typer.Typer(help="Companies on this install.", no_args_is_help=True)
budget_app = typer.Typer(help="Spending limits.", no_args_is_help=True)
policy_app = typer.Typer(help="Company policy.", no_args_is_help=True)
reach_app = typer.Typer(
    help="How to reach this install: local, local network, public URL.",
    invoke_without_command=True,
    no_args_is_help=False,
)
public_app = typer.Typer(
    help="A public URL: a free Cloudflare quick tunnel, a named tunnel, or Tailscale.",
    no_args_is_help=True,
)
reach_app.add_typer(public_app, name="public")

# `reach --watch` timing; tests shorten it and bound the loop.
watch_hooks = SimpleNamespace(interval=5.0, sleep=time.sleep, max_iterations=None)

INACTIVE = {"retired", "rejected"}


def _try(st: CliState, path: str, **params):
    """A best-effort read: a subsystem that errors must not hide the rest of the status."""
    try:
        return st.client().get(path, **params)
    except CliError as exc:
        if exc.exit_code in (3, 7):  # not signed in / unreachable: that is the answer
            raise
        return None


def parse_usd(text: str) -> int | None:
    """'12.5' -> 12_500_000 micro-USD; 'none' -> None (no limit)."""
    if text.strip().lower() in ("none", "off", "unlimited", "null"):
        return None
    try:
        value = Decimal(text.strip().lstrip("$"))
    except InvalidOperation as exc:
        raise CliError(
            f"{text!r} is not an amount in USD (e.g. 12.50 or none).", code="usage"
        ) from exc
    if value < 0:
        raise CliError("A budget cannot be negative.", code="usage")
    return int(value * 1_000_000)


def parse_value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def register(app: typer.Typer) -> None:
    app.add_typer(notes_app, name="notes")
    app.add_typer(company_app, name="company")
    app.add_typer(goal_app, name="goal")
    app.add_typer(budget_app, name="budget")
    app.add_typer(policy_app, name="policy")
    app.add_typer(reach_app, name="reach")

    # ------------------------------------------------------------------ status

    @command(app, "status")
    def status(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """The company at a glance: AI Co-founder, headcount, work, spend, alerts, reachability."""
        st = state(ctx, json_out)
        client = st.client()
        health = st.anonymous_client().get("/health")
        cid = st.company_id()
        base = f"/companies/{cid}"
        overview = client.get(f"{base}/overview")
        agents = client.paged(f"{base}/agents")
        goals = _try(st, f"{base}/goals", status="active", limit=20) or {"items": []}
        cofounder = _try(st, f"{base}/cofounder/summary")
        alerts = _try(st, f"{base}/alerts", status="open", limit=50) or {"items": []}
        reach = _try(st, "/reachability")
        system = _try(st, "/system/status")
        update = _try(st, "/system/update")

        live_agents = [a for a in agents if a["status"] not in INACTIVE]
        by_family = Counter(a.get("role_family") or "generic" for a in live_agents)
        by_state = Counter((a.get("live") or {}).get("state", "offline") for a in live_agents)
        founder = overview.get("founder")
        spend = overview.get("spending") or {}
        data = {
            "server": {
                "url": st.api_base(),
                "version": health.get("version"),
                "status": health["status"],
            },
            "company": overview["company"],
            "cofounder": {
                "name": (founder or {}).get("name"),
                "status": (founder or {}).get("status"),
                "state": ((founder or {}).get("live") or {}).get("state"),
                "summary": (cofounder or {}).get("plan", {}).get("summary") if cofounder else None,
                "wake_pending": (cofounder or {}).get("wake_pending") if cofounder else None,
            },
            "goals": [
                {"id": g["id"], "title": g["title"], "priority": g["priority"]}
                for g in goals["items"]
            ],
            "headcount": {
                "total": len(live_agents),
                "by_family": dict(by_family),
                "by_state": dict(by_state),
            },
            "tasks": overview.get("tasks"),
            "approvals": overview.get("approvals"),
            "spending": spend,
            "alerts_open": len(alerts["items"]),
            "reachability": {
                "mode": (reach or {}).get("mode"),
                "active_url": (reach or {}).get("active_url"),
                "public_url": _public_url(reach),
                "public_url_ephemeral": bool(((reach or {}).get("tunnel") or {}).get("ephemeral")),
                "tunnel_state": ((reach or {}).get("tunnel") or {}).get("state"),
            }
            if reach
            else None,
            "emergency_stop": (system or {}).get("emergency_stop"),
            "update": update,
            "agents": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "role": a["role_title"],
                    "status": a["status"],
                    "state": (a.get("live") or {}).get("state"),
                    "summary": (a.get("live") or {}).get("summary"),
                }
                for a in live_agents
            ],
        }
        if st.json:
            render.print_json(data)
            return
        c = data["company"]
        render.say(
            f"{c['name']}  [{c['status']}]   {data['server']['url']}  (v{data['server']['version']})",
            style="bold",
        )
        stop = data["emergency_stop"]
        if stop and stop.get("active"):
            render.say(
                f"EMERGENCY STOP is on: {stop.get('reason') or 'no reason given'}", style="bold red"
            )
        if update and update.get("update_available"):
            render.say(
                f"Update available: Foundry AI OS {update['latest_version']} "
                f"(you have {update['current_version']}). To update: {update['update_command']}",
                style="yellow",
            )
        r = data["reachability"]
        if r and r["public_url"]:
            render.say(
                f"Public URL: {r['public_url']}"
                + ("  (changes on restart)" if r["public_url_ephemeral"] else ""),
                style="bold",
            )
        elif r:
            render.say("Public URL: none  (turn one on: foundryos reach public start)", style="dim")
        cf = data["cofounder"]
        render.say(
            f"AI Co-founder: {cf['name'] or '-'} ({cf['status'] or '-'}, {cf['state'] or '-'})"
        )
        if cf["summary"]:
            render.say(f"  {short(cf['summary'], 110)}", style="dim")
        render.say("Goals: " + (", ".join(g["title"] for g in data["goals"]) or "none active"))
        hc = data["headcount"]
        render.say(
            f"Headcount: {hc['total']}  ("
            + ", ".join(f"{k} {v}" for k, v in sorted(hc["by_family"].items()))
            + ")  |  now: "
            + ", ".join(f"{k} {v}" for k, v in sorted(hc["by_state"].items()))
        )
        t = data["tasks"] or {}
        if t.get("available"):
            bs = t.get("by_status", {})
            render.say(
                f"Tasks: {t.get('open', 0)} open  (in progress {bs.get('in_progress', 0)}, "
                f"ready {bs.get('ready', 0)}, blocked {bs.get('blocked', 0)}, in review {bs.get('in_review', 0)}, "
                f"done {bs.get('done', 0)}, failed {bs.get('failed', 0)})"
            )
        a = data["approvals"] or {}
        if a.get("available"):
            render.say(f"Approvals waiting: {a.get('pending_total', 0)}")
        if spend.get("available"):
            for label in ("today", "month"):
                p = spend.get(label) or {}
                k = p.get("by_cost_kind") or {}
                limit = p.get("budget_micros")
                render.say(
                    f"Spend {label}: {usd(p.get('spent_micros'))}  "
                    f"(verified {usd(k.get('verified_micros'))}, estimated {usd(k.get('estimated_micros'))}, "
                    f"unknown {usd(k.get('unknown_micros'))})  limit {usd(limit) if limit is not None else 'none'}"
                )
        render.say(f"Open alerts: {data['alerts_open']}")
        if data["reachability"]:
            r = data["reachability"]
            shown = r["active_url"] if r["active_url"] != r["public_url"] else None
            render.say(f"Reachability: {r['mode']}" + (f"  {shown}" if shown else ""))

    @command(company_app, "ls")
    def company_ls(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Companies you belong to."""
        st = state(ctx, json_out)
        items = st.client().paged("/companies")
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda c: c["id"][:8]),
                ("name", "name"),
                ("status", "status"),
                ("mission", lambda c: short(c.get("mission"), 50)),
            ],
            empty='No company yet. Create one: foundryos company create "Name"',
        )

    @command(company_app, "create")
    def company_create(
        ctx: typer.Context,
        name: Annotated[str, typer.Argument()],
        mission: Annotated[str | None, typer.Option(help="What the company is for")] = None,
        idea: Annotated[
            str | None,
            typer.Option(
                help="One line (at most 280 characters); becomes the first Founder's note."
            ),
        ] = None,
        timezone: Annotated[
            str | None, typer.Option(help="IANA name, e.g. Europe/Berlin (default UTC).")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Create a company (with its AI Co-founder and HR agent)."""
        from app.cli import onboarding

        st = state(ctx, json_out)
        if idea is not None:
            onboarding.idea_error(idea)
        if timezone is not None:
            onboarding.timezone_error(timezone)
        body = {
            "name": name,
            **({"mission": mission} if mission else {}),
            **({"timezone": timezone} if timezone else {}),
        }
        client = st.client()
        created = client.post("/companies", body)
        idea_state = None
        if idea and idea.strip():
            try:
                idea_state = onboarding.seed_idea(client, created["id"], idea)
            except CliError as exc:
                idea_state = "failed"
                render.say(f"The idea could not be saved as a note: {exc.message}", err=True)
        if st.json:
            render.print_json({**created, "idea": idea_state} if idea else created)
        else:
            render.say(f"Company {created['name']} created ({created['id']}).")
            if idea_state == "waiting":
                render.say(
                    "Your idea becomes the first note once the model API is ready: "
                    'run `foundryos setup --only ai`, then `foundryos note "..."`.'
                )
            elif idea_state == "note":
                render.say("Saved your idea as the first Founder's note.")

    # ------------------------------------------------------------------ notes

    @command(app, "note")
    def note(
        ctx: typer.Context,
        text: Annotated[str, typer.Argument(help='The thought, or "-" to read it from stdin.')],
        json_out: JsonOpt = False,
    ) -> None:
        """Write a Founder's note. The AI Co-founder triages it and acts on it."""
        st = state(ctx, json_out)
        body = sys.stdin.read().strip() if text == "-" else text
        if not body:
            raise CliError("The note is empty.", code="usage")
        created = st.client().post(st.cpath("/notes"), {"body": body})
        if st.json:
            render.print_json(created)
        else:
            render.say(f"Note {created['id'][:8]} saved. The AI Co-founder will triage it.")

    @command(notes_app, "ls")
    def notes_ls(
        ctx: typer.Context,
        status: Annotated[str | None, typer.Option(help="new, triaged or archived")] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """List notes, newest first."""
        st = state(ctx, json_out)
        items = st.client().paged(st.cpath("/notes"), status=status)
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda n: n["id"][:8]),
                ("status", "status"),
                ("category", "triage.category"),
                ("replies", "reply_count"),
                ("note", lambda n: short(n["body"], 60)),
            ],
            empty='No notes yet. Add one with: foundryos note "..."',
        )

    @command(notes_app, "show")
    def notes_show(
        ctx: typer.Context, note_id: Annotated[str, typer.Argument()], json_out: JsonOpt = False
    ) -> None:
        """One note with its triage and the AI Co-founder's replies."""
        st = state(ctx, json_out)
        found = resolve(st, "notes", note_id)
        detail = st.client().get(st.cpath(f"/notes/{found['id']}"))
        if st.json:
            render.print_json(detail)
            return
        render.say(
            f"Note {detail['id'][:8]}  [{detail['status']}]  {detail['created_at']}", style="bold"
        )
        render.say(detail["body"])
        triage = detail.get("triage")
        if triage:
            render.say(f"\nTriage: {triage['category']}: {triage['summary']}", style="bold")
            for action in triage.get("actions", []):
                render.say(f"  - {action.get('type')}: {action.get('label')}")
        else:
            render.say("\nNot triaged yet.", style="dim")
        for reply in detail.get("replies", []):
            who = "AI" if reply["author_type"] == "agent" else "You"
            render.say(f"\n{who} ({reply['created_at']}):", style="bold")
            render.say(reply["body"])

    # ------------------------------------------------------------------ goals

    @command(goal_app, "ls")
    def goal_ls(
        ctx: typer.Context,
        status: Annotated[
            str | None, typer.Option(help="proposed, active, achieved or abandoned")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """List goals."""
        st = state(ctx, json_out)
        items = st.client().paged(st.cpath("/goals"), status=status)
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("id", lambda g: g["id"][:8]),
                ("status", "status"),
                ("priority", "priority"),
                ("title", "title"),
                ("target", "target_date"),
            ],
            empty='No goals yet. Add one with: foundryos goal add "..."',
        )

    @command(goal_app, "add")
    def goal_add(
        ctx: typer.Context,
        title: Annotated[str, typer.Argument()],
        description: Annotated[str | None, typer.Option("--description", "-d")] = None,
        priority: Annotated[str, typer.Option(help="low, normal, high or urgent")] = "normal",
        target: Annotated[str | None, typer.Option(help="Target date YYYY-MM-DD")] = None,
        draft: Annotated[
            bool, typer.Option("--draft", help="Create it as 'proposed' instead of active.")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Add a goal (active unless --draft)."""
        st = state(ctx, json_out)
        body = {"title": title, "priority": priority, "status": "proposed" if draft else "active"}
        if description:
            body["description"] = description
        if target:
            body["target_date"] = target
        created = st.client().post(st.cpath("/goals"), body)
        if st.json:
            render.print_json(created)
        else:
            render.say(f"Goal {created['id'][:8]} created ({created['status']}).")

    # ------------------------------------------------------------------ budget & policy

    @command(budget_app, "show")
    def budget_show(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Company and agent budgets against what has been spent."""
        st = state(ctx, json_out)
        client = st.client()
        policy = client.get(st.cpath("/policy"))
        summary = _try(st, st.cpath("/finance/summary"))
        data = {
            "company": {
                "daily_budget_micros": policy.get("daily_budget_micros"),
                "monthly_budget_micros": policy.get("monthly_budget_micros"),
                "max_agent_monthly_budget_micros": policy.get("max_agent_monthly_budget_micros"),
                "hire_default_monthly_budget_micros": policy.get(
                    "hire_default_monthly_budget_micros"
                ),
            },
            "budgets": (summary or {}).get("budgets", []),
            "totals": (summary or {}).get("totals"),
        }
        if st.json:
            render.print_json(data)
            return
        c = data["company"]
        render.render_kv(
            {k: (usd(v) if v is not None else "no limit") for k, v in c.items()},
            [(k.replace("_micros", "").replace("_", " "), k) for k in c],
            title="Company limits",
        )
        if data["totals"]:
            t = data["totals"]
            render.say(
                f"\nThis period: verified {usd(t.get('verified_micros'))}, "
                f"estimated {usd(t.get('estimated_micros'))}, unknown {usd(t.get('unknown_micros'))}"
            )
        if data["budgets"]:
            render.render_table(
                data["budgets"],
                [
                    ("scope", "scope"),
                    ("agent", lambda b: (b.get("agent_id") or "-")[:8]),
                    ("period", "period"),
                    ("limit", lambda b: usd(b.get("limit_micros"))),
                    ("verified", lambda b: usd(b.get("verified_micros"))),
                    ("estimated", lambda b: usd(b.get("estimated_micros"))),
                ],
                empty="",
            )

    @command(budget_app, "set")
    def budget_set(
        ctx: typer.Context,
        daily: Annotated[str | None, typer.Option(help="USD per day, or 'none'")] = None,
        monthly: Annotated[str | None, typer.Option(help="USD per month, or 'none'")] = None,
        agent: Annotated[
            str | None, typer.Option(help="Set this agent's budget instead of the company's.")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Set daily/monthly limits in USD (company-wide, or for one agent)."""
        st = state(ctx, json_out)
        if daily is None and monthly is None:
            raise CliError("Give --daily and/or --monthly (USD, or 'none').", code="usage")
        body: dict = {}
        if daily is not None:
            body["daily_budget_micros"] = parse_usd(daily)
        if monthly is not None:
            body["monthly_budget_micros"] = parse_usd(monthly)
        if agent:
            target = resolve(st, "agents", agent)
            result = st.client().patch(st.cpath(f"/agents/{target['id']}"), body)
        else:
            result = st.client().patch(st.cpath("/policy"), body)
        if st.json:
            render.print_json(result)
        else:
            who = f"agent {target['name']}" if agent else "company"
            render.say(
                f"Budget for {who}: daily {usd(result.get('daily_budget_micros')) if result.get('daily_budget_micros') is not None else 'none'}, "
                f"monthly {usd(result.get('monthly_budget_micros')) if result.get('monthly_budget_micros') is not None else 'none'}."
            )

    @command(policy_app, "show")
    def policy_show(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Every policy setting."""
        st = state(ctx, json_out)
        policy = st.client().get(st.cpath("/policy"))
        if st.json:
            render.print_json(policy)
            return
        skip = {"id", "company_id", "created_at", "updated_at"}
        render.render_table(
            [{"key": k, "value": v} for k, v in sorted(policy.items()) if k not in skip],
            [
                ("setting", "key"),
                (
                    "value",
                    lambda r: (
                        json.dumps(r["value"])
                        if isinstance(r["value"], (list, dict))
                        else r["value"]
                    ),
                ),
            ],
            empty="No policy.",
        )

    @command(policy_app, "set")
    def policy_set(
        ctx: typer.Context,
        assignments: Annotated[list[str], typer.Argument(help="key=value, e.g. max_headcount=20")],
        json_out: JsonOpt = False,
    ) -> None:
        """Change settings: values are JSON (true, 20, null, ["a"]) or plain text. Money is *_micros."""
        st = state(ctx, json_out)
        body = {}
        for item in assignments:
            if "=" not in item:
                raise CliError(f"{item!r} is not key=value.", code="usage")
            key, _, value = item.partition("=")
            body[key.strip()] = parse_value(value.strip())
        result = st.client().patch(st.cpath("/policy"), body)
        if st.json:
            render.print_json(result)
        else:
            render.say("Policy updated: " + ", ".join(sorted(body)))

    # ------------------------------------------------------------------ kill switches

    @command(app, "pause")
    def pause(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Pause the company: no dispatch, running work is cancelled and re-queued."""
        st = state(ctx, json_out)
        result = st.client().post(st.cpath("/pause"))
        if st.json:
            render.print_json(result)
        else:
            render.say(f"{result['name']} is paused. Resume with: foundryos resume")

    @command(app, "resume")
    def resume(
        ctx: typer.Context,
        all_: Annotated[
            bool, typer.Option("--all", help="Lift the instance-wide emergency stop instead.")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Resume the company (or, with --all, lift the global emergency stop)."""
        st = state(ctx, json_out)
        if all_:
            result = st.client().post("/system/resume")
            msg = "Emergency stop lifted."
        else:
            result = st.client().post(st.cpath("/resume"))
            msg = f"{result['name']} resumed."
        if st.json:
            render.print_json(result)
        else:
            render.say(msg)

    @command(app, "stop")
    def stop(
        ctx: typer.Context,
        all_: Annotated[
            bool,
            typer.Option(
                "--all", help="EMERGENCY stop: cancel every company's runs on this install (owner)."
            ),
        ] = False,
        reason: Annotated[
            str | None, typer.Option(help="Why (with --all; kept in the audit log).")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Stop Foundry AI OS (the background copy). With --all: the instance-wide emergency stop."""
        st = state(ctx, json_out)
        if not all_:
            if reason:
                raise CliError(
                    "--reason belongs to the emergency stop: use `foundryos stop --all --reason ...`. "
                    "(`foundryos stop` alone stops the program; to pause one company use "
                    "`foundryos pause`.)",
                    code="usage",
                )
            from app.cli.commands.lifecycle import stop_program

            stop_program()
            return
        result = st.client().post("/system/emergency-stop", {"reason": reason} if reason else {})
        if st.json:
            render.print_json(result)
        else:
            render.say(
                f"Emergency stop is ON ({result.get('runs_cancelled') or 0} run(s) cancelled). "
                "Lift it with: foundryos resume --all",
                style="bold red",
            )

    # ------------------------------------------------------------------ reachability

    @reach_app.callback(invoke_without_command=True)
    @guarded
    def reach(
        ctx: typer.Context,
        qr: Annotated[
            bool, typer.Option("--qr/--no-qr", help="Draw a QR code for the LAN address.")
        ] = True,
        watch: Annotated[
            bool,
            typer.Option(
                "--watch",
                help="Keep running and print the public URL each time it changes (Ctrl-C ends).",
            ),
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """How to reach this install: local, local network, public URL."""
        if ctx.invoked_subcommand is not None:
            state(ctx, json_out)
            return
        st = state(ctx, json_out)
        if watch:
            _watch_public_url(st)
            return
        r = st.client().get("/reachability")
        if st.json:
            render.print_json(r)
            return
        render.say(
            f"Mode: {r['mode']}   device: {r['device_name']}   sign-in: {r['auth_mode']}",
            style="bold",
        )
        for url in r.get("local_urls", []):
            render.say(f"  local   {url}")
        lan = r.get("lan") or {}
        lan_urls = [a["url"] for a in lan.get("addresses", []) if a.get("url")]
        if lan.get("mdns_url"):
            lan_urls.insert(0, lan["mdns_url"])
        if lan.get("enabled"):
            for url in lan_urls:
                render.say(f"  network {url}")
        elif lan.get("requested"):
            render.say("  network: switched on, restart `foundryos up` to apply.", style="yellow")
        else:
            render.say("  network: off (enable it in the web app: Reachability)", style="dim")
        t = r.get("tunnel") or {}
        if t.get("url"):
            note = "  changes on restart" if t.get("ephemeral") else ""
            render.say(f"  public  {t['url']}  ({t['state']}{note})")
        else:
            render.say(f"  public: no tunnel ({t.get('state', 'stopped')})", style="dim")
            if t.get("last_url"):
                render.say(f"          last URL was {t['last_url']}", style="dim")
        if r.get("public_exposure_blocked_reason"):
            render.say(f"  exposure blocked: {r['public_exposure_blocked_reason']}", style="yellow")
        if qr and lan.get("enabled") and lan_urls:
            try:
                import segno

                segno.make(lan_urls[0]).terminal(compact=True)
            except Exception:  # a missing QR must never fail the command
                pass

    @command(public_app, "start")
    def public_start(
        ctx: typer.Context,
        kind: Annotated[
            str,
            typer.Argument(
                metavar="[quick|named|tailscale]",
                help="quick: free random *.trycloudflare.com address, no account (default). "
                "named: your own hostname on a connected Cloudflare account (--hostname). "
                "tailscale: Tailscale Funnel.",
            ),
        ] = "quick",
        hostname: Annotated[
            str | None, typer.Option("--hostname", help="For `named`: e.g. app.example.com.")
        ] = None,
        yes: Annotated[
            bool,
            typer.Option("--yes", "-y", help="Download cloudflared without asking (it is shown)."),
        ] = False,
        timeout: Annotated[float, typer.Option(help="Seconds to wait for the URL.")] = 60.0,
        json_out: JsonOpt = False,
    ) -> None:
        """Turn on a public URL (and download cloudflared first, if you agree)."""
        st = state(ctx, json_out)
        provider = tunnel_flow.provider_kind(kind)
        if provider is None:
            raise CliError(
                f"Unknown provider {kind!r}: use quick, named or tailscale.", code="usage"
            )
        if provider == "cloudflare_named" and not hostname:
            raise CliError(
                "`named` needs --hostname, e.g. --hostname app.example.com", code="usage"
            )

        def say(text: str) -> None:
            render.say(text, err=st.json)

        def confirm(plan: dict) -> bool:
            size = plan.get("size_bytes")
            say("cloudflared is not installed. The official release would be downloaded:")
            say(f"  version  {plan.get('version')}")
            say(f"  from     {plan.get('url')}")
            say(f"  sha256   {plan.get('sha256')}  (checked after the download)")
            if size:
                say(f"  size     {size / 1_000_000:.1f} MB")
            if yes:
                return True
            return typer.confirm("Download and install it?", default=False, err=st.json)

        url = tunnel_flow.enable_public_url(
            st, provider=provider, hostname=hostname, confirm=confirm, say=say, timeout=timeout
        )
        if st.json:
            render.print_json({"url": url, "running": url is not None})
        elif url:
            render.say(f"Public URL: {url}", style="bold green")
            if provider == "cloudflare_quick":
                render.say(
                    "It changes every time the tunnel restarts. Follow it with: "
                    "foundryos reach --watch   (also saved in <data>/public-url.json)",
                    style="dim",
                )
        if url is None:
            raise typer.Exit(1)

    @command(public_app, "stop")
    def public_stop(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Turn the public URL off (it stays off after a restart)."""
        st = state(ctx, json_out)
        result = st.client().post("/reachability/tunnel/stop")
        if st.json:
            render.print_json(result)
        else:
            render.say("The public URL is off.")


def _public_url(reach: dict | None) -> str | None:
    tunnel = (reach or {}).get("tunnel") or {}
    return tunnel.get("url") if tunnel.get("state") == "running" else None


def _watch_public_url(st: CliState) -> None:
    """Print the public URL now and every time it changes, until Ctrl-C. A restart of the server
    (which is when a quick-tunnel URL changes) is waited out, not an error."""
    hooks = watch_hooks
    last: tuple | None = None
    last_url: str | None = None
    waiting = False
    polls = 0
    try:
        while True:
            try:
                tunnel = st.client().get("/reachability").get("tunnel") or {}
                waiting = False
            except CliError as exc:
                if exc.exit_code != EXIT_UNREACHABLE:
                    raise
                if not waiting:
                    render.say(
                        "The server is not answering; waiting for it...", style="dim", err=True
                    )
                waiting = True
                tunnel = None
            if tunnel is not None:
                url = _public_url({"tunnel": tunnel})
                key = (tunnel.get("state"), url)
                if key != last:
                    last = key
                    _print_watch(st, tunnel, url, last_url)
                    last_url = url or last_url
            polls += 1
            if hooks.max_iterations is not None and polls >= hooks.max_iterations:
                return
            hooks.sleep(hooks.interval)
    except KeyboardInterrupt:
        render.say("", err=True)


def _print_watch(st: CliState, tunnel: dict, url: str | None, previous: str | None) -> None:
    state_name = tunnel.get("state")
    if st.json:
        render.print_json({"state": state_name, "url": url, "previous_url": previous})
    elif url and previous and url != previous:
        render.say(f"Public URL changed: {url}   (was {previous})", style="bold")
    elif url:
        render.say(f"Public URL: {url}", style="bold")
    else:
        extra = f"  ({tunnel['error']})" if tunnel.get("error") else ""
        render.say(f"Public URL: none ({state_name}){extra}", style="dim")
