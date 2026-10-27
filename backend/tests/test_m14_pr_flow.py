"""M14 WP-B: the pull request state machine end to end against the fake GitHub and a real bare
repository over smart HTTP. The contracts of the other packages are stand-ins (see
tests/m14_sync_helpers.py); the sync loop, the push gate, the guardrail checks, the git engine and
the fake's real merges are the production code paths."""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import select

from app.models.accounts import Notification
from tests.guardrail_helpers import add_rule
from tests.helpers import api, new_task
from tests.m14_git_remote import run_git
from tests.m14_helpers import (
    LOGIN,
    fake_ci,
    github_world,  # noqa: F401  (fixture)
)
from tests.m14_sync_helpers import harness_factory  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NAME = f"{LOGIN}/app"


def merges(h) -> list:
    return [c for c in h.world.github.calls_to("PUT") if c.path.endswith("/merge")]


def to_approved(h, *, files=None):
    """Ship a change, let the reviewer accept it: the PR is `approved` after the next sync."""
    h.ship(files)
    pr = h.pr()
    assert pr is not None and pr.stage == "reviewing", pr and pr.stage
    h.verdict("accepted")
    return pr


# ------------------------------------------------------------------ opening


def test_a_quiet_period_batches_runs_before_the_pull_request_is_opened(harness_factory):
    h = harness_factory(pr_batch_quiet_seconds=600)
    h.sync()
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    assert fin.merged
    h.sync(advance=1)
    assert h.pr() is None and h.remote_sha(h.work) is None
    _ws, fin = h.merge_run({"b.txt": "b\n"}, run_id="run2")  # a second run restarts the timer
    h.sync(advance=300)
    assert h.pr() is None
    h.sync(advance=200)
    assert h.pr() is None  # 200 s after the last merge: still inside the window
    h.sync(advance=450)
    pr = h.pr()
    assert pr is not None and pr.number == 1 and pr.stage == "reviewing"
    assert h.remote_sha(h.work) == pr.head_sha == h.work_sha()
    # one pull request carries both runs
    assert "feature" not in pr.title
    gh_pr = h.world.github.repo(NAME).pulls[1]
    assert gh_pr["head_ref"] == h.work and gh_pr["base_ref"] == "main"
    body = h.world.github.repo(NAME).issues[1]["body"]
    assert "Changes" in body and "Foundry" in body


def test_no_pull_request_without_work_or_without_a_net_change(harness_factory):
    h = harness_factory()
    h.sync(advance=5)
    assert h.pr() is None
    # a run that changes nothing
    _ws, fin = h.merge_run({})
    assert not fin.changed
    h.sync(advance=5)
    assert h.pr() is None and h.remote_sha(h.work) is None


def test_a_pull_request_that_already_exists_is_adopted(harness_factory):
    h = harness_factory()
    h.sync()
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    assert fin.merged
    # a crashed earlier attempt: the branch was pushed and the PR opened, Foundry has no row for it
    sha = h.work_sha()
    run_git(
        "push", "-q", str(h.world.remote.repo_path(NAME)), f"{sha}:refs/heads/{h.work}", cwd=h.repo
    )
    client = h.world.client()
    opened = client.create_pull(NAME, title="Earlier", body="x", head=h.work, base="main")
    h.sync(advance=1)
    pr = h.pr()
    assert pr.number == opened["number"] and pr.stage == "reviewing" and pr.head_sha == sha
    assert len(h.pulls()) == 1
    assert len(h.world.github.repo(NAME).pulls) == 1  # nothing was opened twice


def test_a_crash_between_the_row_and_the_api_call_is_resumed(harness_factory, monkeypatch):
    h = harness_factory()
    h.sync()
    _ws, fin = h.merge_run({"a.txt": "a\n"})
    assert fin.merged
    h.world.github.fail_next(500, "boom", method="POST", match="/pulls")
    h.sync(advance=1)
    pr = h.pr()
    assert pr is not None and pr.number == 0 and pr.stage == "opening"
    assert h.remote_sha(h.work) == h.work_sha()  # the branch was pushed
    h.sync(advance=3600)
    pr = h.pr()
    assert pr.number == 1 and pr.stage == "reviewing" and len(h.pulls()) == 1


def test_the_review_task_is_requested_after_the_push_and_the_head_is_frozen(harness_factory):
    h = harness_factory()
    pr = to_approved(h)
    assert pr.review_sha == pr.head_sha and pr.review_task_id and h.review_calls == 1
    assert h.audit("repository.pr_opened") and h.audit("repository.pushed")
    # more work lands while the PR is under review: it is NOT pushed (the head is frozen)
    head = pr.head_sha
    _ws, fin = h.merge_run({"more.txt": "more\n"}, run_id="run2")
    assert fin.merged and h.work_sha() != head
    h.sync(advance=5)
    assert h.remote_sha(h.work) == head
    assert h.pr().head_sha == head


# ------------------------------------------------------------------ review, CI and merge


def test_accept_green_ci_and_auto_merge_merges_exactly_the_reviewed_sha(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    fake_ci(h.world, NAME, "success")
    pr = to_approved(h, files={"feature.txt": "feature\n"})
    h.sync(advance=5)
    assert h.pr().stage == "merged" and h.pr().merged_sha
    [call] = merges(h)
    assert call.body["sha"] == pr.head_sha and call.body["merge_method"] == "squash"
    assert h.world.remote.read_file(NAME, "main", "feature.txt") == "feature\n"
    assert h.audit("repository.pr_merged")
    # the next sync takes the squash commit into the work branch: nothing left to ship
    h.sync(advance=10)
    h.sync(advance=10)
    assert len(h.pulls()) == 1 and h.git("diff", "--stat", "origin/main", h.work) == ""
    assert h.binding().sync_state == "idle"


def test_pending_ci_waits_and_failing_ci_asks_for_a_fix(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    fake_ci(h.world, NAME, "pending")
    to_approved(h)
    h.sync(advance=5)
    h.sync(advance=5)
    assert h.pr().stage == "approved" and h.pr().checks_state == "pending" and not merges(h)
    fake_ci(h.world, NAME, "failure", log="boom")
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "changes_requested" and pr.ci_fix_attempts == 1
    [item] = h.items(kind="check_failure")
    assert item.pull_request_id == pr.id and item.revision == 0 and item.task_id
    [task] = h.tasks(id=item.task_id)
    assert "Fix failing CI" in task.title and task.input_refs == [f"repo:ci-fix:{pr.id}"]
    assert not merges(h)


def test_if_present_with_no_checks_merges_only_after_the_wait(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge", ci_mode="if_present", ci_wait_seconds=600)
    fake_ci(h.world, NAME, None)
    to_approved(h)
    h.sync(advance=60)
    assert h.pr().stage == "approved" and not merges(h)
    h.sync(advance=600)
    assert h.pr().stage == "merged" and len(merges(h)) == 1


def test_required_ci_with_no_checks_goes_to_a_person(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge", ci_mode="required", ci_wait_seconds=600)
    fake_ci(h.world, NAME, None)
    to_approved(h)
    h.sync(advance=60)
    assert h.pr().stage == "approved"
    h.sync(advance=700)
    pr = h.pr()
    assert pr.stage == "awaiting_human" and "No CI result" in (pr.last_error or "")
    assert h.alerts("github_pr_needs_human") and not merges(h)
    # the checks show up later and are green: the merge resumes by itself
    fake_ci(h.world, NAME, "success")
    h.sync(advance=60)
    assert h.pr().stage in ("merged", "approved", "merging")


def test_ci_fix_cycle_pushes_a_fast_forward_and_the_pull_request_is_reviewed_again(
    harness_factory,
):
    h = harness_factory(merge_mode="pr_auto_merge", max_ci_fix_attempts=3)
    fake_ci(h.world, NAME, "failure")
    pr = to_approved(h)
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "changes_requested"
    [item] = h.items(kind="check_failure")
    first_head = pr.head_sha
    # the fix task is still open: nothing happens
    h.sync(advance=5)
    assert h.pr().stage == "changes_requested" and h.remote_sha(h.work) == first_head
    # an engineer fixes it: a run merges, the task is done
    _ws, fin = h.merge_run({"fix.txt": "fix\n"}, run_id="fix1")
    assert fin.merged
    h.finish_task(item.task_id)
    fake_ci(h.world, NAME, "success")
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "reviewing" and pr.revision == 1 and pr.head_sha == h.work_sha()
    assert h.remote_sha(h.work) == pr.head_sha != first_head
    # fast-forward only: the old head is an ancestor of the new one on the remote
    assert h.world.remote.is_ancestor(NAME, first_head, pr.head_sha)
    assert pr.review_sha == pr.head_sha and pr.verdict is None and h.review_calls == 2
    h.verdict("accepted")
    h.sync(advance=5)
    h.sync(advance=5)
    assert h.pr().stage == "merged"
    assert h.world.remote.read_file(NAME, "main", "fix.txt") == "fix\n"


def test_changes_requested_by_the_reviewer_creates_the_next_revision(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge", max_pr_revisions=1)
    fake_ci(h.world, NAME, "success")
    h.ship()
    h.verdict("changes_requested", note="rename it")
    # C's record_verdict also created the fix task and its item
    fix_task = new_task(
        h.client, h.cid, title="Address the review", project_id=h.bound.project["id"]
    )["id"]
    with h.app.state.session_factory() as db:
        from app.models.repository import RepoItem

        pr = h.pr()
        db.add(
            RepoItem(
                company_id=h.cid,
                repository_id=h.rid,
                kind="internal_review",
                external_id=f"iv:{pr.id}",
                number=pr.number,
                status="tasked",
                task_id=fix_task,
                pull_request_id=pr.id,
                revision=0,
            )
        )
        db.commit()
    h.sync(advance=5)
    assert h.pr().stage == "changes_requested"
    _ws, fin = h.merge_run({"rename.txt": "x\n"}, run_id="fix1")
    assert fin.merged
    h.finish_task(fix_task)
    h.sync(advance=5)
    assert h.pr().stage == "reviewing" and h.pr().revision == 1
    # the revision cap: a second change request is handed to a person
    h.verdict("changes_requested")
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "awaiting_human" and not merges(h)
    assert h.alerts("github_pr_needs_human")


def test_ci_fix_attempts_are_bounded(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge", max_ci_fix_attempts=0)
    fake_ci(h.world, NAME, "failure")
    to_approved(h)
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "awaiting_human" and pr.ci_fix_attempts == 1
    assert "after 0 fix attempts" in pr.last_error


def test_an_upstream_only_head_change_keeps_the_verdict(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    fake_ci(h.world, NAME, "pending")  # stay in `approved`
    pr = to_approved(h)
    h.sync(advance=5)
    assert h.pr().stage == "approved"
    # someone merges main into the PR branch on GitHub ("update branch")
    h.world.human_commit(NAME, {"upstream.txt": "u\n"})
    h.world.client().update_pull_branch(NAME, 1, pr.head_sha)
    new_head = h.remote_sha(h.work)
    assert new_head != pr.head_sha
    h.sync(advance=5)
    pr2 = h.pr()
    assert pr2.head_sha == new_head and pr2.review_sha == pr.review_sha
    assert pr2.verdict == "accepted" and pr2.revision == 0 and pr2.stage == "approved"
    assert h.review_calls == 1
    # the human commit on the work branch (a person's change) is merged into the local branch
    assert h.git("show", f"{h.work}:upstream.txt") == "u"


def test_a_real_change_to_the_head_by_a_person_is_reviewed_again(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    fake_ci(h.world, NAME, "pending")
    pr = to_approved(h)
    h.sync(advance=5)
    h.world.human_commit(NAME, {"human.txt": "h\n"}, branch=h.work)
    h.sync(advance=5)
    pr2 = h.pr()
    assert pr2.revision == 1 and pr2.stage == "reviewing" and pr2.verdict is None
    assert pr2.head_sha != pr.head_sha and h.review_calls == 2


# ------------------------------------------------------------------ human merge, close


def test_human_merge_mode_waits_for_a_person_and_notices_the_merge(harness_factory):
    h = harness_factory(merge_mode="pr_human_merge")
    fake_ci(h.world, NAME, "success")
    to_approved(h)
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "awaiting_human" and not merges(h)
    with h.app.state.session_factory() as db:
        notes = list(db.scalars(select(Notification).where(Notification.kind == "github_pr_ready")))
    assert notes and "#1" in notes[0].title
    h.sync(advance=300)
    assert h.pr().stage == "awaiting_human"
    h.world.github.human_merge(NAME, 1, method="squash", login="alice")
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "merged" and pr.merged_by == "alice" and pr.merged_sha
    assert h.world.remote.read_file(NAME, "main", "feature.txt") == "feature\n"


def test_a_pull_request_closed_by_a_person_stops_the_reopen_loop(harness_factory):
    h = harness_factory(merge_mode="pr_human_merge")
    fake_ci(h.world, NAME, "success")
    h.ship()
    h.world.github.repo(NAME).pulls[1].update(
        state="closed", closed_at="2026-01-01T00:00:00Z", head_sha_final=h.pr().head_sha
    )
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "closed" and h.binding().pr_opening_paused
    assert h.alerts("github_pr_closed")
    for _ in range(3):
        h.sync(advance=700)
    assert len(h.pulls()) == 1 and len(h.world.github.repo(NAME).pulls) == 1
    # new work after the closed head resumes pull request opening by itself
    _ws, fin = h.merge_run({"new.txt": "n\n"}, run_id="run2")
    assert fin.merged
    h.sync(advance=5)
    assert len(h.pulls()) == 2 and h.pr().number == 2 and not h.binding().pr_opening_paused


def test_the_owner_can_resume_opening_after_a_close(harness_factory):
    h = harness_factory(merge_mode="pr_human_merge")
    h.ship()
    h.world.github.repo(NAME).pulls[1].update(state="closed", head_sha_final=h.pr().head_sha)
    h.sync(advance=5)
    assert h.binding().pr_opening_paused
    h.sync(advance=700)
    assert len(h.pulls()) == 1
    h.update(pr_opening_paused=False)  # what POST .../pull-requests/resume-opening does
    h.sync(advance=5)
    assert len(h.pulls()) == 2


# ------------------------------------------------------------------ never past protection


def test_a_blocked_pull_request_is_never_merged_even_with_an_admin_token(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    h.world.github.protect(NAME, require_pr=True, approvals=1)
    fake_ci(h.world, NAME, "success")
    to_approved(h)
    h.sync(advance=5)
    pr = h.pr()
    assert pr.mergeable_state == "blocked"
    assert pr.stage == "awaiting_human" and "never bypasses" in pr.last_error
    for _ in range(3):
        h.sync(advance=700)
    assert not merges(h) and h.world.remote.read_file(NAME, "main", "feature.txt") is None
    # a person approves on GitHub; the merge is theirs to press (the stage waits for a person)
    assert h.pr().stage == "awaiting_human"


def test_a_sha_mismatch_on_merge_is_retried_next_sync(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    fake_ci(h.world, NAME, "success")
    to_approved(h)
    h.world.github.fail_next(409, "Head branch was modified", method="PUT", match="/merge")
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "approved" and "409" in (pr.last_error or "")
    h.sync(advance=5)
    assert h.pr().stage == "merged"


def test_a_human_approval_rule_parks_the_merge_until_approved(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    add_rule(
        h.client,
        h.cid,
        tier="external_commitment",
        action="github.pr.merge",
        decision="human_approval",
    )
    fake_ci(h.world, NAME, "success")
    pr = to_approved(h)
    h.sync(advance=5)
    h.sync(advance=5)
    assert h.pr().stage == "merging" and h.binding().sync_state == "awaiting_approval"
    assert not merges(h)
    approval_id = h.pr().approval_id
    assert approval_id
    r = h.client.post(api(h.cid, f"/approvals/{approval_id}/approve"), json={})
    assert r.status_code == 200, r.text
    h.sync(advance=5)
    assert h.pr().stage == "merged"
    [call] = merges(h)
    assert call.body["sha"] == pr.head_sha  # only the approved head
    assert h.binding().sync_state == "idle"


def test_a_head_that_moved_after_the_approval_is_not_merged(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    add_rule(
        h.client,
        h.cid,
        tier="external_commitment",
        action="github.pr.merge",
        decision="human_approval",
    )
    fake_ci(h.world, NAME, "success")
    to_approved(h)
    h.sync(advance=5)
    h.sync(advance=5)
    approval_id = h.pr().approval_id
    h.client.post(api(h.cid, f"/approvals/{approval_id}/approve"), json={})
    h.world.human_commit(NAME, {"sneaky.txt": "s\n"}, branch=h.work)  # the head moves
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage != "merged" and pr.revision == 1 and not merges(h)


# ------------------------------------------------------------------ direct push


def test_direct_push_to_an_unprotected_branch_needs_no_pull_request(harness_factory):
    h = harness_factory(merge_mode="direct_push")
    h.ship()
    assert h.pulls() == [] and h.remote_sha("main") == h.work_sha()
    assert h.world.remote.read_file(NAME, "main", "feature.txt") == "feature\n"
    assert h.audit("repository.pushed")


def test_direct_push_on_a_protected_branch_falls_back_to_a_pull_request(harness_factory):
    h = harness_factory(merge_mode="direct_push")
    h.world.github.protect(NAME, require_pr=True)
    before = h.remote_sha("main")
    h.ship()
    assert h.remote_sha("main") == before
    pr = h.pr()
    assert pr is not None and pr.number == 1
    [alert] = h.alerts("github_direct_push_refused")
    assert "direct_push" in alert.body
    assert h.binding().policy["merge_mode"] == "direct_push"  # the owner's policy is untouched


def test_direct_push_integrates_and_retries_once_after_a_non_fast_forward(
    harness_factory, monkeypatch
):
    from app.services import repo_git

    h = harness_factory(merge_mode="direct_push")
    real_push = repo_git.push
    calls = {"n": 0}

    def racing_push(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            h.world.human_commit(NAME, {"race.txt": "r\n"})  # a person got there first
        return real_push(*args, **kwargs)

    monkeypatch.setattr(repo_git, "push", racing_push)
    h.ship()
    assert calls["n"] == 2
    assert h.world.remote.read_file(NAME, "main", "race.txt") == "r\n"
    assert h.world.remote.read_file(NAME, "main", "feature.txt") == "feature\n"
    assert h.remote_sha("main") == h.work_sha()
    assert h.pulls() == []


def test_a_review_that_ends_without_a_verdict_is_reissued_then_escalated(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    h.ship()
    first = h.pr().review_task_id
    h.finish_task(first, "done")  # the reviewer finished without recording a verdict
    h.sync(advance=5)
    second = h.pr().review_task_id
    assert second != first
    [t2] = h.tasks(id=second)
    assert t2.idempotency_key.endswith(":r1")
    h.finish_task(second, "failed")
    h.sync(advance=5)
    third = h.pr().review_task_id
    [t3] = h.tasks(id=third)
    # (the Co-founder is also the lead here, so the assignee cannot differ)
    assert t3.idempotency_key.endswith(":r2")
    h.finish_task(third, "done")
    h.sync(advance=5)
    pr = h.pr()
    assert pr.stage == "awaiting_human" and h.alerts("github_pr_review_stalled")
