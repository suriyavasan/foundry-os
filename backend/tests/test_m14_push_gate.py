"""M14 WP-B: the push gate. Everything that is about to leave the machine is scanned (every
commit's ADDED lines, not the net diff), before the local merge into the work branch and before
the push. Findings carry kinds, counts and paths, never the matched text."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.schemas.repository import RepoPolicy
from app.services import repo_git, repo_scan
from app.services.repo_scan import PushFinding
from tests.m14_git_remote import run_git
from tests.m14_helpers import (
    LOGIN,
    TOKEN,
    github_world,  # noqa: F401  (fixture)
)
from tests.m14_sync_helpers import harness_factory  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NAME = f"{LOGIN}/app"
SECRET = (
    "github_pat_11GATEGATEGATE0_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6a7B8c9D0e1"
)
STORED = "correct-horse-battery-staple-9137"  # a company credential that has no known shape
IDENT = ["-c", "user.name=Dev", "-c", "user.email=dev@example.test"]


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "r"
    path.mkdir()
    run_git("init", "-q", "-b", "main", cwd=path)
    run_git(*IDENT, "commit", "-q", "--allow-empty", "-m", "base", cwd=path)
    return path


def commit(repo: Path, files: dict[str, str | None], message: str = "change") -> str:
    for name, content in files.items():
        target = repo / name
        if content is None:
            run_git("rm", "-q", "-f", name, cwd=repo)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        run_git("add", name, cwd=repo)
    run_git(*IDENT, "commit", "-q", "--allow-empty", "-m", message, cwd=repo)
    return run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def scan(repo, base="main~0", head="HEAD", *, policy=None, own=(), internal=(), exclude=()):
    base_sha = run_git("rev-parse", base, cwd=repo).stdout.strip() if base else None
    return repo_scan.scan_range(
        repo,
        base_sha,
        head,
        policy=policy or RepoPolicy(),
        own_secrets=own,
        internal=list(internal),
        exclude=exclude,
    )


def kinds(findings) -> set[str]:
    return {f.kind for f in findings}


def start(repo) -> str:
    return run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()


# ------------------------------------------------------------------ content


def test_a_secret_added_and_removed_in_an_intermediate_commit_still_blocks(repo):
    base = start(repo)
    commit(repo, {"config.py": f'KEY = "{SECRET}"\n'}, "add key")
    commit(repo, {"config.py": "KEY = None\n"}, "remove key")
    # the net diff is clean ...
    net = run_git("diff", base, "HEAD", cwd=repo).stdout
    assert SECRET not in net
    # ... but the history that would be pushed contains it
    found = scan(repo, base)
    assert "secret" in kinds(found)
    [f] = [f for f in found if f.kind == "secret"]
    assert f.paths == ("config.py",) and f.count >= 1 and f.blocking


def test_a_stored_credential_value_blocks_even_without_a_known_shape(repo):
    base = start(repo)
    commit(repo, {"notes.txt": f"the password is {STORED}\n"})
    assert scan(repo, base) == []
    found = scan(repo, base, own=[STORED])
    assert kinds(found) == {"own_token"} and found[0].paths == ("notes.txt",)


def test_a_secret_in_a_commit_message_blocks(repo):
    base = start(repo)
    commit(repo, {"a.txt": "a\n"}, f"fix: use {SECRET} for the build")
    found = scan(repo, base)
    assert "secret" in kinds(found)
    assert any("(commit message)" in f.paths for f in found)


def test_internal_only_memory_blocks(repo):
    base = start(repo)
    passage = (
        "our confidential roadmap includes an acquisition of the largest competitor next spring"
    )
    commit(repo, {"notes.md": f"Remember: {passage}.\n"})
    words = passage.lower().split()
    found = scan(repo, base, internal=[words])
    assert kinds(found) == {"internal_memory"}


def test_email_and_phone_are_reported_but_never_block(repo):
    base = start(repo)
    commit(repo, {"AUTHORS": "Alice <alice@example.org>\nCall +1 415 555 0100 or 415-555-0188\n"})
    found = scan(repo, base)
    assert kinds(found) <= {"email", "phone"} and found
    assert repo_scan.blocking(found) == []


def test_the_text_of_a_finding_is_never_in_the_finding(repo):
    base = start(repo)
    commit(repo, {"a.py": f'T = "{SECRET}"\nP = "{STORED}"\n'})
    found = scan(repo, base, own=[STORED])
    blob = json.dumps([f.as_dict() for f in found]) + repr(found)
    assert found and SECRET not in blob and STORED not in blob
    assert "a1B2c3D4" not in blob


def test_a_clean_history_has_no_findings(repo):
    base = start(repo)
    commit(repo, {"src/app.py": "print('hello')\n", "README.md": "# app\n"}, "feat: app")
    assert scan(repo, base) == []
    assert scan(repo, run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()) == []  # empty range


def test_a_secret_resolved_into_a_merge_is_found(repo):
    """An 'evil merge': the conflict resolution itself adds a secret that neither side has."""
    commit(repo, {"f.txt": "one\n"})
    base = start(repo)
    run_git("checkout", "-q", "-b", "side", cwd=repo)
    commit(repo, {"f.txt": "side\n"}, "side")
    run_git("checkout", "-q", "main", cwd=repo)
    commit(repo, {"f.txt": "main\n"}, "main")
    proc = run_git("merge", "--no-commit", "side", cwd=repo, check=False)
    assert proc.returncode != 0
    (repo / "f.txt").write_text(f"resolved {SECRET}\n")
    run_git("add", "f.txt", cwd=repo)
    run_git(*IDENT, "commit", "-q", "-m", "Merge side", cwd=repo)
    assert "secret" in kinds(scan(repo, base))


# ------------------------------------------------------------------ file names and paths


def test_dot_env_blocks_but_the_example_templates_do_not(repo):
    base = start(repo)
    commit(
        repo,
        {
            ".env": "X=1\n",
            "deploy/id_rsa": "x\n",
            "certs/server.pem": "x\n",
            "creds/credentials.json": "{}\n",
            "a.key": "x\n",
        },
    )
    [f] = [f for f in scan(repo, base) if f.kind == "blocked_file"]
    assert set(f.paths) == {
        ".env",
        "deploy/id_rsa",
        "certs/server.pem",
        "creds/credentials.json",
        "a.key",
    }
    base2 = start(repo)
    commit(
        repo,
        {
            ".env.example": "X=\n",
            ".env.sample": "X=\n",
            ".env.template": "X=\n",
            "keys.md": "docs\n",
        },
    )
    assert scan(repo, base2) == []
    commit(repo, {".env.production": "X=1\n"})
    assert kinds(scan(repo, base2)) == {"blocked_file"}


def test_a_file_that_was_added_and_removed_again_is_still_in_the_pushed_history(repo):
    base = start(repo)
    commit(repo, {".env": f"T={SECRET}\n"}, "add env")
    commit(repo, {".env": None}, "remove env")
    assert kinds(scan(repo, base)) == {"secret", "blocked_file"}


def test_workflow_changes_are_protected_unless_the_owner_allows_them(repo):
    base = start(repo)
    commit(repo, {".github/workflows/ci.yml": "on: push\n", ".gitmodules": "[submodule]\n"})
    found = scan(repo, base)
    [f] = [f for f in found if f.kind == "protected_path"]
    assert set(f.paths) == {".github/workflows/ci.yml", ".gitmodules"}
    lifted = scan(repo, base, policy=RepoPolicy(allow_workflow_changes=True))
    [f] = [f for f in lifted if f.kind == "protected_path"]
    assert f.paths == (".gitmodules",)  # only the workflow globs are lifted
    # deleting a workflow is a change to it as well
    base2 = start(repo)
    commit(repo, {".github/workflows/ci.yml": None})
    assert kinds(scan(repo, base2)) == {"protected_path"}


def test_custom_protected_paths_use_globs(repo):
    base = start(repo)
    commit(repo, {"infra/prod/main.tf": "x\n", "infra/dev/main.tf": "x\n", "src/a.py": "x\n"})
    policy = RepoPolicy(protected_paths=["infra/prod/**", "**/*.lock"])
    [f] = scan(repo, base, policy=policy)
    assert f.kind == "protected_path" and f.paths == ("infra/prod/main.tf",)
    commit(repo, {"vendor/deep/x.lock": "x\n"})
    [f] = scan(repo, base, policy=policy)
    assert set(f.paths) == {"infra/prod/main.tf", "vendor/deep/x.lock"}


# ------------------------------------------------------------------ sizes


def test_a_file_over_the_per_file_limit_blocks(repo):
    base = start(repo)
    commit(repo, {"big.bin": "x" * 5000, "small.txt": "s\n"})
    [f] = scan(repo, base, policy=RepoPolicy(max_file_bytes=1024))
    assert f.kind == "file_too_large" and f.paths == ("big.bin",)
    assert scan(repo, base, policy=RepoPolicy(max_file_bytes=10_000)) == []


def test_a_push_over_the_total_limit_blocks(repo):
    base = start(repo)
    commit(repo, {"a.dat": "a" * 700_000})
    commit(repo, {"b.dat": "b" * 700_000})
    found = scan(repo, base, policy=RepoPolicy(max_push_bytes=1024 * 1024))
    assert kinds(found) == {"push_too_large"}


def test_more_than_five_hundred_commits_in_one_push_block(repo):
    base = start(repo)
    stream = ["commit refs/heads/main\nmark :1\ncommitter T <t@t> 1700000000 +0000\ndata 1\nx\n"]
    # a fast-import stream: 501 empty commits on top of main
    lines = []
    for i in range(501):
        lines.append(
            f"commit refs/heads/main\ncommitter T <t@t> {1700000000 + i} +0000\ndata 2\nc\n"
            + (f"from {base}\n" if i == 0 else "")
            + "\n"
        )
    del stream
    run_git("fast-import", "--quiet", cwd=repo, input="".join(lines))
    found = scan(repo, base)
    assert kinds(found) == {"push_too_large"} and "500 commits" in found[0].description


# ------------------------------------------------------------------ what is not ours to scan


def test_commits_made_by_people_on_github_are_not_scanned(repo):
    """origin/<default> and origin/<work> hold commits people made (and pushed); once merged into
    the work branch they are excluded from the scan by `exclude`."""
    commit(repo, {"README.md": "r\n"})
    base = start(repo)
    run_git("checkout", "-q", "-b", "human", cwd=repo)
    commit(repo, {"human.py": f'K = "{SECRET}"\n'}, "a person's commit")
    human = start(repo)
    run_git("update-ref", "refs/remotes/origin/main", human, cwd=repo)
    run_git("checkout", "-q", "main", cwd=repo)
    commit(repo, {"mine.py": "x = 1\n"}, "mine")
    run_git(*IDENT, "merge", "--no-ff", "-q", "-m", "Merge main", human, cwd=repo)
    assert "secret" in kinds(scan(repo, base))  # without the exclusion it would be caught
    assert scan(repo, base, exclude=["refs/remotes/origin/main"]) == []
    # an exclusion that does not exist is ignored, not an error
    assert "secret" in kinds(scan(repo, base, exclude=["refs/remotes/origin/nope"]))


def test_a_whole_history_scan_has_no_base(repo):
    commit(repo, {"old.txt": f"{SECRET}\n"})
    commit(repo, {"old.txt": "clean\n"})
    assert "secret" in kinds(scan(repo, None))


def test_findings_serialise_without_text():
    f = PushFinding("secret", "high", "Credential-shaped strings: github token", 2, ("a.py",))
    assert f.blocking and f.as_dict() == {
        "kind": "secret",
        "severity": "high",
        "description": "Credential-shaped strings: github token",
        "count": 2,
        "paths": ["a.py"],
    }
    assert not PushFinding("email", "low", "x").blocking


# ------------------------------------------------------------------ at push time


def test_a_blocked_push_sets_the_state_raises_an_alert_and_a_reset_unblocks(harness_factory):
    h = harness_factory(merge_mode="pr_auto_merge")
    h.sync()
    # a secret reaches the work branch without passing the merge gate (a person edited the clone)
    h.git("config", "user.email", "x@x")
    (h.repo / "leak.py").write_text(f'K = "{SECRET}"\n')
    h.git("add", "leak.py")
    h.git("-c", "user.name=x", "commit", "-q", "-m", "oops")
    h.sync(advance=1)
    b = h.binding()
    assert b.sync_state == "push_blocked"
    assert h.remote_sha(h.work) is None and h.pr() is None  # nothing left the machine
    [alert] = h.alerts("github_push_blocked")
    assert alert.severity == "critical" and SECRET not in (alert.body or "")
    [event] = h.audit("repository.push_blocked")
    dumped = json.dumps(event.data)
    assert SECRET not in dumped and "leak.py" in dumped
    assert TOKEN not in dumped
    # still blocked on the next sync, with no network write
    h.sync(advance=600)
    assert h.binding().sync_state == "push_blocked"
    # the owner resets the integration branch: the old head is kept as a rescue branch
    repo_git.reset_integration(
        h.repo, h.binding(), to="default_branch", rescue_name="foundry/rescue-20261027"
    )
    assert "leak.py" in h.git("show", "--stat", "--format=", "foundry/rescue-20261027")
    _ws, fin = h.merge_run({"clean.txt": "clean\n"})
    assert fin.merged
    h.sync(advance=1)
    assert h.binding().sync_state == "idle" and h.pr() is not None


def test_the_company_credentials_are_part_of_the_push_gate(harness_factory):
    """The GitHub token itself (a stored credential) must never be pushed."""
    h = harness_factory()
    h.sync()
    (h.repo / "notes.txt").write_text(f"token: {TOKEN}\n")
    h.git("add", "notes.txt")
    h.git("-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "notes")
    h.sync(advance=1)
    assert h.binding().sync_state == "push_blocked" and h.remote_sha(h.work) is None
