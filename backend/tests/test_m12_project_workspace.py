"""M12 WP4: one git repository per project, a worktree per run, serialized merges.

Real git in tmp_path (skipped when git is missing). No coding CLI and no model runs: runs are a
scripted runtime whose "agent" is a Python hook that edits the run's directory, exactly as a CLI
would, and the merge, conflict and cleanup paths are the production ones.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
from pathlib import Path

import pytest
from sqlalchemy import select

from app.engine import executor as executor_module
from app.engine.executor import CLI_MIN_STALL_SECONDS, effective_stall_seconds
from app.models.artifact import Artifact
from app.models.audit import AuditEvent
from app.models.run import TaskRun
from app.models.work import Task
from app.runtimes.cli import workspace as cli_workspace
from app.runtimes.scripted import ScriptedRuntime
from app.services import project_workspaces as pw
from app.services import reviews
from tests.helpers import api, get_task, new_agent, new_task, set_policy, settle

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def show(repo: Path, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"main:{path}"], capture_output=True, text=True
    )
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else None


# ------------------------------------------------------------------ unit level: the service


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def start(root: Path, run_id: str, kind: str = "work") -> pw.Workspace:
    ws = pw.prepare(root, company_id="c1", run_id=run_id, task_kind=kind, project_id="p1")
    assert ws.available, ws.note
    return ws


def end(ws: pw.Workspace, *, ok: bool = True, title: str = "task") -> pw.Finalized:
    return pw.finalize(ws, succeeded=ok, agent_id="a1", agent_name="Ann", task_title=title)


def repo_of(root: Path) -> Path:
    return pw.main_repo(root, "c1", "p1")


def test_the_repository_is_created_once_with_an_initial_commit(root):
    first = start(root, "r1")
    assert first.kind == pw.KIND_PROJECT and first.branch == "run/r1" and first.files == []
    repo = repo_of(root)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(repo, "rev-list", "--count", "main") == "1"
    second = start(root, "r2")
    assert git(repo, "rev-list", "--count", "main") == "1"  # not initialised again
    assert second.base == first.base
    # tenancy in the path: <workspaces>/<company>/projects/<project>/main and <company>/<run>
    assert repo == root / "c1" / "projects" / "p1" / "main"
    assert first.path == root / "c1" / "r1"


def test_sequential_runs_share_files(root):
    one = start(root, "r1")
    (one.path / "src").mkdir()
    (one.path / "src" / "app.py").write_text("print('hi')\n")
    (one.path / "README.md").write_text("# Site\n")
    done = end(one, title="Create the app")
    assert done.merged and not done.conflict and done.error_code is None
    assert done.ref["merged"] is True and done.ref["conflict"] is False
    assert done.ref["base"] == one.base and done.ref["commit"] and done.ref["merge_commit"]
    assert not one.path.exists()  # the worktree is gone
    assert "run/r1" not in git(repo_of(root), "branch", "--list", "run/*")  # merged: branch dropped

    two = start(root, "r2")
    assert sorted(two.files) == ["README.md", "src/app.py"]
    assert (two.path / "src" / "app.py").read_text() == "print('hi')\n"
    (two.path / "src" / "app.py").write_text("print('hi')\nprint('again')\n")
    assert end(two, title="Extend the app").merged
    assert show(repo_of(root), "src/app.py") == "print('hi')\nprint('again')"
    # the run is committed as the agent, merged by the system
    assert "Ann" in git(repo_of(root), "log", "--format=%an", "main")
    assert git(repo_of(root), "log", "-1", "--format=%cn", "main") == pw.COMMITTER_NAME


def test_two_parallel_runs_both_merge(root):
    a, b = start(root, "r1"), start(root, "r2")
    (a.path / "a.txt").write_text("a\n")
    (b.path / "b.txt").write_text("b\n")
    results: dict[str, pw.Finalized] = {}

    def finish(ws: pw.Workspace) -> None:
        results[ws.run_id] = end(ws)

    threads = [threading.Thread(target=finish, args=(w,)) for w in (a, b)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert all(r.merged and not r.conflict for r in results.values()), results
    assert show(repo_of(root), "a.txt") == "a" and show(repo_of(root), "b.txt") == "b"
    assert git(repo_of(root), "status", "--porcelain") == ""  # main's tree is clean


def test_conflicting_edits_fail_with_merge_conflict_and_the_retry_starts_from_the_new_main(root):
    seed = start(root, "r0")
    (seed.path / "shared.txt").write_text("line1\nline2\n")
    assert end(seed).merged

    mine, theirs = start(root, "r1"), start(root, "r2")
    (mine.path / "shared.txt").write_text("mine\nline2\n")
    (theirs.path / "shared.txt").write_text("theirs\nline2\n")
    assert end(theirs).merged
    lost = end(mine, title="Edit the shared file")
    assert lost.conflict and not lost.merged
    assert lost.error_code == "merge_conflict" == pw.ERR_MERGE_CONFLICT
    assert "shared.txt" in (lost.error_message or "") and lost.ref["conflict_files"] == [
        "shared.txt"
    ]
    assert show(repo_of(root), "shared.txt") == "theirs\nline2"  # nothing half-merged
    assert git(repo_of(root), "status", "--porcelain") == ""
    assert lost.ref.get("branch_kept") and "run/r1" in git(repo_of(root), "branch", "--list")
    assert [a.title for a in lost.artifacts] == ["Changes"]
    assert "NOT merged" in lost.artifacts[0].content

    retry = start(root, "r3")  # a retry is a new run: it starts from the current main
    assert (retry.path / "shared.txt").read_text() == "theirs\nline2\n"
    (retry.path / "shared.txt").write_text("theirs\nline2\nline3\n")
    assert end(retry, title="Edit the shared file").merged
    assert show(repo_of(root), "shared.txt") == "theirs\nline2\nline3"


def test_a_review_gets_a_read_only_worktree_and_its_changes_are_discarded(root):
    seed = start(root, "r0")
    (seed.path / "index.html").write_text("<h1>x</h1>\n")
    assert end(seed).merged
    head = git(repo_of(root), "rev-parse", "main")

    review = start(root, "rv", kind="review")
    assert review.kind == pw.KIND_REVIEW and review.branch is None
    assert review.files == ["index.html"]
    assert git(review.path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached
    with pytest.raises(PermissionError):
        (review.path / "index.html").write_text("tampered")
    (review.path / "scratch.txt").write_text("notes")
    done = end(review)
    assert done.ref["kind"] == "review" and done.ref["discarded"] >= 1
    assert not done.merged and not done.artifacts and done.error_code is None
    assert not review.path.exists()
    assert git(repo_of(root), "rev-parse", "main") == head
    assert show(repo_of(root), "scratch.txt") is None
    assert "rv" not in git(repo_of(root), "branch", "--list")


@pytest.mark.parametrize("hook", ["post-commit", "pre-commit", "commit-msg", "post-merge"])
def test_a_planted_hook_never_runs(root, tmp_path, hook):
    marker = tmp_path / "hook-ran"
    script = f"#!/bin/sh\ntouch {marker}\n"
    ws = start(root, "r1")
    repo = repo_of(root)
    for hooks_dir in (repo / ".git" / "hooks", repo / "custom-hooks"):
        hooks_dir.mkdir(exist_ok=True)
        (hooks_dir / hook).write_text(script)
        (hooks_dir / hook).chmod(0o755)
    git(repo, "config", "core.hooksPath", str(repo / "custom-hooks"))  # repo config says "run it"
    fsmon = tmp_path / "fsmonitor"
    fsmon.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fsmon.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(fsmon))
    (ws.path / "f.txt").write_text("x\n")
    result = end(ws)
    assert result.merged, result
    assert not marker.exists()
    # the hook really is armed: plain git (no hardening) runs it
    ident = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    plain = subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "control"],
        capture_output=True,
        env={**os.environ, **ident},
    )
    assert plain.returncode == 0, plain.stderr
    if hook in ("post-commit", "pre-commit", "commit-msg"):
        assert marker.exists()


def test_without_git_runs_fall_back_to_per_run_dirs_and_report_unavailable(root, monkeypatch):
    monkeypatch.setattr(pw, "git_binary", lambda: None)
    assert pw.status()["available"] is False and "git" in pw.status()["reason"]
    ws = pw.prepare(root, company_id="c1", run_id="r1", task_kind="work", project_id="p1")
    assert ws.kind == pw.KIND_RUN_DIR and ws.path == root / "c1" / "r1" and ws.path.is_dir()
    assert ws.available is False and ws.files == []
    (ws.path / "x.txt").write_text("kept")
    done = end(ws)
    assert done.ref["available"] is False and done.ref["kind"] == "run_dir"
    assert done.artifacts == [] and done.error_code is None
    assert (ws.path / "x.txt").read_text() == "kept"  # a per-run dir is left alone
    assert not (root / "c1" / "projects").exists()
    # a task with no project is a plain per-run dir and not an "unavailable" case
    plain = pw.prepare(root, company_id="c1", run_id="r2", task_kind="work", project_id=None)
    assert plain.kind == pw.KIND_RUN_DIR and plain.available is True


def test_a_git_failure_degrades_to_a_per_run_dir(root, monkeypatch):
    def boom(*_a, **_k):
        raise pw.GitError("git init failed: disk full")

    monkeypatch.setattr(pw, "ensure_repo", boom)
    ws = pw.prepare(root, company_id="c1", run_id="r1", task_kind="work", project_id="p1")
    assert ws.kind == pw.KIND_RUN_DIR and ws.available is False and "disk full" in ws.note


def test_the_changes_artifact_is_a_scrubbed_bounded_diff(root):
    ws = start(root, "r1")
    (ws.path / "config.txt").write_text("token = " + "A1b2C3d4" * 4 + "\nplain line\n")
    (ws.path / "zbig.txt").write_text("".join(f"line {i} {'x' * 60}\n" for i in range(6000)))
    done = end(ws, title="Add files")
    [artifact] = done.artifacts
    assert (artifact.title, artifact.kind, artifact.mime_type) == ("Changes", "code", "text/x-diff")
    text = artifact.content
    assert isinstance(text, str) and len(text.encode()) <= pw.MAX_CHANGESET_BYTES
    assert "2 files changed" in text and "merged into main" in text
    assert "A1b2C3d4" * 4 not in text and "[redacted]" in text  # secrets never go into artifacts
    assert "patch truncated" in text
    assert show(repo_of(root), "config.txt").startswith("token = A1b2")  # the repo itself is intact


def test_an_empty_run_leaves_no_branch_and_no_artifact(root):
    ws = start(root, "r1")
    done = end(ws)
    assert not done.merged and not done.conflict and done.artifacts == []
    assert done.ref["commit"] == ws.base
    assert git(repo_of(root), "branch", "--list", "run/*") == ""
    assert git(repo_of(root), "rev-list", "--count", "main") == "1"


def test_a_failed_run_keeps_its_branch_and_merges_nothing(root):
    ws = start(root, "r1")
    (ws.path / "half.txt").write_text("half done\n")
    done = end(ws, ok=False)
    assert not done.merged and done.error_code is None and done.ref["branch_kept"] is True
    assert show(repo_of(root), "half.txt") is None
    assert git(repo_of(root), "show", "run/r1:half.txt") == "half done"
    assert (
        "NOT merged" not in done.artifacts[0].content and "not merged" in done.artifacts[0].content
    )
    assert not ws.path.exists()


def test_too_large_a_workspace_is_refused(root, monkeypatch):
    monkeypatch.setattr(pw, "MAX_STAGED_BYTES", 1000)
    ws = start(root, "r1")
    (ws.path / "big.bin").write_bytes(b"0" * 5000)
    done = end(ws)
    assert done.error_code == pw.ERR_TOO_LARGE and not done.merged
    assert git(repo_of(root), "rev-list", "--count", "main") == "1"  # nothing was committed


def test_symlinks_out_of_the_tree_and_nested_git_are_never_committed(root, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    ws = start(root, "r1")
    (ws.path / "ok.txt").write_text("fine\n")
    (ws.path / "inside-link").symlink_to("ok.txt")
    (ws.path / "evil-link").symlink_to(outside)
    (ws.path / "evil-dir").symlink_to(tmp_path)
    nested = ws.path / "sub" / ".git"
    nested.mkdir(parents=True)
    (nested / "config").write_text("[core]\n\tfsmonitor = touch /tmp/pwned\n")
    (ws.path / "sub" / "file.txt").write_text("sub\n")
    assert end(ws).merged
    tree = git(repo_of(root), "ls-tree", "-r", "--name-only", "main").splitlines()
    assert sorted(tree) == ["inside-link", "ok.txt", "sub/file.txt"]


def test_a_rewritten_dot_git_file_cannot_make_the_run_commit_to_main(root):
    """The run can overwrite its worktree's .git file; the control plane addresses the worktree by
    the administrative directory it recorded, so main only ever moves through the merge."""
    ws = start(root, "r1")
    repo = repo_of(root)
    (ws.path / ".git").write_text(f"gitdir: {repo / '.git'}\n")  # now points at main's own .git
    (ws.path / "sneaky.txt").write_text("x\n")
    head = git(repo, "rev-parse", "main")
    done = end(ws, ok=False)  # a failed run must not touch main at all
    assert git(repo, "rev-parse", "main") == head and show(repo, "sneaky.txt") is None
    assert git(repo, "show", "run/r1:sneaky.txt") == "x" and done.ref["branch_kept"]


def test_a_leftover_dirty_main_tree_does_not_block_the_merge(root):
    seed = start(root, "r0")
    (seed.path / "a.txt").write_text("a\n")
    assert end(seed).merged
    repo = repo_of(root)
    (repo / "a.txt").write_text("dirty leftover of a crash\n")
    (repo / "junk.txt").write_text("junk\n")
    ws = start(root, "r1")
    (ws.path / "b.txt").write_text("b\n")
    assert end(ws).merged
    assert show(repo, "a.txt") == "a" and git(repo, "status", "--porcelain") == ""


def test_path_components_cannot_escape_the_workspaces_root(root):
    with pytest.raises(ValueError):
        pw.project_dir(root, "c1", "../../etc")
    with pytest.raises(ValueError):
        pw.prepare(root, company_id="c1", run_id="../x", task_kind="work", project_id=None)


def test_the_git_dir_file_is_not_snapshotted(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / ".git").write_text("gitdir: /somewhere/else\n")  # what a git worktree has
    (ws / "src" / "a.py").write_text("print(1)\n")
    snap = cli_workspace.snapshot(ws)
    assert list(snap.files) == ["src/a.py"]
    (ws / "src" / "b.py").write_text("print(2)\n")
    (ws / ".git").write_text("gitdir: /changed\n")
    assert [a.title for a in cli_workspace.changed_files(ws, snap)] == ["src/b.py"]


# ------------------------------------------------------------------ through the engine


class RepoRuntime(ScriptedRuntime):
    """Succeeds like the scripted runtime after running a per-task hook against the workspace."""

    id = "repo"
    display_name = "Repo test runtime"

    def __init__(self) -> None:
        super().__init__()
        self.hooks: dict[tuple[str, int], object] = {}
        self.seen: list[dict] = []

    def execute(self, request, sink, cancel):
        info = {
            "title": request.task.title,
            "attempt": request.attempt_number,
            "kind": request.workspace_kind,
            "files": list(request.workspace_files),
            "dir": request.workspace_dir,
        }
        self.seen.append(info)
        hook = self.hooks.get((request.task.title, request.attempt_number)) or self.hooks.get(
            (request.task.title, 0)
        )
        if hook is not None:
            hook(request, info)
        return super().execute(request, sink, cancel)


@pytest.fixture
def repo_runtime(app) -> RepoRuntime:
    runtime = RepoRuntime()
    app.state.runtimes.register(runtime)
    return runtime


def new_project(client, cid, name="Website") -> dict:
    r = client.post(api(cid, "/projects"), json={"name": name, "status": "active"})
    assert r.status_code == 201, r.text
    return r.json()


def project_repo(app, cid, project_id) -> Path:
    return pw.main_repo(app.state.settings.workspaces_dir, cid, project_id)


def runs_of(app, task_id) -> list[TaskRun]:
    with app.state.session_factory() as db:
        rows = list(
            db.scalars(
                select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.attempt_number)
            )
        )
        db.expunge_all()
        return rows


def run_all(scheduler, worker, clock, rounds=3):
    for _ in range(rounds):
        settle(scheduler, worker)
        clock.advance(3600)  # past any retry backoff


def test_sequential_project_tasks_share_the_repository(
    app, client, company, scheduler, worker, clock, repo_runtime
):
    cid = company["id"]
    project = new_project(client, cid)
    agent = new_agent(client, cid, runtime="repo")

    def write_index(request, info):
        (Path(request.workspace_dir) / "index.html").write_text("<h1>Hello</h1>\n")

    def add_style(request, info):
        index = Path(request.workspace_dir) / "index.html"
        assert index.read_text() == "<h1>Hello</h1>\n", "task 2 must see task 1's file"
        (Path(request.workspace_dir) / "style.css").write_text("h1 { color: red }\n")

    repo_runtime.hooks[("Create index", 0)] = write_index
    repo_runtime.hooks[("Add style", 0)] = add_style
    t1 = new_task(
        client, cid, title="Create index", project_id=project["id"], assignee_agent_id=agent["id"]
    )
    t2 = new_task(
        client,
        cid,
        title="Add style",
        project_id=project["id"],
        assignee_agent_id=agent["id"],
        depends_on=[t1["id"]],
    )
    run_all(scheduler, worker, clock)
    assert get_task(client, cid, t1["id"])["status"] == "done"
    assert get_task(client, cid, t2["id"])["status"] == "done", repo_runtime.errors
    by_title = {s["title"]: s for s in repo_runtime.seen}
    assert by_title["Create index"]["kind"] == "project" and by_title["Create index"]["files"] == []
    assert by_title["Add style"]["files"] == ["index.html"]
    assert cid in by_title["Add style"]["dir"]
    repo = project_repo(app, cid, project["id"])
    assert show(repo, "index.html") and show(repo, "style.css")
    assert sorted(git(repo, "ls-tree", "-r", "--name-only", "main").splitlines()) == [
        "index.html",
        "style.css",
    ]

    [run1] = runs_of(app, t1["id"])
    ref = run1.workspace_ref
    assert ref["kind"] == "project" and ref["merged"] is True and ref["conflict"] is False
    assert ref["base"] and ref["commit"] and ref["base"] != ref["commit"]
    with app.state.session_factory() as db:
        changes = db.scalars(select(Artifact).where(Artifact.task_id == t1["id"])).all()
        assert [(a.title, a.kind, a.mime_type) for a in changes] == [
            ("Changes", "code", "text/x-diff")
        ]
        actions = [
            a.action for a in db.scalars(select(AuditEvent).where(AuditEvent.target_id == run1.id))
        ]
        assert "workspace.merged" in actions
    # the worktrees are gone; only the project directory is left under the company
    assert not (Path(app.state.settings.workspaces_dir) / cid / run1.id).exists()


def test_a_merge_conflict_fails_the_run_and_the_retry_succeeds(
    app, client, company, scheduler, worker, clock, repo_runtime
):
    cid = company["id"]
    project = new_project(client, cid)
    agent = new_agent(client, cid, runtime="repo")
    root = Path(app.state.settings.workspaces_dir)

    repo_runtime.hooks[("Seed", 0)] = lambda r, i: (
        Path(r.workspace_dir) / "shared.txt"
    ).write_text("line1\nline2\n")
    seed = new_task(
        client, cid, title="Seed", project_id=project["id"], assignee_agent_id=agent["id"]
    )
    run_all(scheduler, worker, clock, rounds=1)
    assert get_task(client, cid, seed["id"])["status"] == "done"

    def edit_with_a_competitor(request, info):
        shared = Path(request.workspace_dir) / "shared.txt"
        shared.write_text("mine\nline2\n")
        # another run starts from the same main, edits the same line and merges first
        rival = pw.prepare(
            root,
            company_id=cid,
            run_id="rival-run",
            task_kind="work",
            project_id=project["id"],
        )
        (rival.path / "shared.txt").write_text("theirs\nline2\n")
        assert pw.finalize(
            rival, succeeded=True, agent_id="x", agent_name="Rival", task_title="rival"
        ).merged

    def edit_again(request, info):
        shared = Path(request.workspace_dir) / "shared.txt"
        assert shared.read_text() == "theirs\nline2\n", "the retry starts from the new main"
        shared.write_text("theirs\nline2\nline3\n")

    repo_runtime.hooks[("Edit shared", 1)] = edit_with_a_competitor
    repo_runtime.hooks[("Edit shared", 2)] = edit_again
    task = new_task(
        client, cid, title="Edit shared", project_id=project["id"], assignee_agent_id=agent["id"]
    )
    run_all(scheduler, worker, clock, rounds=3)

    first, second = runs_of(app, task["id"])
    assert (first.status, first.exit_reason) == ("failed", "merge_conflict")
    assert first.error["code"] == "merge_conflict"
    assert first.workspace_ref["conflict"] is True and first.workspace_ref["merged"] is False
    assert first.workspace_ref["conflict_files"] == ["shared.txt"]
    assert (second.status, second.workspace_ref["merged"]) == ("succeeded", True)
    assert get_task(client, cid, task["id"])["status"] == "done"
    repo = project_repo(app, cid, project["id"])
    assert show(repo, "shared.txt") == "theirs\nline2\nline3"
    assert f"run/{first.id}" in git(repo, "branch", "--list")  # the failed run's branch is kept
    with app.state.session_factory() as db:
        actions = {a.action for a in db.scalars(select(AuditEvent))}
        assert "workspace.merge_conflict" in actions
        # both runs left a "Changes" artifact; the first one says it was not merged
        texts = [
            a.title for a in db.scalars(select(Artifact).where(Artifact.task_id == task["id"]))
        ]
        assert texts == ["Changes", "Changes"]


def test_a_reviewer_gets_a_read_only_copy_and_sees_the_changes_artifact(
    app, client, company, scheduler, worker, clock, repo_runtime
):
    cid = company["id"]
    project = new_project(client, cid)
    worker_agent = new_agent(client, cid, name="Maker", runtime="repo")
    reviewer = new_agent(client, cid, name="Checker", runtime="repo", role_title="QA Reviewer")
    repo_runtime.hooks[("Build page", 0)] = lambda r, i: (
        Path(r.workspace_dir) / "index.html"
    ).write_text("<h1>Done</h1>\n")
    captured: dict = {}

    def review_it(request, info):
        ws = Path(request.workspace_dir)
        captured["kind"] = request.workspace_kind
        captured["index"] = (ws / "index.html").read_text()
        try:
            (ws / "index.html").write_text("tampered")
            captured["tampered"] = True
        except PermissionError:
            captured["tampered"] = False
        (ws / "scratch.txt").write_text("reviewer notes")
        with app.state.session_factory() as db:
            review_task = db.get(Task, request.task.id)
            captured["target"] = reviews.review_target(db, app.state.storage, review_task)

    repo_runtime.hooks[("Review: Build page", 0)] = review_it
    new_task(
        client,
        cid,
        title="Build page",
        project_id=project["id"],
        assignee_agent_id=worker_agent["id"],
        reviewer_agent_id=reviewer["id"],
        acceptance_criteria="A page exists",
    )
    run_all(scheduler, worker, clock, rounds=2)
    assert "kind" in captured, "the review run never happened"
    assert captured["kind"] == "review" and captured["tampered"] is False
    assert captured["index"] == "<h1>Done</h1>\n"
    titles = [a["title"] for a in captured["target"]["artifacts"]]
    assert "Changes" in titles
    changes = next(a for a in captured["target"]["artifacts"] if a["title"] == "Changes")
    assert "index.html" in (changes["excerpt"] or "") and changes["kind"] == "code"

    repo = project_repo(app, cid, project["id"])
    assert show(repo, "scratch.txt") is None and show(repo, "index.html") == "<h1>Done</h1>"
    with app.state.session_factory() as db:
        review_task = db.scalar(select(Task).where(Task.kind == "review"))
        [review_run] = db.scalars(select(TaskRun).where(TaskRun.task_id == review_task.id))
        assert review_run.workspace_ref["kind"] == "review"
        assert review_run.workspace_ref["discarded"] >= 1
        assert not (Path(app.state.settings.workspaces_dir) / cid / review_run.id).exists()
    assert git(repo, "branch", "--list", "run/*") == ""


def test_without_git_the_engine_falls_back_to_per_run_dirs(
    app, client, company, scheduler, worker, clock, repo_runtime, monkeypatch
):
    monkeypatch.setattr(pw, "git_binary", lambda: None)
    cid = company["id"]
    project = new_project(client, cid)
    agent = new_agent(client, cid, runtime="repo")
    task = new_task(
        client, cid, title="No git here", project_id=project["id"], assignee_agent_id=agent["id"]
    )
    run_all(scheduler, worker, clock, rounds=1)
    assert get_task(client, cid, task["id"])["status"] == "done"
    [seen] = repo_runtime.seen
    assert seen["kind"] == "run_dir" and seen["files"] == [] and Path(seen["dir"]).is_dir()
    [run] = runs_of(app, task["id"])
    assert run.workspace_ref["available"] is False and run.workspace_ref["kind"] == "run_dir"
    assert not (Path(app.state.settings.workspaces_dir) / cid / "projects").exists()


def test_a_task_without_a_project_gets_a_plain_per_run_dir(
    app, client, company, scheduler, worker, clock, repo_runtime
):
    cid = company["id"]
    agent = new_agent(client, cid, runtime="repo")
    task = new_task(client, cid, title="Loose task", assignee_agent_id=agent["id"])
    run_all(scheduler, worker, clock, rounds=1)
    [seen] = repo_runtime.seen
    assert seen["kind"] == "run_dir" and Path(seen["dir"]).is_dir() and cid in seen["dir"]
    [run] = runs_of(app, task["id"])
    assert run.workspace_ref["kind"] == "run_dir" and "available" not in run.workspace_ref


def test_cli_runs_get_a_stall_limit_of_at_least_ten_minutes():
    assert CLI_MIN_STALL_SECONDS >= 600
    assert effective_stall_seconds("cli:claude_code", 180) == 600
    assert effective_stall_seconds("cli:codex", 1800) == 1800  # a longer policy limit still wins
    assert effective_stall_seconds("company_agent", 180) == 180
    assert effective_stall_seconds("scripted", 30) == 30


def test_the_monitor_uses_the_cli_stall_limit(
    app, client, company, scheduler, worker, clock, monkeypatch
):
    class FakeCli(ScriptedRuntime):
        id = "cli:fake"
        display_name = "Fake CLI"

    app.state.runtimes.register(FakeCli())
    set_policy(client, company["id"], stall_seconds=60)
    limits: list[int] = []
    original = executor_module.RunExecutor._monitor

    def spy(self, *args):
        limits.append(args[-1])  # the last argument is stall_seconds
        return original(self, *args)

    monkeypatch.setattr(executor_module.RunExecutor, "_monitor", spy)
    agent = new_agent(client, company["id"], runtime="cli:fake")
    new_task(client, company["id"], assignee_agent_id=agent["id"])
    settle(scheduler, worker)
    assert limits and all(value >= 600 for value in limits)


def test_a_state_change_stays_inside_the_company_tree(
    app, client, company, scheduler, worker, clock, repo_runtime
):
    """Tenancy in paths: every workspace of a company lives under <workspaces>/<company_id>/."""
    cid = company["id"]
    project = new_project(client, cid)
    agent = new_agent(client, cid, runtime="repo")
    new_task(client, cid, title="Anything", project_id=project["id"], assignee_agent_id=agent["id"])
    run_all(scheduler, worker, clock, rounds=1)
    root = Path(app.state.settings.workspaces_dir)
    assert [p.name for p in root.iterdir()] == [cid]
    repo = project_repo(app, cid, project["id"])
    assert repo.is_relative_to(root / cid)
    mode = stat.S_IMODE((repo / ".git" / "foundry.lock").stat().st_mode)
    assert mode == 0o600
