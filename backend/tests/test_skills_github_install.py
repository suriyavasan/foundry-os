"""Installing skills from GitHub through the service/API: pinning, trust, licenses, fail closed."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.skill import Skill, SkillBlob
from tests.helpers import FakeGithub, agent_of_family, api, gh_skill_md, new_agent, skills_by_slug
from tests.test_agent_api import start_run

TOKEN = "ghp_SECRET_TOKEN_VALUE"
SHA1 = "1" * 40
SHA2 = "2" * 40
MIT_TEXT = (
    "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy"
)


@pytest.fixture
def gh() -> FakeGithub:
    fake = FakeGithub()
    fake.add_repo(
        "acme/skills",
        {
            "skills/pdf/SKILL.md": gh_skill_md("pdf"),
            "skills/pdf/references/forms.md": "forms",
            "skills/docx/SKILL.md": gh_skill_md("docx", tags="word, documents"),
        },
        commit=SHA1,
        license="Apache-2.0",
    )
    return fake


@pytest.fixture
def skills_env_override(fake_skills_env, gh):
    fake_skills_env.github = lambda: gh.client(token=TOKEN)
    return fake_skills_env


def install(client, cid, ref, **extra):
    return client.post(api(cid, "/skills/install"), json={"source_ref": ref, **extra})


def test_github_install_pins_the_commit_and_records_provenance(
    app, client, company, fake_scanner, gh
):
    cid = company["id"]
    r = install(client, cid, "github:acme/skills:skills/pdf")  # default branch -> resolved to a sha
    assert r.status_code == 201, r.text
    skill = r.json()["skill"]
    assert skill["slug"] == "pdf" and skill["status"] == "active"
    assert skill["source_kind"] == "github" and skill["trust"] == "community"
    assert skill["source_ref"] == f"github:acme/skills@{SHA1}:skills/pdf"
    assert skill["license"] == "Apache-2.0"  # no skill-level license: the repository's applies
    assert fake_scanner.calls[-1] == ("pdf", "acme/skills/skills/pdf")
    again = install(client, cid, f"github:acme/skills@{SHA1}:skills/pdf")
    assert again.status_code == 200 and again.json()["outcome"] == "unchanged"

    # upstream moves on: the same ref now resolves to a new commit and updates in place
    gh.add_repo(
        "acme/skills",
        {"skills/pdf/SKILL.md": gh_skill_md("pdf") + "\nMore.\n"},
        commit=SHA2,
        license="Apache-2.0",
    )
    updated = install(client, cid, "github:acme/skills:skills/pdf")
    assert updated.json()["outcome"] == "updated"
    assert updated.json()["skill"]["source_ref"] == f"github:acme/skills@{SHA2}:skills/pdf"
    assert updated.json()["skill"]["id"] == skill["id"]
    # everything we fetched came from an immutable commit path
    raw_paths = [p for h, p, _ in gh.requests if h == "raw.githubusercontent.com"]
    assert raw_paths and all(f"/{SHA1}/" in p or f"/{SHA2}/" in p for p in raw_paths)
    # the token reaches neither responses nor the audit trail
    with app.state.session_factory() as db:
        dumps = json.dumps([e.data | {"s": e.summary} for e in db.scalars(select(AuditEvent))])
        dumps += json.dumps(db.scalars(select(Skill.scan_report)).all())
    assert TOKEN not in dumps and TOKEN not in r.text and TOKEN not in updated.text


def test_trusted_and_community_repos_follow_the_policy(client, company, fake_scanner, gh):
    cid = company["id"]
    gh.add_repo(
        "anthropics/skills",
        {"tc/SKILL.md": gh_skill_md("caut"), "td/SKILL.md": gh_skill_md("bad")},
        commit=SHA1,
    )
    gh.add_repo(
        "evil/skills",
        {
            "cc/SKILL.md": gh_skill_md("c-caut"),
            "cd/SKILL.md": gh_skill_md("c-bad"),
            "ok/SKILL.md": gh_skill_md("c-ok"),
        },
        commit=SHA1,
    )
    fake_scanner.verdicts.update(
        {"caut": "caution", "bad": "dangerous", "c-caut": "caution", "c-bad": "dangerous"}
    )
    trusted_caution = install(client, cid, "github:anthropics/skills:tc").json()
    trusted_danger = install(client, cid, "github:anthropics/skills:td").json()
    community_caution = install(client, cid, "github:evil/skills:cc").json()
    community_danger = install(client, cid, "github:evil/skills:cd").json()
    community_ok = install(client, cid, "github:evil/skills:ok").json()
    assert (
        trusted_caution["skill"]["trust"] == "trusted"
        and trusted_caution["skill"]["status"] == "active"
    )
    assert trusted_danger["skill"]["status"] == "quarantined"
    assert community_caution["skill"]["status"] == "quarantined"
    assert community_danger["skill"]["status"] == "quarantined"
    assert community_ok["skill"]["status"] == "active"
    assert {"anthropics/skills/tc", "anthropics/skills/td", "evil/skills/cc"} <= {
        s for _, s in fake_scanner.calls
    }


@pytest.mark.parametrize(
    ("skill_license", "repo_license", "ok"),
    [
        ("MIT", None, True),  # skill-level license wins even when the repo has none
        ("Apache-2.0", "GPL-3.0", True),
        ("GPL-3.0", "MIT", False),  # a disallowed skill-level license is not rescued by the repo
        ("Proprietary. LICENSE.txt has complete terms", "MIT", False),
        (None, "MIT", True),  # falls back to the repository license
        (None, "BSD-3-Clause", True),
        (None, "GPL-3.0", False),
        (None, "AGPL-3.0", False),
        (None, None, False),  # nothing declares a license
    ],
)
def test_license_allowlist(app, client, company, gh, skill_license, repo_license, ok):
    cid = company["id"]
    gh.add_repo(
        "acme/lic",
        {"SKILL.md": gh_skill_md("lic", license=skill_license)},
        commit=SHA1,
        license=repo_license,
    )
    r = install(client, cid, "github:acme/lic")
    if ok:
        assert r.status_code == 201, r.text
    else:
        assert r.status_code == 422 and r.json()["error"]["code"] == "license_not_allowed"
        assert "lic" not in skills_by_slug(client, cid)
        with app.state.session_factory() as db:  # refused before anything was stored
            assert db.scalar(select(func.count(Skill.id))) == 0
            assert db.scalar(select(func.count(SkillBlob.sha256))) == 0


def test_license_file_in_the_skill_is_used(client, company, gh):
    cid = company["id"]
    gh.add_repo(
        "acme/lf", {"SKILL.md": gh_skill_md("lf"), "LICENSE": MIT_TEXT}, commit=SHA1, license=None
    )
    assert install(client, cid, "github:acme/lf").json()["skill"]["license"] == "MIT"
    gh.add_repo(
        "acme/lf2",
        {"SKILL.md": gh_skill_md("lf2"), "LICENSE": "All rights reserved."},
        commit=SHA1,
        license="MIT",
    )
    assert install(client, cid, "github:acme/lf2").status_code == 422


def test_no_scanner_means_no_github_import_and_no_network(app, client, company, fake_scanner, gh):
    cid = company["id"]
    fake_scanner._available = False
    r = install(client, cid, "github:acme/skills:skills/pdf")
    assert r.status_code == 503 and r.json()["error"]["code"] == "scanner_unavailable"
    assert gh.requests == []  # failed closed before touching GitHub


def test_upstream_problems(client, company, gh):
    cid = company["id"]
    assert install(client, cid, "github:nobody/nothing").status_code == 404
    assert install(client, cid, "github:acme/skills:skills/missing").status_code == 404
    assert install(client, cid, "github:acme/skills@no-such-branch:skills/pdf").status_code == 404
    gh.rate_limited = True
    r = install(client, cid, "github:acme/skills:skills/pdf")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error"
    assert TOKEN not in r.text


def test_oversized_github_skill_is_a_validation_error(client, company, gh):
    cid = company["id"]
    gh.add_repo("acme/big", {"SKILL.md": b"x" * (3 * 1024 * 1024)}, commit=SHA1)
    assert install(client, cid, "github:acme/big").status_code == 422


def test_custom_slug_avoids_collisions(client, company, gh):
    cid = company["id"]
    assert (
        install(client, cid, "github:acme/skills:skills/pdf", slug="acme-pdf").json()["skill"][
            "slug"
        ]
        == "acme-pdf"
    )
    assert install(client, cid, "github:acme/skills:skills/pdf", slug="Bad Slug").status_code == 422


def test_catalog_lists_github_skills_and_repositories(client, company, gh):
    cid = company["id"]
    items = client.get(
        api(cid, "/skills/catalog"), params={"source": "github", "q": "acme/skills"}
    ).json()["items"]
    assert {i["slug"] for i in items} == {"pdf", "docx"}
    assert all(i["source_ref"].startswith(f"github:acme/skills@{SHA1}:") for i in items)
    install(client, cid, items[0]["source_ref"])
    items = client.get(
        api(cid, "/skills/catalog"), params={"source": "github", "q": "acme/skills"}
    ).json()["items"]
    assert sorted(i["installed"] for i in items) == [False, True]

    gh.search_items = [
        {
            "full_name": "acme/skills",
            "description": "d",
            "stargazers_count": 3,
            "license": {"spdx_id": "MIT"},
            "html_url": "u",
        }
    ]
    gh.code_items = [{"path": "skills/pdf/SKILL.md", "repository": {"full_name": "acme/skills"}}]
    found = client.get(api(cid, "/skills/catalog"), params={"source": "github", "q": "pdf"}).json()[
        "items"
    ]
    assert {(i["item_type"], i["source_ref"]) for i in found} == {
        ("repository", "github:acme/skills"),
        ("skill", "github:acme/skills:skills/pdf"),
    }
    gh.rate_limited = True
    assert (
        client.get(api(cid, "/skills/catalog"), params={"source": "github", "q": "x"}).status_code
        == 502
    )


def test_hr_installs_through_the_agent_api_and_others_cannot(app, client, company):
    cid = company["id"]
    hr = start_run(app, client, cid, agent_of_family(client, cid, "hr")["id"])
    other = start_run(app, client, cid, new_agent(client, cid, name="Worker")["id"])
    assert (
        other.post(
            "/skills/install", json={"source_ref": "github:acme/skills:skills/pdf"}
        ).status_code
        == 403
    )
    assert (
        hr.post("/skills/install", json={"source_ref": "github:acme/skills:skills/pdf"}).status_code
        == 201
    )
    found = hr.get("/skills/catalog", params={"source": "github", "q": "acme/skills"})
    assert found.status_code == 200 and len(found.json()["items"]) == 2
