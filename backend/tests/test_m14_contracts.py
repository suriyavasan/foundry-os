"""M14 step-0 contracts: the routes exist with their final paths and methods, authorization is
enforced before the (stub) services answer 501 ``{available: false, milestone: "M14"}``, the schemas
validate what the spec says, and the pure helpers (push argv check, policy defaults) behave.

The work packages replace the stub bodies; these tests keep holding unchanged for the parts that are
contracts (paths, tenancy, 403s, validation). Tests that assert the 501 are marked ``stub`` in their
name and are expected to be rewritten by the package that implements the service."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.principal import Principal, PrincipalType
from app.schemas import repository as schemas
from app.services import repo_git, repo_sync
from tests.conftest import human
from tests.helpers import api, new_agent
from tests.m14_helpers import bound_project, github_world  # noqa: F401  (a fixture)

CID = "00000000-0000-0000-0000-000000000000"
RID = "00000000-0000-0000-0000-000000000001"

ROUTES = [
    ("GET", "/repositories/status"),
    ("POST", "/github/inspect"),
    ("GET", "/repositories"),
    ("POST", "/repositories"),
    ("GET", "/repositories/{rid}"),
    ("PATCH", "/repositories/{rid}"),
    ("POST", "/repositories/{rid}/sync"),
    ("POST", "/repositories/{rid}/pause"),
    ("POST", "/repositories/{rid}/resume"),
    ("POST", "/repositories/{rid}/disconnect"),
    ("POST", "/repositories/{rid}/integration/reset"),
    ("POST", "/repositories/{rid}/pull-requests/resume-opening"),
    ("PUT", "/repositories/{rid}/webhook"),
    ("GET", "/repositories/{rid}/pull-requests"),
    ("GET", "/repositories/{rid}/items"),
    ("GET", "/repositories/{rid}/activity"),
]
BODIES = {
    ("POST", "/github/inspect"): {"connected_account_id": "acc"},
    ("POST", "/repositories"): {
        "mode": "connect",
        "connected_account_id": "acc",
        "project_name": "P",
        "full_name": "acme/app",
    },
    ("PATCH", "/repositories/{rid}"): {"policy": {"ci_mode": "ignore"}},
    ("POST", "/repositories/{rid}/disconnect"): {"confirm": True},
    ("POST", "/repositories/{rid}/integration/reset"): {"to": "default_branch", "confirm": True},
    ("PUT", "/repositories/{rid}/webhook"): {"enabled": False},
}
WRITES = [r for r in ROUTES if r[0] != "GET" and r != ("POST", "/github/inspect")]
READS = [r for r in ROUTES if r[0] == "GET" and r[1] != "/repositories/status"]


def call(client, cid: str, method: str, path: str):
    url = api(cid, path.format(rid=RID))
    return client.request(method, url, json=BODIES.get((method, path)))


def test_openapi_lists_every_m14_endpoint(client):
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    for method, path in ROUTES:
        template = "/api/companies/{company_id}" + path.replace("{rid}", "{repository_id}")
        assert method.lower() in paths[template], (method, template)
    assert "post" in paths["/api/webhooks/github/{repository_id}"]
    assert "post" in paths["/api/agent/v1/repo/pulls/{pull_id}/review"]
    schemas_ = spec["components"]["schemas"]
    for name in (
        "ProjectRepository",
        "RepoPolicy",
        "RepoPolicyInput",
        "RepositoryCreate",
        "RepositoryUpdate",
        "RepoPullRequest",
        "RepoPullRequestSummary",
        "RepoItem",
        "GithubInspectRequest",
        "GithubInspectResult",
        "RepoAvailability",
        "WebhookUpdate",
        "IntegrationReset",
        "PullReviewBody",
        "PullReviewResult",
    ):
        assert name in schemas_, name
    policy = client.get("/api/openapi.json").json()["components"]["schemas"]["Policy"]
    assert "new_project_repository" in policy["properties"]


def test_a_non_member_gets_404_everywhere_and_an_unknown_company_too(
    client, client_as, make_user, company
):
    stranger = client_as(human(make_user("stranger-m14@x.test")))
    for method, path in ROUTES:
        assert call(stranger, company["id"], method, path).status_code == 404, (method, path)
        assert call(client, CID, method, path).status_code == 404, (method, path)


def test_agents_get_403_on_every_owner_endpoint(client, client_as, company):
    cid = company["id"]
    agent = new_agent(client, cid)
    ac = client_as(Principal(PrincipalType.AGENT, agent["id"], "Nova", cid))
    for method, path in ROUTES:
        if path == "/repositories/status":
            continue  # membership-level availability probe
        resp = call(ac, cid, method, path)
        assert resp.status_code == 403, (method, path, resp.text)


def test_viewers_may_read_but_not_change(client, client_as, make_user, add_member, company):
    cid = company["id"]
    viewer = make_user("viewer-m14-contract@x.test")
    add_member(cid, viewer.id, "viewer")
    vc = client_as(human(viewer))
    for method, path in WRITES:
        assert call(vc, cid, method, path).status_code == 403, (method, path)
    assert call(vc, cid, "POST", "/github/inspect").status_code == 403
    for method, path in READS:  # allowed: the stub answers "not built yet", not 403
        assert call(vc, cid, method, path).status_code == 501, (method, path)


def test_stub_owner_calls_answer_501_not_available_m14(client, company):
    cid = company["id"]
    for method, path in WRITES + READS:
        resp = call(client, cid, method, path)
        assert resp.status_code == 501, (method, path, resp.text)
        err = resp.json()["error"]
        assert err["code"] == "not_available"
        assert err["details"] == {"available": False, "milestone": "M14"}


def test_stub_availability_says_not_built(client, company):
    body = client.get(api(company["id"], "/repositories/status")).json()
    assert body["available"] is False and body["milestone"] == "M14" and body["reason"]


def test_stub_invalid_bodies_are_422_before_the_service_is_reached(client, company):
    cid = company["id"]
    for body in (
        {"mode": "connect", "connected_account_id": "a", "full_name": "acme/app"},  # no project
        {
            "mode": "connect",
            "connected_account_id": "a",
            "project_name": "P",
            "project_id": "x",
            "full_name": "acme/app",
        },  # both
        {"mode": "connect", "connected_account_id": "a", "project_name": "P"},  # no repository
        {"mode": "create", "connected_account_id": "a", "project_name": "P"},  # no name
        {
            "mode": "create",
            "connected_account_id": "a",
            "project_name": "P",
            "name": "x",
            "work_branch": "main",
        },  # not under foundry/
        {"mode": "create", "connected_account_id": "a", "project_name": "P", "name": "bad name"},
        {
            "mode": "create",
            "connected_account_id": "a",
            "project_name": "P",
            "name": "x",
            "policy": {"poll_interval_seconds": 5},
        },
        {
            "mode": "create",
            "connected_account_id": "a",
            "project_name": "P",
            "name": "x",
            "surprise": True,
        },
    ):
        assert client.post(api(cid, "/repositories"), json=body).status_code == 422, body


def test_stub_the_webhook_receiver_is_public_and_capped(client, anon_client, company):
    r = anon_client.post(f"/api/webhooks/github/{RID}", content=b"{}")
    assert r.status_code == 501  # no auth needed: the signature is the credential
    big = anon_client.post(f"/api/webhooks/github/{RID}", content=b"x" * (1024 * 1024 + 10))
    assert big.status_code == 413


def test_stub_neutral_hooks_do_not_change_existing_behaviour(app):
    with app.state.session_factory() as db:
        assert repo_sync.workspace_spec_for(db, None) is None  # type: ignore[arg-type]
        assert repo_sync.dispatch_hold(db, None) is None  # type: ignore[arg-type]
    assert repo_sync.enqueue_due(app.state.session_factory, None) == 0  # type: ignore[arg-type]


def test_request_sync_and_enqueue_clone_are_real_and_idempotent(app, client, company, github_world):
    from sqlalchemy import select

    from app.models.repository import ProjectRepository
    from app.models.run import Job

    bp = bound_project(github_world, app, client, company["id"], clone=False)
    with app.state.session_factory() as db:
        row = db.get(ProjectRepository, bp.repository_id)
        assert repo_sync.request_sync(db, row.id, "api") is True
        assert row.next_sync_at is not None
        assert repo_sync.request_sync(db, "missing", "api") is False
        first = repo_sync.enqueue_clone(db, row)
        second = repo_sync.enqueue_clone(db, row)
        db.commit()
        assert first.id == second.id and first.kind == "repo_clone"
        assert first.payload == {"repository_id": row.id}
        assert len(list(db.scalars(select(Job).where(Job.kind == "repo_clone")))) == 1


# ------------------------------------------------------------------ pure helpers


def test_default_policy_follows_the_onboarding_defaults():
    created = schemas.default_policy("create")
    assert (created.merge_mode, created.issue_intake, created.ci_mode, created.docs_upkeep) == (
        "pr_auto_merge",
        "collaborators",
        "if_present",
        True,
    )
    connected = schemas.default_policy("connect", has_workflows=False)
    assert (
        connected.merge_mode,
        connected.issue_intake,
        connected.ci_mode,
        connected.docs_upkeep,
    ) == (
        "pr_human_merge",
        "labeled",
        "if_present",
        False,
    )
    assert schemas.default_policy("connect", has_workflows=True).ci_mode == "required"
    plain = schemas.RepoPolicy()
    assert plain.merge_mode == "pr_human_merge" and plain.pr_merge_method == "squash"
    assert plain.allow_workflow_changes is False and plain.releases == "off"
    assert plain.protected_paths == [".github/workflows/**", ".github/actions/**", ".gitmodules"]
    assert (plain.poll_interval_seconds, plain.max_file_bytes, plain.max_push_bytes) == (
        300,
        10 * 1024 * 1024,
        100 * 1024 * 1024,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("ci_wait_seconds", 59),
        ("ci_wait_seconds", 7201),
        ("pr_batch_quiet_seconds", -1),
        ("max_pr_revisions", 11),
        ("max_ci_fix_attempts", 11),
        ("intake_label", ""),
        ("max_open_intake_tasks", 51),
        ("stale_branch_days", 3),
        ("stale_branch_days", 366),
        ("poll_interval_seconds", 59),
        ("poll_interval_seconds", 3601),
        ("max_file_bytes", 10),
        ("max_push_bytes", 10),
        ("merge_mode", "yolo"),
        ("protected_paths", ["x"] * 21),
        ("protected_paths", ["a" * 201]),
    ],
)
def test_policy_ranges_are_enforced(field, value):
    with pytest.raises(ValidationError):
        schemas.RepoPolicy(**{field: value})
    with pytest.raises(ValidationError):
        schemas.RepoPolicyInput(**{field: value})


def test_policy_input_is_all_optional_and_rejects_null_and_unknown_fields():
    assert schemas.RepoPolicyInput().model_dump(exclude_none=True) == {}
    assert schemas.RepoPolicyInput(stale_branch_days=0).stale_branch_days == 0
    with pytest.raises(ValidationError):
        schemas.RepoPolicyInput(merge_mode=None)
    with pytest.raises(ValidationError):
        schemas.RepoPolicyInput(not_a_field=1)


def test_repository_references_and_branch_names():
    assert schemas.parse_repo_ref("acme/app") == ("acme", "app")
    assert schemas.parse_repo_ref("https://github.com/acme/app.git") == ("acme", "app")
    assert schemas.parse_repo_ref("https://github.com/acme/app/") == ("acme", "app")
    for bad in ("acme", "acme/", "/app", "a/b/c", "acme/app.git", "acme/..", "-x/app", "a b/c"):
        assert schemas.parse_repo_ref(bad) is None, bad
    assert schemas.valid_repo_name("my.repo_1-x") and not schemas.valid_repo_name("a.git")
    assert not schemas.valid_repo_name("..") and not schemas.valid_repo_name("a b")
    for good in ("main", "foundry/app", "release/1.2", "feature-x"):
        assert schemas.valid_branch_name(good), good
    for bad in (
        "",
        "a..b",
        "a b",
        "a~1",
        "a^",
        "a:b",
        "a?",
        "a*",
        "a[",
        "/a",
        "a/",
        "a//b",
        "-x",
        "a.lock",
        "a/.hidden",
        "a@{1}",
        "@",
        "HEAD",
        "x" * 256,
        "a\\b",
    ):
        assert not schemas.valid_branch_name(bad), bad


def test_repository_create_accepts_the_two_onboarding_shapes():
    new = schemas.RepositoryCreate(
        mode="create", connected_account_id="a", project_name="P", name="app", visibility="private"
    )
    assert new.initialize and new.company_default and new.policy.model_dump(exclude_none=True) == {}
    existing = schemas.RepositoryCreate(
        mode="connect",
        connected_account_id="a",
        project_id="p1",
        full_name="https://github.com/acme/app",
        work_branch="foundry/app",
        default_branch="develop",
    )
    assert existing.work_branch == "foundry/app"
    with pytest.raises(ValidationError):  # work branch must differ from the default branch
        schemas.RepositoryCreate(
            mode="connect",
            connected_account_id="a",
            project_id="p",
            full_name="a/b",
            work_branch="foundry/x",
            default_branch="foundry/x",
        )
    with pytest.raises(ValidationError):  # visibility other than private/public
        schemas.RepositoryCreate(
            mode="create",
            connected_account_id="a",
            project_name="P",
            name="x",
            visibility="internal",
        )


def test_the_only_push_is_exactly_one_sha_to_refs_heads():
    sha = "a" * 40
    ok = ["git", "-c", "x=y", "push", "--porcelain", "origin", f"{sha}:refs/heads/foundry/app"]
    repo_git.assert_safe_push_argv(ok)
    bad = [
        ["git", "push", "-f", "origin", f"{sha}:refs/heads/x"],
        ["git", "push", "--force", "origin", f"{sha}:refs/heads/x"],
        ["git", "push", "--force-with-lease", "origin", f"{sha}:refs/heads/x"],
        ["git", "push", "--mirror", "origin"],
        ["git", "push", "--delete", "origin", "x"],
        ["git", "push", "-d", "origin", "x"],
        ["git", "push", "--tags", "origin"],
        ["git", "push", "--all", "origin"],
        ["git", "push", "--prune", "origin", f"{sha}:refs/heads/x"],
        ["git", "push", "origin", f"+{sha}:refs/heads/x"],
        ["git", "push", "origin", ":refs/heads/x"],
        ["git", "push", "origin", f"{sha}:refs/tags/v1"],
        ["git", "push", "origin", f"{sha}:main"],
        ["git", "push", "origin", f"{sha}:refs/heads/a", f"{sha}:refs/heads/b"],
        ["git", "push", "origin"],
        ["git", "fetch", "origin"],
        ["git", "push", "--porcelain", "--force", "origin", f"{sha}:refs/heads/x"],
        ["git", "push", "-fu", "origin", f"{sha}:refs/heads/x"],
    ]
    for argv in bad:
        with pytest.raises(repo_git.UnsafePush):
            repo_git.assert_safe_push_argv(argv)
