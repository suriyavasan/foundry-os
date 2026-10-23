"""Materialization: exactly the assigned ACTIVE skills land in HERMES_HOME/skills."""

from __future__ import annotations

import os
import stat

import pytest

from app.services import skills as skills_service
from app.skills.blobstore import hash_dir
from tests.helpers import agent_of_family, api, new_agent, seed_library, skills_by_slug, write_skill


@pytest.fixture
def skills_env_override(fake_skills_env):
    return fake_skills_env


def materialize(app, cid, agent_id, home):
    with app.state.session_factory() as db:
        return skills_service.materialize_for_agent(
            db, cid, agent_id, home, blobs=app.state.skills.blobs
        )


@pytest.fixture
def world(client, company, fake_runtime):
    cid = company["id"]
    seed_library(client, cid)
    worker = new_agent(client, cid, name="Worker")
    return cid, worker


def assign(client, cid, agent_id, *slugs):
    r = client.post(api(cid, f"/agents/{agent_id}/skills"), json={"slugs": list(slugs)})
    assert r.status_code == 200, r.text


def test_writes_exactly_the_assigned_active_skills(app, client, world, tmp_path):
    cid, worker = world
    assign(client, cid, worker["id"], "spike", "github")
    home = tmp_path / "home"
    result = materialize(app, cid, worker["id"], home)
    assert sorted(result.written) == ["github", "spike"] and result.skipped == []
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["github", "spike"]
    blobs = app.state.skills.blobs
    skills = skills_by_slug(client, cid)
    for slug in ("github", "spike"):
        assert hash_dir(home / "skills" / slug) == skills[slug]["blob_sha256"]
        assert (home / "skills" / slug / "SKILL.md").is_file()
    assert blobs.verify(skills["spike"]["blob_sha256"])


def test_removes_stale_and_unassigned_skills_and_leftovers(app, client, world, tmp_path):
    cid, worker = world
    assign(client, cid, worker["id"], "spike", "github")
    home = tmp_path / "home"
    materialize(app, cid, worker["id"], home)
    # later: github removed, plus junk Hermes or a crashed run left behind
    client.delete(api(cid, f"/agents/{worker['id']}/skills/github"))
    (home / "skills" / "not-assigned").mkdir()
    (home / "skills" / "not-assigned" / "SKILL.md").write_text("x")
    (home / "skills" / ".tmp-spike-dead").mkdir()
    (home / "skills" / "stray.txt").write_text("x")
    result = materialize(app, cid, worker["id"], home)
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["spike"]
    assert sorted(result.removed) == [".tmp-spike-dead", "github", "not-assigned", "stray.txt"]
    assert result.unchanged == ["spike"] and result.written == []


def test_replaces_a_modified_skill_directory_atomically(app, client, world, tmp_path):
    cid, worker = world
    assign(client, cid, worker["id"], "spike")
    home = tmp_path / "home"
    materialize(app, cid, worker["id"], home)
    target = home / "skills" / "spike"
    (target / "SKILL.md").write_text("tampered by the agent")
    (target / "extra.md").write_text("extra")
    result = materialize(app, cid, worker["id"], home)
    assert result.written == ["spike"]
    sha = skills_by_slug(client, cid)["spike"]["blob_sha256"]
    assert hash_dir(target) == sha and not (target / "extra.md").exists()
    assert [p.name for p in (home / "skills").iterdir()] == ["spike"]  # no temp dirs left


def test_a_symlink_where_a_skill_should_be_is_replaced(app, client, world, tmp_path):
    cid, worker = world
    assign(client, cid, worker["id"], "spike")
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "SKILL.md").write_text("elsewhere")
    os.symlink(secret, home / "skills" / "spike")
    materialize(app, cid, worker["id"], home)
    assert not (home / "skills" / "spike").is_symlink()
    assert (secret / "SKILL.md").read_text() == "elsewhere"  # the link target was not written into


def test_quarantined_archived_and_foreign_skills_never_materialize(
    app, client, world, tmp_path, fake_scanner
):
    cid, worker = world
    from tests.test_agent_api import start_run

    fake_scanner.verdicts["shady"] = "dangerous"
    hr = start_run(app, client, cid, agent_of_family(client, cid, "hr")["id"])
    shady = hr.post(
        "/skills/author",
        json={"slug": "shady", "name": "Shady", "description": "d", "body_markdown": "# s\n"},
    ).json()["skill"]
    assert shady["status"] == "quarantined"
    assign(client, cid, worker["id"], "spike", "github", "xlsx")
    # release then re-quarantine is not possible; simulate an assignment made while active
    released = client.post(api(cid, f"/skills/{shady['id']}/unquarantine"))
    assert released.status_code == 200
    assign(client, cid, worker["id"], "shady")
    skills = skills_by_slug(client, cid)
    client.post(api(cid, f"/skills/{skills['github']['id']}/archive"))
    # a skill quarantined after assignment (e.g. rescanned) is dropped at the next run
    from sqlalchemy import update

    from app.models.skill import Skill

    with app.state.session_factory() as db:
        db.execute(update(Skill).where(Skill.slug == "xlsx").values(status="quarantined"))
        db.commit()
    home = tmp_path / "home"
    result = materialize(app, cid, worker["id"], home)
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["shady", "spike"]
    assert sorted(result.written) == ["shady", "spike"]

    # another company's skill with the same slug is never used for this agent
    other = client.post("/api/companies", json={"name": "Other"}).json()
    seed_library(client, other["id"])
    again = materialize(app, other["id"], worker["id"], tmp_path / "home2")
    assert again.written == [] and not any((tmp_path / "home2" / "skills").iterdir())


def test_missing_or_corrupt_blobs_are_skipped_not_copied(app, client, world, tmp_path):
    cid, worker = world
    assign(client, cid, worker["id"], "spike", "github")
    skills = skills_by_slug(client, cid)
    blobs = app.state.skills.blobs
    (blobs.path(skills["spike"]["blob_sha256"]) / "SKILL.md").write_text("corrupted")
    import shutil

    shutil.rmtree(blobs.path(skills["github"]["blob_sha256"]))
    home = tmp_path / "home"
    result = materialize(app, cid, worker["id"], home)
    assert {s["slug"]: s["reason"] for s in result.skipped} == {
        "spike": "blob_corrupt",
        "github": "blob_missing",
    }
    assert list((home / "skills").iterdir()) == []


def test_executable_scripts_keep_their_bit_and_nothing_else_does(
    app, client, world, tmp_path, fake_runtime
):
    cid, worker = world
    write_skill(
        fake_runtime / "skills" / "x",
        "scripted",
        files={"scripts/run.sh": "echo hi\n", "notes.md": "n"},
    )
    (fake_runtime / "skills" / "x" / "scripted" / "scripts" / "run.sh").chmod(0o755)
    (fake_runtime / "skills" / "x" / "scripted" / "notes.md").chmod(0o755)
    app.state.skills.hermes.skills(refresh=True)
    assert (
        client.post(api(cid, "/skills/install"), json={"source_ref": "hermes:scripted"}).status_code
        == 201
    )
    assign(client, cid, worker["id"], "scripted")
    home = tmp_path / "home"
    materialize(app, cid, worker["id"], home)
    base = home / "skills" / "scripted"
    assert (base / "scripts" / "run.sh").stat().st_mode & stat.S_IXUSR
    assert not (base / "notes.md").stat().st_mode & stat.S_IXUSR


def test_agent_with_no_skills_gets_an_empty_directory(app, client, world, tmp_path):
    cid, worker = world
    home = tmp_path / "home"
    (home / "skills" / "old").mkdir(parents=True)
    result = materialize(app, cid, worker["id"], home)
    assert list((home / "skills").iterdir()) == [] and result.removed == ["old"]
