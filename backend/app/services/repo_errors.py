"""Errors of GitHub-backed projects (M14), one class per code in the spec's error table
(docs/specs/m14-github-backed-projects.md section 6.3). Raised by the repository services; rendered
by the shared ``{"error": {code, message, details}}`` envelope."""

from __future__ import annotations

from app.core.errors import Conflict, NotAvailable, ValidationFailed

MILESTONE = "M14"


def not_built(what: str = "GitHub-backed projects") -> NotAvailable:
    """What a step-0 stub raises until its work package replaces it (rule 3: never faked)."""
    return NotAvailable(f"{what} are not built yet", milestone=MILESTONE)


class ProjectHasRepository(Conflict):
    code = "project_has_repository"
    default_message = "This project already has a repository"


class NoAccount(Conflict):
    code = "no_account"
    default_message = "Connect a GitHub account first"


class ConfirmPublicRequired(ValidationFailed):
    code = "confirm_public_required"
    default_message = "A public repository needs an explicit confirmation (confirm_public)"


class RepositoryExists(Conflict):
    code = "repository_exists"
    default_message = "That repository already exists"


class RepositoryNotAccessible(Conflict):
    code = "repository_not_accessible"
    default_message = "The token cannot see that repository"


class RepositoryArchived(Conflict):
    code = "repository_archived"
    default_message = "That repository is archived"


class TokenCannotPush(Conflict):
    """``details.missing_permissions`` lists what the token lacks."""

    code = "token_cannot_push"
    default_message = "The token cannot push to that repository"


class RepositoryTooLarge(Conflict):
    code = "repository_too_large"
    default_message = "That repository is larger than this installation allows"


class AdministrationPermissionMissing(Conflict):
    code = "administration_permission_missing"
    default_message = (
        "The token cannot create repositories (it needs the Administration permission); "
        "create an empty repository on github.com and connect it instead"
    )


class BranchProtected(Conflict):
    code = "branch_protected"
    default_message = "The base branch is protected, so direct_push is not possible"


class RepositoryBusy(Conflict):
    code = "repository_busy"
    default_message = "A pull request is merging or a run of the project is active"


class RepoNotBound(Conflict):
    code = "repo_not_bound"
    default_message = "That repository is not bound to a project of this company"


class PullRequestChanged(Conflict):
    code = "pull_request_changed"
    default_message = "The pull request changed since it was reviewed"


class PublicUrlEphemeral(Conflict):
    code = "public_url_ephemeral"
    default_message = "Webhooks need a stable public URL; a quick tunnel's address changes"
