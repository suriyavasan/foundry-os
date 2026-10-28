"""M14 WP-D: the ``repo`` step of ``foundryos setup``.

Most tests run the real CLI against a fake HTTP server (``FakeApi``, an ``httpx.MockTransport``) that
speaks the step-0 routes, so the CLI's own behaviour is pinned down: what it sends, in which order,
what it prints and what it never prints. The last test runs the step against the real services in the
test application and the GitHub fake (no network anywhere).
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from app.cli import context, onboarding
from app.cli.client import CliError
from app.cli.commands import onboard
from app.cli.main import app as cli_app
from tests.m14_cli_fakes import (  # noqa: F401  (fixtures + the fake server)
    IDEA,
    TOKEN,
    cfg_dir,
    fake,
    runner,
)
from tests.m14_helpers import github_world  # noqa: F401  (a fixture)
from tests.m14_service_helpers import m14, set_repo  # noqa: F401  (m14 is a fixture)

# ------------------------------------------------------------------ the pure rules


def test_github_token_is_an_allowed_secret_and_never_an_option():
    assert "github_token" in onboarding.SECRET_KEYS
    assert onboarding.parse_secrets("github_token=github_pat_abc=def\n") == {
        "github_token": "github_pat_abc=def"
    }


def test_the_repo_step_comes_after_ai_and_before_the_public_url():
    steps = list(onboarding.STEPS)
    assert "work" not in steps
    assert steps.index("ai") < steps.index("repo") < steps.index("public-url")


def test_repo_reference_rules():
    split = onboarding.split_repo_ref
    assert split("existing", "octo/widget") == ("octo", "widget")
    assert split("existing", "https://github.com/octo/widget.git") == ("octo", "widget")
    assert split("new", "widget") == (None, "widget")
    assert split("new", "octo/widget") == ("octo", "widget")
    for choice, bad in (
        ("existing", "widget"),  # an existing repository needs its owner
        ("existing", "octo/wid get"),
        ("new", "a/b/c"),
        ("new", "../x"),
        ("new", "x.git"),
    ):
        with pytest.raises(CliError) as exc:
            split(choice, bad)
        assert exc.value.exit_code == 2


def test_repo_name_from_the_company_name():
    slug = onboarding.slugify_repo_name
    assert slug("Acme Labs") == "acme-labs"
    assert slug("  Ünï & Co.  ") == "n-co"
    assert slug("***") == "company"
    assert len(slug("x" * 300)) <= 100


def test_option_validation():
    def opts(**kw):
        return onboarding.WizardOptions(**kw)

    onboarding.validate_options(opts(github="existing", github_repo="a/b", github_merge="pr-auto"))
    for bad in (
        opts(github="maybe"),
        opts(github_repo="a/b"),  # details without a choice
        opts(github="none", project_name="X"),
        opts(github="existing", github_repo="a/b", github_visibility="public"),
        opts(github="new", github_merge="auto"),
        opts(github="new", github_ci="always"),
        opts(github="new", github_issues="some"),
        opts(github="new", github_default_branch="bad branch"),
        opts(github="new", project_name=""),
        opts(github="new", github_visibility="public", non_interactive=True),
    ):
        with pytest.raises(CliError) as exc:
            onboarding.validate_options(bad)
        assert exc.value.exit_code == 2
    # interactive: the public confirmation is asked, not demanded up front
    onboarding.validate_options(opts(github="new", github_visibility="public"))
    onboarding.validate_options(
        opts(github="new", github_visibility="public", non_interactive=True, github_public_ok=True)
    )


def test_permission_checklist_differs_by_mode():
    create = "\n".join(onboarding.permission_lines("create"))
    connect = "\n".join(onboarding.permission_lines("connect"))
    assert "Administration" in create and "Administration" not in connect.split("Not needed")[0]
    assert "Contents" in connect and "Read and write" in connect
    assert onboarding.GITHUB_TOKEN_URL in "\n".join(onboarding.token_instructions("connect"))


# ------------------------------------------------------------------ non-interactive: the three choices


def setup_args(*extra: str) -> list[str]:
    return ["setup", "--only", "repo", "--non-interactive", *extra]


def test_new_repository_non_interactive(runner, fake):
    r = runner(
        *setup_args("--github", "new", "--github-repo", "acme-app", "--idea", IDEA, "--json"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    data = r.json()
    assert data["steps"]["repo"] == "done" and data["steps"]["idea"] == "note"
    assert data["repo"]["full_name"] == "foundry-bot/acme-app"
    assert data["repo"]["merge_mode"] == "pr_auto_merge"
    [create] = fake.bodies("POST", "/repositories")
    assert create == {
        "mode": "create",
        "connected_account_id": fake.accounts[0]["id"],
        "name": "acme-app",
        "visibility": "private",
        "confirm_public": False,
        "description": IDEA,
        "project_name": "acme-app",
    }  # defaults are the server's: no policy sent
    # the token travelled once, in the body of the credential request, and nowhere else
    assert fake.credentials[0]["value"] == TOKEN
    assert fake.where_seen(TOKEN) == ["POST /credentials"]
    assert TOKEN not in r.out + r.err


def test_the_idea_becomes_a_note_only_after_the_repository_exists(runner, fake):
    runner(
        *setup_args("--github", "new", "--idea", IDEA),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    order = [
        c for c in fake.calls if (c[0], c[1]) in {("POST", "/repositories"), ("POST", "/notes")}
    ]
    assert [c[1] for c in order] == ["/repositories", "/notes"]
    [active_poll] = [i for i, c in enumerate(fake.calls) if c[:2] == ("GET", "/repositories/r1")][
        -1:
    ]
    assert active_poll < fake.calls.index(order[1])  # it waited for `active` first
    assert fake.notes == [IDEA]
    [create] = fake.bodies("POST", "/repositories")
    assert create["name"] == "acme-labs"  # the company name, slugified, when no name was given


def test_existing_repository_with_every_choice_given(runner, fake):
    r = runner(
        *setup_args(
            "--github", "existing",
            "--github-repo", "https://github.com/octo/widget.git",
            "--github-merge", "pr-auto",
            "--github-ci", "required",
            "--github-issues", "everyone",
            "--github-default-branch", "develop",
            "--project-name", "Widgets",
            "--json",
        ),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )  # fmt: skip
    assert r.json()["steps"]["repo"] == "done"
    [create] = fake.bodies("POST", "/repositories")
    assert create == {
        "mode": "connect",
        "connected_account_id": fake.accounts[0]["id"],
        "full_name": "octo/widget",
        "default_branch": "develop",
        "policy": {
            "merge_mode": "pr_auto_merge",
            "ci_mode": "required",
            "issue_intake": "everyone",
        },
        "project_name": "Widgets",
    }
    assert fake.where_seen(TOKEN) == ["POST /credentials"]


def test_choices_equal_to_the_servers_defaults_are_not_sent(runner, fake):
    runner(
        *setup_args(
            "--github", "existing", "--github-repo", "octo/widget", "--github-merge", "pr-human"
        ),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    [create] = fake.bodies("POST", "/repositories")
    assert "policy" not in create and "default_branch" not in create  # main is the repo's own


def test_ci_default_follows_the_repository_workflows(runner, fake):
    fake.remote_repos["octo/widget"]["has_workflows"] = True
    runner(
        *setup_args(
            "--github", "existing", "--github-repo", "octo/widget", "--github-ci", "if-present"
        ),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    [create] = fake.bodies("POST", "/repositories")
    # with workflows the default is `required`, so an explicit if-present is a choice to send
    assert create["policy"] == {"ci_mode": "if_present"}


def test_no_github_is_the_non_interactive_default_and_can_be_explicit(runner, fake):
    r = runner(*setup_args("--json"))
    assert r.json()["steps"]["repo"] == "skipped"
    r = runner(*setup_args("--github", "none", "--json"))
    assert r.json()["steps"]["repo"] == "skipped"
    assert fake.bodies("POST", "/repositories") == [] and fake.credentials == []
    assert fake.calls_to("/github/inspect") == []


def test_a_missing_token_exits_2_and_lists_what_is_missing(runner, fake):
    r = runner(*setup_args("--github", "existing"), expect=2)
    assert "--github-repo" in r.err and "github_token" in r.err
    assert fake.bodies("POST", "/repositories") == [] and fake.credentials == []


def test_a_connected_github_account_is_reused_without_a_token(runner, fake):
    fake.add_account(login="foundry-bot")
    r = runner(*setup_args("--github", "new", "--github-repo", "acme-app", "--json"))
    assert r.json()["steps"]["repo"] == "done"
    assert fake.credentials == []  # no new token was stored
    [create] = fake.bodies("POST", "/repositories")
    assert create["connected_account_id"] == fake.accounts[0]["id"]


def test_a_new_token_gets_its_own_unique_account_label(runner, fake):
    fake.add_account(login="foundry-bot", label="GitHub")
    runner(
        *setup_args("--github", "new", "--github-repo", "acme-app"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    assert [a["label"] for a in fake.accounts] == ["GitHub", "GitHub 2"]


def test_invalid_repo_reference_exits_2_before_any_request(runner, fake):
    runner(*setup_args("--github", "existing", "--github-repo", "not a repo"), expect=2)
    runner(*setup_args("--github", "new", "--github-repo", "a/b/c"), expect=2)
    runner(*setup_args("--github-repo", "a/b"), expect=2)
    assert fake.calls == []


def test_public_needs_an_explicit_confirmation_when_there_is_nobody_to_ask(runner, fake):
    r = runner(*setup_args("--github", "new", "--github-visibility", "public"), expect=2)
    assert "--github-public-ok" in r.err and fake.calls == []
    runner(
        *setup_args(
            "--github", "new", "--github-repo", "acme-app",
            "--github-visibility", "public", "--github-public-ok",
        ),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )  # fmt: skip
    [create] = fake.bodies("POST", "/repositories")
    assert create["visibility"] == "public" and create["confirm_public"] is True


def test_a_token_github_refuses_leaves_nothing_behind_and_is_never_echoed(runner, fake):
    fake.token_error = f"Bad credentials for {TOKEN}"
    r = runner(
        *setup_args("--github", "new", "--github-repo", "acme-app"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
        expect=1,
    )
    assert "did not accept the token" in r.err and TOKEN not in r.out + r.err
    assert fake.accounts == [] and fake.bodies("POST", "/repositories") == []


@pytest.mark.parametrize(
    ("code", "status", "details", "expect_in_message", "exit_code"),
    [
        ("administration_permission_missing", 409, None, "empty repository on github.com", 6),
        (
            "token_cannot_push",
            409,
            {"missing_permissions": ["Contents: write"]},
            "Contents: write",
            6,
        ),
        ("repository_exists", 409, None, "Choose another name", 6),
        ("confirm_public_required", 422, None, "--github-public-ok", 1),
    ],
)
def test_refusals_come_with_the_fix(
    runner, fake, code, status, details, expect_in_message, exit_code
):
    fake.fail("POST", "/repositories", status, code, "The server says no.", details)
    r = runner(
        *setup_args("--github", "new", "--github-repo", "acme-app"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
        expect=exit_code,
    )
    assert "The server says no." in r.err and expect_in_message in r.err
    assert TOKEN not in r.out + r.err
    assert fake.notes == []  # nothing was seeded after a failure


def test_a_failed_repository_step_does_not_save_the_idea_and_says_how(runner, fake):
    fake.fail("POST", "/repositories", 409, "repository_exists", "It exists.")
    r = runner(
        *setup_args("--github", "new", "--github-repo", "acme-app", "--idea", IDEA),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
        expect=6,
    )
    assert fake.notes == [] and "--only repo --idea" in r.err


def test_the_repository_is_polled_until_active(runner, fake):
    fake.progress = ["creating", "cloning", "cloning", "active"]
    r = runner(
        *setup_args("--github", "new", "--github-repo", "acme-app"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    polls = fake.calls_to("/repositories/r1")
    assert len(polls) == 4
    assert "creating..." in r.out + r.err and "ready" in r.out


def test_a_clone_that_fails_is_reported_with_its_reason(runner, fake):
    fake.progress = ["error"]
    fake.status_reason = "The clone failed: repository not found"
    r = runner(
        *setup_args("--github", "existing", "--github-repo", "octo/widget"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
        expect=1,
    )
    assert "repository not found" in r.err


def test_a_slow_clone_is_not_an_error(runner, fake):
    fake.progress = ["cloning"] * 1000
    r = runner(
        *setup_args("--github", "existing", "--github-repo", "octo/widget", "--json"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    assert r.json()["steps"]["repo"] == "pending"
    assert "foundryos repo status" in r.err


def test_a_codebase_onboarding_is_announced(runner, fake):
    fake.onboarding = "pending"
    r = runner(
        *setup_args("--github", "existing", "--github-repo", "octo/widget"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    assert "read the codebase" in r.out


def test_a_server_without_repositories_is_explicit(runner, fake):
    fake.available = {"available": False, "reason": "git is not installed", "milestone": "M14"}
    r = runner(
        *setup_args("--github", "new", "--github-repo", "acme-app"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
        expect=1,
    )
    assert "git is not installed" in r.err and fake.credentials == []
    r = runner(*setup_args("--json"))  # nothing asked for: no error
    assert r.json()["steps"]["repo"] == "skipped"


# ------------------------------------------------------------------ resumable


def test_a_rerun_says_already_done(runner, fake):
    fake.add_repo("foundry-bot/acme-app", is_company_default=True)
    r = runner(*setup_args("--json"))
    assert r.json()["steps"]["repo"] == "already_done"
    assert "Already done: foundry-bot/acme-app" in r.err
    r = runner(*setup_args("--github", "new", "--json"), input="")
    assert r.json()["steps"]["repo"] == "already_done"  # new + no name: the company's is there
    r = runner(
        *setup_args("--github", "existing", "--github-repo", "FOUNDRY-bot/Acme-App", "--json")
    )
    assert r.json()["steps"]["repo"] == "already_done"
    assert fake.bodies("POST", "/repositories") == [] and fake.credentials == []


def test_another_repository_can_be_added_by_naming_it(runner, fake):
    fake.add_repo("foundry-bot/acme-app", is_company_default=True)
    runner(
        *setup_args("--github", "existing", "--github-repo", "octo/widget", "--json"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    assert len(fake.bodies("POST", "/repositories")) == 1


def test_a_disconnected_repository_does_not_count_as_done(runner, fake):
    fake.add_repo("octo/widget", status="disconnected")
    r = runner(
        *setup_args("--github", "existing", "--github-repo", "octo/widget", "--json"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    assert r.json()["steps"]["repo"] == "done"


# ------------------------------------------------------------------ interactive


def test_interactive_new_repository(runner, fake):
    fake.owners = [{"login": "foundry-bot", "kind": "user"}, {"login": "acme-org", "kind": "org"}]
    answers = "\n".join(
        [
            "1",  # new repository
            TOKEN,  # the token (hidden)
            "acme-app",  # repository name
            "2",  # owner: acme-org
            "1",  # private
            "main",  # default branch
            "2",  # merge: pull requests that I merge
            "1",  # CI: if present
            "2",  # issues: labelled only
            "Acme App",  # project name
        ]
    ) + "\n"  # fmt: skip
    r = runner("setup", "--only", "repo", input=answers)
    assert "fine-grained personal access token" in r.out
    assert onboarding.GITHUB_TOKEN_URL in r.out and "Administration" in r.out
    assert TOKEN not in r.out + r.err
    [create] = fake.bodies("POST", "/repositories")
    assert create == {
        "mode": "create",
        "connected_account_id": fake.accounts[0]["id"],
        "name": "acme-app",
        "owner": "acme-org",
        "visibility": "private",
        "confirm_public": False,
        "policy": {"merge_mode": "pr_human_merge", "issue_intake": "labeled"},
        "project_name": "Acme App",
    }
    assert fake.where_seen(TOKEN) == ["POST /credentials"]


def test_interactive_public_asks_twice(runner, fake):
    answers = "\n".join(["1", TOKEN, "", "2", "n"]) + "\n"  # new, token, default name, public, no
    r = runner("setup", "--only", "repo", input=answers, expect=2)
    assert "PUBLIC" in r.out and fake.bodies("POST", "/repositories") == []


def test_interactive_existing_repository_retries_a_bad_name(runner, fake):
    answers = "\n".join(
        [
            "2",  # existing
            TOKEN,
            "nobody/nothing",  # does not exist
            "https://github.com/octo/widget",
            "",  # base branch: the default
            "1", "2", "3",  # pr-auto, required, everyone
            "",  # project name: the repo's
        ]
    ) + "\n"  # fmt: skip
    r = runner("setup", "--only", "repo", input=answers)
    assert "does not exist" in r.out + r.err
    [create] = fake.bodies("POST", "/repositories")
    assert create["full_name"] == "octo/widget" and create["project_name"] == "widget"
    assert create["policy"] == {
        "merge_mode": "pr_auto_merge",
        "ci_mode": "required",
        "issue_intake": "everyone",
    }


def test_interactive_reuses_a_connected_account_when_asked(runner, fake):
    fake.add_account(login="foundry-bot")
    answers = "\n".join(["2", "y", "octo/widget", "", "2", "1", "2", ""]) + "\n"
    runner("setup", "--only", "repo", input=answers)
    assert fake.credentials == []
    answers = "\n".join(["y", "2", "n", TOKEN, "octo/widget2", "", "2", "1", "2", ""]) + "\n"
    fake.remote_repos["octo/widget2"] = dict(
        fake.remote_repos["octo/widget"], full_name="octo/widget2"
    )
    runner("setup", "--only", "repo", input=answers)
    assert len(fake.credentials) == 1


def test_interactive_wrong_token_is_asked_for_again(runner, fake):
    fake.token_error = "Bad credentials"
    fake.bad_tokens_left = 1  # the first one is refused, the second works
    answers = (
        "\n".join(["1", "bad-token-value", TOKEN, "acme-app", "1", "main", "1", "1", "1", ""])
        + "\n"
    )
    r = runner("setup", "--only", "repo", input=answers)
    assert "did not accept the token" in r.err and "bad-token-value" not in r.out + r.err
    assert len(fake.bodies("POST", "/repositories")) == 1


def test_interactive_no_github(runner, fake):
    r = runner("setup", "--only", "repo", input="3\n")
    assert "Skipped: no GitHub repository" in r.out
    assert fake.credentials == [] and fake.bodies("POST", "/repositories") == []


def test_interactive_missing_permissions_ask_before_going_on(runner, fake):
    fake.missing = ["Administration: write"]
    answers = "\n".join(["1", TOKEN, "n"]) + "\n"
    r = runner("setup", "--only", "repo", input=answers, expect=1)
    assert "[missing] Administration: write" in r.out
    assert fake.bodies("POST", "/repositories") == []


# ------------------------------------------------------------------ the whole wizard around it


def test_the_summary_shows_the_repository(runner, fake):
    r = runner(
        *setup_args("--github", "existing", "--github-repo", "octo/widget"),
        "--secrets-stdin",
        input=f"github_token={TOKEN}\n",
    )
    assert re.search(r"Repository\s+octo/widget \(pr_human_merge, active\)", r.out)


def test_setup_help_lists_the_repository_flags(runner, fake):
    out = runner("setup", "--help").out
    for flag in (
        "--github",
        "--github-repo",
        "--github-visibility",
        "--github-public-ok",
        "--github-default-branch",
        "--github-merge",
        "--github-ci",
        "--github-issues",
        "--project-name",
        "github_token",
    ):
        assert flag in out, flag
    assert "repo" in out  # --only lists the new step


# ------------------------------------------------------------------ against the real services


def test_the_step_against_the_real_services(app, client, company, m14, cfg_dir, monkeypatch):
    """No fake server: the real routes and services behind the in-process app, GitHub faked."""
    from app.services import ai_setup, github_accounts

    # tolerant of the keyword arguments the probe has gained
    monkeypatch.setattr(
        github_accounts,
        "probe_capabilities",
        lambda *a, **kw: {"contents": True, "pull_requests": True, "issues": True},
    )
    m14.add_repo("foundry-bot/app")
    monkeypatch.setattr(ai_setup, "ENFORCED", False)
    monkeypatch.setenv("COMPANYOS_TOKEN", app.state.owner_token.get())
    monkeypatch.setattr(context, "http_factory", lambda url: client)
    cid = company["id"]
    flipped: list[str] = []

    def worker_finishes_the_clone(seconds: float) -> None:
        if not flipped:
            [row] = client.get(f"/api/companies/{cid}/repositories").json()["items"]
            set_repo(app, row["id"], status="active")
            flipped.append(row["id"])

    monkeypatch.setattr(onboard, "sleep", worker_finishes_the_clone)
    result = CliRunner().invoke(
        cli_app,
        [
            "setup", "--only", "repo", "--non-interactive", "--github", "existing",
            "--github-repo", "foundry-bot/app", "--project-name", "App", "--idea", IDEA,
            "--secrets-stdin", "--json",
        ],
        input=f"github_token={m14.token}\n",
    )  # fmt: skip
    assert result.exit_code == 0, result.stderr + result.stdout
    data = json.loads(result.stdout)
    assert data["steps"]["repo"] == "done" and data["steps"]["idea"] == "note"
    [row] = client.get(f"/api/companies/{cid}/repositories").json()["items"]
    assert row["full_name"] == "foundry-bot/app" and row["project_name"] == "App"
    assert row["policy"]["merge_mode"] == "pr_human_merge" and row["origin"] == "connected"
    notes = client.get(f"/api/companies/{cid}/notes").json()["items"]
    assert [n["body"] for n in notes] == [IDEA]
    assert m14.token not in result.stdout + result.stderr
    # a rerun finds it
    again = CliRunner().invoke(cli_app, ["setup", "--only", "repo", "--non-interactive", "--json"])
    assert json.loads(again.stdout)["steps"]["repo"] == "already_done"
