"""``foundryos repo``: GitHub-backed projects from a terminal (milestone M14).

Everything goes over HTTP to the owner API (``/api/companies/{id}/repositories`` and
``/github/inspect``), like every other command (docs/architecture.md D23). The GitHub token comes only
from a hidden prompt or ``--secrets-stdin`` (``github_token=...``): never argv, never printed.

The functions at the top are shared with the setup wizard (``foundryos setup``, the ``repo`` step).
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable
from typing import Annotated, Any

import typer

from app.cli import onboarding
from app.cli import render as render_mod
from app.cli.client import EXIT_NOT_FOUND, ApiClient, CliError
from app.cli.context import CliState, JsonOpt, command, state
from app.cli.onboarding import (
    GITHUB_CI_FLAGS,
    GITHUB_ISSUES,
    GITHUB_MERGE_FLAGS,
    GITHUB_VISIBILITY,
    usage,
)
from app.cli.render import short

Ref = Annotated[str | None, typer.Argument(help="Id, owner/name or project")]
repo_app = typer.Typer(help="GitHub repositories of your projects.", no_args_is_help=True)

# Replaced in tests.
sleep: Callable[[float], None] = time.sleep

POLL_SECONDS = 2.0
WAIT_SECONDS = 660.0  # the server gives a clone 600 s
FAILED_STATES = ("error", "auth_failed", "disconnected", "paused")

# What a person should do about the refusals they can fix. The server's message comes first.
HINTS = {
    "no_account": "Connect a GitHub token first (foundryos repo connect asks for it).",
    "confirm_public_required": (
        "A public repository makes everything your agents commit public. "
        "Confirm with --github-public-ok (setup) or --public-ok (repo connect)."
    ),
    "repository_exists": (
        "That repository already exists. Choose another name, or connect it as an existing repository."
    ),
    "repository_not_accessible": (
        "The token cannot see that repository. Check the name, and that the token's repository "
        "access includes it (or that an organisation admin approved the token)."
    ),
    "repository_archived": "Unarchive the repository on github.com first; archived repositories are read-only.",
    "token_cannot_push": (
        "The token needs Contents: Read and write on this repository (see docs/github.md)."
    ),
    "administration_permission_missing": (
        "Creating a repository needs the token's Administration (Read and write) permission. Add it "
        "for now, or create an empty repository on github.com yourself and connect it as an existing one."
    ),
    "repository_too_large": "The repository is larger than this install allows (repo_max_size_mb).",
    "project_has_repository": "That project already has a repository. Disconnect it first: foundryos repo disconnect.",
    "branch_protected": (
        "Direct push is refused while the default branch is protected; use pr-human or pr-auto."
    ),
    "git_unavailable": "Install git on this machine, then check it with: foundryos doctor",
    "repository_busy": (
        "A pull request is being merged or a run of the project is active. Try again in a minute."
    ),
    "public_url_ephemeral": (
        "Webhooks need a stable public URL (a named tunnel or Tailscale), not a quick tunnel. "
        "Polling works without one."
    ),
    "webhook_permission_missing": (
        "The token lacks the Webhooks permission (Read and write). Add it, or keep polling."
    ),
}


def friendly(exc: CliError, *secrets: str | None) -> CliError:
    """The server's refusal with the fix appended, the missing permissions listed, and any secret
    redacted. Keeps the exit code and the code."""
    details = exc.details if isinstance(exc.details, dict) else {}
    code = details.get("reason") if exc.code in ("conflict", "error") else exc.code
    message = onboarding.redact(exc.message, *secrets)
    missing = details.get("missing_permissions")
    if missing:
        message += " Missing: " + ", ".join(str(m) for m in missing) + "."
    hint = HINTS.get(str(exc.code)) or HINTS.get(str(code))
    if hint and hint not in message:
        message += " " + hint
    return CliError(message, exit_code=exc.exit_code, code=exc.code, details=exc.details)


# ------------------------------------------------------------------ shared API steps


def cpath(company_id: str, suffix: str = "") -> str:
    return f"/companies/{company_id}{suffix}"


def availability(api: ApiClient, company_id: str) -> dict:
    """``GET /repositories/status``; a server without the feature (501) counts as not available."""
    try:
        return api.get(cpath(company_id, "/repositories/status")) or {}
    except CliError as exc:
        if exc.code in ("not_available", "not_found") or exc.exit_code == EXIT_NOT_FOUND:
            return {"available": False, "reason": exc.message, "milestone": "M14"}
        raise


def working_github_accounts(api: ApiClient, company_id: str) -> list[dict]:
    accounts = api.paged(cpath(company_id, "/connected-accounts"))
    return [a for a in accounts if a.get("provider") == "github" and a.get("status") == "connected"]


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def connect_token(api: ApiClient, company_id: str, token: str) -> dict:
    """Store the token in the vault and connect it as a GitHub account. Returns the connected account.

    A token GitHub refuses leaves nothing behind: the account is revoked again and the error (with the
    token redacted) is raised. The token only ever travels in the body of the credential request."""
    taken_creds = {c.get("name") for c in api.paged(cpath(company_id, "/credentials"))}
    taken_labels = {a.get("label") for a in api.paged(cpath(company_id, "/connected-accounts"))}
    try:
        credential = api.post(
            cpath(company_id, "/credentials"),
            {"name": _unique("GitHub token", taken_creds), "kind": "api_key", "value": token},
        )
        account = api.post(
            cpath(company_id, "/connected-accounts"),
            {
                "provider": "github",
                "label": _unique("GitHub", taken_labels),
                "credential_id": credential["id"],
            },
        )
    except CliError as exc:
        raise friendly(exc, token) from None
    if account.get("status") != "connected":
        reason = onboarding.redact(
            account.get("last_error") or "GitHub did not accept the token", token
        )
        with contextlib.suppress(CliError):
            api.delete(cpath(company_id, f"/connected-accounts/{account['id']}"))
        raise CliError(f"GitHub did not accept the token: {reason}", code="invalid_token")
    return account


def inspect_github(
    api: ApiClient,
    company_id: str,
    account_id: str,
    mode: str,
    *,
    full_name: str | None = None,
    owner: str | None = None,
) -> dict:
    body: dict[str, Any] = {"connected_account_id": account_id, "mode": mode}
    if full_name:
        body["full_name"] = full_name
    if owner:
        body["owner"] = owner
    return api.post(cpath(company_id, "/github/inspect"), body)


def create_binding(api: ApiClient, company_id: str, body: dict) -> dict:
    try:
        return api.post(cpath(company_id, "/repositories"), body)
    except CliError as exc:
        raise friendly(exc) from None


def wait_for_active(
    api: ApiClient,
    company_id: str,
    repo: dict,
    *,
    say: Callable[[str], None],
    dot: Callable[[], None],
    sleep: Callable[[float], None] = lambda seconds: sleep(seconds),
    interval: float = POLL_SECONDS,
    timeout: float = WAIT_SECONDS,
) -> dict:
    """Poll until the repository is ``active``. Raises ``CliError`` for a failed state; a wait that runs
    out returns the last row (status still creating/cloning) so the caller can say "still going"."""
    current, last = repo, None
    for _ in range(max(1, int(timeout / interval))):
        status = current.get("status")
        if status == "active":
            return current
        if status in FAILED_STATES:
            raise CliError(
                f"The repository is {status}: {current.get('status_reason') or 'see foundryos repo status'}",
                code=status,
            )
        if status != last:
            say(f"  {status}...")
            last = status
        else:
            dot()
        sleep(interval)
        current = api.get(cpath(company_id, f"/repositories/{repo['id']}"))
    return current


def resolve_project(api: ApiClient, company_id: str, ref: str) -> dict:
    items = api.paged(cpath(company_id, "/projects"))
    lowered = ref.lower()
    found = [p for p in items if p.get("id") == ref]
    if not found and len(ref) >= 6:
        found = [p for p in items if str(p.get("id", "")).startswith(ref)]
    if not found:
        found = [p for p in items if str(p.get("name", "")).lower() == lowered]
    if not found:
        raise CliError(f"No project matches {ref!r}.", exit_code=EXIT_NOT_FOUND, code="not_found")
    if len(found) > 1:
        raise CliError(f"{ref!r} matches several projects; use its id.", code="ambiguous")
    return found[0]


def resolve_repository(st: CliState, ref: str | None) -> dict:
    """A repository by id (or prefix), ``owner/name``, name or project name; with no reference, the only
    one that is not disconnected."""
    api = st.client()
    items = api.paged(st.cpath("/repositories"))
    if ref is None:
        live = [r for r in items if r.get("status") != "disconnected"] or items
        if not live:
            raise CliError(
                "No repository is connected. Run `foundryos repo connect` (or `foundryos setup --only repo`).",
                exit_code=EXIT_NOT_FOUND,
                code="not_found",
            )
        if len(live) > 1:
            names = ", ".join(r["full_name"] for r in live)
            raise CliError(
                f"Several repositories: {names}. Say which one.", code="ambiguous_repository"
            )
        return live[0]
    lowered = ref.lower()
    found = [r for r in items if r["id"] == ref]
    if not found and len(ref) >= 6:
        found = [r for r in items if r["id"].startswith(ref)]
    if not found:
        found = [
            r
            for r in items
            if lowered
            in {
                str(r.get("full_name", "")).lower(),
                str(r.get("name", "")).lower(),
                str(r.get("project_name") or "").lower(),
            }
        ]
    if not found:
        raise CliError(
            f"No repository matches {ref!r}.", exit_code=EXIT_NOT_FOUND, code="not_found"
        )
    if len(found) > 1:
        raise CliError(
            f"{ref!r} matches several repositories: "
            + ", ".join(r["full_name"] for r in found)
            + ". Use the id.",
            code="ambiguous_repository",
        )
    return found[0]


# ------------------------------------------------------------------ rendering


def pr_cell(repo: dict) -> str:
    pr = repo.get("open_pull_request")
    return f"#{pr['number']} {pr['stage']}" if pr else "-"


def when(value: Any) -> str:
    return str(value)[:16].replace("T", " ") if value else "-"


def repo_columns() -> list:
    return [
        ("id", lambda r: r["id"][:8]),
        ("project", lambda r: short(r.get("project_name") or r["project_id"][:8], 24)),
        ("repository", "full_name"),
        ("status", "status"),
        ("sync", "sync_state"),
        ("merge", "policy.merge_mode"),
        ("pull request", pr_cell),
        ("last sync", lambda r: when(r.get("last_synced_at"))),
        ("token expires", lambda r: when(r.get("token_expires_at"))),
    ]


def status_hints(repo: dict) -> list[str]:
    """What to do next, from the state alone."""
    hints: list[str] = []
    status, sync = repo.get("status"), repo.get("sync_state")
    if status == "auth_failed":
        hints.append("GitHub refused the token. Replace it: foundryos repo token")
    if status == "paused":
        hints.append("Syncing is paused. Resume it: foundryos repo resume")
    if status == "error":
        hints.append(
            "The repository is in an error state; read the reason above, then: foundryos repo sync"
        )
    if sync == "push_blocked":
        hints.append(
            "A push was blocked (a secret, a protected path or a too-large file). "
            "Read the alert in the web app, then reset the integration branch: foundryos repo reset-integration"
        )
    if sync == "upstream_conflict":
        hints.append(
            "The default branch conflicts with the agents' work; a task was created to resolve it."
        )
    if sync == "upstream_rewritten":
        hints.append(
            "The default branch was rewritten upstream; Foundry pushes nothing until you reset: "
            "foundryos repo reset-integration"
        )
    if repo.get("pr_opening_paused"):
        hints.append(
            "Pull request opening is paused (a PR was closed unmerged): foundryos repo resume-prs"
        )
    wh = repo.get("webhook") or {}
    if wh.get("stale"):
        hints.append("The public URL changed since the webhook was created; polling continues.")
    return hints


# ------------------------------------------------------------------ the token


def read_token(*, from_stdin: bool, prompt: str = "GitHub token (hidden)") -> str:
    """``github_token=`` on stdin with ``--secrets-stdin``, else a hidden prompt. Never an option."""
    if from_stdin:
        secrets = onboarding.parse_secrets(sys.stdin.read())
        token = secrets.get("github_token")
        if not token:
            raise usage("--secrets-stdin needs a github_token=... line.")
    else:
        token = typer.prompt(prompt, hide_input=True)
    error = onboarding.github_token_error(token)
    if error:
        raise usage(error + ".")
    return token.strip()


def token_account(
    st: CliState, *, account_ref: str | None, from_stdin: bool, interactive_hint: bool = True
) -> dict:
    """The GitHub account for ``repo connect``: ``--account``, a token on stdin, the one working
    account, or a hidden prompt."""
    api, cid = st.client(), st.company_id()
    accounts = working_github_accounts(api, cid)
    if account_ref:
        match = [
            a
            for a in accounts
            if account_ref in (a["id"], a.get("label")) or a["id"].startswith(account_ref)
        ]
        if len(match) != 1:
            raise CliError(
                f"No working GitHub account matches {account_ref!r}.",
                exit_code=EXIT_NOT_FOUND,
                code="not_found",
            )
        return match[0]
    if from_stdin:
        return connect_token(api, cid, read_token(from_stdin=True))
    if len(accounts) == 1:
        return accounts[0]
    if len(accounts) > 1:
        raise CliError(
            "Several GitHub accounts are connected: "
            + ", ".join(f"{a['label']} ({a['id'][:8]})" for a in accounts)
            + ". Pick one with --account.",
            code="ambiguous_account",
        )
    if interactive_hint:
        for line in onboarding.token_instructions("connect"):
            typer.echo(line, err=st.json)
    return connect_token(api, cid, read_token(from_stdin=False))


# ------------------------------------------------------------------ the commands


def _confirm_or_yes(yes: bool, question: str) -> None:
    if yes:
        return
    if not typer.confirm(question, default=False):
        raise CliError("Cancelled; nothing was changed.", code="cancelled")


def register(app: typer.Typer) -> None:
    app.add_typer(repo_app, name="repo")

    @command(repo_app, "list")
    def repo_list(ctx: typer.Context, json_out: JsonOpt = False) -> None:
        """Repositories of your projects, with their sync state and open pull request."""
        st = state(ctx, json_out)
        api = st.client()
        items = api.paged(st.cpath("/repositories"))
        if st.json:
            render_mod.print_json(items)
            return
        if not items:
            avail = availability(api, st.company_id())
            if avail.get("available") is False:
                render_mod.say(
                    f"GitHub-backed projects are not available on this machine: {avail.get('reason')}",
                    style="yellow",
                )
                return
        render_mod.render_table(
            items,
            repo_columns(),
            empty="No repository is connected. Connect one with: foundryos repo connect",
        )

    @command(repo_app, "status")
    def repo_status(
        ctx: typer.Context,
        repo: Annotated[
            str | None, typer.Argument(help="Id, owner/name or project (default: the only one)")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """One repository in detail: state, branches, policy, pull request, token expiry."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        found = st.client().get(st.cpath(f"/repositories/{found['id']}"))
        hints = status_hints(found)
        if st.json:
            render_mod.print_json({**found, "hints": hints})
            return
        pol = found.get("policy") or {}
        pr = found.get("open_pull_request")
        render_mod.render_kv(
            {
                **found,
                "branches": f"{found['default_branch']} <- {found['work_branch']}",
                "state": found["status"]
                + (f" ({found['status_reason']})" if found.get("status_reason") else ""),
                "pr": f"#{pr['number']} {pr['stage']} {pr.get('url') or ''}".strip()
                if pr
                else "none open",
                "merge": f"{pol.get('merge_mode')} ({pol.get('pr_merge_method')}), CI {pol.get('ci_mode')}",
                "intake": pol.get("issue_intake"),
                "hook": "on"
                if (found.get("webhook") or {}).get("enabled")
                else f"off (polling every {pol.get('poll_interval_seconds')}s)",
                "limit": found.get("rate_limit_remaining"),
            },
            [
                ("repository", "full_name"),
                ("url", "html_url"),
                ("project", "project_name"),
                ("visibility", "visibility"),
                ("origin", "origin"),
                ("status", "state"),
                ("sync state", "sync_state"),
                ("branches", "branches"),
                ("merge", "merge"),
                ("issue intake", "intake"),
                ("pull request", "pr"),
                ("webhook", "hook"),
                ("codebase onboarding", "onboarding_status"),
                ("last sync", lambda r: when(r.get("last_synced_at"))),
                ("next sync", lambda r: when(r.get("next_sync_at"))),
                ("token expires", lambda r: when(r.get("token_expires_at"))),
                ("API calls left", "limit"),
                ("company default", "is_company_default"),
                ("id", "id"),
            ],
            title=found["full_name"],
        )
        for hint in hints:
            render_mod.say(f"! {hint}", style="yellow")

    def _act(ctx, repo, json_out, suffix: str, body: dict | None, done: str) -> None:
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        try:
            result = st.client().post(st.cpath(f"/repositories/{found['id']}{suffix}"), body)
        except CliError as exc:
            raise friendly(exc) from None
        if st.json:
            render_mod.print_json(result)
            return
        render_mod.say(done.format(repo=found["full_name"]))

    @command(repo_app, "sync")
    def repo_sync(ctx: typer.Context, repo: Ref = None, json_out: JsonOpt = False) -> None:
        """Sync now instead of waiting for the next poll."""
        _act(ctx, repo, json_out, "/sync", None, "Sync queued for {repo}.")

    @command(repo_app, "pause")
    def repo_pause(ctx: typer.Context, repo: Ref = None, json_out: JsonOpt = False) -> None:
        """Stop all network traffic for this repository (local work continues)."""
        _act(ctx, repo, json_out, "/pause", None, "Paused {repo}. Local work continues.")

    @command(repo_app, "resume")
    def repo_resume(ctx: typer.Context, repo: Ref = None, json_out: JsonOpt = False) -> None:
        """Resume syncing a paused repository."""
        _act(ctx, repo, json_out, "/resume", None, "Resumed {repo}.")

    @command(repo_app, "resume-prs")
    def repo_resume_prs(ctx: typer.Context, repo: Ref = None, json_out: JsonOpt = False) -> None:
        """Let Foundry open pull requests again after one was closed without merging."""
        _act(
            ctx,
            repo,
            json_out,
            "/pull-requests/resume-opening",
            None,
            "Pull requests may be opened again for {repo}.",
        )

    @command(repo_app, "disconnect")
    def repo_disconnect(
        ctx: typer.Context,
        repo: Ref = None,
        yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Disconnect a project from its repository. The clone stays; the project keeps working locally."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        _confirm_or_yes(
            yes,
            f"Disconnect {found['full_name']}? Nothing is deleted on GitHub; the project then works "
            "locally on its branch",
        )
        _act(
            ctx,
            found["id"],
            json_out,
            "/disconnect",
            {"confirm": True},
            "Disconnected {repo}. Reconnect later with: foundryos repo connect",
        )

    @command(repo_app, "reset-integration")
    def repo_reset(
        ctx: typer.Context,
        repo: Ref = None,
        to: Annotated[
            str,
            typer.Option(
                "--to",
                help="remote-work-branch (what GitHub has on the work branch) or default-branch.",
            ),
        ] = "remote-work-branch",
        yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Reset the integration branch (after a blocked push or an upstream rewrite).

        The old head is kept as the local branch foundry/rescue-<time>; nothing is force-pushed."""
        target = {
            "remote-work-branch": "remote_work_branch",
            "default-branch": "default_branch",
        }.get(to)
        if target is None:
            raise usage("--to must be remote-work-branch or default-branch.")
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        _confirm_or_yes(
            yes,
            f"Reset the work branch of {found['full_name']} to {to}? Unmerged agent work stays on a "
            "rescue branch, but the project's branch moves",
        )
        _act(
            ctx,
            found["id"],
            json_out,
            "/integration/reset",
            {"to": target, "confirm": True},
            "Reset the integration branch of {repo}; the old head is on a foundry/rescue-* branch.",
        )

    @command(repo_app, "webhook")
    def repo_webhook(
        ctx: typer.Context,
        repo: Ref = None,
        on: Annotated[
            bool, typer.Option("--on/--off", help="Create or remove the webhook.")
        ] = True,
        json_out: JsonOpt = False,
    ) -> None:
        """Turn the optional GitHub webhook on or off (needs a stable public URL; polling is the default)."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        try:
            result = st.client().put(
                st.cpath(f"/repositories/{found['id']}/webhook"), {"enabled": on}
            )
        except CliError as exc:
            raise friendly(exc) from None
        if st.json:
            render_mod.print_json(result)
            return
        render_mod.say(f"Webhook {'on' if on else 'off'} for {found['full_name']}.")

    @command(repo_app, "set")
    def repo_set(
        ctx: typer.Context,
        repo: Ref = None,
        merge: Annotated[
            str | None, typer.Option("--merge", help="pr-human, pr-auto or direct.")
        ] = None,
        merge_method: Annotated[
            str | None, typer.Option("--merge-method", help="squash, merge or rebase.")
        ] = None,
        ci: Annotated[
            str | None, typer.Option("--ci", help="if-present, required or ignore.")
        ] = None,
        issues: Annotated[
            str | None,
            typer.Option("--issues", help="off, collaborators, labeled or everyone."),
        ] = None,
        poll_interval: Annotated[
            int | None, typer.Option("--poll-interval", help="Seconds between syncs (60-3600).")
        ] = None,
        internal_review: Annotated[
            bool | None,
            typer.Option("--internal-review/--no-internal-review", help="Agents review PRs first."),
        ] = None,
        docs_upkeep: Annotated[
            bool | None, typer.Option("--docs-upkeep/--no-docs-upkeep", help="Weekly README check.")
        ] = None,
        workflow_changes: Annotated[
            bool | None,
            typer.Option(
                "--workflow-changes/--no-workflow-changes",
                help="Let agents edit .github/workflows (needs the token's Workflows permission).",
            ),
        ] = None,
        company_default: Annotated[
            bool | None,
            typer.Option(
                "--company-default/--no-company-default", help="Default for new projects."
            ),
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """Change a repository's policy (owner/admin). Only the options you give are changed."""
        if merge_method not in (None, "squash", "merge", "rebase"):
            raise usage("--merge-method must be squash, merge or rebase.")
        onboarding.validate_choice("--merge", merge, tuple(GITHUB_MERGE_FLAGS))
        onboarding.validate_choice("--ci", ci, tuple(GITHUB_CI_FLAGS))
        onboarding.validate_choice("--issues", issues, GITHUB_ISSUES)
        policy: dict[str, Any] = {}
        for key, value in (
            ("merge_mode", GITHUB_MERGE_FLAGS.get(merge or "")),
            ("pr_merge_method", merge_method),
            ("ci_mode", GITHUB_CI_FLAGS.get(ci or "")),
            ("issue_intake", issues),
            ("poll_interval_seconds", poll_interval),
            ("pr_internal_review", internal_review),
            ("docs_upkeep", docs_upkeep),
            ("allow_workflow_changes", workflow_changes),
        ):
            if value is not None:
                policy[key] = value
        body: dict[str, Any] = {}
        if policy:
            body["policy"] = policy
        if company_default is not None:
            body["is_company_default"] = company_default
        if not body:
            raise usage("Nothing to change: give at least one option (see --help).")
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        try:
            result = st.client().patch(st.cpath(f"/repositories/{found['id']}"), body)
        except CliError as exc:
            raise friendly(exc) from None
        if st.json:
            render_mod.print_json(result)
            return
        changed = sorted([*policy, *(k for k in body if k != "policy")])
        render_mod.say(f"Updated {found['full_name']}: {', '.join(changed)}.")

    @command(repo_app, "prs")
    def repo_prs(ctx: typer.Context, repo: Ref = None, json_out: JsonOpt = False) -> None:
        """Pull requests Foundry opened for this repository."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        items = st.client().paged(st.cpath(f"/repositories/{found['id']}/pull-requests"))
        if st.json:
            render_mod.print_json(items)
            return
        render_mod.render_table(
            items,
            [
                ("#", "number"),
                ("title", lambda p: short(p["title"], 50)),
                ("stage", "stage"),
                ("rev", "revision"),
                ("verdict", "verdict"),
                ("CI", "checks_state"),
                ("mergeable", "mergeable_state"),
                ("opened", lambda p: when(p.get("opened_at"))),
            ],
            empty="No pull requests yet.",
        )

    @command(repo_app, "items")
    def repo_items(
        ctx: typer.Context,
        repo: Ref = None,
        kind: Annotated[
            str | None, typer.Option(help="issue, review, internal_review, check_failure, alert")
        ] = None,
        status: Annotated[
            str | None, typer.Option(help="new, queued, tasked, ignored, done")
        ] = None,
        json_out: JsonOpt = False,
    ) -> None:
        """What came in from GitHub (issues, reviews, CI failures, alerts). Untrusted text, clipped."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        items = st.client().paged(
            st.cpath(f"/repositories/{found['id']}/items"), kind=kind, status=status
        )
        if st.json:
            render_mod.print_json(items)
            return
        render_mod.render_table(
            items,
            [
                ("kind", "kind"),
                ("#", "number"),
                ("title", lambda i: short(i.get("title"), 50)),
                ("author", "author"),
                ("status", "status"),
                ("why ignored", "ignore_reason"),
            ],
            empty="Nothing has come in from GitHub.",
        )

    @command(repo_app, "activity")
    def repo_activity(ctx: typer.Context, repo: Ref = None, json_out: JsonOpt = False) -> None:
        """What Foundry did with this repository, newest first."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        items = (
            st.client()
            .get(st.cpath(f"/repositories/{found['id']}/activity"), limit=50)
            .get("items", [])
        )
        if st.json:
            render_mod.print_json(items)
            return
        render_mod.render_table(
            items,
            [
                ("when", lambda e: when(e.get("created_at"))),
                ("action", "action"),
                ("what", lambda e: short(e.get("summary"), 80)),
            ],
            empty="No activity yet.",
        )

    @command(repo_app, "token")
    def repo_token(
        ctx: typer.Context,
        repo: Ref = None,
        secrets_stdin: Annotated[
            bool,
            typer.Option("--secrets-stdin", help="Read a github_token=... line from stdin."),
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Replace the GitHub token (expired, revoked or re-scoped). Asks for it hidden; never an option."""
        st = state(ctx, json_out)
        found = resolve_repository(st, repo)
        api = st.client()
        account_id = found.get("connected_account_id")
        if not account_id:
            raise CliError("This repository has no connected GitHub account.", code="no_account")
        accounts = api.paged(st.cpath("/connected-accounts"))
        account = next((a for a in accounts if a["id"] == account_id), None)
        if account is None or not account.get("credential_id"):
            raise CliError(
                "The GitHub account of this repository is gone. Connect the repository again: "
                "foundryos repo connect",
                code="no_account",
            )
        if not secrets_stdin:
            for line in onboarding.token_instructions("connect"):
                typer.echo(line, err=st.json)
        token = read_token(from_stdin=secrets_stdin, prompt="New GitHub token (hidden)")
        try:
            api.post(st.cpath(f"/credentials/{account['credential_id']}/rotate"), {"value": token})
            check = api.post(st.cpath(f"/connected-accounts/{account_id}/test"))
        except CliError as exc:
            raise friendly(exc, token) from None
        if not check.get("ok"):
            raise CliError(
                "GitHub did not accept the new token: "
                + onboarding.redact(str(check.get("message") or "the test failed"), token),
                code="invalid_token",
            )
        try:
            api.post(st.cpath(f"/repositories/{found['id']}/sync"))
        except CliError as exc:
            raise friendly(exc, token) from None
        if st.json:
            render_mod.print_json({"ok": True, "repository": found["full_name"], "synced": True})
            return
        render_mod.say(f"The new token works. A sync of {found['full_name']} is queued.")

    @command(repo_app, "connect")
    def repo_connect(
        ctx: typer.Context,
        repo: Annotated[
            str,
            typer.Option(
                "--repo",
                help="OWNER/NAME or a github.com URL (with --create: NAME or OWNER/NAME).",
            ),
        ],
        project: Annotated[
            str | None, typer.Option("--project", help="An existing project (name or id).")
        ] = None,
        project_name: Annotated[
            str | None, typer.Option("--project-name", help="Create a project with this name.")
        ] = None,
        create: Annotated[
            bool, typer.Option("--create", help="Create the repository on GitHub first.")
        ] = False,
        visibility: Annotated[
            str, typer.Option("--visibility", help="With --create: private or public.")
        ] = "private",
        public_ok: Annotated[
            bool, typer.Option("--public-ok", help="Confirm that a public repository is intended.")
        ] = False,
        default_branch: Annotated[
            str | None, typer.Option("--default-branch", help="The branch pull requests target.")
        ] = None,
        merge: Annotated[
            str | None, typer.Option("--merge", help="pr-human, pr-auto or direct.")
        ] = None,
        ci: Annotated[
            str | None, typer.Option("--ci", help="if-present, required or ignore.")
        ] = None,
        issues: Annotated[
            str | None, typer.Option("--issues", help="off, collaborators, labeled or everyone.")
        ] = None,
        publish_local: Annotated[
            bool,
            typer.Option(
                "--publish-local",
                help="The project already has local history: push it to this EMPTY repository.",
            ),
        ] = False,
        account: Annotated[
            str | None, typer.Option("--account", help="A connected GitHub account (label or id).")
        ] = None,
        no_wait: Annotated[
            bool, typer.Option("--no-wait", help="Return when the clone is queued.")
        ] = False,
        secrets_stdin: Annotated[
            bool,
            typer.Option("--secrets-stdin", help="Read a github_token=... line from stdin."),
        ] = False,
        json_out: JsonOpt = False,
    ) -> None:
        """Back a project with a GitHub repository (new or existing).

        The token is asked for hidden (or read from --secrets-stdin) unless a GitHub account is already
        connected. See docs/github.md for the token permissions."""
        onboarding.validate_choice("--visibility", visibility, GITHUB_VISIBILITY)
        onboarding.validate_choice("--merge", merge, tuple(GITHUB_MERGE_FLAGS))
        onboarding.validate_choice("--ci", ci, tuple(GITHUB_CI_FLAGS))
        onboarding.validate_choice("--issues", issues, GITHUB_ISSUES)
        if bool(project) == bool(project_name):
            raise usage("Give exactly one of --project (an existing project) and --project-name.")
        owner, name = onboarding.split_repo_ref("new" if create else "existing", repo)
        if default_branch is not None:
            onboarding.validate_github_options(
                onboarding.WizardOptions(github="existing", github_default_branch=default_branch)
            )
        if create and visibility == "public" and not public_ok:
            raise usage(
                "A public repository makes everything your agents commit public; add --public-ok."
            )
        st = state(ctx, json_out)
        api, cid = st.client(), st.company_id()
        avail = availability(api, cid)
        if avail.get("available") is False:
            raise CliError(
                f"GitHub-backed projects are not available here: {avail.get('reason')}",
                code="not_available",
            )
        body: dict[str, Any] = {"mode": "create" if create else "connect"}
        body["connected_account_id"] = token_account(
            st, account_ref=account, from_stdin=secrets_stdin
        )["id"]
        if project:
            body["project_id"] = resolve_project(api, cid, project)["id"]
        else:
            body["project_name"] = project_name
        if create:
            body.update(name=name, visibility=visibility, confirm_public=public_ok)
            if owner:
                body["owner"] = owner
        else:
            body["full_name"] = f"{owner}/{name}"
            if publish_local:
                body["publish_local"] = True
        if default_branch:
            body["default_branch"] = default_branch
        policy = {
            k: v
            for k, v in (
                ("merge_mode", GITHUB_MERGE_FLAGS.get(merge or "")),
                ("ci_mode", GITHUB_CI_FLAGS.get(ci or "")),
                ("issue_intake", issues),
            )
            if v
        }
        if policy:
            body["policy"] = policy
        created = create_binding(api, cid, body)
        say = lambda m: typer.echo(m, err=st.json)  # noqa: E731
        say(
            f"Connecting {created['full_name']} to {created.get('project_name') or 'the project'}..."
        )
        if created.get("status_reason"):
            say(f"Note: {created['status_reason']}")
        final = created
        if not no_wait:
            final = wait_for_active(
                api,
                cid,
                created,
                say=say,
                dot=lambda: typer.echo(".", nl=False, err=True),
                sleep=lambda seconds: sleep(seconds),
            )
        if st.json:
            render_mod.print_json(final)
            return
        if final.get("status") == "active":
            render_mod.say(f"{final['full_name']} is connected and in sync.")
            if final.get("onboarding_status") == "pending":
                render_mod.say(
                    "The AI team will first learn the codebase, then pick up other work."
                )
        else:
            render_mod.say(
                f"{final['full_name']} is {final.get('status')}; check progress with: foundryos repo status"
            )


__all__ = ["register", "repo_app"]
