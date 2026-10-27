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
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.runtimes.base import ArtifactRef
from app.services import repo_git, secrets_scan
from app.services.repo_git import (  # noqa: F401  (re-exported: M12 code and tests use these names)
    COMMITTER_EMAIL,
    COMMITTER_NAME,
    EXCLUDES,
    GIT_TIMEOUT_SECONDS,
    GitError,
)

try:  # POSIX only; without it the project repository is reported as unavailable
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from app.services.repo_scan import ScanContext
    from app.services.repo_sync import WorkspaceSpec

logger = logging.getLogger("company_os.project_workspaces")

LOCK_TIMEOUT_SECONDS = 120
MAX_STAGED_BYTES = 50 * 1024 * 1024
MAX_CHANGESET_BYTES = 200 * 1024
MAX_WORKSPACE_FILES = 60
MAIN_BRANCH = "main"

KIND_PROJECT = "project"  # a worktree on branch run/<run_id>
KIND_REVIEW = "review"  # a detached, read-only worktree; changes are discarded
KIND_RUN_DIR = "run_dir"  # a plain per-run directory

ERR_MERGE_CONFLICT = "merge_conflict"  # exactly this string: WP2's retry policy may classify it
ERR_TOO_LARGE = "workspace_too_large"
ERR_WORKSPACE = "workspace_error"


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
    return repo_git.base_env(identity)


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
    return repo_git.run_git(
        args,
        cwd=cwd,
        git_dir=git_dir,
        work_tree=work_tree,
        identity=identity,
        check=check,
        timeout=GIT_TIMEOUT_SECONDS,
        text=False,
        binary=binary,
    )


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


project_lock = _project_lock  # public name for repo_git / repo_sync


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
    integration: str = MAIN_BRANCH  # the branch a successful run is merged into (M14: work branch)
    spec: WorkspaceSpec | None = None  # M14: how repo_sync asked for this workspace
    conflict_files: list[str] = field(default_factory=list)  # upstream_merge: files to resolve

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


def _existing_repo(root: Path, company_id: str, project_id: str) -> Path:
    """A remote-backed project's clone (never created here: ``repo_sync`` clones it)."""
    repo = main_repo(root, company_id, project_id)
    if not (repo / ".git").is_dir():
        raise GitError("the project's repository clone is not ready")
    return repo


def prepare(
    root: Path,
    *,
    company_id: str,
    run_id: str,
    task_kind: str,
    project_id: str | None,
    spec: WorkspaceSpec | None = None,
) -> Workspace:
    """The directory a run works in. Never raises for a git problem: the run falls back to a
    per-run directory and the result says so (``available`` False, ``note``).

    ``spec`` (M14, from ``repo_sync.workspace_spec_for``) names the integration branch of a
    GitHub-backed project and the special workspace kinds (read-only review of a commit, an
    upstream merge in progress); without it everything is M12: branch ``main``."""
    path = Path(root) / _safe_component(company_id) / _safe_component(run_id)
    wants_repo = project_id is not None and task_kind in ("work", "review")
    if not wants_repo:
        return _scratch(path, run_id)
    assert project_id is not None
    state = status()
    if not state["available"]:
        return _scratch(path, run_id, available=False, note=state["reason"])
    branch_name = spec.branch if spec is not None else MAIN_BRANCH
    remote_backed = spec is not None and spec.repository_id is not None
    read_only = task_kind == "review" or (
        spec is not None and (spec.read_only or spec.kind == KIND_REVIEW)
    )
    upstream = spec is not None and spec.kind == "upstream_merge" and not read_only
    try:
        if remote_backed:
            repo = _existing_repo(Path(root), company_id, project_id)
        else:
            repo = ensure_repo(Path(root), company_id, project_id)
        branch = f"run/{run_id}"
        path.parent.mkdir(parents=True, exist_ok=True)
        conflicts: list[str] = []
        with _project_lock(repo):
            _git(["worktree", "prune"], cwd=repo, check=False)
            if path.exists():  # leftover of a crashed earlier attempt with this run id
                shutil.rmtree(path, ignore_errors=True)
            _git(["branch", "-D", branch], cwd=repo, check=False)
            if (
                _git(
                    ["rev-parse", "--verify", "-q", f"refs/heads/{branch_name}"],
                    cwd=repo,
                    check=False,
                ).returncode
                != 0
            ):
                raise GitError(f"the project branch {branch_name} does not exist")
            start = spec.ref if spec is not None and spec.ref else f"refs/heads/{branch_name}"
            base = _out(_git(["rev-parse", f"{start}^{{commit}}"], cwd=repo))
            if read_only:
                _git(["worktree", "add", "-q", "--detach", str(path), base], cwd=repo)
            else:
                _git(["worktree", "add", "-q", "-b", branch, str(path), base], cwd=repo)
            gitdir = Path(_out(_git(["rev-parse", "--absolute-git-dir"], cwd=path)))
            if upstream:
                assert spec is not None and spec.upstream_sha
                _git(
                    ["merge", "--no-ff", "--no-commit", spec.upstream_sha],
                    cwd=path,
                    git_dir=gitdir,
                    work_tree=path,
                    check=False,
                )
                unmerged = _git(
                    ["diff", "--name-only", "-z", "--diff-filter=U"],
                    cwd=path,
                    git_dir=gitdir,
                    work_tree=path,
                    check=False,
                )
                conflicts = [n for n in unmerged.stdout.decode("utf-8", "replace").split("\0") if n]
            files = _list_files(repo, base)
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
            integration=branch_name,
            spec=spec,
            conflict_files=conflicts,
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
        current = _out(_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=repo, check=False))
        if current != ws.integration:
            _git(["checkout", "-q", "-f", ws.integration], cwd=repo)
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


_CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.MULTILINE)
ERR_SECRET = "secret_detected"
ERR_PROTECTED = "protected_path"
ERR_UNRESOLVED = "merge_conflict_unresolved"


def _unresolved_files(ws: Workspace) -> list[str]:
    """Files of an upstream merge that still carry conflict markers."""
    bad: list[str] = []
    for name in ws.conflict_files:
        target = (ws.path / name).resolve()
        if ws.path.resolve() not in target.parents:
            continue
        try:
            text = target.read_text(errors="replace")
        except OSError:
            continue
        if _CONFLICT_MARKER.search(text):
            bad.append(name)
    return bad


def _blocked_by_gate(
    ws: Workspace, commit: str, scan: ScanContext | None
) -> tuple[str, str, list[dict[str, Any]]] | None:
    """Run the push gate over the run's commit before it can reach the integration branch of a
    remote-backed project. Returns (error code, message, findings) or None. The message names
    kinds and paths, never the matched text."""
    if (
        scan is None
        or ws.spec is None
        or not ws.spec.scan_secrets
        or not ws.base
        or ws.repo is None
    ):
        return None
    from app.services import repo_scan  # local: repo_scan imports repo_git, not this module

    findings = repo_scan.blocking(
        repo_scan.scan_commit_for_merge(
            ws.repo,
            ws.base,
            commit,
            policy=scan.policy,
            own_secrets=scan.own_secrets,
            internal=scan.internal,
            exclude=scan.exclude(ws.spec),
        )
    )
    if not findings:
        return None
    kinds = {f.kind for f in findings}
    if kinds & {"secret", "own_token", "internal_memory", "blocked_file"}:
        code = ERR_SECRET
    elif "protected_path" in kinds:
        code = ERR_PROTECTED
    else:
        code = ERR_TOO_LARGE
    parts = []
    for f in findings:
        where = f" ({', '.join(f.paths[:5])})" if f.paths else ""
        parts.append(f"{f.description}{where}")
    message = (
        "The run's changes were NOT merged because they must not leave the machine: "
        + "; ".join(parts)
        + ". Remove them from the project files (a secret must be read from the environment, "
        "never written to a file) and try again."
    )
    return code, message[:900], [f.as_dict() for f in findings]


def finalize(
    ws: Workspace,
    *,
    succeeded: bool,
    agent_id: str,
    agent_name: str,
    task_title: str,
    scan: ScanContext | None = None,
) -> Finalized:
    """Commit what the run left, merge it on success, produce the "Changes" artifact and remove
    the worktree. Never raises. A *successful* run that could not be merged comes back with
    ``error_code`` set (``merge_conflict``, ``workspace_too_large``, ``workspace_error``, and for
    GitHub-backed projects ``secret_detected``, ``protected_path``,
    ``merge_conflict_unresolved``)."""
    ref = ws.ref()
    if ws.kind == KIND_RUN_DIR or ws.repo is None or ws.gitdir is None:
        return Finalized(ref)
    if ws.kind == KIND_REVIEW:
        return _finalize_review(ws, ref)
    out = Finalized(ref)
    upstream = ws.spec is not None and ws.spec.kind == "upstream_merge"
    try:
        if upstream and succeeded:
            unresolved = _unresolved_files(ws)
            if unresolved:
                ref["error"] = ERR_UNRESOLVED
                out.error_code = ERR_UNRESOLVED
                out.error_message = (
                    "Conflict markers are still present in: "
                    + ", ".join(unresolved[:10])
                    + ". Resolve every conflict (remove the <<<<<<<, ======= and >>>>>>> lines) "
                    "and finish again."
                )
                _finish_failed(ws)
                return out
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
        merging = upstream and (
            _worktree_git(ws, ["rev-parse", "-q", "--verify", "MERGE_HEAD"], check=False).returncode
            == 0
        )
        has_changes = merging or (
            _worktree_git(ws, ["diff", "--cached", "--quiet"], check=False).returncode == 1
        )
        if has_changes:
            identity = (agent_name[:80] or "agent", f"{agent_id}@agents.foundry.invalid")
            message = f"{task_title[:200]}\n\nRun {ws.run_id}"
            _worktree_git(ws, ["commit", "-q", "--no-verify", "-m", message], identity=identity)
        commit = _out(_worktree_git(ws, ["rev-parse", "HEAD"]))
        ref["commit"] = commit
        out.changed = has_changes
        if (
            upstream
            and merging
            and ws.spec is not None
            and ws.spec.upstream_sha
            and not repo_git.is_ancestor(ws.repo, ws.spec.upstream_sha, commit)
        ):
            raise GitError("the upstream merge was not completed")
        if has_changes:
            blocked = _blocked_by_gate(ws, commit, scan)
            if blocked is not None:
                code, message, findings = blocked
                ref["error"] = code
                ref["blocked"] = findings
                out.error_code = code if succeeded else None
                out.error_message = message
                _finish_failed(ws)  # the branch is deleted, not kept: it holds the secret
                return out
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
