"""The push gate (M14 step 0: contracts; WP-B implements the bodies).

Every commit about to leave the machine is scanned, every commit's ADDED lines and not just the net
diff (a secret added and removed in an intermediate commit is still in the history that would be
pushed). The same scan runs when a run is merged into the work branch of a remote-backed project,
so a secret never reaches the integration branch (docs/specs/m14-github-backed-projects.md 5.4).

Findings carry kind, severity, description, count and paths, NEVER the matched text.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.schemas.repository import RepoPolicy
from app.services.repo_errors import not_built

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


def scan_range(
    repo: Path,
    base: str | None,
    head: str,
    *,
    policy: RepoPolicy,
    own_secrets: Iterable[str],
    internal: Iterable[list[str]],
) -> list[PushFinding]:
    """Scan ``base..head`` (``base`` None = the whole history up to ``head``): read
    ``git log -p --no-ext-diff --no-textconv`` added lines (capped at ``policy.max_push_bytes``),
    run ``dlp.scan_text`` with the company's stored secrets and internal memory, and check blocked
    file names, ``policy.protected_paths`` (workflow globs lifted only by
    ``allow_workflow_changes``), ``policy.max_file_bytes`` and the commit / byte limits."""
    raise not_built()


def scan_commit_for_merge(
    repo: Path,
    base: str,
    head: str,
    *,
    policy: RepoPolicy,
    own_secrets: Iterable[str],
    internal: Iterable[list[str]],
) -> list[PushFinding]:
    """The same scan for one run branch about to be merged into the work branch
    (``project_workspaces.finalize``)."""
    raise not_built()
