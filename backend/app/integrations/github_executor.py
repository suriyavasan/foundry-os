"""GitHub provider-action executor (M8 ``github.read`` and the domain flow, M14 repository actions).

Runs INSIDE the control plane after the guardrail engine decided. The agent never sees the token.
Every repository action is validated again here (an approval binds the call, not its sanity):

* ``repo`` must be an ACTIVE binding of the company (409 ``repo_not_bound``) that uses the same
  connected account as the credential;
* the agent must be the AI Co-founder, the lead of the bound project, or running a task of it (403);
* parameters are checked, free text passes the same DLP the engine applies to a message body;
* whatever GitHub authored (issue and PR titles and bodies, comments, reviews, CI logs, alert
  descriptions) is returned clipped, scrubbed, flagged for prompt injection and wrapped in
  ``<untrusted source="github">`` blocks (decision D42).

Nothing returned or raised carries the token.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, GuardrailDenied, NotFound, ValidationFailed
from app.core.principal import CompanyContext, Principal, PrincipalType, Role
from app.guardrails import dlp
from app.guardrails.engine import GuardEnv
from app.guardrails.providers import ProviderCall, disclosure_footer
from app.integrations import catalog
from app.integrations.base import ProviderError
from app.integrations.executors import CONTENT_KEYS, IntegrationExecutor, _s, _slot_free
from app.integrations.github import GithubClient
from app.models.agent import Agent
from app.models.company import Company
from app.models.enums import AgentKind
from app.models.reachability import ConnectedAccount
from app.models.repository import ProjectRepository
from app.models.run import TaskRun
from app.models.work import Project, Task
from app.schemas.repository import FULL_NAME_RE, valid_repo_name
from app.services import connected_accounts, domains, github_accounts, policy
from app.services import repositories as repositories_service
from app.services.agent_auth import AgentRunContext
from app.services.repo_errors import ProjectHasRepository, RepoNotBound

MAX_COMMENT = 20_000
MAX_TITLE = 256
MAX_LIST = 30
MAX_BODY_READ = 8 * 1024
MAX_COMMENT_READ = 4 * 1024
MAX_LOG_JOBS = 3
TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

BOUND_ACTIONS = frozenset(
    {
        "github.issue.read",
        "github.pr.read",
        "github.checks.read",
        "github.alerts.read",
        "github.issue.comment",
        "github.pr.comment",
        "github.issue.create",
        "github.issue.update",
        "github.release.draft",
        "github.release.publish",
    }
)


@dataclass
class _Scope:
    """Everything a bound-repository action needs, resolved and authorized."""

    binding: ProjectRepository
    agent: Agent
    task: Task | None
    account: ConnectedAccount


def _num(params: dict[str, Any], key: str = "number") -> int:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationFailed(
            f"`{key}` must be a positive integer",
            details=[{"loc": ["params", key], "msg": "positive integer required"}],
        )
    return value


def _text(params: dict[str, Any], key: str, limit: int, *, required: bool = True) -> str | None:
    value = params.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValidationFailed(
            f"`{key}` must be non-empty text" if required else f"`{key}` must be text",
            details=[{"loc": ["params", key], "msg": "text required"}],
        )
    if len(value) > limit:
        raise ValidationFailed(f"`{key}` is longer than {limit} characters")
    return value


def _names(params: dict[str, Any], key: str) -> list[str]:
    value = params.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise ValidationFailed(f"`{key}` must be a list of at most 10 label names")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 50:
            raise ValidationFailed(f"`{key}` must hold label names of 1-50 characters")
        if item.strip() not in out:
            out.append(item.strip())
    return out


def _limit(params: dict[str, Any], default: int = 20) -> int:
    value = params.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIST:
        return default
    return value


def _who(item: dict[str, Any]) -> str | None:
    user = item.get("user")
    return user.get("login") if isinstance(user, dict) else None


def _labels_of(issue: dict[str, Any]) -> list[str]:
    return [
        str(lb.get("name") if isinstance(lb, dict) else lb) for lb in (issue.get("labels") or [])
    ]


def _wrapped(text: str | None, limit: int) -> str:
    return github_accounts.untrusted_block(text, limit)


class GithubExecutor(IntegrationExecutor):
    provider = "github"
    actions = catalog.SPECS["github"].actions

    # ------------------------------------------------------------------ plumbing

    def _agent_ctx(self, agent: Agent) -> CompanyContext:
        return CompanyContext(
            agent.company_id,
            Principal(PrincipalType.AGENT, agent.id, agent.name, agent.company_id),
            Role.AGENT,
        )

    def _run_of(self, db: Session, call: ProviderCall) -> tuple[Agent, TaskRun | None, Task | None]:
        agent = db.get(Agent, call.agent_id)
        if agent is None or agent.company_id != call.company_id:
            raise Forbidden("Unknown agent")
        run = db.get(TaskRun, call.run_id) if call.run_id else None
        if run is not None and run.company_id != call.company_id:
            run = None
        task = db.get(Task, run.task_id) if run is not None else None
        return agent, run, task

    def _resolve(
        self,
        db: Session,
        company_id: str,
        agent: Agent,
        task: Task | None,
        account: ConnectedAccount,
        params: dict[str, Any],
    ) -> _Scope:
        repo = (_s(params, "repo") or "").strip()
        if not FULL_NAME_RE.match(repo):
            raise ValidationFailed(
                "`repo` must look like owner/name",
                details=[{"loc": ["params", "repo"], "msg": "owner/name required"}],
            )
        binding = github_accounts.active_binding(db, company_id, repo)
        if binding is None or binding.connected_account_id != account.id:
            raise RepoNotBound(
                f"{repo} is not bound to a project of this company with this account",
                details={"repo": repo},
            )
        if not github_accounts.agent_may_use_binding(db, agent, task, binding):
            raise Forbidden(
                "Only the AI Co-founder, the project lead or an agent working on this project's "
                "task may use its repository",
                details={"repo": repo},
            )
        return _Scope(binding, agent, task, account)

    @contextmanager
    def _session(
        self, db: Session, call: ProviderCall, params: dict[str, Any]
    ) -> Iterator[tuple[_Scope, GithubClient]]:
        account = self.account(db, call)
        agent, _run, task = self._run_of(db, call)
        scope = self._resolve(db, call.company_id, agent, task, account, params)
        client = connected_accounts.client_for(db, self.ic, account)
        assert isinstance(client, GithubClient)
        try:
            yield scope, client
        finally:
            client.http.close()

    def _dlp(self, db: Session, company_id: str, *texts: str | None) -> None:
        """The engine scans ``body``; the other free-text fields (a title, a closing comment) get
        the same scan here, so nothing the company must not publish leaves through them."""
        text = "\n".join(t for t in texts if t)
        if not text:
            return
        own = GuardEnv(cipher=self.ic.cipher).own_secrets(db, company_id)
        found = dlp.scan_text(text, own_secrets=own, internal=dlp.internal_snippets(db, company_id))
        if found:
            kinds = ", ".join(sorted({f.kind for f in found}))
            raise GuardrailDenied(
                f"Outbound DLP blocked this content ({kinds}). Remove it and submit a revised "
                "version; nothing was sent.",
                details={"reason": "dlp_blocked", "dlp_findings": [f.as_dict() for f in found]},
            )

    def _with_footer(self, db: Session, company_id: str, body: str) -> str:
        """Agents do not pass as people: comments carry the company's AI disclosure line."""
        limits = policy.load_policy(db, company_id)
        company = db.get(Company, company_id)
        if company is None:
            return body
        footer = disclosure_footer(limits, company.name)
        if footer and footer not in body:
            return f"{body.rstrip()}\n\n---\n{footer}"
        return body

    def prevalidate(self, db, rc, credential, action: str, params: dict[str, Any]) -> None:
        if action not in BOUND_ACTIONS and action != "github.repo.create":
            return super().prevalidate(db, rc, credential, action, params)
        account = connected_accounts.account_for_credential(db, rc.ctx.company_id, credential.id)
        if account is None or account.provider != self.provider or account.status != "connected":
            raise Conflict(
                "This credential is not a working connected github account: connect or test the "
                "account first",
                details={"reason": "no_account"},
            )
        if action == "github.repo.create":
            return self._check_repo_create(db, rc, params)
        scope = self._resolve(db, rc.ctx.company_id, rc.agent, rc.task, account, params)
        getattr(self, "_check_" + action.replace(".", "_"))(db, rc.ctx.company_id, scope, params)

    # ------------------------------------------------------------------ github.read (M8)

    def do_github_read(self, db, call, params):
        repo = _s(params, "repo") or ""
        if repo.count("/") != 1:
            raise ValidationFailed("`repo` must look like owner/name")
        client = connected_accounts.client_for(db, self.ic, self.account(db, call))
        assert isinstance(client, GithubClient)
        try:
            path = _s(params, "path", required=False)
            if path:
                body = client.json(
                    "GET", f"/repos/{repo}/contents/{path.lstrip('/')}", what="read the file"
                )
                return {"repo": repo, "path": path, "entry": _slim(body)}
            info = client.repo(repo)
        finally:
            client.http.close()
        if info is None:
            raise NotFound("Repository not found")
        return {
            k: info.get(k)
            for k in ("full_name", "description", "default_branch", "html_url", "private")
        }

    # ------------------------------------------------------------------ domain flow (M8)

    def do_domain_preview(self, db, call, params):
        pv = domains.build_preview(
            db,
            self.ic,
            call.company_id,
            _s(params, "registry_id") or "",
            _s(params, "name") or "",
            _s(params, "target", required=False),
        )
        return pv

    def check_domain_register_pr(self, db, rc, params):
        _slot_free(db, rc.ctx.company_id)
        self._verify_content(db, rc.ctx.company_id, params)

    def _verify_content(self, db, company_id, params):
        pv = domains.build_preview(
            db,
            self.ic,
            company_id,
            _s(params, "registry_id") or "",
            _s(params, "name") or "",
            _s(params, "target", required=False),
        )
        if not pv["eligible"]:
            raise ValidationFailed(pv["ineligible_reason"] or "Not eligible")
        for key in (*CONTENT_KEYS, "publishes_as"):
            if params.get(key) != pv[key]:
                raise ValidationFailed(
                    f"`{key}` does not match what the registry definition generates; call "
                    "domain.preview and submit exactly its content",
                    details={"reason": "content_mismatch", "field": key},
                )
        return pv

    def do_domain_register_pr(self, db, call, params):
        """Open the registry pull request with exactly the content the approval was bound to. The
        content must be what the registry definition generates for (registry_id, name, target)."""
        pv = self._verify_content(db, call.company_id, params)
        _slot_free(db, call.company_id)
        reg = domains.execute_registry_pr(db, self.ic, call.company_id, pv, None)
        return {"status": reg.status, "pr_url": reg.pr_url, "domain": reg.domain}

    # ------------------------------------------------------------------ reads (untrusted wrap)

    def _check_github_issue_read(self, db, company_id, scope, params):
        if params.get("number") is not None:
            _num(params)

    def do_github_issue_read(self, db, call, params):
        self._check_github_issue_read(db, call.company_id, None, params)
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            if params.get("number") is not None:
                number = _num(params)
                issue = client.get_issue(repo, number)
                comments = client.issue_comments(repo, number)[:MAX_LIST]
                text = "\n".join(
                    [issue.get("title") or "", issue.get("body") or ""]
                    + [c.get("body") or "" for c in comments]
                )
                return {
                    "repo": repo,
                    "number": number,
                    "is_pull_request": "pull_request" in issue,
                    "state": issue.get("state"),
                    "author": _who(issue),
                    "author_association": issue.get("author_association"),
                    "labels": _labels_of(issue),
                    "url": issue.get("html_url"),
                    "title": _wrapped(issue.get("title"), 300),
                    "body": _wrapped(issue.get("body"), MAX_BODY_READ),
                    "comments": [
                        {
                            "id": c.get("id"),
                            "author": _who(c),
                            "author_association": c.get("author_association"),
                            "body": _wrapped(c.get("body"), MAX_COMMENT_READ),
                        }
                        for c in comments
                    ],
                    "untrusted": True,
                    "possible_prompt_injection": github_accounts.injection_flags(text),
                    "notice": github_accounts.UNTRUSTED_NOTICE,
                }
            state = (
                params.get("state") if params.get("state") in ("open", "closed", "all") else "open"
            )
            found = client.list_issues(repo, state=str(state), per_page=50, max_pages=1)
            issues = [i for i in (found or []) if "pull_request" not in i][: _limit(params)]
            return {
                "repo": repo,
                "issues": [
                    {
                        "number": i.get("number"),
                        "state": i.get("state"),
                        "author": _who(i),
                        "author_association": i.get("author_association"),
                        "labels": _labels_of(i),
                        "title": _wrapped(i.get("title"), 300),
                        "possible_prompt_injection": github_accounts.injection_flags(
                            i.get("title"), i.get("body")
                        ),
                    }
                    for i in issues
                ],
                "untrusted": True,
                "notice": github_accounts.UNTRUSTED_NOTICE,
            }

    def _check_github_pr_read(self, db, company_id, scope, params):
        _num(params)

    def do_github_pr_read(self, db, call, params):
        number = _num(params)
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            pr = client.get_pull(repo, number)
            reviews = client.list_reviews(repo, number)[:MAX_LIST]
            comments = client.list_review_comments(repo, number)[:MAX_LIST]
            text = "\n".join(
                [pr.get("title") or "", pr.get("body") or ""]
                + [r.get("body") or "" for r in reviews]
                + [c.get("body") or "" for c in comments]
            )
            return {
                "repo": repo,
                "number": number,
                "state": pr.get("state"),
                "merged": bool(pr.get("merged")),
                "draft": bool(pr.get("draft")),
                "mergeable_state": pr.get("mergeable_state"),
                "author": _who(pr),
                "head": {
                    "ref": (pr.get("head") or {}).get("ref"),
                    "sha": (pr.get("head") or {}).get("sha"),
                },
                "base": {"ref": (pr.get("base") or {}).get("ref")},
                "url": pr.get("html_url"),
                "title": _wrapped(pr.get("title"), 300),
                "body": _wrapped(pr.get("body"), MAX_BODY_READ),
                "reviews": [
                    {
                        "id": r.get("id"),
                        "author": _who(r),
                        "author_association": r.get("author_association"),
                        "state": r.get("state"),
                        "commit_id": r.get("commit_id"),
                        "body": _wrapped(r.get("body"), MAX_COMMENT_READ),
                    }
                    for r in reviews
                ],
                "review_comments": [
                    {
                        "id": c.get("id"),
                        "author": _who(c),
                        "author_association": c.get("author_association"),
                        "path": c.get("path"),
                        "body": _wrapped(c.get("body"), MAX_COMMENT_READ),
                    }
                    for c in comments
                ],
                "untrusted": True,
                "possible_prompt_injection": github_accounts.injection_flags(text),
                "notice": github_accounts.UNTRUSTED_NOTICE,
            }

    def _check_github_checks_read(self, db, company_id, scope, params):
        if params.get("number") is not None:
            _num(params)
        ref = params.get("ref")
        if ref is not None and (not isinstance(ref, str) or not ref.strip() or len(ref) > 255):
            raise ValidationFailed("`ref` must be a branch name or a commit SHA")

    def do_github_checks_read(self, db, call, params):
        self._check_github_checks_read(db, call.company_id, None, params)
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            if params.get("number") is not None:
                pr = client.get_pull(repo, _num(params))
                ref = (pr.get("head") or {}).get("sha") or scope.binding.default_branch
            else:
                ref = str(params.get("ref") or scope.binding.default_branch)
            notes: list[str] = []
            checks: list[dict[str, Any]] = []
            try:
                for run in client.check_runs(repo, ref) or []:
                    checks.append(
                        {
                            "name": run.get("name"),
                            "status": run.get("status"),
                            "conclusion": run.get("conclusion"),
                            "url": run.get("html_url"),
                        }
                    )
            except ProviderError as exc:
                if (exc.details or {}).get("reason") not in ("not_accessible", "forbidden"):
                    raise
                notes.append("The token cannot read check runs; showing workflow runs only")
            logs: list[dict[str, Any]] = []
            if params.get("logs", True):
                logs = self._failed_logs(client, repo, ref, notes)
            return {
                "repo": repo,
                "ref": ref,
                "checks": checks,
                "failed_job_logs": logs,
                "notes": notes,
                "untrusted": bool(logs),
                "notice": github_accounts.UNTRUSTED_NOTICE if logs else None,
            }

    def _failed_logs(self, client, repo, ref, notes) -> list[dict[str, Any]]:
        sha = ref
        if not SHA_RE.match(ref):
            try:
                sha = client.get_branch(repo, ref)["commit"]["sha"]
            except (ProviderError, KeyError, TypeError):
                notes.append("The reference could not be resolved to a commit")
                return []
        out: list[dict[str, Any]] = []
        try:
            runs = client.workflow_runs(repo, sha)
            for run in runs:
                if run.get("conclusion") not in ("failure", "timed_out", "startup_failure"):
                    continue
                for job in client.run_jobs(repo, run["id"]):
                    if job.get("conclusion") not in ("failure", "timed_out"):
                        continue
                    if len(out) >= MAX_LOG_JOBS:
                        notes.append(f"Only the first {MAX_LOG_JOBS} failed jobs are shown")
                        return out
                    tail = client.job_log_tail(repo, job["id"])
                    out.append(
                        {
                            "workflow": run.get("name"),
                            "job": job.get("name"),
                            "url": job.get("html_url"),
                            "log_tail": github_accounts.untrusted_block(tail, 32 * 1024),
                            "possible_prompt_injection": github_accounts.injection_flags(tail),
                        }
                    )
        except ProviderError as exc:
            if (exc.details or {}).get("reason") not in ("not_accessible", "forbidden"):
                raise
            notes.append("The token cannot read workflow runs or logs")
        return out

    def _check_github_alerts_read(self, db, company_id, scope, params):
        if params.get("state") not in (None, "open", "fixed", "dismissed"):
            raise ValidationFailed("`state` must be open, fixed or dismissed")

    def do_github_alerts_read(self, db, call, params):
        self._check_github_alerts_read(db, call.company_id, None, params)
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            alerts = client.dependabot_alerts(repo, str(params.get("state") or "open"))
            out = []
            for a in alerts[: _limit(params, MAX_LIST)]:
                adv = a.get("security_advisory") or {}
                dep = (a.get("dependency") or {}).get("package") or {}
                patched = (a.get("security_vulnerability") or {}).get("first_patched_version") or {}
                out.append(
                    {
                        "number": a.get("number"),
                        "state": a.get("state"),
                        "severity": adv.get("severity"),
                        "ghsa_id": adv.get("ghsa_id"),
                        "package": dep.get("name"),
                        "ecosystem": dep.get("ecosystem"),
                        "patched_version": patched.get("identifier"),
                        "url": a.get("html_url"),
                        "summary": _wrapped(adv.get("summary"), 500),
                        "description": _wrapped(adv.get("description"), 4 * 1024),
                    }
                )
            return {
                "repo": repo,
                "alerts": out,
                "untrusted": True,
                "notice": github_accounts.UNTRUSTED_NOTICE,
            }

    # ------------------------------------------------------------------ comments and issues

    def _check_comment(self, db, company_id, params):
        _num(params)
        body = _text(params, "body", MAX_COMMENT)
        self._dlp(db, company_id, body)

    def _check_github_issue_comment(self, db, company_id, scope, params):
        self._check_comment(db, company_id, params)

    def _check_github_pr_comment(self, db, company_id, scope, params):
        self._check_comment(db, company_id, params)

    def _post_comment(self, db, call, params, *, want_pull: bool) -> dict[str, Any]:
        self._check_comment(db, call.company_id, params)
        number = _num(params)
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            target = client.get_issue(repo, number)
            is_pull = "pull_request" in target
            if is_pull != want_pull:
                what, other = ("a pull request", "github.pr.comment")
                if not is_pull:
                    what, other = ("an issue", "github.issue.comment")
                raise ValidationFailed(
                    f"#{number} is {what}; use {other}", details={"reason": "wrong_target"}
                )
            body = self._with_footer(db, call.company_id, str(params["body"]))
            posted = client.create_issue_comment(repo, number, body)
            return {
                "repo": repo,
                "number": number,
                "comment_id": posted.get("id"),
                "url": posted.get("html_url"),
            }

    def do_github_issue_comment(self, db, call, params):
        return self._post_comment(db, call, params, want_pull=False)

    def do_github_pr_comment(self, db, call, params):
        return self._post_comment(db, call, params, want_pull=True)

    def _check_github_issue_create(self, db, company_id, scope, params):
        title = _text(params, "title", MAX_TITLE)
        body = _text(params, "body", MAX_COMMENT, required=False)
        _names(params, "labels")
        self._dlp(db, company_id, title, body)

    def _existing_labels(self, client, repo: str, wanted: list[str]) -> None:
        known = {str(lb.get("name")).lower() for lb in client.list_labels(repo)}
        missing = [name for name in wanted if name.lower() not in known]
        if missing:
            raise ValidationFailed(
                f"Unknown label(s): {', '.join(missing)}. Only labels that exist in the "
                "repository can be used.",
                details={"reason": "unknown_label", "labels": missing},
            )

    def do_github_issue_create(self, db, call, params):
        self._check_github_issue_create(db, call.company_id, None, params)
        labels = _names(params, "labels")
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            if labels:
                self._existing_labels(client, repo, labels)
            body = self._with_footer(db, call.company_id, str(params.get("body") or ""))
            made = client.create_issue(repo, title=str(params["title"]), body=body, labels=labels)
            return {"repo": repo, "number": made.get("number"), "url": made.get("html_url")}

    def _check_github_issue_update(self, db, company_id, scope, params):
        _num(params)
        add, remove = _names(params, "labels"), _names(params, "remove_labels")
        state = params.get("state")
        if state not in (None, "open", "closed"):
            raise ValidationFailed("`state` must be open or closed")
        title = _text(params, "title", MAX_TITLE, required=False)
        comment = _text(params, "comment", MAX_COMMENT, required=False)
        if state == "closed" and not (comment or "").strip():
            raise ValidationFailed(
                "Closing an issue needs a `comment` that says why",
                details={"reason": "comment_required"},
            )
        if not (add or remove or state or title):
            raise ValidationFailed("Nothing to change: give labels, remove_labels, state or title")
        self._dlp(db, company_id, title, comment)

    def do_github_issue_update(self, db, call, params):
        self._check_github_issue_update(db, call.company_id, None, params)
        add, remove = _names(params, "labels"), _names(params, "remove_labels")
        state, title = params.get("state"), _text(params, "title", MAX_TITLE, required=False)
        comment = _text(params, "comment", MAX_COMMENT, required=False)
        number = _num(params)
        with self._session(db, call, params) as (scope, client):
            repo = scope.binding.full_name
            issue = client.get_issue(repo, number)
            if "pull_request" in issue and state:
                raise ValidationFailed(
                    "Pull requests are opened and closed by Foundry's pull request flow, not here",
                    details={"reason": "wrong_target"},
                )
            labels = None
            if add or remove:
                if add:
                    self._existing_labels(client, repo, add)
                current = _labels_of(issue)
                drop = {r.lower() for r in remove}
                labels = [n for n in [*current, *add] if n.lower() not in drop]
                labels = list(dict.fromkeys(labels))
            commented = None
            if comment and comment.strip():  # the reason is posted FIRST, then the state changes
                body = self._with_footer(db, call.company_id, comment)
                commented = client.create_issue_comment(repo, number, body).get("id")
            changed = client.update_issue(
                repo,
                number,
                state=str(state) if state else None,
                labels=labels,
                title=title,
            )
            return {
                "repo": repo,
                "number": number,
                "state": changed.get("state"),
                "labels": _labels_of(changed),
                "comment_id": commented,
                "url": changed.get("html_url"),
            }

    # ------------------------------------------------------------------ releases

    def _check_release(self, db, company_id, scope, params, *, publish: bool) -> None:
        mode = (scope.binding.policy or {}).get("releases", "off")
        if mode == "off" or (publish and mode != "publish"):
            raise Conflict(
                "Releases are "
                + (
                    "turned off for this repository"
                    if mode == "off"
                    else "limited to drafts (policy releases = draft)"
                ),
                details={"reason": "releases_disabled", "releases": mode},
            )
        tag = _text(params, "tag", 100)
        if not TAG_RE.match(str(tag)):
            raise ValidationFailed(
                "`tag` must look like v1.2.3 (optionally with a -rc.1 style suffix)",
                details={"reason": "bad_tag"},
            )
        name = _text(params, "name", MAX_TITLE, required=False)
        body = _text(params, "body", MAX_COMMENT, required=False)
        target = _text(params, "target", 255, required=False)
        if target and target != scope.binding.default_branch and not SHA_RE.match(target):
            raise ValidationFailed(
                f"`target` must be the default branch ({scope.binding.default_branch}) or its "
                "head commit; releases are cut from the default branch only",
                details={"reason": "target_not_default_branch"},
            )
        self._dlp(db, company_id, name, body)

    def _check_github_release_draft(self, db, company_id, scope, params):
        self._check_release(db, company_id, scope, params, publish=False)

    def _check_github_release_publish(self, db, company_id, scope, params):
        self._check_release(db, company_id, scope, params, publish=True)

    def _create_release(self, db, call, params, *, draft: bool) -> dict[str, Any]:
        with self._session(db, call, params) as (scope, client):
            self._check_release(db, call.company_id, scope, params, publish=not draft)
            repo, binding = scope.binding.full_name, scope.binding
            target = str(params.get("target") or binding.default_branch)
            if SHA_RE.match(target):  # a commit is allowed only when it IS the default head
                head = client.get_branch(repo, binding.default_branch)["commit"]["sha"]
                if target != head:
                    raise ValidationFailed(
                        "`target` is not the head of the default branch; releases are cut from "
                        "the default branch only",
                        details={"reason": "target_not_default_branch"},
                    )
            body = str(params.get("body") or "")
            made = client.create_release(
                repo,
                tag=str(params["tag"]),
                name=str(params.get("name") or params["tag"]),
                body=body,
                draft=draft,  # a draft stays a draft whatever the agent passed
                target_commitish=target,
            )
            return {
                "repo": repo,
                "tag": made.get("tag_name"),
                "draft": bool(made.get("draft")),
                "url": made.get("html_url"),
            }

    def do_github_release_draft(self, db, call, params):
        return self._create_release(db, call, params, draft=True)

    def do_github_release_publish(self, db, call, params):
        """Reached only after a human approval bound to this exact call."""
        return self._create_release(db, call, params, draft=False)

    # ------------------------------------------------------------------ github.repo.create

    def _project_for_create(self, db: Session, company_id: str, agent: Agent, params) -> Project:
        project_id = _s(params, "project_id")
        project = db.get(Project, project_id) if project_id else None
        if project is None or project.company_id != company_id:
            raise ValidationFailed(
                "`project_id` must be a project of this company",
                details=[{"loc": ["params", "project_id"], "msg": "unknown project"}],
            )
        if agent.kind != AgentKind.FOUNDER and project.lead_agent_id != agent.id:
            raise Forbidden(
                "Only the AI Co-founder or the project's lead can ask for a repository for it"
            )
        existing = db.scalar(
            select(ProjectRepository.id).where(
                ProjectRepository.company_id == company_id,
                ProjectRepository.project_id == project.id,
                ProjectRepository.status != "disconnected",
            )
        )
        if existing is not None:
            raise ProjectHasRepository()
        return project

    def _check_repo_create(self, db, rc, params) -> None:
        name = _s(params, "name") or ""
        if not valid_repo_name(name):
            raise ValidationFailed("`name` is not a valid repository name")
        owner = _s(params, "owner", required=False)
        if owner and not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", owner):
            raise ValidationFailed("`owner` is not a valid GitHub user or organisation name")
        visibility = params.get("visibility", "private")
        if visibility not in ("private", "public"):
            raise ValidationFailed("`visibility` must be private or public")
        if visibility == "public" and params.get("confirm_public") is not True:
            raise ValidationFailed(
                "A public repository needs `confirm_public: true`: the code becomes public",
                details={"reason": "confirm_public_required"},
            )
        description = _text(params, "description", 350, required=False)
        self._dlp(db, rc.ctx.company_id, description)
        self._project_for_create(db, rc.ctx.company_id, rc.agent, params)

    def do_github_repo_create(self, db, call, params):
        """Reached only after a human approval bound to this exact call. The repository is created
        and bound to the project by the repository service."""
        account = self.account(db, call)
        agent, run, task = self._run_of(db, call)
        if run is None or task is None:
            raise Forbidden("A repository can only be requested from a run")
        rc = AgentRunContext(ctx=self._agent_ctx(agent), run=run, task=task, agent=agent)
        self._check_repo_create(db, rc, params)
        summary = repositories_service.bind_for_agent(
            db, rc, self.ic, {**params, "connected_account_id": account.id}
        )
        connected_accounts.refresh_scope(db, account)
        return summary


def _slim(body: Any) -> Any:
    if isinstance(body, list):
        return [{"name": e.get("name"), "type": e.get("type")} for e in body[:100]]
    if isinstance(body, dict):
        return {k: body.get(k) for k in ("name", "type", "size", "html_url")}
    return None


__all__ = ["GithubExecutor"]
