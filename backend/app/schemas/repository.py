"""M14 schemas: GitHub-backed projects (docs/specs/m14-github-backed-projects.md section 6.3).

``default_policy`` is the one place the onboarding defaults of "new repository" and "existing
repository" live (section 2.1); the web app and the CLI read them from the API, never duplicate
them.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ApiModel, RequestModel, Timestamp

MergeMode = Literal["pr_human_merge", "pr_auto_merge", "direct_push"]
MergeMethod = Literal["squash", "merge", "rebase"]
CiMode = Literal["required", "if_present", "ignore"]
IssueIntake = Literal["off", "collaborators", "labeled", "everyone"]
AlertLevel = Literal["off", "low", "medium", "high", "critical"]
ReleaseMode = Literal["off", "draft", "publish"]
ReviewAuthors = Literal["collaborators", "everyone"]
RepoMode = Literal["create", "connect"]
Visibility = Literal["private", "public", "internal"]
RepoStatus = Literal[
    "pending", "creating", "cloning", "active", "paused", "auth_failed", "error", "disconnected"
]
SyncState = Literal[
    "idle",
    "upstream_conflict",
    "upstream_rewritten",
    "push_blocked",
    "rate_limited",
    "awaiting_approval",
]
PullStage = Literal[
    "opening",
    "reviewing",
    "changes_requested",
    "approved",
    "merging",
    "awaiting_human",
    "merged",
    "closed",
]
OnboardingStatus = Literal["not_needed", "pending", "running", "done", "failed"]
ItemKind = Literal["issue", "review", "internal_review", "check_failure", "alert"]
ItemStatus = Literal["new", "queued", "tasked", "ignored", "done"]
TokenKind = Literal["fine_grained", "classic", "other"]

DEFAULT_PROTECTED_PATHS = [".github/workflows/**", ".github/actions/**", ".gitmodules"]
WORK_BRANCH_PREFIX = "foundry/"
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
FULL_NAME_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})$")
_URL_RE = re.compile(
    r"^https?://[^/]+/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


def parse_repo_ref(text: str) -> tuple[str, str] | None:
    """``owner/name`` or a github.com URL (``.git`` optional) -> ``(owner, name)``, else None."""
    value = text.strip()
    match = _URL_RE.match(value) if "://" in value else FULL_NAME_RE.match(value)
    if match is None:
        return None
    owner, name = match.group(1), match.group(2)
    if name in (".", "..") or name.endswith(".git"):
        return None
    return owner, name


def valid_repo_name(name: str) -> bool:
    return bool(NAME_RE.match(name)) and name not in (".", "..") and not name.endswith(".git")


_BAD_REF = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def valid_branch_name(name: str) -> bool:
    """git ref-format rules for a branch name (``git check-ref-format --branch``, simplified)."""
    if not name or len(name) > 255 or name in ("@", "HEAD"):
        return False
    if _BAD_REF.search(name) or ".." in name or "@{" in name or "//" in name:
        return False
    if name.startswith(("/", "-")) or name.endswith(("/", ".", ".lock")):
        return False
    return not any(part.startswith(".") or part.endswith(".lock") for part in name.split("/"))


# ------------------------------------------------------------------ policy


def _check_stale(v: int) -> int:
    if v != 0 and v < 7:
        raise ValueError("stale_branch_days must be 0 (never) or between 7 and 365")
    return v


def _check_paths(v: list[str]) -> list[str]:
    cleaned = [p.strip() for p in v]
    if any(not p or len(p) > 200 for p in cleaned):
        raise ValueError("each protected path is a glob of 1-200 characters")
    return list(dict.fromkeys(cleaned))


class RepoPolicy(ApiModel):
    """The owner's repository policy (``project_repositories.policy``)."""

    merge_mode: MergeMode = "pr_human_merge"
    pr_merge_method: MergeMethod = "squash"
    pr_internal_review: bool = True
    ci_mode: CiMode = "if_present"
    ci_wait_seconds: int = Field(default=600, ge=60, le=7200)
    pr_batch_quiet_seconds: int = Field(default=600, ge=0, le=86_400)
    max_pr_revisions: int = Field(default=3, ge=0, le=10)
    max_ci_fix_attempts: int = Field(default=3, ge=0, le=10)
    issue_intake: IssueIntake = "collaborators"
    intake_label: str = Field(default="foundry", min_length=1, max_length=50)
    max_open_intake_tasks: int = Field(default=5, ge=0, le=50)
    respond_to_reviews: bool = True
    review_authors: ReviewAuthors = "collaborators"
    fix_ci_failures: bool = True
    security_alerts: AlertLevel = "high"
    releases: ReleaseMode = "off"
    stale_branch_days: int = Field(default=30, ge=0, le=365)
    docs_upkeep: bool = False
    allow_workflow_changes: bool = False
    protected_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PROTECTED_PATHS), max_length=20
    )
    poll_interval_seconds: int = Field(default=300, ge=60, le=3600)
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_push_bytes: int = Field(default=100 * 1024 * 1024, ge=1024 * 1024, le=500 * 1024 * 1024)

    @field_validator("stale_branch_days")
    @classmethod
    def _stale(cls, v: int) -> int:
        return _check_stale(v)

    @field_validator("protected_paths")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return _check_paths(v)


class RepoPolicyInput(RequestModel):
    """The same fields, all optional: create and patch apply only what is given."""

    merge_mode: MergeMode | None = None
    pr_merge_method: MergeMethod | None = None
    pr_internal_review: bool | None = None
    ci_mode: CiMode | None = None
    ci_wait_seconds: int | None = Field(default=None, ge=60, le=7200)
    pr_batch_quiet_seconds: int | None = Field(default=None, ge=0, le=86_400)
    max_pr_revisions: int | None = Field(default=None, ge=0, le=10)
    max_ci_fix_attempts: int | None = Field(default=None, ge=0, le=10)
    issue_intake: IssueIntake | None = None
    intake_label: str | None = Field(default=None, min_length=1, max_length=50)
    max_open_intake_tasks: int | None = Field(default=None, ge=0, le=50)
    respond_to_reviews: bool | None = None
    review_authors: ReviewAuthors | None = None
    fix_ci_failures: bool | None = None
    security_alerts: AlertLevel | None = None
    releases: ReleaseMode | None = None
    stale_branch_days: int | None = Field(default=None, ge=0, le=365)
    docs_upkeep: bool | None = None
    allow_workflow_changes: bool | None = None
    protected_paths: list[str] | None = Field(default=None, max_length=20)
    poll_interval_seconds: int | None = Field(default=None, ge=60, le=3600)
    max_file_bytes: int | None = Field(default=None, ge=1024, le=50 * 1024 * 1024)
    max_push_bytes: int | None = Field(default=None, ge=1024 * 1024, le=500 * 1024 * 1024)

    @field_validator(
        "merge_mode",
        "pr_merge_method",
        "pr_internal_review",
        "ci_mode",
        "ci_wait_seconds",
        "pr_batch_quiet_seconds",
        "max_pr_revisions",
        "max_ci_fix_attempts",
        "issue_intake",
        "intake_label",
        "max_open_intake_tasks",
        "respond_to_reviews",
        "review_authors",
        "fix_ci_failures",
        "security_alerts",
        "releases",
        "stale_branch_days",
        "docs_upkeep",
        "allow_workflow_changes",
        "protected_paths",
        "poll_interval_seconds",
        "max_file_bytes",
        "max_push_bytes",
        mode="before",
    )
    @classmethod
    def _not_null(cls, v: Any) -> Any:  # absent means "unchanged"; an explicit null is an error
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("stale_branch_days")
    @classmethod
    def _stale(cls, v: int | None) -> int | None:
        return None if v is None else _check_stale(v)

    @field_validator("protected_paths")
    @classmethod
    def _paths(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_paths(v)


def default_policy(mode: RepoMode, *, has_workflows: bool = False) -> RepoPolicy:
    """Onboarding defaults (section 2.1).

    * ``create`` (a new repository the company owns): ``pr_auto_merge``, intake from collaborators,
      CI ``if_present``, docs upkeep on.
    * ``connect`` (an existing repository humans already work in): ``pr_human_merge``, intake only
      of issues labelled ``foundry``, CI ``required`` when ``.github/workflows`` exists, docs
      upkeep off.
    """
    if mode == "create":
        return RepoPolicy(
            merge_mode="pr_auto_merge",
            issue_intake="collaborators",
            ci_mode="if_present",
            docs_upkeep=True,
        )
    return RepoPolicy(
        merge_mode="pr_human_merge",
        issue_intake="labeled",
        ci_mode="required" if has_workflows else "if_present",
        docs_upkeep=False,
    )


# ------------------------------------------------------------------ create / update


class RepositoryCreate(RequestModel):
    mode: RepoMode
    connected_account_id: str = Field(min_length=1, max_length=36)
    # Exactly one: bind an existing project, or create one with this name.
    project_id: str | None = Field(default=None, max_length=36)
    project_name: str | None = Field(default=None, min_length=1, max_length=200)
    project_description: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=39)
    name: str | None = Field(default=None, max_length=100)
    full_name: str | None = Field(default=None, max_length=300)  # owner/name or a github.com URL
    visibility: Literal["private", "public"] = "private"
    confirm_public: bool = False
    description: str | None = Field(default=None, max_length=350)
    default_branch: str | None = Field(default=None, max_length=255)
    work_branch: str | None = Field(default=None, max_length=255)
    initialize: bool = True  # create: README + .gitignore as the first commit
    publish_local: bool = False  # connect an empty remote: push the local history
    company_default: bool = True
    policy: RepoPolicyInput = Field(default_factory=RepoPolicyInput)

    @field_validator("owner")
    @classmethod
    def _owner(cls, v: str | None) -> str | None:
        if v is not None and not OWNER_RE.match(v):
            raise ValueError("not a valid GitHub user or organisation name")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is not None and not valid_repo_name(v):
            raise ValueError("not a valid repository name (letters, digits, '.', '_', '-')")
        return v

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, v: str | None) -> str | None:
        if v is not None and parse_repo_ref(v) is None:
            raise ValueError("expected OWNER/NAME or https://github.com/OWNER/NAME")
        return v

    @field_validator("default_branch")
    @classmethod
    def _default_branch(cls, v: str | None) -> str | None:
        if v is not None and not valid_branch_name(v):
            raise ValueError("not a valid branch name")
        return v

    @field_validator("work_branch")
    @classmethod
    def _work_branch(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not valid_branch_name(v) or not v.startswith(WORK_BRANCH_PREFIX):
            raise ValueError(
                f"the work branch must be a valid branch name under {WORK_BRANCH_PREFIX}"
            )
        return v

    @model_validator(mode="after")
    def _shape(self) -> RepositoryCreate:
        if bool(self.project_id) == bool(self.project_name):
            raise ValueError("give exactly one of project_id and project_name")
        if self.mode == "connect" and not (self.full_name or (self.owner and self.name)):
            raise ValueError("connecting a repository needs full_name (or owner and name)")
        if self.mode == "create" and not (self.name or self.full_name):
            raise ValueError("creating a repository needs a name")
        if self.work_branch and self.work_branch == self.default_branch:
            raise ValueError("the work branch must differ from the default branch")
        if self.description and self.mode == "connect":
            raise ValueError("description applies only when creating a repository")
        return self


class RepositoryUpdate(RequestModel):
    policy: RepoPolicyInput | None = None
    default_branch: str | None = Field(default=None, max_length=255)
    is_company_default: bool | None = None

    @field_validator("default_branch")
    @classmethod
    def _default_branch(cls, v: str | None) -> str | None:
        if v is not None and not valid_branch_name(v):
            raise ValueError("not a valid branch name")
        return v

    @field_validator("policy", "is_company_default", mode="before")
    @classmethod
    def _not_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("must not be null")
        return v


class DisconnectBody(RequestModel):
    confirm: bool = False


class IntegrationReset(RequestModel):
    to: Literal["remote_work_branch", "default_branch"]
    confirm: bool = False


class WebhookUpdate(RequestModel):
    enabled: bool


# ------------------------------------------------------------------ responses


class RepoPullRequestSummary(ApiModel):
    id: str
    number: int
    url: str | None
    title: str
    stage: PullStage
    revision: int
    head_sha: str | None
    verdict: str | None
    checks_state: str
    mergeable_state: str | None
    opened_at: Timestamp | None


class RepoPullRequest(ApiModel):
    id: str
    company_id: str
    repository_id: str
    number: int
    url: str | None
    title: str
    head_branch: str
    base_branch: str
    head_sha: str | None
    review_sha: str | None
    verdict_sha: str | None
    merged_sha: str | None
    stage: PullStage
    revision: int
    review_task_id: str | None
    verdict: str | None
    verdict_note: str | None
    verdict_agent_id: str | None
    checks_state: str
    checks: list[dict[str, Any]]
    checks_since: Timestamp | None
    ci_fix_attempts: int
    mergeable_state: str | None
    human_review_state: str | None
    last_review_seen_at: Timestamp | None
    approval_id: str | None
    merged_by: str | None
    task_ids: list[str]
    opened_at: Timestamp | None
    merged_at: Timestamp | None
    closed_at: Timestamp | None
    last_error: str | None
    created_at: Timestamp
    updated_at: Timestamp


class RepoItem(ApiModel):
    """GitHub content that became (or may become) a task. Bodies are clipped excerpts and always
    untrusted: ``untrusted`` is true on every item and clients must never present them as ours."""

    id: str
    company_id: str
    repository_id: str
    kind: ItemKind
    external_id: str
    number: int | None
    title: str | None
    url: str | None
    author: str | None
    author_association: str | None
    remote_state: str | None
    status: ItemStatus
    ignore_reason: str | None
    task_id: str | None
    pull_request_id: str | None
    revision: int | None
    severity: str | None
    body_excerpt: str | None
    injection_flags: list[str]
    data: dict[str, Any]
    untrusted: bool = True
    remote_updated_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp


class WebhookState(ApiModel):
    enabled: bool = False
    url: str | None = None
    stale: bool = False  # the public URL changed since the hook was created


class ProjectRepository(ApiModel):
    """A repository binding. Never includes the token, the ETags, the sync lease or the webhook
    secret's credential id."""

    id: str
    company_id: str
    project_id: str
    project_name: str | None = None
    provider: str
    auth_kind: str
    connected_account_id: str | None
    owner: str
    name: str
    full_name: str
    html_url: str | None
    remote_url: str
    visibility: Visibility
    default_branch: str
    work_branch: str
    origin: Literal["created", "connected", "published"]
    status: RepoStatus
    status_reason: str | None
    sync_state: SyncState
    policy: RepoPolicy
    capabilities: dict[str, Any]
    protection: dict[str, Any]
    last_synced_at: Timestamp | None
    next_sync_at: Timestamp | None
    last_issues_sync_at: Timestamp | None
    last_alerts_sync_at: Timestamp | None
    last_cleanup_at: Timestamp | None
    token_expires_at: Timestamp | None
    rate_limit_reset_at: Timestamp | None
    sync_failures: int
    rate_limit_remaining: int | None
    last_fetched_default_sha: str | None
    last_pushed_sha: str | None
    integration_head_sha: str | None
    webhook_id: str | None
    webhook_url: str | None
    webhook: WebhookState = Field(default_factory=WebhookState)
    onboarding_status: OnboardingStatus
    onboarding_task_id: str | None
    is_company_default: bool
    pr_opening_paused: bool
    open_pull_request: RepoPullRequestSummary | None = None
    created_by_type: str | None
    created_by_id: str | None
    created_at: Timestamp
    updated_at: Timestamp


class RepoAvailability(ApiModel):
    """``available: false`` means git (or file locking) is missing here; nothing is faked."""

    available: bool
    reason: str | None = None
    git_version: str | None = None
    milestone: str = "M14"


# ------------------------------------------------------------------ inspect


class GithubInspectRequest(RequestModel):
    connected_account_id: str = Field(min_length=1, max_length=36)
    full_name: str | None = Field(default=None, max_length=300)  # OWNER/NAME or a URL
    owner: str | None = Field(default=None, max_length=39)
    # Which permission checklist to evaluate: a new repository needs Administration, an existing
    # one does not. Default ``connect``.
    mode: RepoMode = "connect"

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, v: str | None) -> str | None:
        if v is not None and parse_repo_ref(v) is None:
            raise ValueError("expected OWNER/NAME or https://github.com/OWNER/NAME")
        return v

    @field_validator("owner")
    @classmethod
    def _owner(cls, v: str | None) -> str | None:
        if v is not None and not OWNER_RE.match(v):
            raise ValueError("not a valid GitHub user or organisation name")
        return v


class OwnerCandidate(ApiModel):
    login: str
    kind: Literal["user", "org"]


class RepoInspect(ApiModel):
    full_name: str
    exists: bool
    accessible: bool
    archived: bool = False
    empty: bool = False
    size_kb: int | None = None
    visibility: Visibility | None = None
    default_branch: str | None = None
    html_url: str | None = None
    protected: bool = False
    protection: dict[str, Any] = Field(default_factory=dict)
    has_workflows: bool = False
    can_push: bool = False
    capabilities: dict[str, bool] = Field(default_factory=dict)


class GithubInspectResult(ApiModel):
    """What the onboarding step shows after the token is entered. Never contains the token."""

    connected_account_id: str
    login: str | None = None
    token_kind: TokenKind = "other"
    token_expires_at: Timestamp | None = None
    owners: list[OwnerCandidate] = Field(default_factory=list)
    repository: RepoInspect | None = None
    missing_permissions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sso_url: str | None = None
    organisation_approval_pending: bool = False


# ------------------------------------------------------------------ agent route


class PullReviewBody(RequestModel):
    """The internal reviewer's verdict (``POST /api/agent/v1/repo/pulls/{pull_id}/review``)."""

    decision: Literal["accept", "request_changes"]
    note: str = Field(default="", max_length=8000)
    head_sha: str | None = Field(default=None, max_length=40)  # optional: the SHA the agent read


class PullReviewResult(ApiModel):
    pull_id: str
    number: int
    verdict: Literal["accepted", "changes_requested"]
    verdict_sha: str | None
    fix_task_id: str | None = None


class SyncQueued(ApiModel):
    queued: bool = True


class WebhookAccepted(ApiModel):
    accepted: bool = True
