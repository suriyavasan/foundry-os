"""M14 WP-D: ``foundryos repo ...`` and the doctor checks for git and the GitHub repositories.

The commands run against ``FakeApi`` (tests/m14_cli_fakes.py), an HTTP fake of the step-0 routes, so
each test pins which endpoint is hit, with which body, what is printed and what never is (the token).
The doctor checks run against the test application's database and a faked ``git``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cli import onboarding
from app.core.config import Settings
from app.ops import doctor
from tests.m14_cli_fakes import TOKEN, cfg_dir, fake, runner  # noqa: F401  (fixtures)
from tests.m14_helpers import LOGIN, github_world  # noqa: F401  (a fixture)
from tests.m14_service_helpers import (  # noqa: F401  (m14 is a fixture)
    connect_body,
    github_account,
    m14,
    post_repo,
    set_repo,
)

# ------------------------------------------------------------------ list and status


def test_list_shows_every_repository(runner, fake):
    fake.add_repo("acme/app", is_company_default=True)
    fake.add_repo(
        "acme/site",
        status="auth_failed",
        open_pull_request={"number": 7, "stage": "reviewing"},
    )
    out = runner("repo", "list").out
    assert "acme/app" in out and "acme/site" in out and "auth_failed" in out
    assert "#7 reviewing" in out and "pr_human_merge" in out
    rows = runner("--json", "repo", "list").json()
    assert [r["full_name"] for r in rows] == ["acme/app", "acme/site"]


def test_list_when_empty_points_to_connect(runner, fake):
    assert "foundryos repo connect" in runner("repo", "list").out
    assert runner("--json", "repo", "list").json() == []


def test_list_when_the_feature_is_not_available_says_why(runner, fake):
    fake.available = {"available": False, "reason": "git is not installed", "milestone": "M14"}
    assert "git is not installed" in runner("repo", "list").out


def test_status_defaults_to_the_only_repository(runner, fake):
    fake.add_repo(
        "acme/app",
        open_pull_request={"number": 3, "stage": "awaiting_human", "url": "https://x/pull/3"},
    )
    out = runner("repo", "status").out
    assert "acme/app" in out and "main <- foundry/app" in out
    assert "#3 awaiting_human" in out and "pr_human_merge (squash), CI if_present" in out
    assert "off (polling every 300s)" in out


def test_status_explains_what_to_do(runner, fake):
    fake.add_repo(
        "acme/app",
        status="auth_failed",
        sync_state="push_blocked",
        pr_opening_paused=True,
        webhook={"enabled": True, "stale": True},
    )
    out = runner("repo", "status").out
    assert "foundryos repo token" in out and "reset-integration" in out
    assert "foundryos repo resume-prs" in out and "public URL changed" in out
    data = runner("--json", "repo", "status").json()
    assert data["hints"] and data["full_name"] == "acme/app"


def test_a_repository_is_found_by_id_prefix_name_or_project(runner, fake):
    fake.add_repo("acme/app", project_name="Main App")
    fake.add_repo("acme/site", project_name="Website")
    assert "acme/site" in runner("repo", "status", "r2").out
    assert "acme/site" in runner("repo", "status", "ACME/Site").out
    assert "acme/app" in runner("repo", "status", "main app").out
    assert "acme/app" in runner("repo", "status", "app").out


def test_references_that_do_not_resolve(runner, fake):
    r = runner("repo", "status", expect=5)
    assert "foundryos repo connect" in r.err  # nothing connected
    fake.add_repo("acme/app")
    fake.add_repo("acme/site")
    r = runner("repo", "status", expect=1)
    assert "Several repositories" in r.err and "acme/app" in r.err
    runner("repo", "status", "nothing", expect=5)
    fake.add_repo("other/app")
    r = runner("repo", "status", "app", expect=1)
    assert "matches several" in r.err


def test_a_disconnected_repository_is_not_the_default(runner, fake):
    fake.add_repo("acme/old", status="disconnected")
    fake.add_repo("acme/app")
    assert "acme/app" in runner("repo", "status").out


# ------------------------------------------------------------------ actions


@pytest.mark.parametrize(
    ("args", "path", "words"),
    [
        (["sync"], "/repositories/r1/sync", "Sync queued"),
        (["pause"], "/repositories/r1/pause", "Paused"),
        (["resume"], "/repositories/r1/resume", "Resumed"),
        (["resume-prs"], "/repositories/r1/pull-requests/resume-opening", "opened again"),
    ],
)
def test_simple_actions_hit_their_endpoint(runner, fake, args, path, words):
    fake.add_repo("acme/app")
    out = runner("repo", *args).out
    assert words in out and ("POST", path) in [c[:2] for c in fake.calls]
    assert runner("--json", "repo", *args).json() is not None


def test_disconnect_asks_first(runner, fake):
    fake.add_repo("acme/app")
    r = runner("repo", "disconnect", input="n\n", expect=1)
    assert "Cancelled" in r.err and fake.calls_to("/repositories/r1/disconnect") == []
    runner("repo", "disconnect", input="y\n")
    runner("repo", "disconnect", "--yes")
    sent = fake.bodies("POST", "/repositories/r1/disconnect")
    assert sent == [{"confirm": True}, {"confirm": True}]


def test_disconnect_that_is_busy_says_how_to_retry(runner, fake):
    fake.add_repo("acme/app")
    fake.fail("POST", "/repositories/r1/disconnect", 409, "repository_busy", "A PR is merging.")
    r = runner("repo", "disconnect", "--yes", expect=6)
    assert "A PR is merging." in r.err and "Try again in a minute" in r.err


def test_reset_integration_maps_its_target_and_confirms(runner, fake):
    fake.add_repo("acme/app")
    runner("repo", "reset-integration", input="n\n", expect=1)
    assert fake.calls_to("/repositories/r1/integration/reset") == []
    runner("repo", "reset-integration", "--yes")
    runner("repo", "reset-integration", "--to", "default-branch", "--yes")
    assert fake.bodies("POST", "/repositories/r1/integration/reset") == [
        {"to": "remote_work_branch", "confirm": True},
        {"to": "default_branch", "confirm": True},
    ]
    runner("repo", "reset-integration", "--to", "somewhere", "--yes", expect=2)


def test_webhook_on_off_and_its_refusals(runner, fake):
    fake.add_repo("acme/app")
    runner("repo", "webhook")
    runner("repo", "webhook", "--off")
    assert fake.bodies("PUT", "/repositories/r1/webhook") == [{"enabled": True}, {"enabled": False}]
    fake.fail("PUT", "/repositories/r1/webhook", 409, "public_url_ephemeral", "Quick tunnel.")
    r = runner("repo", "webhook", expect=6)
    assert "stable public URL" in r.err
    fake.fail(
        "PUT",
        "/repositories/r1/webhook",
        409,
        "conflict",
        "Cannot create the hook.",
        {"reason": "webhook_permission_missing"},
    )
    r = runner("repo", "webhook", expect=6)
    assert "Webhooks permission" in r.err


def test_set_changes_only_what_is_given(runner, fake):
    fake.add_repo("acme/app")
    runner(
        "repo", "set", "--merge", "pr-auto", "--ci", "required", "--issues", "off",
        "--poll-interval", "120", "--merge-method", "rebase", "--no-docs-upkeep",
        "--company-default",
    )  # fmt: skip
    [body] = fake.bodies("PATCH", "/repositories/r1")
    assert body == {
        "policy": {
            "merge_mode": "pr_auto_merge",
            "ci_mode": "required",
            "issue_intake": "off",
            "poll_interval_seconds": 120,
            "pr_merge_method": "rebase",
            "docs_upkeep": False,
        },
        "is_company_default": True,
    }
    runner("repo", "set", expect=2)  # nothing to change
    runner("repo", "set", "--merge", "yolo", expect=2)
    runner("repo", "set", "--merge-method", "fast", expect=2)
    runner("repo", "set", "--issues", "some", expect=2)
    assert len(fake.bodies("PATCH", "/repositories/r1")) == 1


def test_set_direct_push_on_a_protected_branch_is_explained(runner, fake):
    fake.add_repo("acme/app")
    fake.fail("PATCH", "/repositories/r1", 409, "branch_protected", "main is protected.")
    r = runner("repo", "set", "--merge", "direct", expect=6)
    assert "main is protected." in r.err and "pr-human or pr-auto" in r.err


def test_prs_items_and_activity_tables(runner, fake):
    fake.add_repo("acme/app")
    fake.pulls = [
        {
            "number": 4, "title": "Add the feature", "stage": "reviewing", "revision": 1,
            "verdict": None, "checks_state": "pending", "mergeable_state": "clean",
            "opened_at": "2026-10-27T09:00:00Z",
        }
    ]  # fmt: skip
    fake.items = [
        {
            "kind": "issue", "number": 12, "title": "Crash on start", "author": "octocat",
            "status": "ignored", "ignore_reason": "author_not_allowed",
        }
    ]  # fmt: skip
    fake.activity = [
        {
            "created_at": "2026-10-27T09:00:00Z",
            "action": "repository.pr_opened",
            "summary": "Opened #4",
        }
    ]
    assert "Add the feature" in runner("repo", "prs").out
    assert "author_not_allowed" in runner("repo", "items").out
    assert "Opened #4" in runner("repo", "activity").out
    assert runner("repo", "prs").out and runner("--json", "repo", "prs").json()[0]["number"] == 4
    runner("repo", "items", "--kind", "issue", "--status", "ignored")
    [(_, _, _)] = [c for c in fake.calls if c[1] == "/repositories/r1/items"][-1:]


def test_empty_tables_say_so(runner, fake):
    fake.add_repo("acme/app")
    assert "No pull requests yet." in runner("repo", "prs").out
    assert "Nothing has come in" in runner("repo", "items").out
    assert "No activity yet." in runner("repo", "activity").out


# ------------------------------------------------------------------ the token


def test_token_replaces_the_secret_in_place_and_syncs(runner, fake):
    fake.add_account(login="foundry-bot")
    fake.credentials.append({"id": "cred1", "name": "GitHub token", "value": "old"})
    fake.add_repo("acme/app", connected_account_id="a1")
    r = runner("repo", "token", input=f"{TOKEN}\n")
    assert "The new token works" in r.out
    assert onboarding.GITHUB_TOKEN_URL in r.out  # the instructions
    assert TOKEN not in r.out + r.err
    assert fake.credentials[0]["value"] == TOKEN
    paths = [c[:2] for c in fake.calls]
    assert ("POST", "/credentials/cred1/rotate") in paths
    assert ("POST", "/connected-accounts/a1/test") in paths
    assert paths.index(("POST", "/connected-accounts/a1/test")) < paths.index(
        ("POST", "/repositories/r1/sync")
    )
    assert fake.where_seen(TOKEN) == ["POST /credentials/cred1/rotate"]


def test_token_from_stdin_for_scripts(runner, fake):
    fake.add_account()
    fake.credentials.append({"id": "cred1", "name": "GitHub token", "value": "old"})
    fake.add_repo("acme/app", connected_account_id="a1")
    r = runner("repo", "token", "--secrets-stdin", "--json", input=f"github_token={TOKEN}\n")
    assert r.json() == {"ok": True, "repository": "acme/app", "synced": True}
    assert TOKEN not in r.out + r.err
    runner("repo", "token", "--secrets-stdin", input="password=x\n", expect=2)
    runner("repo", "token", "--secrets-stdin", input="# nothing\n", expect=2)


def test_a_token_github_still_refuses_is_reported_without_echoing_it(runner, fake):
    fake.add_account()
    fake.credentials.append({"id": "cred1", "name": "GitHub token", "value": "old"})
    fake.add_repo("acme/app", connected_account_id="a1")
    fake.check_ok = False
    r = runner("repo", "token", input=f"{TOKEN}\n", expect=1)
    assert "did not accept the new token" in r.err and TOKEN not in r.out + r.err
    assert fake.calls_to("/repositories/r1/sync") == []


def test_a_token_with_spaces_is_a_usage_error(runner, fake):
    fake.add_account()
    fake.add_repo("acme/app", connected_account_id="a1")
    runner("repo", "token", "--secrets-stdin", input="github_token=two words\n", expect=2)


def test_token_without_its_account_says_what_to_do(runner, fake):
    fake.add_repo("acme/app", connected_account_id="a9")
    r = runner("repo", "token", input=f"{TOKEN}\n", expect=1)
    assert "foundryos repo connect" in r.err


# ------------------------------------------------------------------ connect


def test_connect_an_existing_repository_to_a_new_project(runner, fake):
    r = runner(
        "repo", "connect", "--repo", "https://github.com/octo/widget", "--project-name", "Widgets",
        "--merge", "pr-auto", "--ci", "ignore", "--issues", "labeled", "--default-branch", "develop",
        "--secrets-stdin", input=f"github_token={TOKEN}\n",
    )  # fmt: skip
    assert "octo/widget is connected and in sync" in r.out
    [body] = fake.bodies("POST", "/repositories")
    assert body == {
        "mode": "connect",
        "connected_account_id": fake.accounts[0]["id"],
        "project_name": "Widgets",
        "full_name": "octo/widget",
        "default_branch": "develop",
        "policy": {"merge_mode": "pr_auto_merge", "ci_mode": "ignore", "issue_intake": "labeled"},
    }
    assert TOKEN not in r.out + r.err and fake.where_seen(TOKEN) == ["POST /credentials"]


def test_connect_to_an_existing_project_uses_its_id(runner, fake):
    fake.add_account()
    runner("repo", "connect", "--repo", "octo/widget", "--project", "existing", "--publish-local")
    [body] = fake.bodies("POST", "/repositories")
    assert body["project_id"] == "p1" and "project_name" not in body
    assert body["publish_local"] is True
    assert fake.credentials == []  # the one connected account was used
    runner("repo", "connect", "--repo", "octo/widget", "--project", "nope", expect=5)


def test_connect_needs_exactly_one_project(runner, fake):
    fake.add_account()
    runner("repo", "connect", "--repo", "octo/widget", expect=2)
    runner(
        "repo",
        "connect",
        "--repo",
        "octo/widget",
        "--project",
        "x",
        "--project-name",
        "y",
        expect=2,
    )
    runner("repo", "connect", "--repo", "widget", "--project-name", "y", expect=2)
    assert fake.bodies("POST", "/repositories") == []


def test_connect_can_create_a_repository(runner, fake):
    fake.add_account()
    r = runner(
        "repo", "connect", "--create", "--repo", "acme-org/new-app", "--project-name", "New App",
    )  # fmt: skip
    [body] = fake.bodies("POST", "/repositories")
    assert body == {
        "mode": "create",
        "connected_account_id": "a1",
        "project_name": "New App",
        "name": "new-app",
        "owner": "acme-org",
        "visibility": "private",
        "confirm_public": False,
    }
    assert "is connected" in r.out


def test_a_public_repository_must_be_confirmed(runner, fake):
    fake.add_account()
    r = runner(
        "repo", "connect", "--create", "--repo", "new-app", "--project-name", "X",
        "--visibility", "public", expect=2,
    )  # fmt: skip
    assert "--public-ok" in r.err and fake.bodies("POST", "/repositories") == []
    runner(
        "repo", "connect", "--create", "--repo", "new-app", "--project-name", "X",
        "--visibility", "public", "--public-ok",
    )  # fmt: skip
    assert fake.bodies("POST", "/repositories")[0]["confirm_public"] is True


def test_connect_asks_for_the_token_hidden_when_no_account_exists(runner, fake):
    r = runner(
        "repo", "connect", "--repo", "octo/widget", "--project-name", "W", input=f"{TOKEN}\n"
    )
    assert onboarding.GITHUB_TOKEN_URL in r.out and "Contents" in r.out
    assert TOKEN not in r.err and fake.where_seen(TOKEN) == ["POST /credentials"]
    assert len(fake.credentials) == 1


def test_connect_with_several_accounts_wants_a_choice(runner, fake):
    fake.add_account(label="Work")
    fake.add_account(label="Personal")
    r = runner("repo", "connect", "--repo", "octo/widget", "--project-name", "W", expect=1)
    assert "--account" in r.err
    runner(
        "repo", "connect", "--repo", "octo/widget", "--project-name", "W", "--account", "Personal"
    )
    assert fake.bodies("POST", "/repositories")[0]["connected_account_id"] == "a2"
    runner(
        "repo",
        "connect",
        "--repo",
        "octo/widget",
        "--project-name",
        "W",
        "--account",
        "Nope",
        expect=5,
    )


def test_connect_shows_the_servers_refusal_with_the_fix(runner, fake):
    fake.add_account()
    fake.fail(
        "POST",
        "/repositories",
        409,
        "token_cannot_push",
        "The token cannot push.",
        {"missing_permissions": ["Contents: write"]},
    )
    r = runner("repo", "connect", "--repo", "octo/widget", "--project-name", "W", expect=6)
    assert "Contents: write" in r.err and "docs/github.md" in r.err


def test_connect_no_wait_and_a_slow_clone(runner, fake):
    fake.add_account()
    fake.progress = ["cloning"] * 1000
    r = runner("repo", "connect", "--repo", "octo/widget", "--project-name", "W", "--no-wait")
    assert "is cloning" in r.out and fake.calls_to("/repositories/r1") == []
    r = runner("repo", "connect", "--repo", "octo/other", "--project-name", "V")
    assert "foundryos repo status" in r.out  # still going: not an error


def test_connect_reports_local_history_moved_aside(runner, fake):
    fake.add_account()
    original = fake.create_repo

    def with_reason(body):
        row = original(body)
        row["status_reason"] = "The local history was kept in projects/app/local-20261027T0900"
        return row

    fake.create_repo = with_reason
    r = runner("repo", "connect", "--repo", "octo/widget", "--project-name", "W", "--no-wait")
    assert "local history was kept" in r.out


def test_connect_when_the_feature_is_not_available(runner, fake):
    fake.add_account()
    fake.available = {"available": False, "reason": "git is not installed", "milestone": "M14"}
    r = runner("repo", "connect", "--repo", "octo/widget", "--project-name", "W", expect=1)
    assert "git is not installed" in r.err and fake.bodies("POST", "/repositories") == []


def test_repo_is_listed_in_the_help(runner):
    out = runner("--help").out
    assert "repo" in out
    sub = runner("repo", "--help").out
    for name in ("list", "status", "sync", "connect", "disconnect", "token", "reset-integration"):
        assert name in sub


# ------------------------------------------------------------------ doctor: git


def test_doctor_git_missing_old_and_fine(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    missing = doctor.check_git()
    assert missing.status == "warn" and "Install git" in (missing.fix or "")

    class Done:
        returncode = 0

        def __init__(self, out):
            self.stdout = out

    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(doctor, "_run", lambda cmd, **kw: Done("git version 2.20.1\n"))
    old = doctor.check_git()
    assert old.status == "warn" and "2.20.1" in old.detail
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **kw: Done("git version 2.39.5 (Apple Git-154)\n")
    )
    fine = doctor.check_git()
    assert fine.status == "ok" and "2.39.5" in fine.detail
    monkeypatch.setattr(doctor, "_run", lambda cmd, **kw: None)
    assert doctor.check_git().status == "warn"


def test_git_version_parsing():
    assert doctor._git_version_tuple("git version 2.43.0") == (2, 43, 0)
    assert doctor._git_version_tuple("git version 2.39.5 (Apple Git-154)") == (2, 39, 5)
    assert doctor._git_version_tuple("nonsense") is None


# ------------------------------------------------------------------ doctor: the repositories


def _bind(client, cid, m14, name="app", label="GitHub"):
    account = github_account(client, cid, m14, label=label)
    m14.add_repo(f"{LOGIN}/{name}")
    made = post_repo(client, cid, connect_body(account, f"{LOGIN}/{name}", project_name=name))
    assert made.status_code == 201, made.text
    return made.json()["id"]


def test_doctor_repositories_none_then_healthy_then_problems(app, client, company, m14):
    settings = app.state.settings
    none = doctor.check_github_repositories(settings)
    assert none.status == "info" and "foundryos repo connect" in (none.fix or "")

    cid = company["id"]
    rid = _bind(client, cid, m14)
    set_repo(app, rid, status="cloning")
    assert "still being set up" in doctor.check_github_repositories(settings).detail
    set_repo(app, rid, status="active")
    ok = doctor.check_github_repositories(settings)
    assert ok.status == "ok" and "1 connected" in ok.detail

    set_repo(app, rid, status="auth_failed")
    bad = doctor.check_github_repositories(settings)
    assert bad.status == "warn" and "refused the token" in bad.detail
    assert "foundryos repo token" in (bad.fix or "")

    set_repo(app, rid, status="active", sync_state="push_blocked")
    blocked = doctor.check_github_repositories(settings)
    assert blocked.status == "warn" and "push blocked" in blocked.detail

    soon = datetime.now(UTC) + timedelta(days=3)
    set_repo(app, rid, sync_state="idle", token_expires_at=soon)
    expiring = doctor.check_github_repositories(settings)
    assert expiring.status == "warn" and "expires" in expiring.detail
    assert "foundryos repo token" in (expiring.fix or "")

    set_repo(app, rid, token_expires_at=datetime.now(UTC) - timedelta(days=1))
    assert "expired" in doctor.check_github_repositories(settings).detail

    set_repo(app, rid, token_expires_at=datetime.now(UTC) + timedelta(days=90), status="paused")
    assert "paused" in doctor.check_github_repositories(settings).detail

    set_repo(app, rid, status="disconnected")  # a disconnected binding is not a problem
    assert doctor.check_github_repositories(settings).status == "info"


def test_doctor_repositories_without_the_table(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'empty.db'}",
        data_dir=tmp_path,
        runtime_dir=tmp_path / "no-runtime",
    )
    check = doctor.check_github_repositories(settings)
    assert check.status == "info" and "not set up yet" in check.detail


def test_run_checks_includes_git_and_github(tmp_path: Path, monkeypatch):
    stub = doctor.Check("stub", "ok", "stubbed")
    monkeypatch.setattr(doctor, "check_ollama", lambda *a, **k: stub)
    monkeypatch.setattr(doctor, "check_docker", lambda *a, **k: stub)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        data_dir=tmp_path,
        runtime_dir=tmp_path / "no-runtime",
    )
    names = [c.name for c in doctor.run_checks(settings)]
    assert "git" in names and "github repos" in names
