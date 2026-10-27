"""M14 WP-B: workspaces of GitHub-backed projects (work branch instead of `main`, the push gate
before the merge, upstream-merge and read-only review workspaces) and the engine hooks that use
them. Real git, a real bare remote; the runs are scripted."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.repository import RepoPullRequest
from app.models.run import TaskRun
from app.models.work import Task
from app.runtimes.scripted import ScriptedRuntime
from app.services import project_workspaces as pw
from app.services import repo_sync, retry_policy
from tests.helpers import new_agent, new_task, settle
from tests.m14_git_remote import run_git
from tests.m14_helpers import (
    LOGIN,
    TOKEN,
    github_world,  # noqa: F401  (fixture)
)
from tests.m14_sync_helpers import harness_factory  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NAME = f"{LOGIN}/app"
SECRET = (
    "github_pat_11WORKSPACE0_"
    + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0Pp9Oo8Nn7Mm6Ll5Kk4Jj3Ii2Hh1Gg0Ff9Ee8Dd7"
)


# ------------------------------------------------------------------ branches


def test_a_run_branches_from_the_work_branch_and_merges_back_into_it(harness_factory):
    h = harness_factory()
    h.sync()
    h.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    before = h.work_sha()
    ws, fin = h.merge_run({"a.txt": "a\n"}, run_id="r1")
    assert ws.branch == "run/r1" and ws.base == before and ws.integration == h.work
    assert fin.merged and fin.ref["merge_commit"] == h.work_sha()
    assert h.git("show", f"{h.work}:a.txt") == "a"
    assert h.git("branch", "--list", "run/*") == ""
    assert h.git("symbolic-ref", "--short", "HEAD") == h.work
    assert h.git("log", "-1", "--format=%s", h.work).startswith("Merge run/r1: Change")


def test_the_merge_message_names_the_run_so_the_pull_request_can_list_it(harness_factory):
    from app.services import repo_git

    h = harness_factory()
    h.sync()
    base = h.work_sha()
    h.merge_run({"a.txt": "a\n"}, run_id="runA", title="Add a")
    h.merge_run({"b.txt": "b\n"}, run_id="runB", title="Add b")
    assert repo_git.merged_runs(h.repo, base, h.work) == ["runA", "runB"]


def test_a_local_only_project_is_unchanged_and_never_scanned(tmp_path):
    root = tmp_path / "ws"
    ws = pw.prepare(root, company_id="c1", run_id="r1", task_kind="work", project_id="p1")
    assert ws.available and ws.integration == "main" and ws.spec is None
    (ws.path / "secret.txt").write_text(f"{SECRET}\n")
    fin = pw.finalize(ws, succeeded=True, agent_id="a1", agent_name="Dev", task_title="t")
    assert fin.merged and fin.error_code is None  # M12 behaviour: no push gate without a remote
    assert pw.main_repo(root, "c1", "p1").joinpath("secret.txt").exists()


def test_a_remote_backed_project_without_its_clone_falls_back_instead_of_creating_one(
    tmp_path,
):
    spec = repo_sync.WorkspaceSpec(kind="project", branch="foundry/x", repository_id="r")
    ws = pw.prepare(
        tmp_path / "ws",
        company_id="c1",
        run_id="r1",
        task_kind="work",
        project_id="p1",
        spec=spec,
    )
    assert not ws.available and ws.kind == "run_dir" and "not ready" in ws.note
    assert not (tmp_path / "ws" / "c1" / "projects").exists()  # nothing was git-init'ed


# ------------------------------------------------------------------ the push gate before the merge


def blocked_cases():
    return [
        ("a secret", {"conf.py": f'K = "{SECRET}"\n'}, "secret_detected"),
        ("a .env file", {".env": "A=1\n"}, "secret_detected"),
        ("a workflow", {".github/workflows/ci.yml": "on: push\n"}, "protected_path"),
        ("a huge file", {"big.bin": "x" * 20_000}, "workspace_too_large"),
    ]


@pytest.mark.parametrize(("label", "files", "code"), blocked_cases(), ids=lambda v: str(v)[:20])
def test_the_push_gate_refuses_to_merge_and_deletes_the_run_branch(
    harness_factory, label, files, code
):
    h = harness_factory(max_file_bytes=10_240)
    h.sync()
    before = h.work_sha()
    ws, fin = h.merge_run(files, run_id="bad")
    assert fin.error_code == code and not fin.merged, label
    assert fin.ref["blocked"] and fin.ref["error"] == code
    assert SECRET not in fin.error_message and SECRET not in str(fin.ref)
    assert h.work_sha() == before
    assert h.git("branch", "--list", "run/*") == ""  # deleted, NOT kept: it holds the secret
    assert fin.artifacts == []  # no "Changes" artifact spreading the content
    assert not ws.path.exists()
    assert retry_policy.classify(code, fin.error_message) == (retry_policy.PERMANENT), (
        "refusals of the push gate are permanent failures"
    )


def test_a_failed_run_with_a_secret_does_not_keep_its_branch_either(harness_factory):
    h = harness_factory()
    h.sync()
    ws, fin = h.merge_run({"conf.py": f'K = "{SECRET}"\n'}, run_id="f1", succeeded=False)
    assert fin.error_code is None and not fin.merged  # the run already failed on its own
    assert h.git("branch", "--list", "run/*") == ""


def test_a_stored_credential_of_the_company_is_refused_too(harness_factory):
    h = harness_factory()
    h.sync()
    scan = h.scan_context(own_secrets=("company-internal-password-1234",))
    _ws, fin = h.merge_run({"x.txt": "pw = company-internal-password-1234\n"}, scan=scan)
    assert fin.error_code == "secret_detected"


def test_workflow_files_pass_when_the_owner_allows_them(harness_factory):
    h = harness_factory(allow_workflow_changes=True)
    h.sync()
    _ws, fin = h.merge_run({".github/workflows/ci.yml": "on: push\n"})
    assert fin.merged and fin.error_code is None


def test_clean_work_is_merged_and_email_addresses_do_not_block(harness_factory):
    h = harness_factory()
    h.sync()
    _ws, fin = h.merge_run({"AUTHORS": "Alice <alice@example.org>\n", "src/a.py": "x = 1\n"})
    assert fin.merged and fin.error_code is None and fin.artifacts


# ------------------------------------------------------------------ upstream merge


def conflict(h):
    """work and origin/main both changed README.md; the sync found the conflict."""
    h.sync()
    _ws, fin = h.merge_run({"README.md": "# mine\n"}, run_id="mine")
    assert fin.merged
    h.sync(advance=1)
    theirs = h.world.human_commit(
        NAME, {"README.md": "# theirs\n", "other.py": f'K = "{SECRET}"\n'}, message="human"
    )
    h.sync(advance=1)
    assert h.binding().sync_state == "upstream_conflict"
    return theirs


def test_an_upstream_merge_workspace_leaves_the_conflict_markers_to_resolve(harness_factory):
    h = harness_factory()
    theirs = conflict(h)
    spec = h.spec(kind="upstream_merge", upstream_sha=theirs)
    root = Path(h.app.state.settings.workspaces_dir)
    ws = pw.prepare(
        root,
        company_id=h.cid,
        run_id="up1",
        task_kind="work",
        project_id=h.bound.project["id"],
        spec=spec,
    )
    assert ws.available and ws.conflict_files == ["README.md"]
    text = (ws.path / "README.md").read_text()
    assert "<<<<<<<" in text and "=======" in text and ">>>>>>>" in text
    # MERGE_HEAD lives in the trusted worktree git dir
    assert (ws.gitdir / "MERGE_HEAD").read_text().strip() == theirs
    # still unresolved: the run is refused with a retryable error and nothing is committed
    fin = pw.finalize(
        ws,
        succeeded=True,
        agent_id="a" * 8,
        agent_name="Dev",
        task_title="t",
        scan=h.scan_context(),
    )
    assert fin.error_code == "merge_conflict_unresolved" and not fin.merged
    assert retry_policy.classify(fin.error_code, fin.error_message) == retry_policy.TRANSIENT
    assert "README.md" in fin.error_message
    assert h.git("branch", "--list", "run/*") == "" and not ws.path.exists()


def test_a_resolved_upstream_merge_becomes_a_two_parent_merge_commit_on_the_work_branch(
    harness_factory,
):
    h = harness_factory()
    theirs = conflict(h)
    own = h.work_sha()
    spec = h.spec(kind="upstream_merge", upstream_sha=theirs)
    root = Path(h.app.state.settings.workspaces_dir)
    ws = pw.prepare(
        root,
        company_id=h.cid,
        run_id="up2",
        task_kind="work",
        project_id=h.bound.project["id"],
        spec=spec,
    )
    (ws.path / "README.md").write_text("# mine and theirs\n")
    # the human commit that came with it (it has a "secret" in other.py) is NOT ours to scan
    fin = pw.finalize(
        ws,
        succeeded=True,
        agent_id="a" * 8,
        agent_name="Dev",
        task_title="Resolve conflicts",
        scan=h.scan_context(),
    )
    assert fin.merged and fin.error_code is None, fin.error_message
    # the run's own commit is the merge of upstream into the work branch (two parents) ...
    run_commit = fin.ref["commit"]
    parents = h.git("rev-list", "--parents", "-n1", run_commit).split()[1:]
    assert parents == [own, theirs]
    # ... and the work branch contains upstream afterwards
    assert h.git("merge-base", "--is-ancestor", theirs, h.work, check=False) == ""
    assert h.git("show", f"{h.work}:README.md") == "# mine and theirs"
    h.sync(advance=1)
    assert h.binding().sync_state == "idle"


def test_the_workspace_spec_of_a_conflict_task_names_the_origin_commit(harness_factory):
    h = harness_factory()
    theirs = conflict(h)
    [task] = [t for t in h.tasks() if (t.idempotency_key or "").startswith("repo-conflict")]
    with h.app.state.session_factory() as db:
        spec = repo_sync.workspace_spec_for(db, db.get(Task, task.id))
    assert spec.kind == "upstream_merge" and spec.upstream_sha == theirs
    assert spec.branch == h.work and spec.scan_secrets and spec.repository_id == h.rid


# ------------------------------------------------------------------ read-only workspaces


def test_a_review_workspace_shows_an_arbitrary_commit_and_is_read_only(harness_factory):
    h = harness_factory()
    h.sync()
    h.merge_run({"v1.txt": "one\n"}, run_id="r1")
    first = h.work_sha()
    h.merge_run({"v1.txt": "two\n", "v2.txt": "new\n"}, run_id="r2")
    assert h.work_sha() != first
    root = Path(h.app.state.settings.workspaces_dir)
    spec = h.spec(kind="review", read_only=True, ref=first, scan_secrets=False)
    ws = pw.prepare(
        root,
        company_id=h.cid,
        run_id="rev1",
        task_kind="review",
        project_id=h.bound.project["id"],
        spec=spec,
    )
    assert ws.kind == "review" and ws.base == first and ws.branch is None
    assert (ws.path / "v1.txt").read_text() == "one\n" and not (ws.path / "v2.txt").exists()
    assert not os.access(ws.path / "v1.txt", os.W_OK)
    assert (ws.path / "v1.txt").stat().st_mode & stat.S_IWUSR == 0
    fin = pw.finalize(ws, succeeded=True, agent_id="a" * 8, agent_name="Dev", task_title="Review")
    assert not fin.merged and fin.ref["commit"] == first and not ws.path.exists()
    assert h.git("branch", "--list", "run/*") == ""


def test_the_codebase_onboarding_task_works_in_a_read_only_checkout(harness_factory):
    h = harness_factory()
    h.update(onboarding_status="pending")
    h.sync()
    [task] = h.tasks(idempotency_key=f"repo-onboard:{h.rid}")
    with h.app.state.session_factory() as db:
        spec = repo_sync.workspace_spec_for(db, db.get(Task, task.id))
    assert spec.kind == "review" and spec.read_only and not spec.scan_secrets
    root = Path(h.app.state.settings.workspaces_dir)
    before = h.work_sha()
    ws = pw.prepare(
        root,
        company_id=h.cid,
        run_id="onb",
        task_kind="work",
        project_id=h.bound.project["id"],
        spec=spec,
    )
    assert ws.kind == "review" and (ws.path / "README.md").exists()
    # whatever the run writes can never be merged
    with pytest.raises(PermissionError):
        (ws.path / "README.md").write_text("tampered\n")
    fin = pw.finalize(ws, succeeded=True, agent_id="a" * 8, agent_name="Dev", task_title="Learn")
    assert not fin.merged and h.work_sha() == before


def test_workspace_specs_follow_the_task_the_binding_and_the_policy(harness_factory):
    h = harness_factory()
    h.sync()
    pid = h.bound.project["id"]
    plain = new_task(h.client, h.cid, title="Plain", project_id=pid)
    loose = new_task(h.client, h.cid, title="No project")
    h.ship()
    pr = h.pr()
    review = new_task(
        h.client,
        h.cid,
        title="Review PR",
        project_id=pid,
        kind="review",
        input_refs=[f"github:pr:{pr.id}"],
    )
    with h.app.state.session_factory() as db:
        spec = repo_sync.workspace_spec_for(db, db.get(Task, plain["id"]))
        assert (spec.kind, spec.branch, spec.scan_secrets, spec.read_only) == (
            "project",
            h.work,
            True,
            False,
        )
        assert spec.max_file_bytes == 10 * 1024 * 1024
        assert ".github/workflows/**" in spec.protected_paths
        assert repo_sync.workspace_spec_for(db, db.get(Task, loose["id"])) is None
        rspec = repo_sync.workspace_spec_for(db, db.get(Task, review["id"]))
        assert rspec.kind == "review" and rspec.read_only and rspec.ref == pr.review_sha
    h.set_policy(allow_workflow_changes=True)
    with h.app.state.session_factory() as db:
        spec = repo_sync.workspace_spec_for(db, db.get(Task, plain["id"]))
        assert ".github/workflows/**" not in spec.protected_paths
    # a binding that is still cloning has no workspace yet (the dispatcher holds the task)
    h.update(status="cloning")
    with h.app.state.session_factory() as db:
        assert repo_sync.workspace_spec_for(db, db.get(Task, plain["id"])) is None


# ------------------------------------------------------------------ through the engine


class RepoRuntime(ScriptedRuntime):
    id = "repo"
    display_name = "Repo test runtime"

    def __init__(self) -> None:
        super().__init__()
        self.hooks: dict[str, object] = {}
        self.seen: list[dict] = []

    def execute(self, request, sink, cancel):
        self.seen.append(
            {
                "title": request.task.title,
                "dir": request.workspace_dir,
                "kind": request.workspace_kind,
                "files": list(request.workspace_files),
            }
        )
        hook = self.hooks.get(request.task.title)
        if hook is not None:
            hook(request)
        return super().execute(request, sink, cancel)


@pytest.fixture
def repo_runtime(app) -> RepoRuntime:
    runtime = RepoRuntime()
    app.state.runtimes.register(runtime)
    return runtime


def test_a_run_merges_into_the_work_branch_and_pokes_the_sync(
    harness_factory, repo_runtime, scheduler, worker, clock
):
    h = harness_factory()
    h.sync()
    h.update(next_sync_at=h.clock() + __import__("datetime").timedelta(hours=1))
    agent = new_agent(h.client, h.cid, runtime="repo")
    repo_runtime.hooks["Build"] = lambda req: (Path(req.workspace_dir) / "built.txt").write_text(
        "ok\n"
    )
    task = new_task(
        h.client,
        h.cid,
        title="Build",
        project_id=h.bound.project["id"],
        assignee_agent_id=agent["id"],
    )
    settle(scheduler, worker)
    assert h.client.get(f"/api/companies/{h.cid}/tasks/{task['id']}").json()["status"] == "done"
    [seen] = repo_runtime.seen
    assert seen["kind"] == "project" and "README.md" in seen["files"]
    assert h.git("show", f"{h.work}:built.txt") == "ok"
    assert h.binding().next_sync_at <= h.clock()  # the merge made the next sync due


def test_a_run_that_writes_a_secret_fails_permanently_and_nothing_is_merged(
    harness_factory, repo_runtime, scheduler, worker, clock, app
):
    h = harness_factory()
    h.sync()
    agent = new_agent(h.client, h.cid, runtime="repo")
    before = h.work_sha()
    repo_runtime.hooks["Leak"] = lambda req: (Path(req.workspace_dir) / "k.py").write_text(
        f'KEY = "{SECRET}"\n'
    )
    task = new_task(
        h.client,
        h.cid,
        title="Leak",
        project_id=h.bound.project["id"],
        assignee_agent_id=agent["id"],
    )
    settle(scheduler, worker)
    with app.state.session_factory() as db:
        runs = list(db.scalars(select(TaskRun).where(TaskRun.task_id == task["id"])))
        assert runs and all(r.status == "failed" for r in runs)  # every attempt hits the gate
        run = runs[0]
        assert run.error["code"] == "secret_detected"
        assert "must not leave the machine" in run.error["message"]
        assert SECRET not in str(run.error) and SECRET not in str(run.workspace_ref)
        assert run.workspace_ref["blocked"] and run.workspace_ref["error"] == "secret_detected"
        audits = list(
            db.scalars(select(AuditEvent).where(AuditEvent.action == "workspace.blocked"))
        )
        assert audits and SECRET not in str(audits[0].data)
    assert h.work_sha() == before and h.git("branch", "--list", "run/*") == ""
    assert retry_policy.is_transient_run(run.error, run.exit_reason) is False


def test_the_review_of_a_pull_request_runs_read_only_at_the_reviewed_commit(
    harness_factory, repo_runtime, scheduler, worker, clock, app
):
    h = harness_factory()
    h.ship({"feature.txt": "feature\n"})
    pr = h.pr()
    reviewer = new_agent(h.client, h.cid, runtime="repo")
    with h.app.state.session_factory() as db:
        review_task = db.get(Task, pr.review_task_id)
        review_task.assignee_agent_id = reviewer["id"]
        db.commit()
    seen = {}

    def hook(req):
        root = Path(req.workspace_dir)
        seen["feature"] = (root / "feature.txt").read_text()
        seen["writable"] = os.access(root / "feature.txt", os.W_OK)

    repo_runtime.hooks[h.tasks(id=pr.review_task_id)[0].title] = hook
    # more work after the review started must not leak into the reviewed checkout
    h.merge_run({"later.txt": "later\n"}, run_id="later")
    settle(scheduler, worker)
    assert seen == {"feature": "feature\n", "writable": False}
    [only] = repo_runtime.seen
    assert "later.txt" not in only["files"] and "feature.txt" in only["files"]


def test_the_dispatcher_holds_work_while_the_clone_is_not_ready(
    harness_factory, repo_runtime, scheduler, worker
):
    h = harness_factory()
    h.sync()
    h.update(status="cloning")
    agent = new_agent(h.client, h.cid, runtime="repo")
    task = new_task(
        h.client,
        h.cid,
        title="Early",
        project_id=h.bound.project["id"],
        assignee_agent_id=agent["id"],
    )
    settle(scheduler, worker)
    assert repo_runtime.seen == []
    h.update(status="active")
    settle(scheduler, worker)
    assert [s["title"] for s in repo_runtime.seen] == ["Early"]
    assert h.client.get(f"/api/companies/{h.cid}/tasks/{task['id']}").json()["status"] == "done"


def test_pull_request_rows_are_not_touched_by_a_blocked_run(harness_factory):
    h = harness_factory()
    h.sync()
    h.merge_run({"conf.py": f'K = "{SECRET}"\n'}, run_id="bad")
    with h.app.state.session_factory() as db:
        assert db.scalars(select(RepoPullRequest)).first() is None
    assert TOKEN not in str(run_git("log", "--all", "--format=%B", cwd=h.repo).stdout)
