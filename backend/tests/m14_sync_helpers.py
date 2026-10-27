"""Shared harness of the M14 WP-B tests (sync loop, PR flow, push gate, workspaces).

``Harness`` binds a project to a repository on the fake GitHub (``tests/m14_helpers.bound_project``)
and drives ``repo_sync.sync_repository`` with the engine's fake clock. The contracts of the other
work packages are replaced by small stand-ins:

* ``github_accounts.client_for_binding`` -> a real ``GithubClient`` over the fake GitHub (the token
  is switchable: ``h.token = ...`` simulates a revoked / rotated token);
* ``repo_maintenance.process`` -> records its calls (``h.maintenance_calls``);
* ``repo_reviews.ensure_pr_review_task`` -> a review task like the real one (``review`` kind, the
  key and refs of the spec), and ``h.verdict(...)`` writes the columns the real ``record_verdict``
  writes.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.integrations.github import GithubClient
from app.models.agent import Agent
from app.models.audit import AuditEvent
from app.models.autonomy import Alert
from app.models.repository import ProjectRepository, RepoItem, RepoPullRequest
from app.models.work import Task
from app.schemas.work import TaskCreate
from app.services import (
    github_accounts,
    repo_maintenance,
    repo_reviews,
    repo_scan,
    repo_sync,
    tasks,
)
from app.services import project_workspaces as pw
from tests.m14_helpers import API_URL, TOKEN, bound_project


class Harness:
    def __init__(self, app, client, company, world, engine, clock, monkeypatch, **bind) -> None:
        self.app = app
        self.client = client
        self.cid = company["id"]
        self.world = world
        self.engine = engine
        self.clock = clock
        self.token = TOKEN
        self.maintenance_calls: list[Any] = []
        self.review_calls = 0
        world.wire_engine(engine)
        monkeypatch.setattr(github_accounts, "client_for_binding", self._client_for_binding)
        monkeypatch.setattr(repo_maintenance, "process", self._process)
        monkeypatch.setattr(repo_reviews, "ensure_pr_review_task", self._ensure_review)
        bind.setdefault("pr_batch_quiet_seconds", 0)
        self.bound = bound_project(world, app, client, self.cid, **bind)
        self.rid = self.bound.repository_id
        self.full_name = self.bound.full_name
        self.work = self.bound.work_branch
        self.default = self.bound.default_branch
        self.repo = self.bound.repo_dir

    # ------------------------------------------------------------ stand-ins

    def _client_for_binding(self, db, env, binding):
        http = httpx.Client(transport=self.world.github.transport())
        client = GithubClient(
            http,
            self.token,
            base_url=API_URL,
            web_url=self.world.web_url,
            etags=binding.etags,
            sleep=lambda s: None,
            write_interval=0,
        )
        return client

    def _process(self, db, env, binding, snapshot, now):
        self.maintenance_calls.append(snapshot)
        return repo_maintenance.MaintenanceReport()

    def _ensure_review(self, db, binding, pr, now, *, attempt=0, assignee=None, settings=None):
        self.review_calls += 1
        lead = assignee or repo_sync.actor_for(db, binding)
        key = f"repo-pr-review:{pr.id}:{pr.revision}" + (f":r{attempt}" if attempt else "")
        data = TaskCreate(
            title=f"Review pull request #{pr.number}: {pr.title}"[:300],
            description="Record your verdict.",
            kind="review",
            project_id=binding.project_id,
            assignee_agent_id=lead.id,
            idempotency_key=key,
            input_refs=[f"github:pr:{pr.id}"],
        )
        task, _ = tasks.create_system_task(db, binding.company_id, data, now=now)
        pr.review_task_id = task.id
        return task

    # ------------------------------------------------------------ driving

    def env(self) -> repo_sync.RepoEnv:
        return repo_sync.RepoEnv.from_engine(self.engine)

    def sync(self, advance: float = 0) -> None:
        if advance:
            self.clock.advance(advance)
        repo_sync.sync_repository(self.env(), self.app.state.session_factory, self.rid)

    def binding(self) -> ProjectRepository:
        with self.app.state.session_factory() as db:
            row = db.get(ProjectRepository, self.rid)
            db.expunge(row)
            return row

    def set_policy(self, **policy: Any) -> None:
        with self.app.state.session_factory() as db:
            row = db.get(ProjectRepository, self.rid)
            row.policy = {**row.policy, **policy}
            db.commit()

    def update(self, **fields: Any) -> None:
        with self.app.state.session_factory() as db:
            row = db.get(ProjectRepository, self.rid)
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()

    def pulls(self) -> list[RepoPullRequest]:
        with self.app.state.session_factory() as db:
            rows = list(
                db.scalars(
                    select(RepoPullRequest)
                    .where(RepoPullRequest.repository_id == self.rid)
                    .order_by(RepoPullRequest.created_at, RepoPullRequest.number)
                )
            )
            db.expunge_all()
            return rows

    def pr(self) -> RepoPullRequest | None:
        rows = self.pulls()
        return rows[-1] if rows else None

    def set_pr(self, **fields: Any) -> None:
        with self.app.state.session_factory() as db:
            row = db.scalars(
                select(RepoPullRequest)
                .where(RepoPullRequest.repository_id == self.rid)
                .order_by(RepoPullRequest.created_at.desc())
            ).first()
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()

    def audit(self, action: str) -> list[AuditEvent]:
        with self.app.state.session_factory() as db:
            rows = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.company_id == self.cid, AuditEvent.action == action
                    )
                )
            )
            db.expunge_all()
            return rows

    def alerts(self, kind: str | None = None) -> list[Alert]:
        with self.app.state.session_factory() as db:
            stmt = select(Alert).where(Alert.company_id == self.cid)
            if kind:
                stmt = stmt.where(Alert.kind == kind)
            rows = list(db.scalars(stmt))
            db.expunge_all()
            return rows

    def tasks(self, **where: Any) -> list[Task]:
        with self.app.state.session_factory() as db:
            stmt = select(Task).where(Task.company_id == self.cid)
            for key, value in where.items():
                stmt = stmt.where(getattr(Task, key) == value)
            rows = list(db.scalars(stmt.order_by(Task.created_at)))
            db.expunge_all()
            return rows

    def items(self, **where: Any) -> list[RepoItem]:
        with self.app.state.session_factory() as db:
            stmt = select(RepoItem).where(RepoItem.repository_id == self.rid)
            for key, value in where.items():
                stmt = stmt.where(getattr(RepoItem, key) == value)
            rows = list(db.scalars(stmt))
            db.expunge_all()
            return rows

    def finish_task(self, task_id: str, status: str = "done") -> None:
        with self.app.state.session_factory() as db:
            task = db.get(Task, task_id)
            task.status = status
            db.commit()

    # ------------------------------------------------------------ the project repository

    def git(self, *args: str, check: bool = True) -> str:
        from tests.m14_git_remote import run_git

        return run_git(*args, cwd=self.repo, check=check).stdout.strip()

    def work_sha(self) -> str:
        return self.git("rev-parse", f"refs/heads/{self.work}")

    def remote_sha(self, branch: str) -> str | None:
        return self.world.remote.branch_sha(self.full_name, branch)

    def policy_model(self):
        from app.schemas.repository import RepoPolicy

        return RepoPolicy.model_validate(self.binding().policy)

    def scan_context(self, **overrides: Any) -> repo_scan.ScanContext:
        ctx = repo_scan.ScanContext(
            policy=self.policy_model(),
            own_secrets=(),
            internal=[],
            exclude_refs=(
                f"refs/remotes/origin/{self.default}",
                f"refs/remotes/origin/{self.work}",
            ),
        )
        for key, value in overrides.items():
            setattr(ctx, key, value)
        return ctx

    def spec(self, **overrides: Any) -> repo_sync.WorkspaceSpec:
        pol = self.policy_model()
        base = {
            "kind": "project",
            "branch": self.work,
            "scan_secrets": True,
            "protected_paths": repo_scan.effective_protected_paths(pol),
            "max_file_bytes": pol.max_file_bytes,
            "repository_id": self.rid,
        }
        base.update(overrides)
        return repo_sync.WorkspaceSpec(**base)

    def merge_run(
        self,
        files: dict[str, str | None],
        *,
        run_id: str = "run1",
        title: str = "Change",
        succeeded: bool = True,
        scan: repo_scan.ScanContext | None = None,
        spec: repo_sync.WorkspaceSpec | None = None,
        task_kind: str = "work",
    ) -> tuple[pw.Workspace, pw.Finalized]:
        """What a run does: a worktree on run/<id> from the work branch, the files written,
        finalize (push gate, commit, merge into the work branch)."""
        root = Path(self.app.state.settings.workspaces_dir)
        project_id = self.bound.project["id"]
        ws = pw.prepare(
            root,
            company_id=self.cid,
            run_id=run_id,
            task_kind=task_kind,
            project_id=project_id,
            spec=spec or self.spec(),
        )
        assert ws.available, ws.note
        for name, content in files.items():
            target = ws.path / name
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        fin = pw.finalize(
            ws,
            succeeded=succeeded,
            agent_id="a" * 8,
            agent_name="Dev",
            task_title=title,
            scan=scan if scan is not None else self.scan_context(),
        )
        return ws, fin

    # ------------------------------------------------------------ pull request helpers

    def verdict(self, decision: str = "accepted", *, note: str = "ok") -> None:
        """What ``repo_reviews.record_verdict`` writes (never the stage)."""
        with self.app.state.session_factory() as db:
            pr = db.scalars(
                select(RepoPullRequest)
                .where(RepoPullRequest.repository_id == self.rid)
                .order_by(RepoPullRequest.created_at.desc())
            ).first()
            assert pr.review_sha, "no review was requested yet"
            pr.verdict = decision
            pr.verdict_note = note
            pr.verdict_sha = pr.review_sha
            if pr.review_task_id:
                task = db.get(Task, pr.review_task_id)
                task.status = "done"
            db.commit()

    def lead(self) -> Agent:
        with self.app.state.session_factory() as db:
            agent = repo_sync.actor_for(db, db.get(ProjectRepository, self.rid))
            db.expunge(agent)
            return agent

    def ship(self, files: dict[str, str] | None = None, *, title: str = "Add feature") -> None:
        """A run merges into the work branch and the sync pushes and opens the PR."""
        _ws, fin = self.merge_run(files or {"feature.txt": "feature\n"}, title=title)
        assert fin.merged, fin.error_message
        self.sync(advance=1)


@pytest.fixture
def harness_factory(app, client, company, github_world, engine, clock, monkeypatch):
    def make(**bind) -> Harness:
        return Harness(app, client, company, github_world, engine, clock, monkeypatch, **bind)

    return make


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


def summary(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**kw)
