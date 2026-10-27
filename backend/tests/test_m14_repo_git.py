"""M14 WP-B: the git engine. Real git against the local smart-HTTP remote (tests/m14_git_remote.py);
no network beyond 127.0.0.1. The token must only ever travel in the environment of one git child
and in the HTTP Authorization header: never argv, never a file."""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import BACKEND_DIR
from app.services import repo_git
from app.services.repo_git import (
    GitAuthFailed,
    GitForbidden,
    GitNetworkError,
    GitNonFastForward,
    RemoteAuth,
    UnsafePush,
    assert_safe_push_argv,
)
from tests.m14_git_remote import run_git as plain_git
from tests.m14_helpers import (
    LOGIN,
    READONLY_TOKEN,
    TOKEN,
    bare_world,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

UNKNOWN_TOKEN = "github_pat_11NOSUCHTOKEN00_" + "q" * 60
REPO = f"{LOGIN}/app"
WORK = "foundry/app"


@pytest.fixture
def settings(make_settings, bare_world, tmp_path):  # noqa: F811
    s = make_settings(data_dir=tmp_path / "data")
    bare_world.wire_settings(s)
    return s


def binding_for(world, full_name=REPO, *, default="main", work=WORK):
    return SimpleNamespace(
        remote_url=f"{world.web_url}/{full_name}.git",
        default_branch=default,
        work_branch=work,
        full_name=full_name,
    )


def auth_for(world, token=TOKEN):
    return RemoteAuth(host=world.remote.host, token=token)


def git(repo: Path, *args: str) -> str:
    return plain_git(*args, cwd=repo).stdout.strip()


@pytest.fixture
def argv_log(monkeypatch):
    """Every argv (and the env of each child) that repo_git hands to a process."""
    seen: list[tuple[list[str], dict]] = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, argv, *a, **kw):
            seen.append((list(argv), dict(kw.get("env") or {})))
            super().__init__(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    return seen


# ------------------------------------------------------------------ the token never leaks


def test_clone_fetch_push_keep_the_token_out_of_argv_config_url_and_fetch_head(
    bare_world, settings, tmp_path, argv_log
):
    w = bare_world
    w.add_repo(REPO, files={"README.md": "hi\n"})
    b, auth = binding_for(w), auth_for(w)
    repo = tmp_path / "clone"
    repo_git.clone(settings, b, auth, repo)
    plain_git("config", "user.email", "t@t", cwd=repo)
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    plain_git("commit", "-q", "-m", "a", cwd=repo)
    sha = git(repo, "rev-parse", "HEAD")
    repo_git.push(settings, b, auth, repo, sha=sha, branch=WORK)
    w.human_commit(REPO, {"b.txt": "b\n"})
    shas = repo_git.fetch(settings, b, auth, repo)
    assert shas["default"] == w.remote.branch_sha(REPO, "main")

    assert w.remote.branch_sha(REPO, WORK) == sha
    for argv, _env in argv_log:
        assert TOKEN not in " ".join(argv)
    envs = [env for _argv, env in argv_log if "FOUNDRY_GIT_TOKEN" in env]
    assert envs and all(e["FOUNDRY_GIT_TOKEN"] == TOKEN for e in envs)
    assert all(e.get("GIT_TERMINAL_PROMPT") == "0" for e in envs)
    config = (repo / ".git" / "config").read_text()
    assert TOKEN not in config and "x-access-token" not in config and "@" not in config
    assert re.search(r"url = http://127\.0\.0\.1:\d+/", config)
    for path in (repo / ".git").rglob("*"):
        if path.is_file() and path.stat().st_size < 1_000_000:
            assert TOKEN.encode() not in path.read_bytes(), path
    assert w.remote.tokens_seen() == {TOKEN}


def test_askpass_answers_only_for_the_expected_host(settings, tmp_path):
    script = repo_git.askpass_script(settings)
    assert script.stat().st_mode & 0o777 == 0o700
    assert script.read_text() == repo_git.ASKPASS_SCRIPT

    def ask(prompt: str, host: str = "github.com") -> str:
        out = subprocess.run(
            [str(script), prompt],
            capture_output=True,
            text=True,
            env={
                "FOUNDRY_GIT_TOKEN": "sekrit-token",
                "FOUNDRY_GIT_HOST": host,
                "PATH": "/bin:/usr/bin",
            },
        )
        return out.stdout.strip()

    assert ask("Username for 'https://github.com': ") == "x-access-token"
    assert ask("Password for 'https://x-access-token@github.com': ") == "sekrit-token"
    # another host, a look-alike host, or a different port: nothing
    assert ask("Password for 'https://evil.example': ") == ""
    assert ask("Password for 'https://x-access-token@github.com.evil.example': ") == ""
    assert ask("Password for 'https://x-access-token@evilgithub.com': ") == ""
    assert ask("Password for 'http://github.com:8080': ") == ""
    assert ask("Username for 'https://evil.example': ") == ""
    assert ask("Passphrase for key '/home/x/.ssh/id_rsa': ") == ""
    assert ask("Password for 'https://github.com': ", host="") == ""


def test_the_askpass_script_is_rewritten_if_it_was_tampered_with(settings):
    script = repo_git.askpass_script(settings)
    script.write_text("#!/bin/sh\necho stolen\n")
    again = repo_git.askpass_script(settings)
    assert again == script and again.read_text() == repo_git.ASKPASS_SCRIPT


def test_a_planted_global_credential_helper_is_never_called(
    bare_world, settings, tmp_path, monkeypatch
):
    w = bare_world
    w.add_repo(REPO)
    marker = tmp_path / "helper-ran"
    helper = tmp_path / "helper.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\necho username=x\necho password=y\n")
    helper.chmod(0o755)
    gitconfig = tmp_path / "evil.gitconfig"
    gitconfig.write_text(f'[credential]\n\thelper = "{helper}"\n[core]\n\taskPass = {helper}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    repo = tmp_path / "clone"
    repo_git.clone(settings, binding_for(w), auth_for(w), repo)
    assert not marker.exists()
    assert (repo / "README.md").exists()


def test_a_tampered_dot_git_config_is_repinned_before_the_token_exists(
    bare_world, settings, tmp_path
):
    w = bare_world
    w.add_repo(REPO)
    b, auth = binding_for(w), auth_for(w)
    repo = tmp_path / "clone"
    repo_git.clone(settings, b, auth, repo)
    with (repo / ".git" / "config").open("a") as f:
        f.write(
            f'[url "http://127.0.0.1:1/"]\n\tinsteadOf = {w.web_url}/\n'
            + "[credential]\n\thelper = !echo pwned\n"
            + "[core]\n\thooksPath = /tmp/evil\n\tfsmonitor = /tmp/evil-monitor\n"
            + '[remote "origin"]\n\tpushurl = http://127.0.0.1:1/x.git\n'
            + '[filter "x"]\n\tclean = /tmp/evil\n'
        )
    (repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
    w.human_commit(REPO, {"c.txt": "c\n"})
    shas = repo_git.fetch(settings, b, auth, repo)  # would hit 127.0.0.1:1 if not repinned
    assert shas["default"] == w.remote.branch_sha(REPO, "main")
    config = (repo / ".git" / "config").read_text()
    for planted in ("insteadOf", "credential", "hooksPath", "fsmonitor", "pushurl", "filter"):
        assert planted not in config
    assert not (repo / ".git" / "info" / "attributes").exists()
    assert (repo / ".git" / "info" / "exclude").read_text().startswith("node_modules/")


def test_pin_config_refuses_a_url_with_credentials(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    plain_git("init", "-q", cwd=repo)
    bad = SimpleNamespace(
        remote_url="https://x-access-token:abc@github.com/o/r.git",
        default_branch="main",
        work_branch=WORK,
    )
    with pytest.raises(ValueError):
        repo_git.pin_config(repo, bad)
    with pytest.raises(ValueError):
        repo_git.pin_config(
            repo,
            SimpleNamespace(
                remote_url="https://github.com/o/r.git", default_branch="main", work_branch="a b"
            ),
        )
    with pytest.raises(ValueError):
        repo_git.pin_config(
            repo,
            SimpleNamespace(
                remote_url="file:///etc/passwd", default_branch="main", work_branch=WORK
            ),
        )


# ------------------------------------------------------------------ clone


def test_clone_of_an_existing_repo_starts_the_work_branch_from_the_default_branch(
    bare_world, settings, tmp_path
):
    w = bare_world
    w.add_repo(REPO, files={"README.md": "hi\n", "src/app.py": "print(1)\n"})
    repo = tmp_path / "clone"
    shas = repo_git.clone(settings, binding_for(w), auth_for(w), repo)
    assert shas == {"default": w.remote.branch_sha(REPO, "main"), "work": None}
    assert git(repo, "symbolic-ref", "--short", "HEAD") == WORK
    assert git(repo, "rev-parse", "HEAD") == shas["default"]
    assert (repo / "src" / "app.py").exists()
    # only the base (and work) branch, no tags
    assert git(repo, "branch", "-r") == "origin/main"
    assert git(repo, "tag") == ""


def test_clone_of_a_repo_whose_work_branch_exists_starts_from_it(bare_world, settings, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    w.human_commit(REPO, {"w.txt": "w\n"}, branch=WORK, parent_branch="main")
    repo = tmp_path / "clone"
    shas = repo_git.clone(settings, binding_for(w), auth_for(w), repo)
    assert shas["work"] == w.remote.branch_sha(REPO, WORK)
    assert git(repo, "rev-parse", "HEAD") == shas["work"]


def test_clone_of_an_empty_repo_then_the_first_commit_sets_the_default_branch(
    bare_world, settings, tmp_path
):
    w = bare_world
    w.add_repo(REPO, empty=True)
    assert w.remote.is_empty(REPO)
    b, auth = binding_for(w, default="trunk"), auth_for(w)
    repo = tmp_path / "clone"
    shas = repo_git.clone(settings, b, auth, repo)
    assert shas == {"default": None, "work": None}
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "trunk"
    (repo / "README.md").write_text("# new\n")
    git(repo, "add", "README.md")
    plain_git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first", cwd=repo)
    sha = git(repo, "rev-parse", "HEAD")
    repo_git.push(settings, b, auth, repo, sha=sha, branch="trunk")
    assert w.remote.branch_sha(REPO, "trunk") == sha
    assert not w.remote.is_empty(REPO)


def test_a_failed_clone_leaves_nothing_behind(bare_world, settings, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    repo = tmp_path / "clone"
    with pytest.raises(GitAuthFailed):
        repo_git.clone(settings, binding_for(w), auth_for(w, UNKNOWN_TOKEN), repo)
    assert not repo.exists()


def test_clone_refuses_a_repository_over_the_size_limit(
    bare_world, settings, tmp_path, monkeypatch
):
    w = bare_world
    w.add_repo(REPO)
    settings.repo_max_size_mb = 1
    monkeypatch.setattr(repo_git, "_repo_size_mb", lambda repo: 5.0)
    repo = tmp_path / "clone"
    with pytest.raises(GitNetworkError, match="larger than"):
        repo_git.clone(settings, binding_for(w), auth_for(w), repo)
    assert not repo.exists()


# ------------------------------------------------------------------ errors


def test_a_wrong_token_is_an_auth_failure_and_a_readonly_token_is_forbidden(
    bare_world, settings, tmp_path
):
    w = bare_world
    w.add_repo(REPO)
    b = binding_for(w)
    repo = tmp_path / "clone"
    repo_git.clone(settings, b, auth_for(w), repo)
    w.human_commit(REPO, {"x.txt": "x\n"})
    with pytest.raises(GitAuthFailed) as exc:
        repo_git.fetch(settings, b, auth_for(w, UNKNOWN_TOKEN), repo)
    assert UNKNOWN_TOKEN not in str(exc.value)
    plain_git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "e",
        cwd=repo,
    )
    sha = git(repo, "rev-parse", "HEAD")
    with pytest.raises(GitForbidden):
        repo_git.push(settings, b, auth_for(w, READONLY_TOKEN), repo, sha=sha, branch=WORK)
    assert w.remote.branch_sha(REPO, WORK) is None


def test_a_non_fast_forward_push_is_reported_and_nothing_is_forced(bare_world, settings, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    b, auth = binding_for(w), auth_for(w)
    repo = tmp_path / "clone"
    repo_git.clone(settings, b, auth, repo)
    plain_git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "mine",
        cwd=repo,
    )
    mine = git(repo, "rev-parse", "HEAD")
    repo_git.push(settings, b, auth, repo, sha=mine, branch=WORK)
    # a person rewrote the work branch on the remote: our next (diverging) push is not a ff
    theirs = w.remote.rewrite_branch(REPO, WORK)
    assert w.remote.branch_sha(REPO, WORK) == theirs
    plain_git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "mine2",
        cwd=repo,
    )
    sha2 = git(repo, "rev-parse", "HEAD")
    with pytest.raises(GitNonFastForward):
        repo_git.push(settings, b, auth, repo, sha=sha2, branch=WORK)
    assert w.remote.branch_sha(REPO, WORK) == theirs  # untouched


def test_a_hung_remote_times_out_and_is_killed(settings, tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    port = listener.getsockname()[1]
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            held.append(conn)  # never answer

    threading.Thread(target=accept, daemon=True).start()
    settings.github_allow_insecure_http = True
    settings.git_network_timeout_seconds = 1
    b = SimpleNamespace(
        remote_url=f"http://127.0.0.1:{port}/o/r.git", default_branch="main", work_branch=WORK
    )
    repo = tmp_path / "clone"
    try:
        with pytest.raises(GitNetworkError, match="timed out"):
            repo_git.clone(settings, b, RemoteAuth(f"127.0.0.1:{port}", TOKEN), repo)
    finally:
        listener.close()
        for conn in held:
            conn.close()
    assert not repo.exists()


def test_plain_http_is_refused_unless_the_test_switch_is_on(bare_world, settings, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    settings.github_allow_insecure_http = False
    with pytest.raises(GitNetworkError):
        repo_git.clone(settings, binding_for(w), auth_for(w), tmp_path / "clone")


# ------------------------------------------------------------------ the push is the only push


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "push", "-f", "origin", "a:refs/heads/x"],
        ["git", "push", "--force", "origin", "a:refs/heads/x"],
        ["git", "push", "--force-with-lease", "origin", "a:refs/heads/x"],
        ["git", "push", "--mirror", "origin"],
        ["git", "push", "--delete", "origin", "x"],
        ["git", "push", "-d", "origin", "x"],
        ["git", "push", "--tags", "origin"],
        ["git", "push", "--all", "origin"],
        ["git", "push", "--prune", "origin", "a:refs/heads/x"],
        ["git", "push", "origin", "+a:refs/heads/x"],
        ["git", "push", "origin", ":refs/heads/x"],
        ["git", "push", "origin", "a:refs/tags/v1"],
        ["git", "push", "origin", "a:main"],
        ["git", "push", "origin", "a:refs/heads/x", "b:refs/heads/y"],
        ["git", "push", "origin"],
        ["git", "push", "--porcelain", "--no-verify", "origin", "a:refs/heads/x"],
        ["git", "fetch", "origin"],
    ],
)
def test_assert_safe_push_argv_refuses(argv):
    with pytest.raises(UnsafePush):
        assert_safe_push_argv(argv)


def test_assert_safe_push_argv_accepts_the_one_shape():
    assert_safe_push_argv(["git", "push", "--porcelain", "origin", f"{'a' * 40}:refs/heads/x"])


def test_push_validates_before_running_git(bare_world, settings, tmp_path, argv_log):
    w = bare_world
    w.add_repo(REPO)
    repo = tmp_path / "clone"
    repo_git.clone(settings, binding_for(w), auth_for(w), repo)
    argv_log.clear()
    for bad in ("HEAD", "main", "g" * 40):
        with pytest.raises(UnsafePush):
            repo_git.push(settings, binding_for(w), auth_for(w), repo, sha=bad, branch=WORK)
    with pytest.raises(ValueError):
        repo_git.push(settings, binding_for(w), auth_for(w), repo, sha="a" * 40, branch="--force")
    assert argv_log == []


def test_no_other_module_runs_git_push():
    """Network git lives in repo_git: nothing else may build a push argv."""
    offenders = []
    for path in (BACKEND_DIR / "app").rglob("*.py"):
        if path.name == "repo_git.py":
            continue
        text = path.read_text()
        # a git push argv: the word "push" next to a remote name / refspec / push flag
        if re.search(r"""['"]push['"]\s*,\s*['"](?:-|origin)""", text):
            offenders.append(str(path.relative_to(BACKEND_DIR)))
    assert offenders == []


# ------------------------------------------------------------------ small helpers


def test_merged_runs_and_diff_for_review(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    plain_git("init", "-q", "-b", "main", cwd=repo)
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    plain_git(*ident, "commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    base = git(repo, "rev-parse", "HEAD")
    for i in (1, 2):
        plain_git("checkout", "-q", "-b", f"run/r{i}", "main", cwd=repo)
        (repo / f"f{i}.txt").write_text(f"{i}\n")
        git(repo, "add", ".")
        plain_git(*ident, "commit", "-q", "-m", f"work {i}", cwd=repo)
        plain_git("checkout", "-q", "main", cwd=repo)
        plain_git(
            *ident,
            "merge",
            "--no-ff",
            "-q",
            "-m",
            f"Merge run/r{i}: Task {i}",
            f"run/r{i}",
            cwd=repo,
        )
    assert repo_git.merged_runs(repo, base, "main") == ["r1", "r2"]
    assert repo_git.merged_runs(repo, "main", "main") == []
    (repo / "secret.txt").write_text(
        "key github_pat_11AAAAAAAAAAAAAAAAAAAAAA_bbbbbbbbbbbbbbbbbbbb\n"
    )
    git(repo, "add", ".")
    plain_git(*ident, "commit", "-q", "-m", "oops", cwd=repo)
    diff = repo_git.diff_for_review(repo, base, "main")
    assert "f1.txt" in diff and "f2.txt" in diff and "github_pat_" not in diff
    assert len(repo_git.diff_for_review(repo, base, "main", limit=200)) <= 200


def test_reset_integration_keeps_the_old_head_as_a_rescue_branch(bare_world, settings, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    b, auth = binding_for(w), auth_for(w)
    repo = tmp_path / "clone"
    repo_git.clone(settings, b, auth, repo)
    plain_git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "bad",
        cwd=repo,
    )
    bad = git(repo, "rev-parse", "HEAD")
    new = repo_git.reset_integration(repo, b, to="default_branch", rescue_name="foundry/rescue-1")
    assert new == w.remote.branch_sha(REPO, "main")
    assert git(repo, "rev-parse", WORK) == new
    assert git(repo, "rev-parse", "foundry/rescue-1") == bad
    with pytest.raises(ValueError):
        repo_git.reset_integration(repo, b, to="elsewhere", rescue_name="foundry/rescue-2")


def test_archive_branch_keeps_a_local_copy_of_a_remote_branch(bare_world, settings, tmp_path):
    w = bare_world
    w.add_repo(REPO)
    b, auth = binding_for(w), auth_for(w)
    repo = tmp_path / "clone"
    repo_git.clone(settings, b, auth, repo)
    sha = w.human_commit(REPO, {"x.txt": "x\n"}, branch="foundry/old", parent_branch="main")
    assert repo_git.archive_branch(settings, b, auth, repo, branch="foundry/old") is True
    assert git(repo, "rev-parse", "refs/foundry-archive/foundry/old") == sha
    assert repo_git.archive_branch(settings, b, auth, repo, branch="foundry/missing") is False
    with pytest.raises(ValueError):
        repo_git.archive_branch(settings, b, auth, repo, branch="--upload-pack=evil")
