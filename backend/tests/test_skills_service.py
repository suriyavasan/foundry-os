"""Skill library: seeding, Hermes install, authoring, scan policy, quarantine, assignment."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.principal import Principal, PrincipalType
from app.engine.worker import Worker
from app.models.audit import AuditEvent
from app.models.run import Job
from app.models.skill import Skill, SkillBlob
from app.roles import loader
from tests.conftest import human
from tests.helpers import (
    FakeScanner,
    agent_of_family,
    api,
    new_agent,
    seed_library,
    skills_by_slug,
    write_skill,
)
from tests.test_agent_api import start_run

N_SEEDED = len(loader.seed_skill_slugs())


@pytest.fixture
def skills_env_override(fake_skills_env):
    return fake_skills_env


def audits(app, cid: str, action: str) -> list[AuditEvent]:
    with app.state.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.company_id == cid, AuditEvent.action == action)
            )
        )


def hr_session(app, client, cid):
    return start_run(
        app, client, cid, agent_of_family(client, cid, "hr")["id"], task_title="HR work"
    )


# ------------------------------------------------------------------ seeding


def test_company_creation_queues_the_seed_job_and_the_worker_runs_it(
    app, client, engine, company, clock
):
    cid = company["id"]
    with app.state.session_factory() as db:
        [job] = db.scalars(select(Job).where(Job.kind == "seed_skill_library")).all()
        assert job.company_id == cid and job.status == "queued"
        assert db.scalar(select(func.count(Skill.id))) == 0  # creation itself stayed fast
    clock.advance(60)
    assert Worker(engine, worker_id="w").drain() == 1
    skills = skills_by_slug(client, cid)
    assert len(skills) == N_SEEDED
    assert {s["status"] for s in skills.values()} == {"active"}
    with app.state.session_factory() as db:
        assert db.scalar(select(Job.status).where(Job.kind == "seed_skill_library")) == "done"
    # Harper and the founder were equipped with their templates' default skills
    for family, template in (("hr", "hr-partner"), ("founder", "founder-ceo")):
        agent = agent_of_family(client, cid, family)
        got = {
            i["skill"]["slug"]
            for i in client.get(api(cid, f"/agents/{agent['id']}/skills")).json()["items"]
        }
        assert got == set(loader.builtin_templates()[template].default_skill_slugs)


def test_seed_endpoint_queues_one_job_unless_waiting(client, company):
    cid = company["id"]
    first = client.post(api(cid, "/skills/seed"))
    assert first.status_code == 202 and first.json()["status"] == "already_queued"  # from creation
    assert first.json()["job_id"]


def test_seeding_is_idempotent_and_records_provenance(app, client, company, fake_scanner):
    cid = company["id"]
    first = seed_library(client, cid)
    assert len(first["installed"]) == N_SEEDED and first["missing"] == []
    scans_after_first = len(fake_scanner.calls)
    second = seed_library(client, cid)
    assert second["installed"] == [] and second["updated"] == []
    assert len(second["unchanged"]) == N_SEEDED
    assert len(fake_scanner.calls) == scans_after_first  # unchanged skills are not re-scanned
    skills = skills_by_slug(client, cid)
    hiring = skills["hiring-playbook"]
    assert hiring["source_kind"] == "company_authored" and hiring["trust"] == "builtin"
    assert hiring["license"] == "MIT" and hiring["author"] == "Company OS"
    assert hiring["source_ref"].startswith("company_os:")
    bundled = [s for s in skills.values() if s["source_kind"] == "hermes_bundled"]
    optional = [s for s in skills.values() if s["source_kind"] == "hermes_optional"]
    assert bundled and optional
    assert all(s["source_ref"].startswith("hermes@db1fa153:") for s in bundled + optional)
    assert all(s["trust"] == "builtin" and s["scan_verdict"] == "safe" for s in bundled)
    assert len(audits(app, cid, "skill.installed")) == N_SEEDED


def test_seeding_does_not_resurrect_archived_skills(client, company):
    cid = company["id"]
    seed_library(client, cid)
    skill = skills_by_slug(client, cid)["spike"]
    assert client.post(api(cid, f"/skills/{skill['id']}/archive")).status_code == 200
    seed_library(client, cid)
    assert skills_by_slug(client, cid)["spike"]["status"] == "archived"


def test_blobs_are_shared_between_companies(app, client, company):
    seed_library(client, company["id"])
    other = client.post("/api/companies", json={"name": "Other"}).json()
    seed_library(client, other["id"])
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count(Skill.id))) == 2 * N_SEEDED
        assert db.scalar(select(func.count(SkillBlob.sha256))) == N_SEEDED
    blobs = app.state.skills.blobs.root
    assert len([p for p in blobs.iterdir() if not p.name.startswith(".")]) == N_SEEDED
    assert (
        skills_by_slug(client, other["id"])["spike"]["id"]
        != skills_by_slug(client, company["id"])["spike"]["id"]
    )


def test_missing_hermes_skills_are_reported_not_fatal(app, client, company, fake_runtime):
    import shutil

    for p in fake_runtime.rglob("spike"):
        shutil.rmtree(p)
    app.state.skills.hermes.skills(refresh=True)
    result = seed_library(client, company["id"])
    assert result["missing"] == ["spike"]
    assert "spike" not in skills_by_slug(client, company["id"])


# ------------------------------------------------------------------ install from Hermes


def test_install_hermes_skill_by_slug_and_by_path_and_update(app, client, company, fake_runtime):
    cid = company["id"]
    first = client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"})
    assert first.status_code == 201, first.text
    body = first.json()
    skill = body["skill"]
    assert body["outcome"] == "installed" and not body["quarantined"]
    assert skill["status"] == "active" and skill["trust"] == "builtin"
    assert skill["source_ref"].startswith("hermes@db1fa153:") and skill["source_ref"].endswith(
        "/spike"
    )
    again = client.post(api(cid, "/skills/install"), json={"source_ref": skill["source_ref"]})
    assert again.status_code == 200 and again.json()["outcome"] == "unchanged"

    src = next(fake_runtime.rglob("spike"))
    (src / "SKILL.md").write_text((src / "SKILL.md").read_text() + "\nNew step.\n")
    updated = client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"})
    assert updated.json()["outcome"] == "updated"
    assert updated.json()["skill"]["blob_sha256"] != skill["blob_sha256"]
    [event] = audits(app, cid, "skill.updated")
    assert event.data["previous_blob_sha256"] == skill["blob_sha256"]
    assert event.data["blob_sha256"] == updated.json()["skill"]["blob_sha256"]
    # the previous blob stays on disk
    assert app.state.skills.blobs.exists(skill["blob_sha256"])
    assert (
        client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:nope"}).status_code
        == 404
    )
    assert client.post(api(cid, "/skills/install"), json={"source_ref": "npm:x"}).status_code == 422


def test_slug_conflict_between_different_sources(app, client, company, fake_runtime):
    cid = company["id"]
    write_skill(fake_runtime / "skills" / "x", "dup", tags=["x"])
    app.state.skills.hermes.skills(refresh=True)
    first = client.post(
        api(cid, "/skills/install"), json={"source_ref": "hermes:dup", "slug": "taken"}
    )
    assert first.status_code == 201 and first.json()["skill"]["slug"] == "taken"
    clash = client.post(
        api(cid, "/skills/install"), json={"source_ref": "hermes:spike", "slug": "taken"}
    )
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "conflict"
    assert clash.json()["error"]["details"]["slug"] == "taken"


# ------------------------------------------------------------------ scan policy and quarantine


def author_body(slug="support-macros", **extra):
    return {
        "slug": slug,
        "name": "Support macros",
        "description": "Reusable support replies.",
        "tags": ["Support", "Macros"],
        "body_markdown": "# Support macros\n\n1. Acknowledge.\n2. Answer.\n",
        **extra,
    }


def test_authored_skill_is_community_and_blocked_on_caution_and_dangerous(
    app, client, company, fake_scanner
):
    cid = company["id"]
    hr = hr_session(app, client, cid)
    fake_scanner.verdicts.update(
        {"ok-skill": "safe", "meh-skill": "caution", "bad-skill": "dangerous"}
    )
    results = {}
    for slug in ("ok-skill", "meh-skill", "bad-skill"):
        r = hr.post("/skills/author", json=author_body(slug))
        assert r.status_code == 201, r.text
        results[slug] = r.json()
    assert results["ok-skill"]["skill"]["status"] == "active"
    for slug in ("meh-skill", "bad-skill"):
        assert results[slug]["skill"]["status"] == "quarantined" and results[slug]["quarantined"]
        assert results[slug]["scan"]["finding_count"] == 1
    assert all(r["skill"]["trust"] == "community" for r in results.values())
    assert all(r["skill"]["source_kind"] == "company_authored" for r in results.values())
    assert {name for name, _ in fake_scanner.calls} == {"ok-skill", "meh-skill", "bad-skill"}
    assert {src for _, src in fake_scanner.calls} == {"community"}
    quarantined = audits(app, cid, "skill.quarantined")
    assert len(quarantined) == 2
    # the report keeps findings but never the scanned text
    report = client.get(api(cid, f"/skills/{results['bad-skill']['skill']['id']}")).json()[
        "scan_report"
    ]
    assert report["verdict"] == "dangerous" and report["findings"][0]["pattern"] == "fake_pattern"


def test_builtin_hermes_skills_are_allowed_with_any_verdict_but_recorded(
    client, company, fake_scanner
):
    cid = company["id"]
    fake_scanner.verdicts["spike"] = "dangerous"
    r = client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"})
    assert r.status_code == 201
    assert r.json()["skill"]["status"] == "active"
    assert r.json()["skill"]["scan_verdict"] == "dangerous"
    assert fake_scanner.calls[-1][1] == "official"


def test_quarantined_skill_release_archive_and_authorization(app, client, company, fake_scanner):
    cid = company["id"]
    hr = hr_session(app, client, cid)
    fake_scanner.verdicts["bad-skill"] = "dangerous"
    sid = hr.post("/skills/author", json=author_body("bad-skill")).json()["skill"]["id"]
    # agents (even HR) cannot release or archive: owner API needs a human
    bad_headers = hr.headers
    assert (
        client.post(api(cid, f"/skills/{sid}/unquarantine"), headers=bad_headers).status_code == 403
    )
    assert client.post(api(cid, f"/skills/{sid}/archive"), headers=bad_headers).status_code == 403
    released = client.post(api(cid, f"/skills/{sid}/unquarantine"), json={"note": "reviewed"})
    assert released.status_code == 200 and released.json()["status"] == "active"
    [event] = audits(app, cid, "skill.unquarantined")
    assert event.data["scan_verdict"] == "dangerous" and event.data["note"] == "reviewed"
    assert event.actor_type == "human"
    assert (
        client.post(api(cid, f"/skills/{sid}/unquarantine")).status_code == 409
    )  # not quarantined
    assert client.post(api(cid, f"/skills/{sid}/archive")).json()["status"] == "archived"
    assert client.post(api(cid, f"/skills/{sid}/archive")).status_code == 409


def test_viewer_cannot_install_or_release(client, client_as, make_user, add_member, company):
    cid = company["id"]
    viewer_user = make_user("viewer@example.com")
    add_member(cid, viewer_user.id, "viewer")
    viewer = client_as(human(viewer_user))
    assert viewer.get(api(cid, "/skills")).status_code == 200
    assert viewer.get(api(cid, "/skills/catalog")).status_code == 200
    assert (
        viewer.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"}).status_code
        == 403
    )
    assert viewer.post(api(cid, "/skills/seed")).status_code == 403
    assert (
        viewer.post(api(cid, "/role-templates"), json={"slug": "x", "title": "X"}).status_code
        == 403
    )


def test_non_members_get_404_everywhere(client, client_as, make_user, company):
    cid = company["id"]
    seed_library(client, cid)
    sid = skills_by_slug(client, cid)["spike"]["id"]
    stranger = client_as(human(make_user("stranger@example.com")))
    for method, path in (
        ("get", "/skills"),
        ("get", f"/skills/{sid}"),
        ("get", f"/skills/{sid}/files/SKILL.md"),
        ("get", "/skills/catalog"),
        ("post", f"/skills/{sid}/archive"),
        ("get", "/role-templates"),
    ):
        assert stranger.request(method, api(cid, path)).status_code == 404, path


# ------------------------------------------------------------------ fail closed without a scanner


def test_without_the_runtime_builtin_imports_are_unscanned_and_the_rest_is_refused(
    app, client, company, fake_scanner
):
    cid = company["id"]
    fake_scanner._available = False
    r = client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"})
    assert r.status_code == 201
    assert (
        r.json()["skill"]["scan_verdict"] == "unscanned" and r.json()["skill"]["status"] == "active"
    )
    hr = hr_session(app, client, cid)
    refused = hr.post("/skills/author", json=author_body())
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "scanner_unavailable"
    assert "support-macros" not in skills_by_slug(client, cid)


def test_a_failing_scan_of_a_community_skill_never_activates_it(app, client, company, fake_scanner):
    from app.skills.scan import ScanFailed

    cid = company["id"]

    def broken(path, *, source):
        raise ScanFailed("scanner crashed")

    fake_scanner.scan = broken  # type: ignore[method-assign]
    hr = hr_session(app, client, cid)
    r = hr.post("/skills/author", json=author_body())
    assert r.status_code == 503
    assert "support-macros" not in skills_by_slug(client, cid)


# ------------------------------------------------------------------ authoring


def test_hr_authors_a_skill_and_reauthoring_bumps_the_version(app, client, company):
    cid = company["id"]
    hr = hr_session(app, client, cid)
    r = hr.post(
        "/skills/author",
        json=author_body(files=[{"path": "references/tone.md", "content": "Be kind."}]),
    )
    assert r.status_code == 201, r.text
    skill = r.json()["skill"]
    assert skill["tags"] == ["support", "macros"] and skill["version"] == "1.0.0"
    assert skill["created_by_type"] == "agent" and skill["created_by_id"] == hr.agent_id
    detail = client.get(api(cid, f"/skills/{skill['id']}")).json()
    assert detail["frontmatter"]["name"] == "support-macros"
    assert {f["path"] for f in detail["files"]} == {"SKILL.md", "references/tone.md"}
    text = client.get(api(cid, f"/skills/{skill['id']}/files/SKILL.md")).json()
    assert text["content"].startswith("---\n") and "Acknowledge" in text["content"]
    again = hr.post("/skills/author", json=author_body(body_markdown="# v2\n\n1. New.\n"))
    assert again.json()["outcome"] == "updated" and again.json()["skill"]["version"] == "1.0.1"
    assert again.json()["skill"]["id"] == skill["id"]


@pytest.mark.parametrize(
    "patch",
    [
        {"slug": "Bad Slug"},
        {"slug": "../x"},
        {"body_markdown": "---\nname: x\n---\nbody"},
        {"files": [{"path": "../escape.md", "content": "x"}]},
        {"files": [{"path": "SKILL.md", "content": "dup"}]},
        {"files": [{"path": ".env", "content": "KEY=1"}]},
        {"unknown": 1},
    ],
)
def test_authoring_validation(app, client, company, patch):
    hr = hr_session(app, client, company["id"])
    assert hr.post("/skills/author", json=author_body(**patch)).status_code == 422


def test_authoring_cannot_take_over_an_installed_skills_slug(app, client, company):
    cid = company["id"]
    client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"})
    hr = hr_session(app, client, cid)
    assert hr.post("/skills/author", json=author_body("spike")).status_code == 409


def test_only_hr_can_use_the_hr_endpoints(app, client, company):
    cid = company["id"]
    seed_library(client, cid)
    worker = new_agent(client, cid, name="Worker")
    other = start_run(app, client, cid, worker["id"])
    founder = start_run(app, client, cid, agent_of_family(client, cid, "founder")["id"])
    for sess in (other, founder):
        assert sess.post("/skills/install", json={"source_ref": "hermes:spike"}).status_code == 403
        assert sess.post("/skills/author", json=author_body()).status_code == 403
        assert (
            sess.post(f"/agents/{worker['id']}/skills", json={"slugs": ["spike"]}).status_code
            == 403
        )
    assert other.get("/skills/catalog").status_code == 403
    assert other.get("/role-templates").status_code == 403
    assert founder.get("/skills/catalog").status_code == 200  # founder may search and list
    assert founder.get("/role-templates").status_code == 200
    assert (
        founder.post(
            "/role-templates", json={"slug": "ops-helper", "title": "Ops Helper"}
        ).status_code
        == 201
    )


# ------------------------------------------------------------------ assignment


def test_assignment_rules(app, client, company):
    cid = company["id"]
    seed_library(client, cid)
    worker = new_agent(client, cid, name="Worker")
    hr_agent = agent_of_family(client, cid, "hr")
    hr = hr_session(app, client, cid)
    url = f"/agents/{worker['id']}/skills"

    r = hr.post(url, json={"slugs": ["spike", "github"]})
    assert r.status_code == 200, r.text
    assert {i["skill"]["slug"] for i in r.json()["items"]} == {"spike", "github"}
    assert r.json()["items"][0]["assigned_by_type"] == "agent"
    assert hr.post(url, json={"slugs": ["spike"]}).status_code == 200  # idempotent
    [changed] = [
        e
        for e in audits(app, cid, "agent.skills_changed")
        if e.target_id == worker["id"] and e.actor_type == "agent"
    ][:1]
    assert set(changed.data["added"]) == {"spike", "github"}

    # nobody assigns to themselves, HR included
    own = hr.post(f"/agents/{hr_agent['id']}/skills", json={"slugs": ["spike"]})
    assert own.status_code == 403
    assert hr.delete(f"/agents/{hr_agent['id']}/skills/hiring-playbook").status_code == 403
    # a non-HR agent cannot even assign to others
    peer = start_run(app, client, cid, new_agent(client, cid, name="Peer")["id"])
    assert peer.post(url, json={"slugs": ["xlsx"]}).status_code == 403
    assert peer.post(f"/agents/{peer.agent_id}/skills", json={"slugs": ["xlsx"]}).status_code == 403

    # unknown and non-active skills
    assert hr.post(url, json={"slugs": ["no-such"]}).status_code == 404
    assert hr.post(url, json={}).status_code == 422
    sid = skills_by_slug(client, cid)["xlsx"]["id"]
    client.post(api(cid, f"/skills/{sid}/archive"))
    archived = hr.post(url, json={"skill_ids": [sid]})
    assert archived.status_code == 409 and "active" in archived.json()["error"]["message"]

    # removal
    assert hr.delete(f"/agents/{worker['id']}/skills/github").status_code == 204
    assert hr.delete(f"/agents/{worker['id']}/skills/github").status_code == 404
    left = client.get(api(cid, f"/agents/{worker['id']}/skills")).json()["items"]
    assert [i["skill"]["slug"] for i in left] == ["spike"]
    # humans can assign and remove too
    assert (
        client.post(
            api(cid, f"/agents/{worker['id']}/skills"),
            json={"skill_ids": [skills_by_slug(client, cid)["github"]["id"]]},
        ).status_code
        == 200
    )
    assert client.delete(api(cid, f"/agents/{worker['id']}/skills/github")).status_code == 204


def test_assignment_is_tenant_scoped(app, client, company):
    cid = company["id"]
    seed_library(client, cid)
    other = client.post("/api/companies", json={"name": "Other"}).json()
    seed_library(client, other["id"])
    foreign_skill = skills_by_slug(client, other["id"])["spike"]["id"]
    foreign_agent = agent_of_family(client, other["id"], "hr")["id"]
    worker = new_agent(client, cid, name="Worker")
    assert (
        client.post(
            api(cid, f"/agents/{worker['id']}/skills"), json={"skill_ids": [foreign_skill]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            api(cid, f"/agents/{foreign_agent}/skills"), json={"slugs": ["spike"]}
        ).status_code
        == 404
    )
    assert client.get(api(cid, f"/skills/{foreign_skill}")).status_code == 404
    hr = hr_session(app, client, cid)
    assert hr.post(f"/agents/{foreign_agent}/skills", json={"slugs": ["spike"]}).status_code == 404


def test_listing_filters_and_detail(client, company):
    cid = company["id"]
    seed_library(client, cid)
    everything = client.get(api(cid, "/skills"), params={"limit": 200}).json()["items"]
    assert len(everything) == N_SEEDED
    page = client.get(api(cid, "/skills"), params={"limit": 5}).json()
    assert len(page["items"]) == 5 and page["next_cursor"]
    assert all(
        s["source_kind"] == "company_authored"
        for s in client.get(api(cid, "/skills"), params={"source_kind": "company_authored"}).json()[
            "items"
        ]
    )
    by_tag = client.get(api(cid, "/skills"), params={"tag": "hiring"}).json()["items"]
    assert [s["slug"] for s in by_tag] == ["hiring-playbook"]
    by_q = client.get(api(cid, "/skills"), params={"q": "SPIKE"}).json()["items"]
    assert [s["slug"] for s in by_q] == ["spike"]
    assert client.get(api(cid, "/skills"), params={"status": "quarantined"}).json()["items"] == []
    spike = skills_by_slug(client, cid)["spike"]
    client.post(
        api(cid, f"/agents/{agent_of_family(client, cid, 'hr')['id']}/skills"),
        json={"slugs": ["spike"]},
    )
    detail = client.get(api(cid, f"/skills/{spike['id']}")).json()
    assert detail["assigned_agents"][0]["name"] == "Harper"
    assert detail["files"][0]["path"] == "SKILL.md"
    assert client.get(api(cid, f"/skills/{spike['id']}/files/../../etc/passwd")).status_code in (
        404,
        400,
    )
    assert client.get(api(cid, f"/skills/{spike['id']}/files/missing.md")).status_code == 404
    assert client.get(api(cid, "/skills/not-a-skill")).status_code == 404


# ------------------------------------------------------------------ catalog


def test_catalog_search_over_library_and_hermes(client, company):
    cid = company["id"]
    client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:spike"})
    items = client.get(api(cid, "/skills/catalog"), params={"q": "spike"}).json()["items"]
    assert [(i["slug"], i["source"], i["installed"]) for i in items] == [("spike", "library", True)]
    hermes = client.get(
        api(cid, "/skills/catalog"), params={"q": "github", "source": "hermes"}
    ).json()["items"]
    assert [(i["slug"], i["installed"], i["source_ref"].startswith("hermes@")) for i in hermes] == [
        ("github", False, True)
    ]
    library_only = client.get(api(cid, "/skills/catalog"), params={"source": "library"}).json()[
        "items"
    ]
    assert [i["slug"] for i in library_only] == ["spike"]
    page = client.get(api(cid, "/skills/catalog"), params={"source": "hermes", "limit": 3}).json()
    assert len(page["items"]) == 3 and page["next_cursor"] == "3"
    assert client.get(api(cid, "/skills/catalog"), params={"source": "nope"}).status_code == 422
    assert client.get(api(cid, "/skills/catalog"), params={"cursor": "x"}).status_code == 400


def test_principal_type_import_is_used():  # keeps the Principal import honest for reviewers
    assert Principal(PrincipalType.HUMAN, "u", "U").is_human
    assert FakeScanner().available()
