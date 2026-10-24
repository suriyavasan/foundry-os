"""agents and skills."""

from __future__ import annotations

from typing import Annotated

import typer

from app.cli import render
from app.cli.client import CliError
from app.cli.commands.company import parse_usd
from app.cli.context import JsonOpt, command, state
from app.cli.render import short, usd
from app.cli.resolve import resolve

agents_app = typer.Typer(help="AI employees.", no_args_is_help=True)
skills_app = typer.Typer(help="The skill library.", no_args_is_help=True)


def _agent_cols():
    return [
        ("id", lambda a: a["id"][:8]),
        ("name", "name"),
        ("role", "role_title"),
        ("family", "role_family"),
        ("status", "status"),
        ("now", "live.state"),
        ("doing", lambda a: short((a.get("live") or {}).get("summary"), 40)),
    ]


def register(app: typer.Typer) -> None:
    app.add_typer(agents_app, name="agents")
    app.add_typer(skills_app, name="skills")

    # ------------------------------------------------------------------ agents

    @command(agents_app, "ls")
    def agents_ls(
        ctx: typer.Context,
        status: Annotated[
            str | None, typer.Option(help="proposed, active, paused, retired ...")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """List agents with what they are doing right now."""
        st = state(ctx, json_out)
        items = st.client().paged(st.cpath("/agents"), status=status)
        if st.json:
            render.print_json(items)
            return
        render.render_table(items, _agent_cols(), empty="No agents.")

    @command(agents_app, "show")
    def agents_show(
        ctx: typer.Context,
        agent: Annotated[str, typer.Argument(help="Name or id")],
        json_out: JsonOpt = False,
    ) -> None:
        """One agent: role, model, budgets, skills, current activity."""
        st = state(ctx, json_out)
        found = resolve(st, "agents", agent)
        skills = st.client().get(st.cpath(f"/agents/{found['id']}/skills"))
        data = {**found, "skills": [s["skill"]["slug"] for s in skills.get("items", [])]}
        if st.json:
            render.print_json(data)
            return
        live = found.get("live") or {}
        render.render_kv(
            {
                **found,
                "now": f"{live.get('state', '-')}: {live.get('summary') or ''}".strip(": "),
                "monthly": usd(found.get("monthly_budget_micros"))
                if found.get("monthly_budget_micros") is not None
                else "no limit",
                "daily": usd(found.get("daily_budget_micros"))
                if found.get("daily_budget_micros") is not None
                else "no limit",
                "skills": data["skills"],
                "model": f"{found.get('model_provider') or '-'} / {found.get('model_name') or '-'}",
            },
            [
                ("id", "id"),
                ("name", "name"),
                ("role", "role_title"),
                ("family", "role_family"),
                ("kind", "kind"),
                ("status", "status"),
                ("now", "now"),
                ("runtime", "runtime"),
                ("model", "model"),
                ("monthly budget", "monthly"),
                ("daily budget", "daily"),
                ("skills", "skills"),
                ("responsibilities", lambda a: short(a.get("responsibilities"), 90)),
            ],
            title=found["name"],
        )

    @command(agents_app, "hire")
    def agents_hire(
        ctx: typer.Context,
        template: Annotated[
            str,
            typer.Option(
                "--template", "-t", help="Role template slug (see: foundryos agents templates)"
            ),
        ],
        name: Annotated[str, typer.Option("--name", "-n", help="The new employee's name")],
        title: Annotated[
            str | None, typer.Option(help="Job title (default: the template's)")
        ] = None,
        manager: Annotated[str | None, typer.Option(help="Manager agent (name or id)")] = None,
        monthly_budget: Annotated[
            str | None, typer.Option("--monthly-budget", help="USD per month")
        ] = None,
        skill: Annotated[
            list[str] | None, typer.Option("--skill", help="Extra skill slug (repeatable)")
        ] = None,
        provider: Annotated[
            str | None, typer.Option(help="Model provider (name or id); default: the company's")
        ] = None,
        model: Annotated[str | None, typer.Option(help="Model name on that provider")] = None,
        no_activate: Annotated[
            bool, typer.Option("--no-activate", help="Leave the hire 'proposed'.")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Hire an AI employee from a role template."""
        st = state(ctx, json_out)
        client = st.client()
        templates = client.get(st.cpath("/role-templates"))
        known = {t["slug"]: t for t in templates.get("items", [])}
        if template not in known:
            raise CliError(
                f"Unknown template {template!r}. Available: {', '.join(sorted(known)) or 'none'}.",
                exit_code=5,
                code="not_found",
            )
        body: dict = {
            "name": name,
            "role_title": title or known[template]["title"],
            "role_template": template,
        }
        if manager:
            body["manager_agent_id"] = resolve(st, "agents", manager)["id"]
        if monthly_budget is not None:
            body["monthly_budget_micros"] = parse_usd(monthly_budget)
        if skill:
            body["skill_slugs"] = skill
        if provider:
            body["model_provider"] = resolve(st, "model-providers", provider, label="provider")[
                "id"
            ]
            body["runtime"] = "company_agent"
        if model:
            body["model_name"] = model
        agent = client.post(st.cpath("/agents"), body)
        if not no_activate:
            agent = client.post(st.cpath(f"/agents/{agent['id']}/activate"), {})
        if st.json:
            render.print_json(agent)
        else:
            render.say(f"Hired {agent['name']} ({agent['role_title']}), status {agent['status']}.")

    @command(agents_app, "templates")
    def agents_templates(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Role templates you can hire from."""
        st = state(ctx, json_out)
        items = st.client().get(st.cpath("/role-templates")).get("items", [])
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("slug", "slug"),
                ("family", "family"),
                ("title", "title"),
                ("skills", lambda t: len(t.get("default_skill_slugs") or [])),
            ],
            empty="No templates.",
        )

    for action, text in (
        ("pause", "Pause an agent (no new work)."),
        ("resume", "Resume a paused agent."),
        ("retire", "Retire an agent for good."),
    ):

        def make(action: str = action, text: str = text) -> None:
            @command(agents_app, action, help=text)
            def _lifecycle(
                ctx: typer.Context,
                agent: Annotated[str, typer.Argument(help="Name or id")],
                reason: Annotated[str | None, typer.Option(help="Why (audit log)")] = None,
                json_out: JsonOpt = False,
            ) -> None:
                st = state(ctx, json_out)
                found = resolve(st, "agents", agent)
                result = st.client().post(
                    st.cpath(f"/agents/{found['id']}/{action}"),
                    {"reason": reason} if reason else {},
                )
                if st.json:
                    render.print_json(result)
                else:
                    render.say(f"{result['name']} is now {result['status']}.")

        make()

    @command(agents_app, "skills")
    def agents_skills(
        ctx: typer.Context,
        agent: Annotated[str, typer.Argument(help="Name or id")],
        add: Annotated[
            list[str] | None, typer.Option("--add", help="Skill slug to assign (repeatable)")
        ] = None,
        remove: Annotated[
            list[str] | None, typer.Option("--remove", help="Skill slug to unassign (repeatable)")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Show an agent's skills; --add / --remove changes them."""
        st = state(ctx, json_out)
        client = st.client()
        found = resolve(st, "agents", agent)
        base = st.cpath(f"/agents/{found['id']}/skills")
        if add:
            client.post(base, {"slugs": add})
        for slug in remove or []:
            client.delete(f"{base}/{slug}")
        items = client.get(base).get("items", [])
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            [i["skill"] for i in items],
            [
                ("slug", "slug"),
                ("name", "name"),
                ("trust", "trust"),
                ("scan", "scan_verdict"),
                ("status", "status"),
            ],
            empty=f"{found['name']} has no skills.",
        )

    # ------------------------------------------------------------------ skills

    skill_cols = [
        ("slug", "slug"),
        ("name", "name"),
        ("source", "source_kind"),
        ("trust", "trust"),
        ("scan", "scan_verdict"),
        ("status", "status"),
    ]

    @command(skills_app, "ls")
    def skills_ls(
        ctx: typer.Context,
        status: Annotated[str | None, typer.Option(help="active, quarantined, archived")] = None,
        tag: Annotated[str | None, typer.Option()] = None,
        q: Annotated[str | None, typer.Option(help="Search text")] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Skills installed in this company."""
        st = state(ctx, json_out)
        items = st.client().paged(st.cpath("/skills"), status=status, tag=tag, q=q)
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items, skill_cols, empty="No skills installed. Try: foundryos skills search <topic>"
        )

    @command(skills_app, "search")
    def skills_search(
        ctx: typer.Context,
        query: Annotated[str, typer.Argument()],
        github: Annotated[
            bool, typer.Option("--github", help="Search GitHub (public repos).")
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Search the library and the Hermes catalog (add --github for GitHub)."""
        st = state(ctx, json_out)
        page = st.client().get(
            st.cpath("/skills/catalog"), q=query, source="github" if github else "all", limit=50
        )
        items = page.get("items", [])
        if st.json:
            render.print_json(items)
            return
        render.render_table(
            items,
            [
                ("slug", "slug"),
                ("type", "item_type"),
                ("source", "source"),
                ("installed", "installed"),
                ("description", lambda s: short(s.get("description"), 50)),
                ("install with", lambda s: s.get("source_ref")),
            ],
            empty=f"Nothing found for {query!r}.",
        )

    @command(skills_app, "install")
    def skills_install(
        ctx: typer.Context,
        source_ref: Annotated[
            str, typer.Argument(help="hermes:<slug>, github:<owner>/<repo>:<path> or a GitHub URL")
        ],
        slug: Annotated[str | None, typer.Option(help="Install under another slug")] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Install a skill (it is scanned first; unsafe ones are quarantined)."""
        st = state(ctx, json_out)
        body = {"source_ref": source_ref, **({"slug": slug} if slug else {})}
        result = st.client().post(st.cpath("/skills/install"), body)
        if st.json:
            render.print_json(result)
            return
        skill = result["skill"]
        msg = f"{skill['slug']}: {result['outcome']}"
        if result.get("quarantined"):
            msg += f" - QUARANTINED ({skill['scan_verdict']}); inspect with: foundryos skills show {skill['slug']}"
        render.say(msg, style="yellow" if result.get("quarantined") else None)

    @command(skills_app, "show")
    def skills_show(
        ctx: typer.Context,
        skill: Annotated[str, typer.Argument(help="Slug or id")],
        json_out: JsonOpt = False,
    ) -> None:
        """A skill's metadata, scan result and who uses it."""
        st = state(ctx, json_out)
        found = resolve(st, "skills", skill)
        detail = st.client().get(st.cpath(f"/skills/{found['id']}"))
        if st.json:
            render.print_json(detail)
            return
        scan = detail.get("scan_report") or {}
        render.render_kv(
            detail,
            [
                ("slug", "slug"),
                ("name", "name"),
                ("description", lambda s: short(s.get("description"), 100)),
                ("source", "source_ref"),
                ("license", "license"),
                ("trust", "trust"),
                (
                    "scan",
                    lambda s: f"{s.get('scan_verdict')} ({scan.get('finding_count', 0)} findings)",
                ),
                ("status", "status"),
                ("files", lambda s: len(s.get("files") or [])),
                ("used by", lambda s: [a["name"] for a in s.get("assigned_agents", [])]),
            ],
            title=detail["name"],
        )
        for f in (scan.get("findings") or [])[:10]:
            render.say(
                f"  ! {f.get('severity')}: {f.get('description')} ({f.get('file')}:{f.get('line')})",
                style="yellow",
            )
