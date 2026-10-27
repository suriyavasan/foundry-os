"""The ONLY module that runs network git (M14, WP-B).

Contract (docs/specs/m14-github-backed-projects.md section 3.2):

* Authentication is ``GIT_ASKPASS`` + the environment of one short-lived git child: the token is
  never in argv, ``.git/config``, the remote URL, FETCH_HEAD or any file. No credential helper.
* Before every network operation ``pin_config`` rewrites ``.git/config`` from the database, so a
  planted ``url.*.insteadOf``, ``credential.helper``, ``core.hooksPath`` or ``remote.*.pushurl``
  is gone before the token exists in any process.
* The only way to push is ``push``: ``git push --porcelain origin <sha>:refs/heads/<branch>``.
  ``assert_safe_push_argv`` refuses force, delete, mirror, tags, prune, ``+`` and ``:`` refspecs and
  any destination outside ``refs/heads/``. Nothing is ever force-pushed; history is append-only.
* Every non-fast-forward rejection raises ``GitNonFastForward``; the caller fetches, integrates and
  retries once.
* stderr is passed through the token mask and ``secrets_scan.scrub`` before it is logged or stored.

``run_git`` is also the hardened git runner of M12's ``project_workspaces`` (no hooks, no GPG, no
fsmonitor, a minimal environment, a timeout, no shell); its default result is text, M12 asks for
bytes.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.core.config import Settings
from app.services import secrets_scan

GIT_TIMEOUT_SECONDS = 60
COMMITTER_NAME = "Foundry AI OS"
COMMITTER_EMAIL = "noreply@foundry.invalid"

# Never committed: dependency trees, caches and the CLIs' own configuration directories.
EXCLUDES = (
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    ".claude/",
    ".codex/",
    ".opencode/",
    ".agents/",
    ".gemini/",
)

HARDENING = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
)

# Flags a push can never carry (``assert_safe_push_argv`` only ever allows ``--porcelain``).
_REFUSED_PUSH_FLAGS = frozenset(
    {"-f", "--force", "--mirror", "--delete", "-d", "--tags", "--all", "--prune"}
)
# Proxy / CA settings of the operator that network git may inherit (they carry no Foundry secret).
_NET_PASSTHROUGH = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GIT_SSL_CAINFO",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
)
ASKPASS_SCRIPT = """#!/bin/sh
# Foundry AI OS: answers git's credential prompts from the environment of ONE git child process.
# Anything but the expected host gets an empty answer, so authentication fails instead of leaking.
[ -n "$FOUNDRY_GIT_HOST" ] || exit 0
case "$1" in
  *"//$FOUNDRY_GIT_HOST'"*|*"@$FOUNDRY_GIT_HOST'"*) ;;
  *) exit 0 ;;
esac
case "$1" in
  Username*) printf '%s\\n' 'x-access-token' ;;
  Password*) printf '%s\\n' "$FOUNDRY_GIT_TOKEN" ;;
esac
exit 0
"""


class GitError(Exception):
    """A git call failed. The message is safe to show (no secrets, no run output)."""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class GitNetworkError(GitError):
    """A network git call failed. The message is scrubbed and safe to show."""


class GitNonFastForward(GitNetworkError):
    """The remote rejected a push because it is not a fast-forward."""


class GitAuthFailed(GitNetworkError):
    """The remote refused the token (HTTP 401/403)."""


class GitForbidden(GitNetworkError):
    """The token is valid but may not do this (HTTP 403: for example no Contents write)."""


class GitProtectedBranch(GitNetworkError):
    """The remote refused the push because the branch is protected."""


class UnsafePush(ValueError):
    """The argv of a push is not exactly ``push --porcelain origin <sha>:refs/heads/<branch>``."""


@dataclass(frozen=True)
class RemoteAuth:
    """What one network git call authenticates with. Lives in memory for that call only."""

    host: str
    token: str = field(repr=False)


# ---------------------------------------------------------------- the runner


def git_binary() -> str | None:
    return shutil.which("git")


def base_env(identity: tuple[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_COMMITTER_NAME": COMMITTER_NAME,
        "GIT_COMMITTER_EMAIL": COMMITTER_EMAIL,
    }
    name, email = identity or (COMMITTER_NAME, COMMITTER_EMAIL)
    env["GIT_AUTHOR_NAME"] = name
    env["GIT_AUTHOR_EMAIL"] = email
    return env


def clean_text(text: str, mask: Iterable[str] = (), limit: int = 300) -> str:
    """Single-line, token-masked, scrubbed text that is safe to log or store."""
    out = " ".join(text.split())
    for secret in mask:
        if secret:
            out = out.replace(secret, "***")
    return (secrets_scan.scrub(out) or "")[:limit]


def run_git(
    args: list[str],
    *,
    cwd: Path,
    git_dir: Path | None = None,
    work_tree: Path | None = None,
    identity: tuple[str, str] | None = None,
    check: bool = True,
    timeout: float = GIT_TIMEOUT_SECONDS,
    extra_env: dict[str, str] | None = None,
    extra_config: tuple[str, ...] = (),
    text: bool = True,
    binary: str | None = None,
    mask: Iterable[str] = (),
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run git with M12's hardening (no hooks, no GPG, no fsmonitor, minimal environment, a
    timeout, no shell). ``text=False`` returns bytes (M12). On a timeout the whole process group
    is killed."""
    exe = binary or git_binary()
    if exe is None:
        raise GitError("git is not installed")
    argv = [exe, *HARDENING]
    for item in extra_config:
        argv += ["-c", item]
    if git_dir is not None:
        argv += ["--git-dir", str(git_dir)]
    if work_tree is not None:
        argv += ["--work-tree", str(work_tree)]
    argv += args
    env = base_env(identity)
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError as exc:
        raise GitError(f"cannot run git ({type(exc).__name__})") from exc
    try:
        out, err = proc.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        raise GitError(f"git {args[0]} timed out after {int(timeout)}s") from exc
    if check and proc.returncode != 0:
        detail = clean_text(err.decode("utf-8", "replace"), mask)
        raise GitError(f"git {args[0]} failed: {detail}", proc.returncode)
    if text:
        return subprocess.CompletedProcess(
            argv, proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        )
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def git_out(repo: Path, *args: str, check: bool = True, **kw: Any) -> str:
    """Stripped stdout of a local git command in ``repo``."""
    return run_git(list(args), cwd=repo, check=check, **kw).stdout.strip()


def ref_sha(repo: Path, ref: str) -> str | None:
    """The commit a ref resolves to, or None."""
    proc = run_git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd=repo, check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        run_git(
            ["merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, check=False
        ).returncode
        == 0
    )


# ---------------------------------------------------------------- askpass and config pinning


def askpass_script(settings: Settings) -> Path:
    """The static 0700 ``<data>/run/git-askpass`` script: prints ``x-access-token`` for a
    Username prompt and ``$FOUNDRY_GIT_TOKEN`` for a Password prompt naming ``$FOUNDRY_GIT_HOST``;
    anything else prints nothing, so authentication fails instead of leaking."""
    run_dir = Path(settings.resolved_data_dir) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        run_dir.chmod(0o700)
    path = run_dir / "git-askpass"
    try:
        if path.read_text() == ASKPASS_SCRIPT and (path.stat().st_mode & 0o777) == 0o700:
            return path
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".askpass-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(ASKPASS_SCRIPT)
        os.chmod(tmp, stat.S_IRWXU)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    return path


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("https", "http")
        or not parts.netloc
        or "@" in parts.netloc
        or any(c in url for c in "\n\r\0 \t")
    ):
        raise ValueError("the remote URL must be http(s) without userinfo")
    return url


def _check_branch(name: str) -> str:
    if (
        not name
        or name.startswith(("-", "/"))
        or ".." in name
        or any(c in name for c in " ~^:?*[\\\n\r\t\0")
        or name.endswith((".lock", "/", "."))
    ):
        raise ValueError(f"invalid branch name {name!r}")
    return name


def _local_core(repo: Path) -> dict[str, str]:
    """Filesystem-dependent core settings git detected at ``git init`` (case, filemode ...)."""
    out: dict[str, str] = {}
    cfg = repo / ".git" / "config"
    for key in ("filemode", "ignorecase", "precomposeunicode"):
        proc = run_git(
            ["config", "--file", str(cfg), "--get", f"core.{key}"], cwd=repo, check=False
        )
        value = proc.stdout.strip().lower()
        if proc.returncode == 0 and value in ("true", "false"):
            out[key] = value
    return out


def pin_config(repo: Path, binding: Any) -> None:
    """Rewrite ``.git/config`` and ``.git/info/exclude`` atomically from the binding. Anything a
    process planted in them (``url.*.insteadOf``, a credential helper, ``core.hooksPath``,
    ``remote.*.pushurl``, filters) is gone afterwards."""
    url = _check_url(binding.remote_url)
    default = _check_branch(binding.default_branch)
    work = _check_branch(binding.work_branch)
    git_dir = repo / ".git"
    core = {"filemode": "true", **_local_core(repo)}
    lines = [
        "[core]",
        "\trepositoryformatversion = 0",
        "\tbare = false",
        "\tlogallrefupdates = true",
        *[f"\t{k} = {v}" for k, v in core.items()],
        '[remote "origin"]',
        f"\turl = {url}",
        f"\tfetch = +refs/heads/{default}:refs/remotes/origin/{default}",
    ]
    if work != default:
        lines.append(f"\tfetch = +refs/heads/{work}:refs/remotes/origin/{work}")
    lines.append("\ttagopt = --no-tags")
    _atomic_write(git_dir / "config", "\n".join(lines) + "\n")
    info = git_dir / "info"
    info.mkdir(exist_ok=True)
    _atomic_write(info / "exclude", "\n".join(EXCLUDES) + "\n")
    for planted in (info / "attributes", git_dir / "config.worktree"):
        with contextlib.suppress(OSError):
            planted.unlink()


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---------------------------------------------------------------- network


def network_config(settings: Settings) -> tuple[str, ...]:
    cfg = [
        "credential.helper=",
        "core.askPass=",
        "core.sshCommand=/bin/false",
        "protocol.allow=never",
        "protocol.https.allow=always",
        "http.followRedirects=false",
        "submodule.recurse=false",
        "fetch.recurseSubmodules=false",
        "transfer.fsckObjects=true",
    ]
    if settings.github_allow_insecure_http:  # tests only: a local smart-HTTP remote
        cfg.append("protocol.http.allow=always")
    return tuple(cfg)


def network_env(settings: Settings, auth: RemoteAuth) -> dict[str, str]:
    env = {
        "GIT_ASKPASS": str(askpass_script(settings)),
        "FOUNDRY_GIT_TOKEN": auth.token,
        "FOUNDRY_GIT_HOST": auth.host,
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key in _NET_PASSTHROUGH:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _classify(text: str, returncode: int, verb: str) -> GitNetworkError:
    low = text.lower()
    text = text[:400]
    if "gh006" in low or "protected branch" in low or "gh013" in low:
        return GitProtectedBranch(f"git {verb}: the branch is protected: {text}", returncode)
    if "non-fast-forward" in low or "fetch first" in low or "stale info" in low:
        return GitNonFastForward(f"git {verb}: not a fast-forward: {text}", returncode)
    if "returned error: 403" in low:
        return GitForbidden(f"git {verb}: the token may not do this (HTTP 403): {text}", returncode)
    if any(
        marker in low
        for marker in (
            "authentication failed",
            "returned error: 401",
            "terminal prompts disabled",
            "could not read username",
            "could not read password",
            "invalid username or password",
            "access denied",
            "permission denied",
        )
    ):
        return GitAuthFailed(f"git {verb}: the remote refused the token: {text}", returncode)
    return GitNetworkError(f"git {verb} failed: {text}", returncode)


def _net(
    settings: Settings,
    auth: RemoteAuth,
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    try:
        proc = run_git(
            args,
            cwd=repo,
            check=False,
            timeout=settings.git_network_timeout_seconds,
            extra_env=network_env(settings, auth),
            extra_config=network_config(settings),
            mask=(auth.token,),
        )
    except GitError as exc:
        raise GitNetworkError(clean_text(str(exc), (auth.token,))) from exc
    if check and proc.returncode != 0:
        detail = clean_text(f"{proc.stderr} {proc.stdout}", (auth.token,), limit=6000)
        raise _classify(detail, proc.returncode, args[0])
    return proc


def remote_heads(settings: Settings, binding: Any, auth: RemoteAuth, repo: Path) -> dict[str, str]:
    """``{branch: sha}`` of the base and work branches on the remote (missing = absent)."""
    pin_config(repo, binding)
    wanted = [f"refs/heads/{binding.default_branch}", f"refs/heads/{binding.work_branch}"]
    proc = _net(settings, auth, repo, ["ls-remote", "origin", *wanted])
    heads: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/") and re.fullmatch(r"[0-9a-f]{40}", sha):
            heads[ref[len("refs/heads/") :]] = sha
    return heads


def _repo_size_mb(repo: Path) -> float:
    total = 0
    for dirpath, _dirs, files in os.walk(repo / ".git"):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, name)).st_size
    return total / (1024 * 1024)


def fetch(settings: Settings, binding: Any, auth: RemoteAuth, repo: Path) -> dict[str, str | None]:
    """Fetch ``origin/<default>`` and ``origin/<work>`` (a missing work branch is fine); returns
    ``{"default": sha | None, "work": sha | None}`` of the remote-tracking refs afterwards."""
    pin_config(repo, binding)
    heads = remote_heads(settings, binding, auth, repo)
    default, work = binding.default_branch, binding.work_branch
    specs = [f"+refs/heads/{b}:refs/remotes/origin/{b}" for b in dict.fromkeys([default, work])]
    present = [s for s, b in zip(specs, dict.fromkeys([default, work]), strict=True) if b in heads]
    if present:
        current = {b: ref_sha(repo, f"refs/remotes/origin/{b}") for b in (default, work)}
        if any(heads.get(b) and heads[b] != current[b] for b in (default, work)):
            _net(
                settings,
                auth,
                repo,
                ["fetch", "--no-tags", "--no-recurse-submodules", "origin", *present],
            )
    return {
        "default": ref_sha(repo, f"refs/remotes/origin/{default}") if default in heads else None,
        "work": ref_sha(repo, f"refs/remotes/origin/{work}") if work in heads else None,
    }


def archive_branch(
    settings: Settings, binding: Any, auth: RemoteAuth, repo: Path, *, branch: str
) -> bool:
    """Keep a local copy of a remote branch under ``refs/foundry-archive/<branch>`` (before the
    branch is deleted on GitHub). Returns True when the copy exists afterwards; False when the
    remote has no such branch."""
    _check_branch(branch)
    pin_config(repo, binding)
    proc = _net(settings, auth, repo, ["ls-remote", "origin", f"refs/heads/{branch}"])
    if not proc.stdout.strip():
        return False
    _net(
        settings,
        auth,
        repo,
        [
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "origin",
            f"+refs/heads/{branch}:refs/foundry-archive/{branch}",
        ],
    )
    return ref_sha(repo, f"refs/foundry-archive/{branch}") is not None


def clone(settings: Settings, binding: Any, auth: RemoteAuth, repo: Path) -> dict[str, str | None]:
    """Create ``repo`` as a clone of the remote's base (and work) branch, no tags, no submodules,
    no LFS. ``repo`` must not exist yet. An empty remote gives a repository on the (unborn)
    default branch with ``origin`` pinned; the caller makes the first commit. Returns
    ``fetch``'s result. Refuses a remote larger than ``repo_max_size_mb``."""
    repo.mkdir(parents=True, exist_ok=False)
    try:
        run_git(["init", "-q"], cwd=repo)
        run_git(
            ["symbolic-ref", "HEAD", f"refs/heads/{_check_branch(binding.default_branch)}"],
            cwd=repo,
        )
        pin_config(repo, binding)
        shas = fetch(settings, binding, auth, repo)
        if _repo_size_mb(repo) > settings.repo_max_size_mb:
            raise GitNetworkError(
                f"the repository is larger than the {settings.repo_max_size_mb} MB limit"
            )
        start = shas["work"] or shas["default"]
        if start:
            run_git(["checkout", "-q", "-B", binding.work_branch, start], cwd=repo)
        return shas
    except BaseException:
        shutil.rmtree(repo, ignore_errors=True)
        raise


def push(
    settings: Settings, binding: Any, auth: RemoteAuth, repo: Path, *, sha: str, branch: str
) -> None:
    """Push ``sha`` to ``refs/heads/<branch>``: the only push in the code base."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise UnsafePush("push needs a full commit SHA")
    _check_branch(branch)
    pin_config(repo, binding)
    args = ["push", "--porcelain", "origin", f"{sha}:refs/heads/{branch}"]
    assert_safe_push_argv(args)
    _net(settings, auth, repo, args)


def assert_safe_push_argv(argv: list[str]) -> None:
    """Refuse anything but ``[... 'push', '--porcelain', 'origin', '<sha>:refs/heads/<branch>']``.

    Pure; used by ``push`` and by the test that no other module pushes."""
    if "push" not in argv:
        raise UnsafePush("not a push")
    tail = argv[argv.index("push") + 1 :]
    flags = [a for a in tail if a.startswith("-")]
    if any(
        f in _REFUSED_PUSH_FLAGS or f.startswith("--force") or f != "--porcelain" for f in flags
    ):
        raise UnsafePush(f"refused push flag: {flags}")
    refspecs = [a for a in tail if not a.startswith("-")][1:]  # [remote, *refspecs]
    if len(refspecs) != 1:
        raise UnsafePush("exactly one refspec is allowed")
    spec = refspecs[0]
    src, sep, dst = spec.partition(":")
    if not sep or not src or spec.startswith(("+", ":")) or not dst.startswith("refs/heads/"):
        raise UnsafePush(f"refused refspec: {spec}")


# ---------------------------------------------------------------- local helpers for the sync


def diff_for_review(repo: Path, base: str, head: str, *, limit: int = 200 * 1024) -> str:
    """The scrubbed three-dot diff attached to a PR review task (at most ``limit`` bytes)."""
    flags = ["--no-ext-diff", "--no-textconv", "--no-color", "-M"]
    stat_out = run_git(["diff", *flags, "--stat=100", f"{base}...{head}"], cwd=repo).stdout
    patch = run_git(["diff", *flags, f"{base}...{head}"], cwd=repo).stdout
    text = secrets_scan.scrub(f"{stat_out.rstrip()}\n\n{patch}") or ""
    data = text.encode("utf-8")
    if len(data) > limit:
        note = b"\n[... diff truncated]\n"
        data = data[: limit - len(note)].decode("utf-8", "ignore").encode() + note
    return data.decode("utf-8", "replace")


_MERGED_RUN = re.compile(r"^Merge run/([A-Za-z0-9_-]+):")


def merged_runs(repo: Path, base: str, head: str) -> list[str]:
    """Run ids named by the ``Merge run/<id>: ...`` commits in ``base..head`` (oldest first)."""
    out = run_git(["log", "--reverse", "--format=%s", f"{base}..{head}"], cwd=repo).stdout
    ids: list[str] = []
    for line in out.splitlines():
        match = _MERGED_RUN.match(line)
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def reset_integration(repo: Path, binding: Any, *, to: str, rescue_name: str) -> str:
    """Move the work branch to ``origin/<work>`` or ``origin/<default>``; the old head is kept as
    the local branch ``rescue_name``. Returns the new head SHA. Under the project lock."""
    from app.services import project_workspaces as pw  # local: pw imports this module

    if to not in ("remote_work_branch", "default_branch"):
        raise ValueError("to must be 'remote_work_branch' or 'default_branch'")
    target_branch = binding.work_branch if to == "remote_work_branch" else binding.default_branch
    _check_branch(rescue_name)
    with pw.project_lock(repo):
        target = ref_sha(repo, f"refs/remotes/origin/{target_branch}")
        if target is None:
            raise GitError(f"origin/{target_branch} is not fetched yet; sync first")
        old = ref_sha(repo, f"refs/heads/{binding.work_branch}")
        if old is not None:
            run_git(["branch", rescue_name, old], cwd=repo)
        run_git(["merge", "--abort"], cwd=repo, check=False)
        run_git(["checkout", "-q", "-f", "-B", binding.work_branch, target], cwd=repo)
        run_git(["reset", "-q", "--hard", target], cwd=repo)
        run_git(["clean", "-fdq"], cwd=repo, check=False)
        return target
