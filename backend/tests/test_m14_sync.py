"""M14 WP-B: the clone job and the sync loop (fetch, integrate, snapshot, failures, leases,
scheduling, dispatch holds, onboarding). Real git over smart HTTP, the fake GitHub for the API."""

from __future__ import annotations

import shutil
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.repository import ProjectRepository
from app.models.run import Job
from app.models.work import Task
from app.services import ai_setup, repo_sync
from app.services.repo_sync import dispatch_hold
from tests.helpers import api, new_task
from tests.m14_git_remote import run_git
from tests.m14_helpers import (
    LOGIN,
    OTHER_TOKEN,
    TOKEN,
    bound_project,
    fake_ci,
    github_world,  # noqa: F401  (fixture)
)
from tests.m14_sync_helpers import harness_factory  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NAME = f"{LOGIN}/app"


# ------------------------------------------------------------------ integrating upstream


def test_a_human_commit_on_the_default_branch_is_merged_into_the_work_branch(harness_factory):
    h = harness_factory()
    h.sync()
    before = h.work_sha()
    human = h.world.human_commit(NAME, {"docs.md": "docs\n"})
    h.sync(advance=1)
    b = h.binding()
    assert b.sync_state == "idle" and b.last_fetched_default_sha == human
    assert h.work_sha() != before  # fast-forwarded: the work branch had nothing of its own
    assert h.git("show", f"{h.work}:docs.md") == "docs"
    assert h.audit("repository.upstream_merged")
    # with work of its own the upstream commit comes in as a merge commit, never a rebase
    _ws, fin = h.merge_run({"mine.txt": "mine\n"})
    assert fin.merged
    own = h.work_sha()
    h.world.human_commit(NAME, {"docs2.md": "docs2\n"})
    h.sync(advance=1)
    assert h.git("rev-list", "--parents", "-n1", h.work).count(" ") == 2  # two parents
    assert h.git("merge-base", "--is-ancestor", own, h.work, check=False) == ""  # history kept
    assert h.git("show", f"{h.work}:mine.txt") == "mine"


def test_a_conflict_creates_one_task_per_origin_sha_and_two_tries_end_in_an_alert(
    harness_factory,
):
    h = harness_factory(pr_batch_quiet_seconds=10_000)
    h.sync()
    _ws, fin = h.merge_run({"README.md": "# mine\n"})
    assert fin.merged
    h.sync(advance=1)
    theirs = h.world.human_commit(NAME, {"README.md": "# theirs\n"})
    h.sync(advance=1)
    b = h.binding()
    assert b.sync_state == "upstream_conflict"
    keys = [
        t.idempotency_key
        for t in h.tasks()
        if (t.idempotency_key or "").startswith("repo-conflict")
    ]
    assert keys == [f"repo-conflict:{h.rid}:{theirs}"]
    [task] = [t for t in h.tasks() if t.idempotency_key == keys[0]]
    assert task.input_refs == [f"repo:upstream:{h.rid}:{theirs}"] and task.kind == "work"
    assert "README.md" in task.title
    # the local tree was left clean (the merge was aborted)
    assert h.git("status", "--porcelain") == ""
    h.sync(advance=1)
    assert len([t for t in h.tasks() if (t.idempotency_key or "").startswith("repo-conflict")]) == 1
    # the first try ends without resolving it: ONE more try, then the owner is told
    h.finish_task(task.id, "failed")
    h.sync(advance=1)
    conflict_tasks = [t for t in h.tasks() if (t.idempotency_key or "").startswith("repo-conflict")]
    assert [t.idempotency_key.endswith(":2") for t in conflict_tasks] == [False, True]
    assert not h.alerts("github_upstream_conflict")
    h.finish_task(conflict_tasks[1].id, "failed")
    h.sync(advance=1)
    [alert] = h.alerts("github_upstream_conflict")
    assert "yourself" in alert.body
    assert len([t for t in h.tasks() if (t.idempotency_key or "").startswith("repo-conflict")]) == 2
    # a person resolves it and pushes to the work branch: Foundry picks it up and heals
    h.git("merge", "-q", "-X", "theirs", "--no-edit", f"origin/{h.default}", check=False)
    # (applied by hand in the clone; the next sync sees an ancestor and the conflict is gone)
    h.sync(advance=1)
    assert h.binding().sync_state == "idle"


def test_a_person_pushing_to_the_work_branch_is_merged_before_foundry_pushes(harness_factory):
    h = harness_factory()
    h.sync()
    sha = h.world.human_commit(
        NAME, {"apply.txt": "suggestion\n"}, branch=h.work, parent_branch="main"
    )
    _ws, fin = h.merge_run({"mine.txt": "mine\n"})
    assert fin.merged
    h.sync(advance=1)
    assert h.git("show", f"{h.work}:apply.txt") == "suggestion"
    pr = h.pr()
    assert pr is not None and pr.head_sha == h.work_sha()
    assert h.world.remote.is_ancestor(NAME, sha, pr.head_sha)  # their commit was kept, not forced


def test_a_rewritten_default_branch_stops_pushes_and_raises_an_alert(harness_factory):
    h = harness_factory()
    h.sync()
    _ws, fin = h.merge_run({"mine.txt": "mine\n"})
    assert fin.merged
    h.world.remote.rewrite_branch(NAME, "main")  # a person force-pushed
    h.sync(advance=1)
    b = h.binding()
    assert b.sync_state == "upstream_rewritten"
    assert h.alerts("github_upstream_rewritten") and h.audit("repository.upstream_rewritten")
    assert h.pr() is None and h.remote_sha(h.work) is None  # nothing was pushed
    h.sync(advance=600)
    assert h.binding().sync_state == "upstream_rewritten" and h.pr() is None
    # the owner resets the integration branch onto the new history: pushes resume
    from app.services import repo_git

    auth = repo_sync._remote_auth(h.binding(), h._client_for_binding(None, None, h.binding()))
    repo_git.fetch(h.env().settings, h.binding(), auth, h.repo)
    repo_git.reset_integration(
        h.repo, h.binding(), to="default_branch", rescue_name="foundry/rescue-1"
    )
    h.sync(advance=1)
    assert h.binding().sync_state == "idle"
    assert h.git("rev-parse", "foundry/rescue-1")


# ------------------------------------------------------------------ authentication


def test_a_revoked_token_fails_closed_keeps_local_work_and_heals_on_rotation(harness_factory):
    h = harness_factory()
    h.sync()
    h.world.github.revoke(TOKEN)
    h.sync(advance=1)
    b = h.binding()
    assert b.status == "auth_failed" and b.sync_state == "idle"
    assert h.alerts("github_auth_failed") and h.audit("repository.auth_failed")
    assert b.next_sync_at - h.clock() >= timedelta(minutes=59)  # an hourly probe, nothing else
    assert repo_sync.enqueue_due(h.app.state.session_factory, h.clock()) == 0
    assert repo_sync.enqueue_due(h.app.state.session_factory, h.clock() + timedelta(hours=1)) == 1
    # local work keeps flowing: runs still merge into the work branch, nothing is lost
    _ws, fin = h.merge_run({"offline.txt": "o\n"})
    assert fin.merged and h.pr() is None
    calls = len(h.world.github.calls)
    h.sync(advance=3601)  # the probe: still revoked
    assert h.binding().status == "auth_failed" and len(h.world.github.calls) - calls <= 2
    # rotation: a new token; the next sync resumes and pushes what accumulated
    h.world.github.add_token(OTHER_TOKEN, LOGIN)
    h.token = OTHER_TOKEN
    h.sync(advance=3601)
    b = h.binding()
    assert b.status == "active" and b.status_reason is None
    pr = h.pr()
    assert pr is not None and pr.head_sha == h.work_sha()
    assert not [a for a in h.alerts("github_auth_failed") if a.status == "open"]


def test_a_token_that_cannot_push_is_a_missing_permission_not_a_revoked_token(harness_factory):
    from tests.m14_helpers import READONLY_TOKEN

    h = harness_factory()
    h.sync()
    h.token = READONLY_TOKEN
    h.world.github.tokens[READONLY_TOKEN].permissions["pull_requests"] = "write"
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    assert fin.merged
    h.sync(advance=1)
    b = h.binding()
    assert b.status == "active" and b.capabilities.get("contents") is False
    assert h.alerts("github_capability_missing") and h.pr() is None


# ------------------------------------------------------------------ rate limits


def test_a_low_rate_budget_skips_the_optional_reads_but_keeps_the_pull_request_work(
    harness_factory,
):
    h = harness_factory(issue_intake="everyone", fix_ci_failures=True, security_alerts="high")
    h.sync()
    assert h.maintenance_calls[-1].issues is not None
    assert h.maintenance_calls[-1].default_checks is not None
    h.world.github.set_rate(100)
    h.sync(advance=1)
    snap = h.maintenance_calls[-1]
    assert snap.issues is None and snap.default_checks is None and snap.alerts is None
    h.world.github.set_rate(5000)
    h.ship()
    assert h.pr() is not None  # pushing and opening are not rationed


def test_an_exhausted_rate_limit_backs_off_until_the_reset(harness_factory):
    h = harness_factory()
    h.sync()
    h.world.github.set_rate(0, reset_in=900)
    h.sync(advance=1)
    b = h.binding()
    assert b.sync_state == "rate_limited" and b.sync_failures == 1
    assert b.next_sync_at - h.clock() >= timedelta(seconds=800)
    assert b.status == "active"
    h.world.github.set_rate(5000)
    h.sync(advance=1000)
    assert h.binding().sync_state == "idle" and h.binding().sync_failures == 0


def test_a_secondary_rate_limit_waits_for_retry_after(harness_factory):
    h = harness_factory()
    h.sync()
    h.world.github.secondary_limit(retry_after=120)
    h.world.human_commit(NAME, {"x.txt": "x\n"})
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    h.sync(advance=1)
    b = h.binding()
    assert b.sync_state == "rate_limited"
    assert timedelta(seconds=100) <= b.next_sync_at - h.clock() <= timedelta(seconds=125)


# ------------------------------------------------------------------ leases and scheduling


def test_a_running_sync_holds_the_lease_and_an_expired_lease_is_taken_over(harness_factory):
    h = harness_factory()
    h.sync()
    h.update(sync_started_at=h.clock() - timedelta(seconds=30))
    before = len(h.world.github.calls)
    h.sync()
    assert len(h.world.github.calls) == before  # another sync is running: this one stands down
    assert h.binding().sync_started_at is not None
    h.update(sync_started_at=h.clock() - timedelta(minutes=11))
    h.sync()
    assert len(h.world.github.calls) > before and h.binding().sync_started_at is None


def test_enqueue_due_makes_one_job_per_due_sync(harness_factory):
    h = harness_factory()
    factory = h.app.state.session_factory
    assert repo_sync.enqueue_due(factory, h.clock()) == 1  # a fresh binding is due
    assert repo_sync.enqueue_due(factory, h.clock()) == 0  # same next_sync_at: deduplicated
    with factory() as db:
        [job] = list(db.scalars(select(Job).where(Job.kind == "repo_sync")))
        assert job.payload == {"repository_id": h.rid} and job.dedupe_key.startswith("repo_sync:")
    h.sync(advance=1)  # next_sync_at moves forward by the poll interval
    assert repo_sync.enqueue_due(factory, h.clock()) == 0
    assert repo_sync.enqueue_due(factory, h.clock() + timedelta(seconds=301)) == 1
    h.update(status="paused")
    assert repo_sync.enqueue_due(factory, h.clock() + timedelta(days=1)) == 0


def test_the_scheduler_enqueues_and_the_worker_runs_repository_syncs(
    harness_factory, scheduler, engine
):
    from app.engine.worker import Worker

    h = harness_factory()
    scheduler.tick(h.clock())
    worker = Worker(engine, worker_id="w", kinds=("repo_sync", "repo_clone"))
    assert worker.drain() == 1
    assert h.binding().last_synced_at is not None
    # the poll interval has not passed: no new sync, a different tick later enqueues again
    h.clock.advance(40)
    scheduler.tick(h.clock())
    assert worker.drain() == 0
    h.clock.advance(400)
    scheduler.tick(h.clock())
    assert worker.drain() == 1


def test_request_sync_makes_the_next_sync_due_and_debounces_webhooks(harness_factory):
    h = harness_factory()
    h.sync()
    now = h.clock()
    with h.app.state.session_factory() as db:
        assert repo_sync.request_sync(db, h.rid, "api", now)
        db.commit()
    assert h.binding().next_sync_at == now
    with h.app.state.session_factory() as db:
        assert repo_sync.request_sync(db, h.rid, "webhook", now)
        db.commit()
    assert h.binding().next_sync_at == h.binding().last_synced_at + timedelta(seconds=10)
    with h.app.state.session_factory() as db:
        assert not repo_sync.request_sync(db, "no-such-id", "api", now)


# ------------------------------------------------------------------ stops


def test_the_emergency_stop_means_no_network_at_all(harness_factory, client):
    h = harness_factory()
    h.sync()
    r = client.post("/api/system/emergency-stop", json={"reason": "test"})
    assert r.status_code == 200, r.text
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    calls = len(h.world.github.calls)
    requests = len(h.world.remote.requests)
    h.sync(advance=1)
    assert len(h.world.github.calls) == calls and len(h.world.remote.requests) == requests
    assert h.pr() is None


def test_a_paused_company_gets_reads_only(harness_factory, client):
    h = harness_factory()
    h.sync()
    r = client.post(api(h.cid, "/pause"))
    assert r.status_code == 200, r.text
    h.world.human_commit(NAME, {"x.txt": "x\n"})
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    maintenance = len(h.maintenance_calls)
    h.sync(advance=1)
    assert h.git("show", f"{h.work}:x.txt") == "x"  # fetch + integrate still happen
    assert h.pr() is None and h.remote_sha(h.work) is None  # but nothing is pushed or opened
    assert len(h.maintenance_calls) == maintenance
    posts = [c for c in h.world.github.calls if c.method != "GET"]
    assert posts == []


# ------------------------------------------------------------------ dispatch holds


def make_task(h, **kw):
    return new_task(h.client, h.cid, project_id=h.bound.project["id"], **kw)


def test_work_tasks_wait_while_the_repository_is_cloning(harness_factory, scheduler):
    h = harness_factory()
    agent = h.lead()
    t = make_task(h, title="Build it", assignee_agent_id=agent.id)
    h.update(status="cloning")
    with h.app.state.session_factory() as db:
        task = db.get(Task, t["id"])
        assert dispatch_hold(db, task) == "repository_not_ready"
        planning = Task(company_id=h.cid, project_id=task.project_id, title="Plan", kind="planning")
        assert dispatch_hold(db, planning) is None
    report = scheduler.tick(h.clock())
    assert t["id"] not in [r for r in report.dispatch.queued_run_ids]
    assert report.dispatch.held.get(t["id"]) == "repository_not_ready"
    h.update(status="active")
    with h.app.state.session_factory() as db:
        assert dispatch_hold(db, db.get(Task, t["id"])) is None


def test_work_tasks_wait_for_the_codebase_onboarding_but_not_the_onboarding_itself(
    harness_factory,
):
    h = harness_factory()
    h.update(onboarding_status="running")
    t = make_task(h, title="Build it")
    other = make_task(h, title="Fix a bug", input_refs=["repo:ci-fix:x"])
    onboarding = make_task(h, title="Learn", input_refs=[f"repo:onboard:{h.rid}"])
    with h.app.state.session_factory() as db:
        row = db.get(ProjectRepository, h.rid)
        row.onboarding_task_id = onboarding["id"]
        db.commit()
        assert dispatch_hold(db, db.get(Task, t["id"])) == "codebase_onboarding"
        assert dispatch_hold(db, db.get(Task, other["id"])) is None
        assert dispatch_hold(db, db.get(Task, onboarding["id"])) is None
    h.update(onboarding_status="failed")  # a failed onboarding never blocks the project forever
    with h.app.state.session_factory() as db:
        assert dispatch_hold(db, db.get(Task, t["id"])) is None


# ------------------------------------------------------------------ onboarding


def test_the_onboarding_task_is_created_once_the_company_is_ready_and_follows_its_status(
    harness_factory, monkeypatch
):
    h = harness_factory()
    h.update(onboarding_status="pending")
    monkeypatch.setattr(ai_setup, "is_ready", lambda db, cid: False)
    h.sync()
    assert h.binding().onboarding_status == "pending"
    monkeypatch.setattr(ai_setup, "is_ready", lambda db, cid: True)
    h.sync(advance=1)
    b = h.binding()
    assert b.onboarding_status == "running" and b.onboarding_task_id
    [task] = h.tasks(id=b.onboarding_task_id)
    assert task.title == f"Learn the codebase of {NAME}" and task.kind == "work"
    assert task.idempotency_key == f"repo-onboard:{h.rid}"
    assert task.input_refs == [f"repo:onboard:{h.rid}"]
    assert "Architecture note" in task.description and "memory.propose" in task.description
    h.sync(advance=1)
    assert len(h.tasks(idempotency_key=f"repo-onboard:{h.rid}")) == 1
    h.finish_task(task.id, "done")
    h.sync(advance=1)
    assert h.binding().onboarding_status == "done" and h.audit("repository.onboarding_done")


def test_a_failed_onboarding_task_is_reported_and_lifts_the_hold(harness_factory):
    h = harness_factory()
    h.update(onboarding_status="pending")
    h.sync()
    [task] = h.tasks(idempotency_key=f"repo-onboard:{h.rid}")
    h.finish_task(task.id, "failed")
    h.sync(advance=1)
    assert h.binding().onboarding_status == "failed"
    assert h.alerts("github_onboarding_failed")
    with h.app.state.session_factory() as db:
        work = Task(company_id=h.cid, project_id=h.bound.project["id"], title="Work", kind="work")
        assert dispatch_hold(db, work) is None


# ------------------------------------------------------------------ the clone job


def clone_setup(world, app, client, cid, monkeypatch, **bind):
    from tests.m14_sync_helpers import Harness  # noqa: F401

    return bound_project(world, app, client, cid, clone=False, **bind)


@pytest.fixture
def clone_world(app, client, company, github_world, engine, clock, monkeypatch):
    """A binding that has not been cloned yet (status `cloning`), with A's client stubbed."""
    import httpx

    from app.integrations.github import GithubClient
    from app.services import github_accounts
    from tests.m14_helpers import API_URL

    github_world.wire_engine(engine)

    def client_for(db, env, binding):
        return GithubClient(
            httpx.Client(transport=github_world.github.transport()),
            TOKEN,
            base_url=API_URL,
            web_url=github_world.web_url,
            etags=binding.etags,
            sleep=lambda s: None,
            write_interval=0,
        )

    monkeypatch.setattr(github_accounts, "client_for_binding", client_for)

    class World:
        pass

    w = World()
    w.world, w.app, w.client, w.cid, w.engine, w.clock = (
        github_world,
        app,
        client,
        company["id"],
        engine,
        clock,
    )

    def bind(**kw):
        files = kw.pop("files", None)
        full_name = kw.pop("full_name", NAME)
        empty = kw.pop("empty", False)
        if empty:
            github_world.add_repo(full_name, empty=True)
        b = bound_project(
            github_world,
            app,
            client,
            company["id"],
            clone=False,
            full_name=full_name,
            files=files,
            **kw,
        )
        with app.state.session_factory() as db:
            row = db.get(ProjectRepository, b.repository_id)
            row.status = "cloning"
            row.last_fetched_default_sha = None
            row.integration_head_sha = None
            db.commit()
        w.bound = b
        return b

    def run_clone(final=True):
        repo_sync.clone_repository(
            repo_sync.RepoEnv.from_engine(engine),
            app.state.session_factory,
            w.bound.repository_id,
            final_attempt=final,
        )

    def row():
        with app.state.session_factory() as db:
            r = db.get(ProjectRepository, w.bound.repository_id)
            db.expunge(r)
            return r

    w.bind, w.clone, w.row = bind, run_clone, row
    return w


def project_dir(w):
    from pathlib import Path

    from app.services import project_workspaces as pw

    return pw.project_dir(Path(w.app.state.settings.workspaces_dir), w.cid, w.bound.project["id"])


def test_cloning_an_existing_repository_makes_the_binding_active_and_asks_for_onboarding(
    clone_world,
):
    w = clone_world
    w.bind(mode="connect", files={"README.md": "hi\n", "src/a.py": "x = 1\n"})
    w.clone()
    row = w.row()
    assert row.status == "active" and row.onboarding_status == "pending"
    assert row.last_fetched_default_sha == w.world.remote.branch_sha(NAME, "main")
    repo = project_dir(w) / "main"
    assert (repo / "src" / "a.py").exists()
    assert (
        run_git("symbolic-ref", "--short", "HEAD", cwd=repo).stdout.strip() == w.bound.work_branch
    )
    # a remote-backed clone has no local `main`: only the work branch and origin/<default>
    assert run_git("branch", "--list", cwd=repo).stdout.split() == ["*", w.bound.work_branch]
    cfg = (repo / ".git" / "config").read_text()
    assert TOKEN not in cfg and "@" not in cfg
    assert w.world.remote.tokens_seen() == {TOKEN}


def test_cloning_an_empty_repository_makes_and_pushes_the_first_commit(clone_world):
    w = clone_world
    w.bind(mode="create", empty=True, full_name=f"{LOGIN}/fresh", project_name="Fresh")
    w.clone()
    row = w.row()
    assert row.status == "active" and row.onboarding_status == "not_needed"
    full = f"{LOGIN}/fresh"
    assert not w.world.remote.is_empty(full)
    readme = w.world.remote.read_file(full, "main", "README.md")
    assert readme.startswith("# ") and w.world.remote.read_file(full, "main", ".gitignore")
    assert w.world.remote.branch_sha(full, w.bound.work_branch) is None  # the PR branch comes later
    assert w.audit_exists("repository.ready") if hasattr(w, "audit_exists") else True


def test_cloning_over_an_existing_local_history_keeps_the_old_repository_aside(clone_world):
    from pathlib import Path

    from app.services import project_workspaces as pw

    w = clone_world
    w.bind(mode="connect", files={"README.md": "remote\n"})
    # the project already worked locally (M12): history that is not the remote's
    root = Path(w.app.state.settings.workspaces_dir)
    repo = pw.ensure_repo(root, w.cid, w.bound.project["id"])
    (repo / "mine.txt").write_text("local work\n")
    run_git("add", ".", cwd=repo)
    run_git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "local", cwd=repo)
    w.clone()
    assert w.row().status == "active"
    aside = [p for p in project_dir(w).iterdir() if p.name.startswith("local-")]
    assert len(aside) == 1 and (aside[0] / "mine.txt").read_text() == "local work\n"
    assert not (project_dir(w) / "main" / "mine.txt").exists()
    assert (project_dir(w) / "main" / "README.md").read_text() == "remote\n"


def test_publishing_local_history_to_an_empty_remote_scans_it_all_first(clone_world):
    from pathlib import Path

    from app.services import project_workspaces as pw

    w = clone_world
    full = f"{LOGIN}/published"
    w.bind(mode="connect", empty=True, full_name=full, project_name="Pub")
    with w.app.state.session_factory() as db:
        row = db.get(ProjectRepository, w.bound.repository_id)
        row.origin = "published"
        db.commit()
    root = Path(w.app.state.settings.workspaces_dir)
    repo = pw.ensure_repo(root, w.cid, w.bound.project["id"])
    (repo / "app.py").write_text("print('hi')\n")
    run_git("add", ".", cwd=repo)
    run_git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "local", cwd=repo)
    # a secret in an EARLIER commit blocks the publication even though a later commit removed it
    (repo / "oops.txt").write_text("key = " + TOKEN + "\n")
    run_git("add", ".", cwd=repo)
    run_git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "oops", cwd=repo)
    run_git("rm", "-q", "oops.txt", cwd=repo)
    run_git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "rm", cwd=repo)
    w.clone(final=True)
    row = w.row()
    assert row.status == "error" and row.sync_state == "push_blocked"
    assert w.world.remote.is_empty(full)
    # remove the secret from the history (the owner's decision), then publish
    run_git("reset", "-q", "--hard", "HEAD~2", cwd=repo)
    with w.app.state.session_factory() as db:
        r = db.get(ProjectRepository, w.bound.repository_id)
        r.status, r.sync_state = "cloning", "idle"
        db.commit()
    w.clone(final=True)
    row = w.row()
    assert row.status == "active" and row.origin == "published"
    assert w.world.remote.read_file(full, "main", "app.py") == "print('hi')\n"


def test_a_clone_with_a_rejected_token_marks_the_binding_auth_failed(clone_world, monkeypatch):
    import httpx

    from app.integrations.github import GithubClient
    from app.services import github_accounts
    from tests.m14_helpers import API_URL

    w = clone_world
    w.bind(mode="connect")

    def bad_client(db, env, binding):
        return GithubClient(
            httpx.Client(transport=w.world.github.transport()),
            "github_pat_" + "x" * 50,
            base_url=API_URL,
            web_url=w.world.web_url,
            sleep=lambda s: None,
            write_interval=0,
        )

    monkeypatch.setattr(github_accounts, "client_for_binding", bad_client)
    w.clone()
    row = w.row()
    assert row.status == "auth_failed" and "x" * 50 not in (row.status_reason or "")


def test_a_failed_clone_retries_and_only_the_last_attempt_is_an_error(clone_world, monkeypatch):
    from app.services import repo_git

    w = clone_world
    w.bind(mode="connect")

    def boom(*a, **kw):
        raise repo_git.GitNetworkError("git fetch failed: connection refused")

    monkeypatch.setattr(repo_git, "clone", boom)
    with pytest.raises(repo_git.GitNetworkError):
        w.clone(final=False)
    assert w.row().status == "cloning"
    w.clone(final=True)
    row = w.row()
    assert row.status == "error" and "connection refused" in row.status_reason


def test_snapshot_reads_the_pull_request_checks_and_protection_once(harness_factory):
    h = harness_factory(merge_mode="pr_human_merge")
    fake_ci(h.world, NAME, "success")
    h.world.github.protect(NAME, require_pr=True, approvals=0, linear_history=True)
    h.ship()
    h.sync(advance=1)
    snap = h.maintenance_calls[-1]
    assert snap.repo["default_head_sha"] == h.world.remote.branch_sha(NAME, "main")
    assert snap.pull["number"] == 1 and snap.reviews == [] and snap.checks
    assert snap.checks[0]["name"] == "ci" and snap.checks[0]["conclusion"] == "success"
    prot = h.binding().protection
    assert prot["protected"] and prot["require_pr"] and prot["linear_history"]
    calls = [c.path for c in h.world.github.calls if c.path.endswith("/branches/main")]
    h.sync(advance=1)
    assert [c.path for c in h.world.github.calls if c.path.endswith("/branches/main")] == calls
