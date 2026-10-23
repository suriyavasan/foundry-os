"""GitHub skill source: discovery, commit pinning, license and size rules. Fixtures only."""

from __future__ import annotations

import httpx
import pytest

from app.skills.blobstore import MAX_FILES, SkillRejected
from app.skills.github import GithubClient, GithubError, GithubNotFound, GithubRateLimited
from app.skills.refs import BadSourceRef, GithubRef, HermesRef, parse_source_ref
from tests.helpers import FakeGithub, gh_skill_md

SHA = "a" * 40


@pytest.fixture
def gh() -> FakeGithub:
    fake = FakeGithub()
    fake.add_repo(
        "acme/skills",
        {
            "skills/pdf/SKILL.md": gh_skill_md("pdf"),
            "skills/pdf/references/forms.md": "forms",
            "skills/pdf/scripts/run.sh": "echo hi\n",
            "skills/docx/SKILL.md": gh_skill_md("docx"),
            "README.md": "readme",
        },
        commit=SHA,
        modes={"skills/pdf/scripts/run.sh": "100755"},
    )
    return fake


def test_source_ref_parsing():
    assert parse_source_ref("hermes:claude-design") == HermesRef("claude-design")
    assert parse_source_ref("hermes@db1fa153:skills/creative/claude-design") == HermesRef(
        "skills/creative/claude-design"
    )
    assert parse_source_ref("github:acme/skills@v1:skills/pdf") == GithubRef(
        "acme", "skills", "v1", "skills/pdf"
    )
    assert parse_source_ref("github:acme/skills") == GithubRef("acme", "skills", None, "")
    assert parse_source_ref("https://github.com/acme/skills/tree/main/skills/pdf") == GithubRef(
        "acme", "skills", "main", "skills/pdf"
    )
    assert parse_source_ref("github:acme/skills@abc:x").canonical(SHA) == (
        f"github:acme/skills@{SHA}:x"
    )
    for bad in (
        "",
        "npm:left-pad",
        "github:acme",
        "github:a/b:../x",
        "github:a/b:/etc",
        "hermes:../x",
    ):
        with pytest.raises(BadSourceRef):
            parse_source_ref(bad)


def test_resolve_commit_and_list_skill_dirs(gh):
    client = gh.client()
    assert client.resolve_commit("acme", "skills", None) == SHA  # default branch
    assert client.resolve_commit("acme", "skills", "v1") == SHA
    assert client.list_skill_paths("acme", "skills", SHA) == ["skills/docx", "skills/pdf"]
    with pytest.raises(GithubNotFound):
        client.resolve_commit("acme", "skills", "no-such-branch")
    with pytest.raises(GithubNotFound):
        client.resolve_commit("nobody", "nothing", None)


def test_download_is_pinned_to_the_commit_and_never_sends_the_token_to_raw(gh):
    client = gh.client(token="ghp_SECRET_TOKEN_VALUE")
    dl = client.download_skill("acme", "skills", SHA, "skills/pdf")
    assert {f.path for f in dl.files} == {"SKILL.md", "references/forms.md", "scripts/run.sh"}
    assert {f.path: f.executable for f in dl.files}["scripts/run.sh"] is True
    raw = [(h, p, hd) for h, p, hd in gh.requests if h == "raw.githubusercontent.com"]
    assert raw and all(f"/{SHA}/" in p for _, p, _ in raw)  # immutable sha, not a branch
    assert all("authorization" not in {k.lower() for k in hd} for _, _, hd in raw)
    api = [hd for h, _, hd in gh.requests if h == "api.github.com"]
    assert api and all(hd.get("authorization") == "Bearer ghp_SECRET_TOKEN_VALUE" for hd in api)


def test_download_refuses_symlinks_submodules_and_oversized_skills(gh):
    gh.add_repo(
        "acme/linky",
        {"SKILL.md": gh_skill_md("linky"), "link": "SKILL.md"},
        commit=SHA,
        modes={"link": "120000"},
    )
    with pytest.raises(SkillRejected, match="symlink"):
        gh.client().download_skill("acme", "linky", SHA, "")
    gh.add_repo(
        "acme/many",
        {"SKILL.md": gh_skill_md("many"), **{f"f{i}.md": "x" for i in range(MAX_FILES)}},
        commit=SHA,
    )
    with pytest.raises(SkillRejected, match="limits"):
        gh.client().download_skill("acme", "many", SHA, "")
    n_before = len([r for r in gh.requests if r[0] == "raw.githubusercontent.com"])
    assert n_before == 0  # limits were enforced from the tree listing: no content was fetched
    gh.add_repo("acme/big", {"SKILL.md": b"x" * (3 * 1024 * 1024)}, commit=SHA)
    with pytest.raises(SkillRejected):
        gh.client().download_skill("acme", "big", SHA, "")


def test_download_requires_a_full_commit_sha(gh):
    with pytest.raises(GithubError, match="commit sha"):
        gh.client().download_skill("acme", "skills", "main", "skills/pdf")


def test_missing_skill_md_is_not_found(gh):
    with pytest.raises(GithubNotFound):
        gh.client().download_skill("acme", "skills", SHA, "skills/nothing")


def test_rate_limit_and_errors_are_reported_without_leaking_the_token(gh):
    gh.rate_limited = True
    client = gh.client(token="ghp_SECRET_TOKEN_VALUE")
    with pytest.raises(GithubRateLimited) as exc:
        client.resolve_commit("acme", "skills", None)
    assert "ghp_" not in str(exc.value)

    def boom(request):
        raise httpx.ConnectError("no route to host ghp_SECRET_TOKEN_VALUE")

    broken = GithubClient(httpx.Client(transport=httpx.MockTransport(boom)), token="ghp_X")
    with pytest.raises(GithubError) as exc2:
        broken.get_repo("a", "b")
    assert "unreachable" in str(exc2.value) and "SECRET" not in str(exc2.value)


def test_code_search_needs_a_token(gh):
    with pytest.raises(GithubError, match="TOKEN"):
        gh.client().search_skill_files("pdf")
    gh.code_items = [
        {
            "path": "skills/pdf/SKILL.md",
            "repository": {"full_name": "acme/skills", "description": "d"},
        }
    ]
    hits = gh.client(token="t").search_skill_files("pdf")
    assert hits[0].repo == "acme/skills" and hits[0].skill_dir == "skills/pdf"


def test_repo_search_uses_the_agent_skills_topic(gh):
    gh.search_items = [
        {
            "full_name": "acme/skills",
            "description": "skills",
            "stargazers_count": 12,
            "default_branch": "main",
            "license": {"spdx_id": "MIT"},
            "html_url": "https://github.com/acme/skills",
        }
    ]
    hits = gh.client().search_repositories("pdf tools")
    assert hits[0].stars == 12 and hits[0].license == "MIT"
