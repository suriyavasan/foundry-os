"""M14 WP-C: what a new project does with the company repository (``new_project_repository``), the
repository on the Co-founder's state rows, and the helpers the GitHub executors use
(``binding_for``, ``agent_may_use``, ``bind_for_agent``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.policy import CompanyPolicy
from app.models.repository import ProjectRepository
from app.services import agent_auth, repositories
from app.services import project_workspaces as pw
from tests.helpers import agent_of_family, api, new_agent
from tests.m14_helpers import LOGIN, bound_project, github_world  # noqa: F401  (a fixture)
from tests.m14_service_helpers import (
    ADMIN_TOKEN,
    audit_actions,
    clone_jobs,
    create_body,
    github_account,
    m14,  # noqa: F401  (a fixture)
    post_repo,
    repo_row,
    set_repo,
)
from tests.test_agent_api import start_run


def set_policy_field(app, cid: str, value: str) -> None:
    with app.state.session_factory() as db:
        db.scalar(
            select(CompanyPolicy).where(CompanyPolicy.company_id == cid)
        ).new_project_repository = value
        db.commit()


def bindings(app, cid: str) -> list[SimpleNamespace]:
    with app.state.session_factory() as db:
        rows = db.scalars(
            select(ProjectRepository)
            .where(ProjectRepository.company_id == cid)
            .order_by(ProjectRepository.created_at, ProjectRepository.id)
        )
        return [
            SimpleNamespace(
                **{c.name: getattr(r, c.name) for c in ProjectRepository.__table__.columns}
            )
            for r in rows
        ]


def founder_creates_project(app, client, cid: str, name: str):
    founder = agent_of_family(client, cid, "founder")
    session = start_run(app, client, cid, founder["id"], task_title=f"Plan {name}")
    return session.post("/projects", json={"name": name, "description": f"{name} project"})


@pytest.fixture
def company_repo(client, company, m14, app):
    """A company whose default repository is bound and whose policy says ``company_repo``."""
    cid = company["id"]
    bound = bound_project(m14, app, client, cid, mode="create", project_name="First")
    set_policy_field(app, cid, "company_repo")
    return SimpleNamespace(cid=cid, bound=bound)


# ------------------------------------------------------------------ the hook


def test_with_local_the_cofounder_creates_a_project_without_a_repository(client, company, m14, app):
    cid = company["id"]
    bound_project(m14, app, client, cid, mode="create", project_name="First")  # policy stays local
    resp = founder_creates_project(app, client, cid, "Second")
    assert resp.status_code == 201, resp.text
    assert [b.project_id for b in bindings(app, cid)] == [resp_project_id(client, cid, "First")]
    assert clone_jobs(app, bindings(app, cid)[0].id) == []


def resp_project_id(client, cid: str, name: str) -> str:
    return next(
        p["id"] for p in client.get(api(cid, "/projects")).json()["items"] if p["name"] == name
    )


def test_with_company_repo_a_cofounder_project_gets_its_own_binding_branch_and_clone(
    client, app, company_repo
):
    cid, first = company_repo.cid, company_repo.bound
    resp = founder_creates_project(app, client, cid, "Mobile App")
    assert resp.status_code == 201, resp.text
    project = resp.json()
    rows = bindings(app, cid)
    assert len(rows) == 2
    new = next(b for b in rows if b.project_id == project["id"])
    default = next(b for b in rows if b.project_id == first.project["id"])
    assert new.full_name == default.full_name and new.remote_url == default.remote_url
    assert new.default_branch == default.default_branch
    assert new.work_branch == "foundry/mobile-app" and new.work_branch != default.work_branch
    assert new.status == "cloning" and new.origin == "connected"
    assert new.is_company_default is False and default.is_company_default is True
    assert new.connected_account_id == default.connected_account_id
    # the repository is shared, so the new binding does not repeat the default one's intake
    assert new.policy["issue_intake"] == "off" and new.policy["security_alerts"] == "off"
    assert new.policy["releases"] == "off" and new.policy["docs_upkeep"] is False
    assert new.policy["merge_mode"] == default.policy["merge_mode"]
    assert new.onboarding_status == "not_needed"
    jobs = clone_jobs(app, new.id)
    assert len(jobs) == 1
    audit = [e for e in audit_actions(app, cid, new.id)]
    assert [e.action for e in audit] == ["repository.connected"]
    assert audit[0].actor_type == "agent" and audit[0].data["mode"] == "company_repo"
    assert audit[0].data["shared_with"] == default.id


def test_a_human_created_project_follows_the_same_policy(client, app, company_repo):
    cid = company_repo.cid
    resp = client.post(api(cid, "/projects"), json={"name": "Docs"})
    assert resp.status_code == 201
    new = next(b for b in bindings(app, cid) if b.project_id == resp.json()["id"])
    assert new.work_branch == "foundry/docs" and new.status == "cloning"
    assert next(e for e in audit_actions(app, cid, new.id)).actor_type == "human"


def test_projects_with_the_same_name_get_different_work_branches(client, app, company_repo):
    cid = company_repo.cid
    a = client.post(api(cid, "/projects"), json={"name": "Docs"}).json()
    b = client.post(api(cid, "/projects"), json={"name": "Docs"}).json()
    branches = {x.project_id: x.work_branch for x in bindings(app, cid)}
    assert branches[a["id"]] == "foundry/docs"
    assert branches[b["id"]].startswith("foundry/docs-") and branches[b["id"]] != branches[a["id"]]


def test_no_binding_without_a_default_repository_or_with_a_disconnected_one(
    client, app, company_repo
):
    cid, first = company_repo.cid, company_repo.bound
    client.post(api(cid, f"/repositories/{first.repository_id}/disconnect"), json={"confirm": True})
    resp = client.post(api(cid, "/projects"), json={"name": "Orphan"})
    assert resp.status_code == 201
    assert len(bindings(app, cid)) == 1  # only the disconnected one
    # and a company that never connected anything just creates the project
    other = client.post("/api/companies", json={"name": "Plain", "mission": "m"}).json()
    set_policy_field(app, other["id"], "company_repo")
    assert client.post(api(other["id"], "/projects"), json={"name": "Solo"}).status_code == 201
    assert bindings(app, other["id"]) == []


def test_without_git_a_project_is_still_created(client, app, company_repo, monkeypatch):
    cid = company_repo.cid
    monkeypatch.setattr(
        pw, "status", lambda: {"available": False, "reason": "git is not installed"}
    )
    resp = client.post(api(cid, "/projects"), json={"name": "No git"})
    assert resp.status_code == 201
    assert len(bindings(app, cid)) == 1


def test_creating_a_repository_for_a_new_project_does_not_bind_twice(client, company, m14, app):
    """The repository service creates the project itself: the project hook must not add a second
    binding even when the policy is already ``company_repo``."""
    cid = company["id"]
    account = github_account(client, cid, m14, token=ADMIN_TOKEN, label="Admin")
    first = post_repo(client, cid, create_body(account, "first", project_name="First"))
    assert first.status_code == 201, first.text
    second = post_repo(client, cid, create_body(account, "second", project_name="Second"))
    assert second.status_code == 201, second.text
    rows = bindings(app, cid)
    assert len(rows) == 2
    assert {r.full_name for r in rows} == {f"{LOGIN}/first", f"{LOGIN}/second"}
    assert len({r.project_id for r in rows}) == 2


def test_a_failed_project_creation_binds_nothing(client, app, company_repo):
    cid = company_repo.cid
    resp = client.post(api(cid, "/projects"), json={"name": ""})
    assert resp.status_code == 422
    assert len(bindings(app, cid)) == 1


# ------------------------------------------------------------------ the Co-founder's state


def test_state_rows_show_the_repository_of_each_project(client, app, company_repo):
    cid, first = company_repo.cid, company_repo.bound
    local = client.post(api(cid, "/projects"), json={"name": "Local only"}).json()
    # a project bound afterwards by the hook is shown too; a project without one shows null
    with app.state.session_factory() as db:
        db.execute(
            ProjectRepository.__table__.delete().where(ProjectRepository.project_id == local["id"])
        )
        db.commit()
    founder = agent_of_family(client, cid, "founder")
    state = start_run(app, client, cid, founder["id"]).get("/state").json()
    rows = {p["id"]: p for p in state["projects"]}
    assert rows[first.project["id"]]["repository"] == {
        "full_name": first.full_name,
        "merge_mode": "pr_auto_merge",
        "status": "active",
    }
    assert rows[local["id"]]["repository"] is None
    set_repo(app, first.repository_id, status="paused")
    state = start_run(app, client, cid, founder["id"], task_title="Again").get("/state").json()
    assert {p["id"]: p for p in state["projects"]}[first.project["id"]]["repository"][
        "status"
    ] == "paused"
    # nothing secret rides along
    text = str(state)
    assert (
        "remote_url" not in text and "connected_account" not in text and "github_pat_" not in text
    )


def test_a_lead_sees_the_repository_only_of_the_projects_it_leads(client, app, company_repo):
    cid, first = company_repo.cid, company_repo.bound
    founder = agent_of_family(client, cid, "founder")
    lead = new_agent(
        client, cid, name="Lena", kind="lead", role_title="Lead", manager_agent_id=founder["id"]
    )
    client.patch(api(cid, f"/projects/{first.project['id']}"), json={"lead_agent_id": lead["id"]})
    other = client.post(api(cid, "/projects"), json={"name": "Not mine"}).json()
    state = start_run(app, client, cid, lead["id"]).get("/state").json()
    ids = {p["id"]: p for p in state["projects"]}
    assert (
        set(ids) == {first.project["id"]}
        and ids[first.project["id"]]["repository"]["full_name"] == first.full_name
    )
    assert other["id"] not in ids


# ------------------------------------------------------------------ binding_for / agent_may_use


def test_binding_for_returns_only_active_bindings_case_insensitively(client, app, company_repo):
    cid, first = company_repo.cid, company_repo.bound
    with app.state.session_factory() as db:
        found = repositories.binding_for(db, cid, first.full_name.upper())
        assert found is not None and found.id == first.repository_id
        assert repositories.binding_for(db, cid, "nobody/nothing") is None
        assert repositories.binding_for(db, "another-company", first.full_name) is None
    set_repo(app, first.repository_id, status="paused")
    with app.state.session_factory() as db:
        assert repositories.binding_for(db, cid, first.full_name) is None


def test_binding_for_prefers_the_company_default_when_projects_share_a_repository(
    client, app, company_repo
):
    cid, first = company_repo.cid, company_repo.bound
    client.post(api(cid, "/projects"), json={"name": "Second"})
    for row in bindings(app, cid):
        set_repo(app, row.id, status="active")
    with app.state.session_factory() as db:
        assert repositories.binding_for(db, cid, first.full_name).id == first.repository_id


def ctx_of(app, session) -> agent_auth.AgentRunContext:
    with app.state.session_factory() as db:
        return agent_auth.authenticate(db, app.state.keys, session.token)


def test_agent_may_use_is_the_cofounder_the_lead_or_a_worker_of_the_project(
    client, app, company_repo
):
    cid, first = company_repo.cid, company_repo.bound
    founder = agent_of_family(client, cid, "founder")
    lead = new_agent(
        client, cid, name="Lena", kind="lead", role_title="Lead", manager_agent_id=founder["id"]
    )
    worker = new_agent(client, cid, name="Wes", role_title="Engineer", manager_agent_id=lead["id"])
    outsider = new_agent(
        client, cid, name="Olga", role_title="Engineer", manager_agent_id=lead["id"]
    )
    client.patch(api(cid, f"/projects/{first.project['id']}"), json={"lead_agent_id": lead["id"]})
    shared = client.post(api(cid, "/projects"), json={"name": "Shared repo"}).json()
    elsewhere = client.post(api(cid, "/projects"), json={"name": "Elsewhere"}).json()
    with (
        app.state.session_factory() as db
    ):  # the second project's binding is on a repository of its own
        row = db.scalar(
            select(ProjectRepository).where(ProjectRepository.project_id == elsewhere["id"])
        )
        row.full_name, row.name = f"{LOGIN}/other", "other"
        db.commit()

    def can(agent_id, **task) -> bool:
        session = start_run(app, client, cid, agent_id, task_title=f"t{agent_id[:4]}", **task)
        rc = ctx_of(app, session)
        with app.state.session_factory() as db:
            binding = db.get(ProjectRepository, first.repository_id)
            return repositories.agent_may_use(db, rc, binding)

    assert can(founder["id"]) is True
    assert can(lead["id"]) is True
    assert can(worker["id"], project_id=first.project["id"]) is True
    assert can(worker["id"], project_id=shared["id"]) is True  # a project on the same repository
    assert can(outsider["id"]) is False
    assert can(outsider["id"], project_id=elsewhere["id"]) is False


# ------------------------------------------------------------------ bind_for_agent


def approved_params(project_id: str, **extra) -> dict:
    return {
        "owner": LOGIN,
        "name": "agent-made",
        "visibility": "private",
        "project_id": project_id,
        **extra,
    }


@pytest.fixture
def agent_world(client, company, m14, app):
    cid = company["id"]
    github_account(client, cid, m14, token=ADMIN_TOKEN, label="Admin")
    founder = agent_of_family(client, cid, "founder")
    lead = new_agent(
        client, cid, name="Lena", kind="lead", role_title="Lead", manager_agent_id=founder["id"]
    )
    worker = new_agent(client, cid, name="Wes", role_title="Engineer", manager_agent_id=lead["id"])
    project = client.post(
        api(cid, "/projects"), json={"name": "Agent project", "lead_agent_id": lead["id"]}
    ).json()
    return SimpleNamespace(cid=cid, founder=founder, lead=lead, worker=worker, project=project)


def bind(app, client, w, agent_id, params):
    session = start_run(app, client, w.cid, agent_id, task_title=f"bind {agent_id[:4]}")
    rc = ctx_of(app, session)
    with app.state.session_factory() as db:
        return repositories.bind_for_agent(db, rc, app.state.integrations, params)


def test_the_cofounder_and_the_lead_can_create_the_repository_after_approval(
    client, app, agent_world, m14
):
    w = agent_world
    summary = bind(app, client, w, w.founder["id"], approved_params(w.project["id"]))
    assert summary["full_name"] == f"{LOGIN}/agent-made" and summary["status"] == "creating"
    assert summary["project_id"] == w.project["id"] and summary["visibility"] == "private"
    assert set(summary) == {
        "repository_id", "full_name", "html_url", "visibility", "status", "project_id",
        "work_branch", "default_branch",
    }  # fmt: skip
    assert ADMIN_TOKEN not in str(summary)
    assert f"{LOGIN}/agent-made" in m14.github.repos
    row = repo_row(app, summary["repository_id"])
    assert (
        row.origin == "created"
        and row.created_by_type == "agent"
        and row.created_by_id == w.founder["id"]
    )
    assert row.is_company_default is False  # an agent never changes the company default
    assert len(clone_jobs(app, row.id)) == 1
    event = audit_actions(app, w.cid, row.id)[0]
    assert event.action == "repository.created" and event.actor_id == w.founder["id"]

    second = client.post(
        api(w.cid, "/projects"), json={"name": "Lead's project", "lead_agent_id": w.lead["id"]}
    ).json()
    again = bind(app, client, w, w.lead["id"], approved_params(second["id"], name="lead-made"))
    assert again["full_name"] == f"{LOGIN}/lead-made"


def test_bind_for_agent_refuses_everyone_else_and_bad_requests(client, app, agent_world, m14):
    w = agent_world
    from app.core.errors import AppError

    def refused(agent_id, params) -> AppError:
        with pytest.raises(AppError) as info:
            bind(app, client, w, agent_id, params)
        return info.value

    assert refused(w.worker["id"], approved_params(w.project["id"])).status_code == 403
    assert refused(w.founder["id"], {"owner": LOGIN, "name": "x"}).status_code == 422  # no project
    assert refused(w.founder["id"], approved_params(w.project["id"], name="")).status_code == 422
    assert refused(w.founder["id"], approved_params("no-such-project")).status_code == 404
    assert f"{LOGIN}/x" not in m14.github.repos
    # one repository per project
    bind(app, client, w, w.founder["id"], approved_params(w.project["id"]))
    again = refused(w.founder["id"], approved_params(w.project["id"], name="second"))
    assert again.status_code == 409 and again.code == "project_has_repository"
    assert f"{LOGIN}/second" not in m14.github.repos


def test_bind_for_agent_without_a_github_account_is_a_conflict(client, company, m14, app):
    cid = company["id"]
    founder = agent_of_family(client, cid, "founder")
    project = client.post(api(cid, "/projects"), json={"name": "P"}).json()
    from app.core.errors import AppError

    with pytest.raises(AppError) as info:
        bind(app, client, SimpleNamespace(cid=cid), founder["id"], approved_params(project["id"]))
    assert info.value.code == "no_account"


def test_a_public_repository_is_created_when_the_approved_scope_said_public(
    client, app, agent_world, m14
):
    w = agent_world
    summary = bind(
        app, client, w, w.founder["id"], approved_params(w.project["id"], visibility="public")
    )
    assert (
        summary["visibility"] == "public"
        and m14.github.repo(f"{LOGIN}/agent-made").private is False
    )
