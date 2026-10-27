"""The ONLY module that runs network git (M14 step 0: contracts; WP-B implements the bodies).

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
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.repo_errors import not_built

GIT_TIMEOUT_SECONDS = 60
# Flags a push can never carry (``assert_safe_push_argv`` only ever allows ``--porcelain``).
_REFUSED_PUSH_FLAGS = frozenset(
    {"-f", "--force", "--mirror", "--delete", "-d", "--tags", "--all", "--prune"}
)


class GitNetworkError(Exception):
    """A network git call failed. The message is scrubbed and safe to show."""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class GitNonFastForward(GitNetworkError):
    """The remote rejected a push because it is not a fast-forward."""


class GitAuthFailed(GitNetworkError):
    """The remote refused the token (HTTP 401/403)."""


class UnsafePush(ValueError):
    """The argv of a push is not exactly ``push --porcelain origin <sha>:refs/heads/<branch>``."""


@dataclass(frozen=True)
class RemoteAuth:
    """What one network git call authenticates with. Lives in memory for that call only."""

    host: str
    token: str = field(repr=False)


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
) -> subprocess.CompletedProcess[str]:
    """Run git with M12's hardening (no hooks, no GPG, no fsmonitor, minimal environment, a
    timeout, no shell). Promoted from ``project_workspaces._git``; behaviour of existing calls is
    unchanged. WP-B implements (stub: not built)."""
    raise not_built()


def askpass_script(settings: Settings) -> Path:
    """The static 0700 ``<data>/run/git-askpass`` script: prints ``x-access-token`` for a
    Username prompt and ``$FOUNDRY_GIT_TOKEN`` for a Password prompt naming ``$FOUNDRY_GIT_HOST``;
    anything else prints nothing, so authentication fails instead of leaking."""
    raise not_built()


def pin_config(repo: Path, binding: Any) -> None:
    """Rewrite ``.git/config`` and ``.git/info/exclude`` atomically from the binding."""
    raise not_built()


def clone(settings: Settings, binding: Any, auth: RemoteAuth, repo: Path) -> None:
    """Clone the base and work branches (no tags, no submodules, no LFS) into ``repo``."""
    raise not_built()


def fetch(settings: Settings, binding: Any, auth: RemoteAuth, repo: Path) -> dict[str, str | None]:
    """Fetch ``origin/<default>`` and ``origin/<work>``; returns the new SHAs by ref."""
    raise not_built()


def push(
    settings: Settings, binding: Any, auth: RemoteAuth, repo: Path, *, sha: str, branch: str
) -> None:
    """Push ``sha`` to ``refs/heads/<branch>``: the only push in the code base."""
    raise not_built()


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


def diff_for_review(repo: Path, base: str, head: str, *, limit: int = 200 * 1024) -> str:
    """The scrubbed three-dot diff attached to a PR review task (at most ``limit`` bytes)."""
    raise not_built()


def merged_runs(repo: Path, base: str, head: str) -> list[str]:
    """Run ids named by the ``Merge run/<id>: ...`` commits in ``base..head``."""
    raise not_built()


def reset_integration(repo: Path, binding: Any, *, to: str, rescue_name: str) -> str:
    """Move the work branch to ``origin/<work>`` or ``origin/<default>``; the old head is kept as
    the local branch ``rescue_name``. Returns the new head SHA."""
    raise not_built()
