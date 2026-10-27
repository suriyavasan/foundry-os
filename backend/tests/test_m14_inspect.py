"""``POST /github/inspect`` (M14 WP-A): what the onboarding step learns about a token and a
repository before anything is created. Everything runs against the fake GitHub."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.github import classify_token
from app.services import github_accounts
from tests.helpers import api, new_agent
from tests.m8_helpers import connect
from tests.m14_helpers import LOGIN, OTHER_TOKEN, READONLY_TOKEN, TOKEN, github_world  # noqa: F401
from tests.test_agent_api import start_run


@pytest.fixture
def account(client, company, github_world):  # noqa: F811
    acc = connect(client, company["id"], "github", label="GitHub", secret=TOKEN)
    assert acc["status"] == "connected"
    return acc


def inspect(client, cid, account, **body):
    r = client.post(
        api(cid, "/github/inspect"), json={"connected_account_id": account["id"], **body}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_token_kind_comes_from_the_prefix_and_the_token_never_comes_back(
    client,
    company,
    account,
    github_world,  # noqa: F811
):
    assert classify_token("github_pat_11ABC") == "fine_grained"
    for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
        assert classify_token(prefix + "x" * 36) == "classic"
    assert classify_token("sk-live-123") == "other"
    assert github_accounts.token_kind("github_pat_11ABC") == "fine_grained"
    cid = company["id"]
    out = inspect(client, cid, account)
    assert out["token_kind"] == "fine_grained" and out["login"] == LOGIN
    assert TOKEN not in str(out) and "github_pat_" not in str(out)
    # the connected account stores the kind and the login too (the check does it)
    meta = account["metadata"]
    assert meta["token_kind"] == "fine_grained" and meta["login"] == LOGIN


def test_owner_candidates_include_the_organisations_of_the_token(
    client,
    company,
    github_world,  # noqa: F811
):
    github_world.github.add_org("acme-org", {LOGIN})
    acc = connect(client, company["id"], "github", label="GH", secret=TOKEN)
    out = inspect(client, company["id"], acc)
    assert out["owners"] == [
        {"login": LOGIN, "kind": "user"},
        {"login": "acme-org", "kind": "org"},
    ]


def test_an_existing_repository_is_described(client, company, account, github_world):  # noqa: F811
    github_world.add_repo(
        f"{LOGIN}/shop",
        files={"README.md": "# shop\n", ".github/workflows/ci.yml": "name: ci\n"},
    )
    out = inspect(client, company["id"], account, full_name=f"https://github.com/{LOGIN}/shop.git")
    repo = out["repository"]
    assert (repo["exists"], repo["accessible"], repo["archived"], repo["empty"]) == (
        True,
        True,
        False,
        False,
    )
    assert repo["full_name"] == f"{LOGIN}/shop" and repo["default_branch"] == "main"
    assert repo["visibility"] == "private" and repo["has_workflows"] is True
    assert repo["can_push"] is True and repo["protected"] is False
    assert repo["capabilities"]["contents"] and repo["capabilities"]["pull_requests"]
    assert out["missing_permissions"] == []


def test_not_found_is_not_the_same_as_no_access(client, company, account, github_world):  # noqa: F811
    cid = company["id"]
    gone = inspect(client, cid, account, full_name=f"{LOGIN}/nope")["repository"]
    assert (gone["exists"], gone["accessible"]) == (False, False)
    out = inspect(client, cid, account, full_name=f"{LOGIN}/nope")
    assert any("404" in w for w in out["warnings"])  # GitHub hides private repositories

    # exists, but the organisation demands SAML SSO authorisation
    github_world.github.add_org("corp", {LOGIN})
    github_world.add_repo("corp/vault", private=True)
    github_world.github.tokens[TOKEN].orgs.add("corp")
    github_world.github.require_sso(TOKEN, "corp")
    out = inspect(client, cid, account, full_name="corp/vault")
    assert (out["repository"]["exists"], out["repository"]["accessible"]) == (True, False)
    assert out["sso_url"].startswith("https://github.com/orgs/corp/sso")


def test_organisation_approval_pending_is_reported(client, company, account, github_world):  # noqa: F811
    github_world.github.add_org("corp", {LOGIN})
    github_world.add_repo("corp/app", private=True)
    github_world.github.tokens[TOKEN].orgs.add("corp")
    github_world.github.require_org_approval(TOKEN, "corp")
    out = inspect(client, company["id"], account, full_name="corp/app")
    assert out["organisation_approval_pending"] is True
    assert out["repository"]["accessible"] is False
    assert any("approve" in w for w in out["warnings"])


def test_archived_empty_and_public_repositories_are_flagged(
    client,
    company,
    account,
    github_world,  # noqa: F811
):
    cid = company["id"]
    github_world.add_repo(f"{LOGIN}/old", archived=True)
    github_world.add_repo(f"{LOGIN}/blank", empty=True)
    github_world.add_repo(f"{LOGIN}/open", private=False)
    old = inspect(client, cid, account, full_name=f"{LOGIN}/old")
    assert old["repository"]["archived"] is True
    assert any("archived" in w for w in old["warnings"])
    blank = inspect(client, cid, account, full_name=f"{LOGIN}/blank")["repository"]
    assert blank["empty"] is True and blank["protection"] == {}
    pub = inspect(client, cid, account, full_name=f"{LOGIN}/open")
    assert pub["repository"]["visibility"] == "public"
    assert any("public" in w for w in pub["warnings"])


def test_the_protection_summary_comes_from_the_branch_and_the_rules(
    client,
    company,
    account,
    github_world,  # noqa: F811
):
    github_world.add_repo(f"{LOGIN}/guarded")
    github_world.github.protect(
        f"{LOGIN}/guarded",
        require_pr=True,
        approvals=2,
        required_checks=["ci"],
        linear_history=True,
    )
    repo = inspect(client, company["id"], account, full_name=f"{LOGIN}/guarded")["repository"]
    assert repo["protected"] is True
    prot = repo["protection"]
    assert prot["branch"] == "main" and prot["require_pr"] is True
    assert prot["required_approvals"] == 2 and prot["required_checks"] is True
    assert prot["linear_history"] is True and prot["non_fast_forward"] is True
    assert prot["required_signatures"] is False
    open_repo = github_world.add_repo(f"{LOGIN}/free")
    assert open_repo.default_branch == "main"
    free = inspect(client, company["id"], account, full_name=f"{LOGIN}/free")["repository"]
    assert free["protected"] is False and free["protection"]["require_pr"] is False


def test_capabilities_turn_features_off_without_failing(
    client,
    company,
    account,
    github_world,  # noqa: F811
):
    github_world.add_repo(f"{LOGIN}/lim")
    github_world.github.deny(f"{LOGIN}/lim", "dependabot")
    github_world.github.deny(f"{LOGIN}/lim", "checks")
    out = inspect(client, company["id"], account, full_name=f"{LOGIN}/lim")
    caps = out["repository"]["capabilities"]
    assert caps["dependabot"] is False and caps["checks"] is False
    assert caps["issues"] is True and caps["actions"] is True
    assert "Dependabot alerts: read" in out["missing_permissions"]
    assert "Checks: read" in out["missing_permissions"]
    assert any("security alert" in w for w in out["warnings"])
    assert out["repository"]["can_push"] is True  # the bind is not refused


def test_a_read_only_token_cannot_push(client, company, github_world):  # noqa: F811
    github_world.add_repo(f"{LOGIN}/ro")
    acc = connect(client, company["id"], "github", label="RO", secret=READONLY_TOKEN)
    out = inspect(client, company["id"], acc, full_name=f"{LOGIN}/ro")
    assert out["repository"]["can_push"] is False
    assert "Contents: read and write" in out["missing_permissions"]


def test_missing_permissions_depend_on_the_mode(client, company, github_world):  # noqa: F811
    cid = company["id"]
    # a classic token without the repo scope cannot create repositories
    github_world.github.add_token("ghp_" + "c" * 36, "classic-user", kind="classic").scopes = [
        "read:user"
    ]
    acc = connect(client, cid, "github", label="classic", secret="ghp_" + "c" * 36)
    create = inspect(client, cid, acc, mode="create")
    assert create["token_kind"] == "classic"
    assert "repo (classic scope)" in create["missing_permissions"]
    assert any("classic token" in w for w in create["warnings"])
    connect_mode = inspect(client, cid, acc, mode="connect")
    assert connect_mode["missing_permissions"] == []
    # a fine-grained token cannot be checked for Administration: a warning, not a block
    fg = connect(client, cid, "github", label="fg", secret=TOKEN)
    out = inspect(client, cid, fg, mode="create")
    assert out["missing_permissions"] == []
    assert any("Administration" in w for w in out["warnings"])


def test_an_expiring_token_is_reported_and_a_dead_one_is_a_clear_error(
    client,
    company,
    github_world,  # noqa: F811
):
    cid = company["id"]
    soon = datetime.now(UTC) + timedelta(days=5)
    github_world.github.tokens[TOKEN].expires_at = soon
    acc = connect(client, cid, "github", label="exp", secret=TOKEN)
    out = inspect(client, cid, acc)
    assert out["token_expires_at"] is not None
    assert any("expires in" in w for w in out["warnings"])
    meta = acc["metadata"]
    assert meta["token_expires_at"].startswith(str(soon.year))
    github_world.github.revoke(TOKEN)
    r = client.post(api(cid, "/github/inspect"), json={"connected_account_id": acc["id"]})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["reason"] == "unauthorized"
    assert TOKEN not in r.text


def test_inspect_is_owner_admin_only_and_tenant_scoped(
    app,
    client,
    company,
    account,
    github_world,  # noqa: F811
):
    cid = company["id"]
    other = client.post("/api/companies", json={"name": "Other Co"}).json()
    # another company cannot use this company's account
    r = client.post(
        api(other["id"], "/github/inspect"), json={"connected_account_id": account["id"]}
    )
    assert r.status_code == 404
    # an agent (run token) cannot inspect
    agent = new_agent(client, cid, name="Ops")
    session = start_run(app, client, cid, agent["id"])
    r = client.post(
        api(cid, "/github/inspect"),
        json={"connected_account_id": account["id"]},
        headers=session.headers,
    )
    assert r.status_code in (401, 403)
    # not a GitHub account
    other_token = connect(client, cid, "github", label="two", secret=OTHER_TOKEN)
    assert inspect(client, cid, other_token)["login"] == "other-user"
    r = client.post(api(cid, "/github/inspect"), json={"connected_account_id": "missing"})
    assert r.status_code == 404
    r = client.post(
        api(cid, "/github/inspect"),
        json={"connected_account_id": account["id"], "full_name": "not a repo"},
    )
    assert r.status_code == 422


def test_client_for_binding_decrypts_once_wires_the_urls_and_refuses_a_revoked_account(
    app,
    client,
    company,
    github_world,  # noqa: F811
):
    from app.models.repository import ProjectRepository
    from app.services.repo_errors import NoAccount
    from app.services.repo_sync import RepoEnv
    from tests.m14_helpers import bound_project

    cid = company["id"]
    bp = bound_project(
        github_world, app, client, cid, mode="connect", full_name=f"{LOGIN}/app", clone=False
    )
    env = RepoEnv.from_integrations(app.state.integrations)
    with app.state.session_factory() as db:
        binding = db.get(ProjectRepository, bp.repository_id)
        binding.etags = {"k": "v"}
        gh = github_accounts.client_for_binding(db, env, binding)
        try:
            assert gh.base_url == github_world.api_url and gh.web_url == github_world.web_url
            assert gh.etags == {"k": "v"} and gh.etags is not binding.etags  # a copy
            assert gh.identity()["login"] == LOGIN and gh.token_kind() == "fine_grained"
        finally:
            gh.http.close()
        auth = github_accounts.remote_auth_for_binding(db, env, binding)
        assert auth.token == TOKEN and auth.host == github_world.remote.host
        assert TOKEN not in repr(auth)
    r = client.delete(api(cid, f"/connected-accounts/{bp.account['id']}"))
    assert r.status_code == 200, r.text
    with app.state.session_factory() as db:
        binding = db.get(ProjectRepository, bp.repository_id)
        with pytest.raises(NoAccount):
            github_accounts.client_for_binding(db, env, binding)
        with pytest.raises(NoAccount):
            github_accounts.remote_auth_for_binding(db, env, binding)


def test_probe_capabilities_reads_only_and_turns_features_off(
    client,
    company,
    account,
    github_world,  # noqa: F811
):
    github_world.add_repo(f"{LOGIN}/probe")
    github_world.github.deny(f"{LOGIN}/probe", "pull_requests")
    gh = github_world.client()
    caps = github_accounts.probe_capabilities(gh, f"{LOGIN}/probe", mode="connect")
    assert caps["pull_requests"] is False and caps["issues"] is True and caps["contents"] is True
    assert set(caps) >= {"statuses", "actions", "checks", "dependabot", "webhooks"}
    assert [c for c in github_world.github.calls if c.method != "GET"] == []  # only reads
    # a dead token is not a missing permission
    github_world.github.revoke(TOKEN)
    with pytest.raises(Exception) as exc:
        github_accounts.probe_capabilities(github_world.client(), f"{LOGIN}/probe")
    assert "401" in str(exc.value) or "rejected" in str(exc.value).lower()
