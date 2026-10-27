"""Shared project repository (M12 WP4, decision D34).

One git repository per project, so the second task of a project sees the files of the first:

    <workspaces>/<company_id>/projects/<project_id>/main    the repository (branch ``main``)
    <workspaces>/<company_id>/<run_id>                      one run's directory

A work task of a project runs in ``git worktree add -b run/<run_id>`` made from ``main``. A review
task gets a *detached* worktree whose files are read-only and which is thrown away afterwards. Any
other task keeps a plain per-run directory. When the run ends the control plane (never the run)
commits what the run left as the agent, merges the branch ``--no-ff`` into ``main`` under a
per-project file lock and removes the worktree. A merge conflict is aborted and reported as
``merge_conflict`` (the retry starts from the new ``main``); the branch of a failed run is kept.

Safety:

* Every git call is ``git -c core.hooksPath=/dev/null -c commit.gpgsign=false
  -c core.fsmonitor=false`` with a minimal environment (no user or system git config, no
  prompts), a 60 s timeout and no shell. A planted hook, filter or fsmonitor can never run.
* The run can edit its worktree, nothing else. The control plane never trusts the worktree's own
  ``.git`` file (the run could rewrite it): it addresses the worktree through ``--git-dir`` of the
  administrative directory it recorded when it created the worktree. Symlinks that leave the
  worktree and nested ``.git`` entries are removed before anything is staged. The main working
  tree is only ever touched by the merge done here. The CLIs' OS sandboxes (Codex) and file
  confinement (Claude Code ``--restricted``, OpenCode ``external_directory: deny``) keep the run
  out of the main repository; that confinement was not exercised against real CLIs here.
* Without ``git`` (or without ``fcntl``) nothing here fails a run: every run keeps a per-run
  directory and the result says ``{"available": false}``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.runtimes.base import ArtifactRef
from app.services import secrets_scan

try:  # POSIX only; without it the project repository is reported as unavailable
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger("company_os.project_workspaces")

GIT_TIMEOUT_SECONDS = 60
LOCK_TIMEOUT_SECONDS = 120
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_CHANGESET_BYTES = 200 * 1024
MAX_WORKSPACE_FILES = 60
MAIN_BRANCH = "main"
COMMITTER_NAME = "Foundry AI OS"
COMMITTER_EMAIL = "noreply@foundry.invalid"

KIND_PROJECT = "project"  # a worktree on branch run/<run_id>
KIND_REVIEW = "review"  # a detached, read-only worktree; changes are discarded
KIND_RUN_DIR = "run_dir"  # a plain per-run directory

ERR_MERGE_CONFLICT = "merge_conflict"  # exactly this string: WP2's retry policy may classify it
ERR_TOO_LARGE = "workspace_too_large"
ERR_WORKSPACE = "workspace_error"

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

_HARDENING = (
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


class GitError(Exception):
    """A git call failed. The message is safe to show (no secrets, no run output)."""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


def git_binary() -> str | None:
    return shutil.which("git")


def available() -> bool:
    return git_binary() is not None and fcntl is not None


def status() -> dict[str, Any]:
    """``{"available": bool, "reason": ...}``: whether projects can share a repository here."""
    if fcntl is None:
        return {"available": False, "reason": "file locking (fcntl) is not available here"}
    if git_binary() is None:
        return {"available": False, "reason": "git is not installed"}
    return {"available": True, "reason": None}


# ---------------------------------------------------------------- git plumbing


def _env(identity: tuple[str, str] | None = None) -> dict[str, str]:
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


def _git(
    args: list[str],
    *,
    cwd: Path,
    git_dir: Path | None = None,
    work_tree: Path | None = None,
    identity: tuple[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    binary = git_binary()
    if binary is None:
        raise GitError("git is not installed")
    argv = [binary, *_HARDENING]
    if git_dir is not None:
        argv += ["--git-dir", str(git_dir)]
    if work_tree is not None:
        argv += ["--work-tree", str(work_tree)]
    argv += args
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=_env(identity),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {GIT_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise GitError(f"cannot run git ({type(exc).__name__})") from exc
    if check and proc.returncode != 0:
        detail = " ".join(proc.stderr.decode("utf-8", "replace").split())[:300]
        raise GitError(f"git {args[0]} failed: {detail}", proc.returncode)
    return proc


def _out(proc: subprocess.CompletedProcess[bytes]) -> str:
    return proc.stdout.decode("utf-8", "replace").strip()


@contextlib.contextmanager
def _file_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
    """An exclusive ``flock`` on ``path`` (also excludes other threads of this process)."""
    assert fcntl is not None
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise GitError("timed out waiting for the project repository lock") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _safe_component(value: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."} or "\0" in value:
        raise ValueError("invalid path component")
    return value


def project_dir(root: Path, company_id: str, project_id: str) -> Path:
    return Path(root) / _safe_component(company_id) / "projects" / _safe_component(project_id)


def main_repo(root: Path, company_id: str, project_id: str) -> Path:
    return project_dir(root, company_id, project_id) / "main"


def _project_lock(repo: Path):
    return _file_lock(repo / ".git" / "foundry.lock")


def _has_main(repo: Path) -> bool:
    proc = _git(["rev-parse", "--verify", "-q", f"refs/heads/{MAIN_BRANCH}"], cwd=repo, check=False)
    return proc.returncode == 0


def ensure_repo(root: Path, company_id: str, project_id: str) -> Path:
    """The project's main repository, created (branch ``main``, one empty commit) on first use."""
    pdir = project_dir(root, company_id, project_id)
    pdir.mkdir(parents=True, exist_ok=True)
    repo = pdir / "main"
    if (repo / ".git").is_dir() and _has_main(repo):
        return repo
    with _file_lock(pdir / "init.lock"):
        if (repo / ".git").is_dir() and _has_main(repo):
            return repo
        repo.mkdir(exist_ok=True)
        if not (repo / ".git").is_dir():
            _git(["init", "-q"], cwd=repo)
        _git(["symbolic-ref", "HEAD", f"refs/heads/{MAIN_BRANCH}"], cwd=repo)
        info = repo / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text("\n".join(EXCLUDES) + "\n")
        _git(["commit", "-q", "--allow-empty", "-m", "Initialize project repository"], cwd=repo)
    return repo


# ---------------------------------------------------------------- workspace


@dataclass
class Workspace:
    kind: str
    path: Path
    run_id: str
    project_id: str | None = None
    repo: Path | None = None  # the project's main repository
    gitdir: Path | None = None  # administrative directory of this run's worktree (trusted)
    branch: str | None = None
    base: str | None = None  # the commit of ``main`` the run started from
    files: list[str] = field(default_factory=list)
    available: bool = True
    note: str | None = None  # why a project run fell back to a per-run directory

    def ref(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "base": self.base,
            "commit": None,
            "merged": False,
            "conflict": False,
        }
        if self.branch:
            out["branch"] = self.branch
        if not self.available:
            out["available"] = False
            out["reason"] = self.note
        return out


@dataclass
class Finalized:
    ref: dict[str, Any]
    artifacts: list[ArtifactRef] = field(default_factory=list)
    error_code: str | None = None  # set when a *successful* run must be reported as failed
    error_message: str | None = None
    merged: bool = False
    conflict: bool = False
    changed: bool = False


def _scratch(path: Path, run_id: str, *, available: bool = True, note: str | None = None):
    path.mkdir(parents=True, exist_ok=True)
    return Workspace(KIND_RUN_DIR, path, run_id, available=available, note=note)


def _list_files(repo: Path, ref: str) -> list[str]:
    proc = _git(["ls-tree", "-r", "-z", "--name-only", ref], cwd=repo, check=False)
    if proc.returncode != 0:
        return []
    names = [n for n in proc.stdout.decode("utf-8", "replace").split("\0") if n]
    names.sort(key=lambda n: (n.count("/"), n))
    return names[:MAX_WORKSPACE_FILES]


def prepare(
    root: Path,
    *,
    company_id: str,
    run_id: str,
    task_kind: str,
    project_id: str | None,
) -> Workspace:
    """The directory a run works in. Never raises for a git problem: the run falls back to a
    per-run directory and the result says so (``available`` False, ``note``)."""
    path = Path(root) / _safe_component(company_id) / _safe_component(run_id)
    wants_repo = project_id is not None and task_kind in ("work", "review")
    if not wants_repo:
        return _scratch(path, run_id)
    assert project_id is not None
    state = status()
    if not state["available"]:
        return _scratch(path, run_id, available=False, note=state["reason"])
    read_only = task_kind == "review"
    try:
        repo = ensure_repo(Path(root), company_id, project_id)
        branch = f"run/{run_id}"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _project_lock(repo):
            _git(["worktree", "prune"], cwd=repo, check=False)
            if path.exists():  # leftover of a crashed earlier attempt with this run id
                shutil.rmtree(path, ignore_errors=True)
            _git(["branch", "-D", branch], cwd=repo, check=False)
            base = _out(_git(["rev-parse", f"refs/heads/{MAIN_BRANCH}"], cwd=repo))
            if read_only:
                _git(["worktree", "add", "-q", "--detach", str(path), MAIN_BRANCH], cwd=repo)
            else:
                _git(["worktree", "add", "-q", "-b", branch, str(path), MAIN_BRANCH], cwd=repo)
            gitdir = Path(_out(_git(["rev-parse", "--absolute-git-dir"], cwd=path)))
            files = _list_files(repo, MAIN_BRANCH)
        if read_only:
            _make_read_only(path)
        return Workspace(
            KIND_REVIEW if read_only else KIND_PROJECT,
            path,
            run_id,
            project_id=project_id,
            repo=repo,
            gitdir=gitdir,
            branch=None if read_only else branch,
            base=base,
            files=files,
        )
    except (GitError, OSError, ValueError) as exc:
        logger.warning("project repository unavailable for run %s: %s", run_id, exc)
        shutil.rmtree(path, ignore_errors=True)
        return _scratch(path, run_id, available=False, note=str(exc)[:300])


def _make_read_only(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            filenames = [f for f in filenames if f != ".git"]
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            with contextlib.suppress(OSError):
                os.chmod(full, stat.S_IMODE(os.stat(full).st_mode) & ~0o222)


def _rmtree(path: Path) -> None:
    def onerror(func, p, _exc):
        with contextlib.suppress(OSError):
            os.chmod(os.path.dirname(p), 0o700)
            os.chmod(p, 0o700)
            func(p)

    shutil.rmtree(path, onerror=onerror)


def _sanitize(root: Path) -> int:
    """Remove what must never be committed from a run's tree: symlinks that leave the tree and
    nested ``.git`` entries. Returns how many were removed."""
    removed = 0
    real_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        for name in list(dirnames):
            full = here / name
            if name == ".git":
                with contextlib.suppress(OSError):
                    if full.is_symlink():
                        full.unlink()
                    else:
                        _rmtree(full)
                dirnames.remove(name)
                removed += 1
            elif full.is_symlink() and not _inside(full, real_root):
                with contextlib.suppress(OSError):
                    full.unlink()
                dirnames.remove(name)
                removed += 1
        for name in filenames:
            full = here / name
            nested_git = name == ".git" and here != root
            if nested_git or (full.is_symlink() and not _inside(full, real_root)):
                with contextlib.suppress(OSError):
                    full.unlink()
                removed += 1
    return removed


def _inside(link: Path, real_root: Path) -> bool:
    try:
        target = Path(os.path.realpath(link))
    except OSError:
        return False
    return target == real_root or real_root in target.parents


# ---------------------------------------------------------------- finalize


def _worktree_git(ws: Workspace, args: list[str], **kw) -> subprocess.CompletedProcess[bytes]:
    assert ws.gitdir is not None
    return _git(args, cwd=ws.path, git_dir=ws.gitdir, work_tree=ws.path, **kw)


def _short(sha: str | None) -> str:
    return (sha or "")[:7]


def _changeset(
    ws: Workspace, commit: str, agent_name: str, *, merged: bool, conflict: bool
) -> ArtifactRef | None:
    assert ws.repo is not None and ws.base
    flags = ["--no-ext-diff", "--no-textconv", "--no-color", "-M"]
    stat_out = _worktree_git(ws, ["diff", *flags, "--stat=100", ws.base, commit]).stdout.decode(
        "utf-8", "replace"
    )
    patch = _worktree_git(ws, ["diff", *flags, ws.base, commit]).stdout.decode("utf-8", "replace")
    state = (
        "merged into main"
        if merged
        else "NOT merged (merge conflict)"
        if conflict
        else "not merged"
    )
    header = (
        f"Changes by {agent_name} (run {ws.run_id[:8]})\n"
        f"Base {_short(ws.base)} -> {_short(commit)}; {state}.\n\n{stat_out.rstrip()}\n\n"
    )
    text = secrets_scan.scrub(header + patch) or ""
    data = text.encode("utf-8")
    if len(data) > MAX_CHANGESET_BYTES:
        note = "\n[... patch truncated at 200 KB]\n"
        data = data[: MAX_CHANGESET_BYTES - len(note)].decode("utf-8", "ignore").encode() + (
            note.encode()
        )
    return ArtifactRef(
        title="Changes",
        content=data.decode("utf-8", "replace"),
        kind="code",
        mime_type="text/x-diff",
    )


def _staged_bytes(ws: Workspace) -> int:
    out = _worktree_git(
        ws, ["diff", "--cached", "--no-renames", "--name-only", "-z", "--diff-filter=AM"]
    ).stdout
    total = 0
    for name in out.decode("utf-8", "replace").split("\0"):
        if not name:
            continue
        with contextlib.suppress(OSError):
            total += os.lstat(ws.path / name).st_size
    return total


def _remove_worktree(ws: Workspace) -> None:
    assert ws.repo is not None
    with _project_lock(ws.repo):
        proc = _git(["worktree", "remove", "--force", str(ws.path)], cwd=ws.repo, check=False)
        if proc.returncode != 0 and ws.path.exists():
            _rmtree(ws.path)
        _git(["worktree", "prune"], cwd=ws.repo, check=False)


def _delete_branch(ws: Workspace) -> None:
    assert ws.repo is not None and ws.branch
    with _project_lock(ws.repo):
        _git(["branch", "-D", ws.branch], cwd=ws.repo, check=False)


def _merge(ws: Workspace, agent_name: str, task_title: str) -> tuple[str | None, list[str]]:
    """Merge the run branch into main. Returns (merge commit, conflicting files); the commit is
    None on a conflict (aborted)."""
    assert ws.repo is not None and ws.branch
    repo = ws.repo
    with _project_lock(repo):
        dirty = _git(["status", "--porcelain", "-z"], cwd=repo).stdout
        if dirty:  # the control plane owns this tree; leftovers of a crash are discarded
            _git(["merge", "--abort"], cwd=repo, check=False)
            _git(["reset", "-q", "--hard"], cwd=repo, check=False)
            _git(["clean", "-fdq"], cwd=repo, check=False)
        message = f"Merge {ws.branch}: {task_title[:120]}\n\nBy {agent_name}."
        proc = _git(
            ["merge", "--no-ff", "--no-edit", "-m", message, ws.branch], cwd=repo, check=False
        )
        if proc.returncode == 0:
            return _out(_git(["rev-parse", "HEAD"], cwd=repo)), []
        unmerged = _git(["diff", "--name-only", "-z", "--diff-filter=U"], cwd=repo, check=False)
        files = [n for n in unmerged.stdout.decode("utf-8", "replace").split("\0") if n]
        _git(["merge", "--abort"], cwd=repo, check=False)
        _git(["reset", "-q", "--hard"], cwd=repo, check=False)
        if not files:
            detail = " ".join(proc.stderr.decode("utf-8", "replace").split())[:300]
            raise GitError(f"git merge failed: {detail}", proc.returncode)
        return None, files


def finalize(
    ws: Workspace,
    *,
    succeeded: bool,
    agent_id: str,
    agent_name: str,
    task_title: str,
) -> Finalized:
    """Commit what the run left, merge it on success, produce the "Changes" artifact and remove
    the worktree. Never raises. A *successful* run that could not be merged comes back with
    ``error_code`` set (``merge_conflict``, ``workspace_too_large``, ``workspace_error``)."""
    ref = ws.ref()
    if ws.kind == KIND_RUN_DIR or ws.repo is None or ws.gitdir is None:
        return Finalized(ref)
    if ws.kind == KIND_REVIEW:
        return _finalize_review(ws, ref)
    out = Finalized(ref)
    try:
        _sanitize(ws.path)
        _worktree_git(ws, ["add", "-A"])
        size = _staged_bytes(ws)
        if size > MAX_STAGED_BYTES:
            _worktree_git(ws, ["reset", "-q"], check=False)
            ref["error"] = ERR_TOO_LARGE
            out.error_code = ERR_TOO_LARGE if succeeded else None
            out.error_message = (
                f"The run left {size // (1024 * 1024)} MB of changes in the project workspace "
                f"(limit {MAX_STAGED_BYTES // (1024 * 1024)} MB); nothing was committed"
            )
            _finish_failed(ws)
            return out
        has_changes = (
            _worktree_git(ws, ["diff", "--cached", "--quiet"], check=False).returncode == 1
        )
        if has_changes:
            identity = (agent_name[:80] or "agent", f"{agent_id}@agents.foundry.invalid")
            message = f"{task_title[:200]}\n\nRun {ws.run_id}"
            _worktree_git(ws, ["commit", "-q", "--no-verify", "-m", message], identity=identity)
        commit = _out(_worktree_git(ws, ["rev-parse", "HEAD"]))
        ref["commit"] = commit
        out.changed = has_changes
        merged = conflict = False
        if has_changes and succeeded:
            merge_commit, files = _merge(ws, agent_name, task_title)
            merged, conflict = merge_commit is not None, merge_commit is None
            if merged:
                ref["merge_commit"] = merge_commit
            else:
                ref["conflict_files"] = files[:50]
                out.error_code = ERR_MERGE_CONFLICT
                out.error_message = (
                    "The run's changes conflict with work merged into the project since it "
                    "started (" + ", ".join(files[:10]) + "). Nothing was merged; the retry "
                    "starts from the current project files."
                )
        ref["merged"], ref["conflict"] = merged, conflict
        out.merged, out.conflict = merged, conflict
        if has_changes:
            artifact = _changeset(ws, commit, agent_name, merged=merged, conflict=conflict)
            if artifact is not None:
                out.artifacts.append(artifact)
        _remove_worktree(ws)
        if not has_changes or merged:
            _delete_branch(ws)  # nothing worth keeping
        else:
            ref["branch_kept"] = True
    except (GitError, OSError) as exc:
        logger.warning("project workspace of run %s could not be finalized: %s", ws.run_id, exc)
        ref["error"] = str(exc)[:300]
        if succeeded:
            out.error_code = ERR_WORKSPACE
            out.error_message = f"The run's work could not be saved to the project: {exc}"[:500]
        with contextlib.suppress(GitError, OSError):
            _remove_worktree(ws)
    return out


def _finish_failed(ws: Workspace) -> None:
    """Remove a worktree whose changes were refused: nothing was committed, so no branch is kept."""
    with contextlib.suppress(GitError, OSError):
        _remove_worktree(ws)
    with contextlib.suppress(GitError, OSError):
        _delete_branch(ws)


def _finalize_review(ws: Workspace, ref: dict[str, Any]) -> Finalized:
    discarded = 0
    with contextlib.suppress(GitError, OSError):
        discarded = len(
            [n for n in _worktree_git(ws, ["status", "--porcelain", "-z"]).stdout.split(b"\0") if n]
        )
    ref["commit"] = ws.base
    ref["discarded"] = discarded
    with contextlib.suppress(GitError, OSError):
        _remove_worktree(ws)
    return Finalized(ref)


def discard(ws: Workspace) -> None:
    """Throw a prepared workspace away without committing anything (the run never started)."""
    if ws.kind == KIND_RUN_DIR or ws.repo is None:
        return
    with contextlib.suppress(GitError, OSError):
        _remove_worktree(ws)
    if ws.branch:
        with contextlib.suppress(GitError, OSError):
            _delete_branch(ws)
