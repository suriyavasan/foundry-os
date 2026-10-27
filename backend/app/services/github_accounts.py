"""GitHub accounts for repository bindings (M14 step 0: contracts; WP-A implements).

Reads the M8 connected account's credential inside the control plane (the token never leaves it),
builds a ``GithubClient`` and inspects what a token can do (docs/specs/m14-github-backed-projects.md
2.2 and 2.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.principal import CompanyContext, require_human_admin
from app.integrations.context import IntegrationContext
from app.integrations.github import GithubClient
from app.models.repository import ProjectRepository
from app.schemas.repository import GithubInspectRequest, GithubInspectResult, RepoMode
from app.services.repo_errors import not_built

if TYPE_CHECKING:
    from app.services.repo_sync import RepoEnv


def token_kind(token: str) -> str:
    """``fine_grained`` (``github_pat_``), ``classic`` (``ghp_`` / ``gho_`` / ...) or ``other``,
    derived from the prefix inside the control plane."""
    raise not_built()


def inspect(
    db: Session, ctx: CompanyContext, ic: IntegrationContext, data: GithubInspectRequest
) -> GithubInspectResult:
    """``POST /github/inspect``: login, token kind and expiry, owner candidates and, for a
    repository, exists / accessible / archived / empty / size / default branch / protection /
    workflows / push allowed / capabilities / ``missing_permissions`` / ``warnings``. Owner/admin
    humans only. Never returns the token."""
    require_human_admin(ctx, "inspect a GitHub account")
    raise not_built()


def client_for_binding(
    db: Session, env: RepoEnv | IntegrationContext, binding: ProjectRepository
) -> GithubClient:
    """A client for the binding's connected account: decrypts the credential once, passes the
    binding's ETags and the ``github_api_url`` / ``github_web_url`` settings. 401 marks the binding
    ``auth_failed`` (the caller handles it)."""
    raise not_built()


def probe_capabilities(
    client: GithubClient, full_name: str, *, mode: RepoMode = "connect"
) -> dict[str, bool]:
    """Which features this token can use on the repository (contents, pull requests, issues,
    checks, actions, dependabot, administration, ...): missing ones turn a feature off, never fail
    the bind."""
    raise not_built()
