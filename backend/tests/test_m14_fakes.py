"""Self-tests of the M14 fakes: the smart-HTTP git remote and the fake GitHub API. If these are
wrong, every later M14 test would be testing the fakes' mistakes, so they pin down the behaviours the
other suites rely on (auth, read-only tokens, non-fast-forward rejection, merges that really land in
the bare repository, ETags, rate limits ...). No real network, no real GitHub."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.base import ProviderError
from app.integrations.github import NOT_MODIFIED
from tests.m14_git_remote import run_git
from tests.m14_helpers import (
    LOGIN,
    OTHER_TOKEN,
    READONLY_TOKEN,
    TOKEN,
    askpass_env,
    bare_world,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REPO = f"{LOGIN}/app"


def clone(world, tmp_path, token, dest="work", *, full_name=REPO, check=True):
    return run_git(
        "clone",
        "-q",
        world.remote.clone_url(full_name),
        str(tmp_path / dest),
        env=askpass_env(tmp_path, token),
        check=check,
    )


# ------------------------------------------------------------------ the git remote


def test_clone_over_http_needs_the_token_and_the_token_never_leaves_the_header(
    bare_world, tmp_path
):
    w = bare_world
    w.add_repo(REPO, files={"README.md": "hello\n"})
    clone(w, tmp_path, TOKEN)
    assert (tmp_path / "work" / "README.md").read_text() == "hello\n"
    # git first tried anonymously (401), then sent Basic x-access-token:<token>
    reqs = w.remote.requests_for("git-upload-pack")
    assert reqs and any(r.status == 200 for r in reqs)
    assert any(r.status == 401 and r.authorization is None for r in reqs)
    assert {r.user for r in reqs if r.user} == {"x-access-token"}
    assert w.remote.tokens_seen() == {TOKEN}
    # the token is in no file of the clone either
    config = (tmp_path / "work" / ".git" / "config").read_text()
    assert TOKEN not in config and "x-access-token" not in config


def test_a_wrong_or_missing_token_is_refused(bare_world, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    bad = clone(w, tmp_path, "github_pat_nope_" + "x" * 30, "bad", check=False)
    assert bad.returncode != 0 and "uthentication failed" in bad.stderr
    assert not (tmp_path / "bad").exists()
    anonymous = run_git(
        "clone",
        "-q",
        w.remote.clone_url(REPO),
        str(tmp_path / "anon"),
        env={"GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    assert anonymous.returncode != 0
    # a token that cannot see the (private) repository is told nothing: 404
    other = clone(w, tmp_path, OTHER_TOKEN, "other", check=False)
    assert other.returncode != 0


def test_a_read_only_token_can_clone_but_not_push(bare_world, tmp_path):
    w = bare_world
    w.add_repo(REPO, files={"a.txt": "a\n"})
    clone(w, tmp_path, READONLY_TOKEN)
    work = tmp_path / "work"
    (work / "b.txt").write_text("b\n")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-q", "-m", "b", cwd=work)
    push = run_git(
        "push",
        "origin",
        "HEAD:refs/heads/main",
        cwd=work,
        env=askpass_env(tmp_path, READONLY_TOKEN),
        check=False,
    )
    assert push.returncode != 0 and "403" in push.stderr
    assert any(r.status == 403 and r.service == "git-receive-pack" for r in w.remote.requests)
    assert w.remote.read_file(REPO, "main", "b.txt") is None


def test_a_write_token_pushes_and_a_non_fast_forward_is_rejected(bare_world, tmp_path):
    w = bare_world
    w.add_repo(REPO, files={"a.txt": "a\n"})
    clone(w, tmp_path, TOKEN)
    work = tmp_path / "work"
    env = askpass_env(tmp_path, TOKEN)
    (work / "mine.txt").write_text("mine\n")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-q", "-m", "mine", cwd=work)
    run_git("push", "-q", "origin", "HEAD:refs/heads/feature", cwd=work, env=env)
    assert w.remote.read_file(REPO, "feature", "mine.txt") == "mine\n"
    # someone else moves main; a push of our (diverged) history is not a fast-forward
    w.human_commit(REPO, {"theirs.txt": "theirs\n"})
    rejected = run_git("push", "origin", "HEAD:refs/heads/main", cwd=work, env=env, check=False)
    assert rejected.returncode != 0
    assert "non-fast-forward" in rejected.stderr or "rejected" in rejected.stderr
    assert w.remote.read_file(REPO, "main", "mine.txt") is None
    assert w.remote.read_file(REPO, "main", "theirs.txt") == "theirs\n"


def test_an_empty_repository_can_be_cloned_and_receives_the_first_push(bare_world, tmp_path):
    w = bare_world
    w.add_repo(REPO, empty=True)
    assert w.remote.is_empty(REPO)
    clone(w, tmp_path, TOKEN)
    work = tmp_path / "work"
    (work / "README.md").write_text("first\n")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-q", "-m", "first", cwd=work)
    run_git(
        "push", "-q", "origin", "HEAD:refs/heads/main", cwd=work, env=askpass_env(tmp_path, TOKEN)
    )
    assert w.remote.read_file(REPO, "main", "README.md") == "first\n"


# ------------------------------------------------------------------ the fake GitHub API


def test_identity_scopes_expiry_and_org_listing(bare_world):
    w = bare_world
    expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    w.github.add_token("classic_x", "alice", kind="classic", orgs={"acme"})
    w.github.tokens[TOKEN].expires_at = expiry
    me = w.client().identity()
    assert me["login"] == LOGIN
    client = w.client()
    client.identity()
    assert client.token_expires_at == expiry
    classic = w.client("classic_x")
    assert classic.identity()["login"] == "alice" and classic.scopes == ["repo"]
    assert [o["login"] for o in classic.list_orgs()] == ["acme"]
    with pytest.raises(ProviderError) as exc:
        w.client("revoked_token").identity()
    assert exc.value.details["status"] == 401


def test_create_repo_needs_administration_and_rejects_duplicates(bare_world):
    w = bare_world
    # the default token has no Administration permission
    with pytest.raises(ProviderError) as exc:
        w.client().create_repo(LOGIN, "new-one")
    assert exc.value.details["reason"] == "not_accessible"
    w.github.add_token("admin_tok", LOGIN, permissions={"administration": "write"})
    admin = w.client("admin_tok")
    created = admin.create_repo(LOGIN, "new-one", private=True, description="d")
    assert created["full_name"] == f"{LOGIN}/new-one" and created["private"] is True
    assert w.remote.exists(f"{LOGIN}/new-one") and w.remote.is_empty(f"{LOGIN}/new-one")
    assert admin.is_empty(f"{LOGIN}/new-one") is True
    with pytest.raises(ProviderError) as exc:
        admin.create_repo(LOGIN, "new-one")
    assert exc.value.details["status"] == 422
    # an organisation the user does not belong to looks like it does not exist
    with pytest.raises(ProviderError) as exc:
        admin.create_repo("stranger-org", "x", org=True)
    assert exc.value.details["status"] == 404


def test_a_pull_request_is_created_adopted_and_merged_for_real(bare_world):
    w = bare_world
    w.add_repo(REPO, files={"README.md": "base\n"})
    base = w.remote.branch_sha(REPO, "main")
    w.remote.commit(REPO, "foundry/app", {"feature.txt": "feature\n"}, parent_branch="main")
    c = w.client()
    pr = c.create_pull(
        REPO, title="Add feature", body="Refs #1", head=f"{LOGIN}:foundry/app", base="main"
    )
    assert pr["number"] == 1 and pr["state"] == "open" and pr["mergeable_state"] == "clean"
    # a second identical PR is refused; the open one can be found again
    with pytest.raises(ProviderError) as exc:
        c.create_pull(REPO, title="again", body="", head=f"{LOGIN}:foundry/app", base="main")
    assert exc.value.details["already_exists"] is True
    assert c.find_pull(REPO, f"{LOGIN}:foundry/app")["number"] == 1
    head = pr["head"]["sha"]
    # a stale SHA is refused (409), the right one merges and lands in the bare repository
    with pytest.raises(ProviderError) as exc:
        c.merge_pull(REPO, 1, sha=base, method="squash")
    assert exc.value.details["reason"] == "sha_mismatch"
    merged = c.merge_pull(REPO, 1, sha=head, method="squash")
    assert merged["merged"] is True
    assert w.remote.branch_sha(REPO, "main") == merged["sha"]
    assert w.remote.read_file(REPO, "main", "feature.txt") == "feature\n"
    done = c.get_pull(REPO, 1)
    assert (
        done["merged"] is True and done["state"] == "closed" and done["merged_by"]["login"] == LOGIN
    )
    with pytest.raises(ProviderError) as exc:
        c.merge_pull(REPO, 1, sha=head, method="squash")
    assert exc.value.details["reason"] == "not_mergeable"


def test_mergeable_state_reflects_conflicts_protection_checks_and_staleness(bare_world):
    w = bare_world
    w.add_repo(REPO, files={"a.txt": "one\n"})
    c = w.client()
    w.remote.commit(REPO, "work", {"a.txt": "mine\n", "new.txt": "n\n"}, parent_branch="main")
    pr = c.create_pull(REPO, title="t", body="", head="work", base="main")
    n = pr["number"]
    assert c.get_pull(REPO, n)["mergeable_state"] == "clean"
    # failing CI: unstable (mergeable by the API, but not "clean")
    w.github.set_ci(REPO, "failure")
    assert c.get_pull(REPO, n)["mergeable_state"] == "unstable"
    w.github.set_ci(REPO, "success")
    assert c.get_pull(REPO, n)["mergeable_state"] == "clean"
    # branch protection: an approval is required -> blocked until a stranger approves
    w.github.protect(REPO, approvals=1)
    assert c.get_pull(REPO, n)["mergeable_state"] == "blocked"
    with pytest.raises(ProviderError) as exc:
        c.merge_pull(REPO, n, sha=pr["head"]["sha"], method="squash")
    assert exc.value.details["reason"] == "not_mergeable"
    w.github.human_review(REPO, n, "APPROVED", author="alice")
    assert c.get_pull(REPO, n)["mergeable_state"] == "clean"
    # required status check pending -> blocked again
    w.github.set_ci(REPO, "pending", required=True)
    assert c.get_pull(REPO, n)["mergeable_state"] == "blocked"
    w.github.set_ci(REPO, "success", required=True)
    # strict protection and a moved base -> behind
    w.github.protect(REPO, approvals=0, required_checks=["ci"], strict=True)
    w.remote.commit(REPO, "main", {"other.txt": "o\n"})
    assert c.get_pull(REPO, n)["mergeable_state"] == "behind"
    head_before = c.get_pull(REPO, n)["head"]["sha"]
    c.update_pull_branch(REPO, n, head_before)
    assert c.get_pull(REPO, n)["mergeable_state"] == "clean"
    # a conflicting base change -> dirty
    w.github.unprotect(REPO)
    w.remote.commit(REPO, "main", {"a.txt": "theirs\n"})
    assert c.get_pull(REPO, n)["mergeable_state"] == "dirty"


def test_issues_labels_comments_and_the_since_filter(bare_world):
    w = bare_world
    w.add_repo(REPO)
    num = w.github.human_issue(REPO, "Bug", "it breaks", author="alice", labels=["foundry"])
    w.github.human_comment(REPO, num, "me too", author="bob", association="NONE")
    c = w.client()
    issues = c.list_issues(REPO)
    assert [i["number"] for i in issues] == [num]
    assert (
        issues[0]["labels"] == [{"name": "foundry"}] and issues[0]["author_association"] == "OWNER"
    )
    assert [x["body"] for x in c.issue_comments(REPO, num)] == ["me too"]
    posted = c.create_issue_comment(REPO, num, "on it")
    assert posted["user"]["login"] == LOGIN
    c.update_issue(REPO, num, labels=["bug"], state="closed")
    assert c.list_issues(REPO) == []
    assert c.list_issues(REPO, state="closed")[0]["state"] == "closed"
    assert {lbl["name"] for lbl in c.list_labels(REPO)} >= {"bug", "foundry"}
    future = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert c.list_issues(REPO, state="all", since=future) == []


def test_pull_requests_show_up_in_the_issue_list_with_a_marker(bare_world):
    w = bare_world
    w.add_repo(REPO)
    w.remote.commit(REPO, "x", {"x.txt": "x\n"}, parent_branch="main")
    c = w.client()
    c.create_pull(REPO, title="PR", body="", head="x", base="main")
    items = c.list_issues(REPO, state="all")
    assert len(items) == 1 and "pull_request" in items[0]


def test_ci_checks_logs_and_the_log_download_carries_no_authorization(bare_world):
    w = bare_world
    w.add_repo(REPO)
    sha = w.remote.branch_sha(REPO, "main")
    w.github.set_ci(REPO, "failure", sha=sha, name="tests", log="line1\nBOOM\n")
    c = w.client()
    runs = c.check_runs(REPO, sha)
    assert [(r["name"], r["conclusion"]) for r in runs] == [("tests", "failure")]
    workflow = c.workflow_runs(REPO, sha)
    assert workflow[0]["conclusion"] == "failure"
    jobs = c.run_jobs(REPO, workflow[0]["id"])
    assert jobs and jobs[0]["conclusion"] == "failure"
    tail = c.job_log_tail(REPO, jobs[0]["id"], limit=1000)
    assert "BOOM" in tail
    # the second request went to the log host without the Authorization header
    assert w.github.log_calls and "authorization" not in w.github.log_calls[0].headers
    assert any(call.authorization for call in w.github.calls)
    assert c.combined_status(REPO, sha)["sha"] == sha
    # a denied capability answers 403 "Resource not accessible by personal access token"
    w.github.deny(REPO, "checks")
    with pytest.raises(ProviderError) as exc:
        c.check_runs(REPO, sha)
    assert exc.value.details["reason"] == "not_accessible"


def test_alerts_releases_hooks_and_ref_deletion(bare_world):
    w = bare_world
    w.add_repo(REPO)
    w.github.add_alert(REPO, 1, "requests", "high")
    w.github.add_alert(REPO, 2, "flask", "low", state="fixed")
    w.remote.commit(REPO, "foundry/old", {"o.txt": "o\n"}, parent_branch="main")
    w.github.tokens[TOKEN].permissions["webhooks"] = "write"
    c = w.client()
    assert [a["number"] for a in c.dependabot_alerts(REPO)] == [1]
    assert [a["number"] for a in c.dependabot_alerts(REPO, "fixed")] == [2]
    rel = c.create_release(REPO, tag="v1.0.0", name="One", body="notes", draft=True)
    assert rel["draft"] is True and w.github.repo(REPO).releases[0]["tag_name"] == "v1.0.0"
    hook = c.create_hook(
        REPO, url="https://x.test/hook", secret="s3cret-value-123", events=["push"]
    )
    assert hook["config"]["secret"] == "********"
    # webhooks need that permission
    w.github.tokens[TOKEN].permissions["webhooks"] = "none"
    with pytest.raises(ProviderError):
        c.create_hook(REPO, url="https://x.test/hook", secret="s3cret-value-123", events=["push"])
    c.delete_ref(REPO, "foundry/old")
    assert w.remote.branch_sha(REPO, "foundry/old") is None
    # the protected default branch cannot be deleted
    w.github.protect(REPO)
    with pytest.raises(ProviderError):
        c.delete_ref(REPO, "main")


def test_etags_return_304_without_spending_the_rate_limit(bare_world):
    w = bare_world
    w.add_repo(REPO)
    w.github.human_issue(REPO, "One")
    etags: dict[str, str] = {}
    c = w.client(etags=etags)
    first = c.list_issues(REPO, conditional=True)
    assert len(first) == 1 and etags
    remaining = w.github.rate_remaining
    again = w.client(etags=etags).list_issues(REPO, conditional=True)
    assert again is NOT_MODIFIED
    assert w.github.rate_remaining == remaining  # a 304 is free
    w.github.human_issue(REPO, "Two")
    changed = w.client(etags=etags).list_issues(REPO, conditional=True)
    assert changed is not NOT_MODIFIED and len(changed) == 2


def test_rate_limit_headers_primary_and_secondary_limits(bare_world):
    w = bare_world
    w.add_repo(REPO)
    from app.integrations.github import GithubRateLimited

    c = w.client()
    c.get_repo(REPO)
    assert c.last_rate.limit == 5000 and c.last_rate.remaining == 4999
    assert c.last_rate.reset_at is not None
    w.github.secondary_limit(retry_after=45)
    with pytest.raises(GithubRateLimited) as exc:
        c.create_issue(REPO, title="x")
    assert exc.value.retry_after == 45 and exc.value.wait_seconds() == 45
    w.github.set_rate(0, reset_in=120)
    with pytest.raises(GithubRateLimited) as exc:
        c.get_repo(REPO)
    assert exc.value.retry_after is None and exc.value.wait_seconds() >= 60
    assert exc.value.reset_at is not None


def test_sso_org_approval_and_injected_failures(bare_world):
    w = bare_world
    w.add_repo("acme/secret", org=True)
    w.github.tokens[TOKEN].orgs.add("acme")
    w.github.tokens[TOKEN].repos = None
    w.github.add_org("acme", {LOGIN})
    w.github.require_sso(TOKEN, "acme")
    from app.integrations.github import GithubSsoRequired

    with pytest.raises(GithubSsoRequired) as exc:
        w.client().get_repo("acme/secret")
    assert exc.value.sso_url.startswith("https://github.com/orgs/acme/sso")
    w.github.tokens[TOKEN].sso_orgs.clear()
    w.github.require_org_approval(TOKEN, "acme")
    with pytest.raises(ProviderError) as exc2:
        w.client().get_repo("acme/secret")
    assert "approval" in exc2.value.message
    w.github.tokens[TOKEN].approval_pending_orgs.clear()
    w.github.fail_next(502, "Bad gateway", match="/repos/acme/secret")
    with pytest.raises(ProviderError) as exc3:
        w.client().get_repo("acme/secret")
    assert exc3.value.details["status"] == 502
    assert w.client().get_repo("acme/secret")["full_name"] == "acme/secret"


def test_human_merge_close_and_rewrite_helpers(bare_world):
    w = bare_world
    w.add_repo(REPO, files={"a.txt": "a\n"})
    w.remote.commit(REPO, "feat", {"f.txt": "f\n"}, parent_branch="main")
    c = w.client()
    c.create_pull(REPO, title="t", body="", head="feat", base="main")
    w.github.human_merge(REPO, 1, method="merge", login="alice")
    pr = c.get_pull(REPO, 1)
    assert pr["merged"] and pr["merged_by"]["login"] == "alice"
    w.remote.commit(REPO, "feat2", {"g.txt": "g\n"}, parent_branch="main")
    c.create_pull(REPO, title="t2", body="", head="feat2", base="main")
    w.github.human_close(REPO, 2)
    assert c.get_pull(REPO, 2)["state"] == "closed" and not c.get_pull(REPO, 2)["merged"]
    old = w.remote.branch_sha(REPO, "main")
    new = w.remote.rewrite_branch(REPO, "main")
    assert new != old and not w.remote.is_ancestor(REPO, old, new)
