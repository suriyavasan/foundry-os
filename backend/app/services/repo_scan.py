"""The push gate (M14, WP-B).

Every commit about to leave the machine is scanned, every commit's ADDED lines and not just the net
diff (a secret added and removed in an intermediate commit is still in the history that would be
pushed). The same scan runs when a run is merged into the work branch of a remote-backed project,
so a secret never reaches the integration branch (docs/specs/m14-github-backed-projects.md 5.4).

Findings carry kind, severity, description, count and paths, NEVER the matched text.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.guardrails import dlp
from app.schemas.repository import RepoPolicy
from app.services import repo_git

# Which finding kinds block a push (``email`` / ``phone`` are reported only).
BLOCKING_KINDS = frozenset(
    {
        "secret",
        "own_token",
        "internal_memory",
        "blocked_file",
        "protected_path",
        "file_too_large",
        "push_too_large",
    }
)
# Added paths that are never pushed (``.env.example`` / ``.env.sample`` / ``.env.template`` are ok).
BLOCKED_FILE_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.keystore",
    ".git-credentials",
    ".pypirc",
    "credentials.json",
)
ALLOWED_ENV_TEMPLATES = (".env.example", ".env.sample", ".env.template")
MAX_COMMITS_PER_PUSH = 500


@dataclass(frozen=True)
class PushFinding:
    kind: str  # one of BLOCKING_KINDS, or "email" / "phone"
    severity: str  # low | medium | high
    description: str
    count: int = 1
    paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "description": self.description,
            "count": self.count,
            "paths": list(self.paths),
        }


def blocking(findings: Iterable[PushFinding]) -> list[PushFinding]:
    return [f for f in findings if f.blocking]


_MAX_PATHS = 20
_RAW = re.compile(r"^(:+)(.*)\t(.+)$")
_ENV_TEMPLATES = tuple(t.lower() for t in ALLOWED_ENV_TEMPLATES)


@dataclass
class ScanContext:
    """What the push gate needs besides the commits: the repository policy, the company's own
    stored secrets, its internal-only memory and the refs that are NOT ours to scan (upstream
    commits made by people on GitHub)."""

    policy: RepoPolicy
    own_secrets: tuple[str, ...] = ()
    internal: list[list[str]] = field(default_factory=list)
    exclude_refs: tuple[str, ...] = ()

    def exclude(self, spec: Any = None) -> tuple[str, ...]:
        return self.exclude_refs


def _glob_regex(glob: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(glob):
        ch = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def effective_protected_paths(policy: RepoPolicy) -> tuple[str, ...]:
    """``policy.protected_paths`` with the workflow globs lifted by ``allow_workflow_changes``."""
    paths = tuple(policy.protected_paths)
    if policy.allow_workflow_changes:
        paths = tuple(p for p in paths if not p.startswith(".github/"))
    return paths


def _is_protected(path: str, globs: tuple[str, ...]) -> bool:
    return any(_glob_regex(g).match(path) for g in globs)


def is_blocked_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    if name in _ENV_TEMPLATES:
        return False
    return any(fnmatch.fnmatchcase(name, glob) for glob in BLOCKED_FILE_GLOBS)


def _rev_args(base: str | None, head: str, exclude: Iterable[str], repo: Path) -> list[str]:
    spec = [f"{base}..{head}"] if base else [head]
    for ref in exclude:
        if repo_git.ref_sha(repo, ref):
            spec.append(f"^{ref}")
    return spec


_LINE_WINDOW = 2000
_LINE_OVERLAP = 300


def _windowed(text: str) -> str:
    """Very long lines (minified bundles, data blobs) are cut into overlapping windows, so the
    secret patterns run in linear time and a credential on a cut is still seen whole."""
    if len(text) <= _LINE_WINDOW:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        if len(line) <= _LINE_WINDOW:
            out.append(line)
            continue
        step = _LINE_WINDOW - _LINE_OVERLAP
        out.extend(line[i : i + _LINE_WINDOW] for i in range(0, len(line), step))
    return "\n".join(out)


def _unquote(path: str) -> str:
    return path[1:-1] if len(path) >= 2 and path[0] == path[-1] == '"' else path


@dataclass
class _Collected:
    commits: list[str] = field(default_factory=list)
    changed: list[tuple[str, str]] = field(default_factory=list)  # (status, path)
    blobs: dict[str, tuple[str, str]] = field(default_factory=dict)  # blob sha -> (mode, path)
    added: dict[str, list[str]] = field(default_factory=dict)  # path -> added lines
    added_bytes: int = 0
    truncated: bool = False


def _collect(repo: Path, revs: list[str], max_bytes: int) -> _Collected:
    """Every commit of the range, its changed paths (incl. combined diffs of merges) and its ADDED
    lines (never the net diff: a secret added and removed in between is in the history)."""
    col = _Collected()
    col.commits = repo_git.run_git(["rev-list", *revs], cwd=repo).stdout.split()
    if not col.commits:
        return col
    flags = ["--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--cc"]
    raw = repo_git.run_git(
        ["-c", "core.quotePath=false", "log", *flags, "--raw", "--no-abbrev", "--format=", *revs],
        cwd=repo,
    ).stdout
    for line in raw.splitlines():
        match = _RAW.match(line)
        if not match:
            continue
        colons, meta, path = len(match.group(1)), match.group(2).split(), _unquote(match.group(3))
        if len(meta) < 2 * (colons + 1) + 1:
            continue
        modes, shas, status = meta[: colons + 1], meta[colons + 1 : 2 * colons + 2], meta[-1]
        mode, blob = modes[-1], shas[-1]
        col.changed.append((status, path))
        if "D" not in status and mode != "160000" and set(blob) != {"0"}:
            col.blobs[blob] = (mode, path)
    # added lines: ``git log -p`` with a hunk-aware parser (a content line like "++ b/x" is
    # "+++ b/x" in the output; only the header before the first hunk is ever read as a path)
    proc = subprocess.Popen(  # noqa: S603 - run through run_git's argv builder below
        [
            repo_git.git_binary() or "git",
            *repo_git.HARDENING,
            "-c",
            "core.quotePath=false",
            "log",
            "-p",
            "-U0",
            *flags,
            "--format=",
            *revs,
        ],
        cwd=repo,
        env=repo_git.base_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    assert proc.stdout is not None
    path = "(unknown)"
    in_hunk = False
    parents = 1
    try:
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", "replace").rstrip("\n")
            if in_hunk and line[:1] in ("+", "-", " ", "\\") and not line.startswith(("@@",)):
                if line.startswith("\\"):
                    continue
                prefix = line[:parents]
                if "+" in prefix:
                    text = line[parents:]
                    col.added.setdefault(path, []).append(text)
                    col.added_bytes += len(text) + 1
                    if col.added_bytes > max_bytes:
                        col.truncated = True
                        break
                continue
            if (
                line.startswith("diff --git ")
                or line.startswith("diff --cc ")
                or line.startswith("diff --combined ")
            ):
                in_hunk = False
                parents = 2 if line.startswith(("diff --cc", "diff --combined")) else 1
                path = (
                    _unquote(line.split(" b/", 1)[-1]) if " b/" in line else line.split(" ", 2)[-1]
                )
            elif not in_hunk and line.startswith("+++ "):
                target = line[4:].split("\t")[0]
                if target != "/dev/null":
                    path = _unquote(target[2:] if target.startswith("b/") else target)
            elif line.startswith("@@"):
                in_hunk = True
                if line.startswith("@@@"):
                    parents = line.count("@") // 2 - 1
                    parents = max(parents, 2)
                else:
                    parents = 1
    finally:
        if col.truncated:
            proc.kill()
        proc.stdout.close()
        proc.wait()
    return col


def _messages(repo: Path, commits: list[str]) -> str:
    """The commit messages (they are pushed too), via ``cat-file --batch`` (exact framing)."""
    if not commits:
        return ""
    proc = repo_git.run_git(
        ["cat-file", "--batch"],
        cwd=repo,
        text=False,
        input_bytes=("\n".join(commits) + "\n").encode(),
    )
    data = proc.stdout
    out: list[str] = []
    pos = 0
    while pos < len(data):
        end = data.find(b"\n", pos)
        if end < 0:
            break
        header = data[pos:end].split()
        if len(header) < 3 or header[1] != b"commit":
            pos = end + 1
            continue
        size = int(header[2])
        body = data[end + 1 : end + 1 + size].decode("utf-8", "replace")
        out.append(body.split("\n\n", 1)[1] if "\n\n" in body else "")
        pos = end + 1 + size + 1
    return "\n".join(out)


def _blob_sizes(repo: Path, blobs: Iterable[str]) -> dict[str, int]:
    shas = list(blobs)
    if not shas:
        return {}
    proc = repo_git.run_git(
        ["cat-file", "--batch-check"],
        cwd=repo,
        input_bytes=("\n".join(shas) + "\n").encode(),
    )
    sizes: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            sizes[parts[0]] = int(parts[2])
    return sizes


def scan_range(
    repo: Path,
    base: str | None,
    head: str,
    *,
    policy: RepoPolicy,
    own_secrets: Iterable[str],
    internal: Iterable[list[str]],
    exclude: Iterable[str] = (),
) -> list[PushFinding]:
    """Scan ``base..head`` (``base`` None = the whole history up to ``head``; ``exclude`` refs
    that exist are subtracted, so upstream commits made by people are not scanned): read
    ``git log -p --no-ext-diff --no-textconv`` added lines (capped at ``policy.max_push_bytes``),
    run ``dlp.scan_text`` with the company's stored secrets and internal memory, and check blocked
    file names, ``policy.protected_paths`` (workflow globs lifted only by
    ``allow_workflow_changes``), ``policy.max_file_bytes`` and the commit / byte limits."""
    revs = _rev_args(base, head, exclude, repo)
    col = _collect(repo, revs, policy.max_push_bytes)
    findings: list[PushFinding] = []
    if not col.commits:
        return findings
    secrets = tuple(s for s in own_secrets if s)
    passages = list(internal)

    def add(kind: str, severity: str, description: str, count: int, paths: Iterable[str] = ()):
        findings.append(PushFinding(kind, severity, description, count, tuple(paths)[:_MAX_PATHS]))

    # --- sizes (first: a push over the limit is refused without reading all of it)
    sizes = _blob_sizes(repo, col.blobs)
    total = sum(sizes.values())
    oversize = col.truncated or total > policy.max_push_bytes
    big = sorted({col.blobs[b][1] for b, size in sizes.items() if size > policy.max_file_bytes})
    if big:
        add(
            "file_too_large",
            "high",
            f"Files larger than {policy.max_file_bytes // 1024} KB",
            len(big),
            big,
        )
    if oversize:
        add(
            "push_too_large",
            "high",
            f"More than {policy.max_push_bytes // (1024 * 1024)} MB of new content",
            1,
        )
    elif len(col.commits) > MAX_COMMITS_PER_PUSH:
        add(
            "push_too_large",
            "high",
            f"More than {MAX_COMMITS_PER_PUSH} commits in one push",
            len(col.commits),
        )
    # --- content (per file, plus the commit messages)
    by_kind: dict[str, dict[str, Any]] = {}
    chunks = (
        {} if oversize else {path: _windowed("\n".join(lines)) for path, lines in col.added.items()}
    )
    if not oversize:
        chunks["(commit message)"] = _messages(repo, col.commits)
    for path, text in chunks.items():
        for found in dlp.scan_text(text, own_secrets=secrets, internal=passages):
            slot = by_kind.setdefault(
                found.kind,
                {
                    "severity": found.severity,
                    "description": found.description,
                    "count": 0,
                    "paths": [],
                },
            )
            slot["count"] += found.count
            if path not in slot["paths"]:
                slot["paths"].append(path)
    for kind in ("secret", "own_token", "internal_memory", "email", "phone"):
        if kind in by_kind:
            slot = by_kind[kind]
            add(kind, slot["severity"], slot["description"], slot["count"], slot["paths"])
    # --- file names
    blocked = sorted({p for st, p in col.changed if "D" not in st and is_blocked_file(p)})
    if blocked:
        add(
            "blocked_file",
            "high",
            "Files that must never be pushed (keys, .env, credentials)",
            len(blocked),
            blocked,
        )
    globs = effective_protected_paths(policy)
    protected = sorted({p for _st, p in col.changed if _is_protected(p, globs)})
    if protected:
        add(
            "protected_path",
            "high",
            "Changes to protected paths (workflows and the like)",
            len(protected),
            protected,
        )
    return findings


def scan_commit_for_merge(
    repo: Path,
    base: str,
    head: str,
    *,
    policy: RepoPolicy,
    own_secrets: Iterable[str],
    internal: Iterable[list[str]],
    exclude: Iterable[str] = (),
) -> list[PushFinding]:
    """The same scan for one run branch about to be merged into the work branch
    (``project_workspaces.finalize``)."""
    return scan_range(
        repo,
        base,
        head,
        policy=policy,
        own_secrets=own_secrets,
        internal=internal,
        exclude=exclude,
    )
