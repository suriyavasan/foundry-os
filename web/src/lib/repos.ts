/**
 * Pure rules for the GitHub repository views (docs/specs/m14-github-backed-projects.md, §2 and §6.4): labels, the
 * permission checklist, the reference/branch checks that mirror the server's, the onboarding form and the body it sends.
 * Tested on its own (repos.test.ts); the components only render these.
 *
 * Policy defaults are NOT duplicated here: a field left on "Recommended" is omitted from the request and the server applies
 * `default_policy(mode, has_workflows)`. The text only says what the recommendation means.
 */
import type {
  CiMode,
  GithubInspectResult,
  IssueIntake,
  MergeMethod,
  MergeMode,
  ProjectRepository,
  PullStage,
  RepoItemKind,
  RepoPolicy,
  RepoPolicyInput,
  RepoStatus,
  RepoSyncState,
  RepositoryCreate,
} from '@/api/types'

export const GITHUB_TOKEN_URL = 'https://github.com/settings/personal-access-tokens/new'
export const GITHUB_DOCS_PATH = 'docs/github.md'

/** Onboarding / dialog choice: a new repository, an existing one (the third choice, no GitHub, creates nothing). */
export type RepoChoice = 'new' | 'existing'
export const choiceMode = (c: RepoChoice): 'create' | 'connect' => (c === 'new' ? 'create' : 'connect')

/* ---------------------------------------- labels ---------------------------------------- */

export const MERGE_MODE_LABEL: Record<MergeMode, string> = {
  pr_human_merge: 'Pull requests, a human merges',
  pr_auto_merge: 'Pull requests, merged automatically',
  direct_push: 'Push straight to the default branch',
}
export const MERGE_MODE_SHORT: Record<MergeMode, string> = {
  pr_human_merge: 'human merge',
  pr_auto_merge: 'auto-merge',
  direct_push: 'direct push',
}
export const MERGE_MODE_HELP: Record<MergeMode, string> = {
  pr_human_merge: 'Foundry opens pull requests and waits for a person to merge them on GitHub.',
  pr_auto_merge: 'Foundry merges after its own review and green CI, and only when GitHub reports the pull request as mergeable.',
  direct_push: 'No pull requests. Only works when the default branch is not protected.',
}
export const MERGE_METHOD_LABEL: Record<MergeMethod, string> = { squash: 'Squash', merge: 'Merge commit', rebase: 'Rebase' }
export const CI_MODE_LABEL: Record<CiMode, string> = {
  required: 'Required: at least one check, all green',
  if_present: 'If present: all reported checks green',
  ignore: 'Ignore CI (not recommended)',
}
export const ISSUE_INTAKE_LABEL: Record<IssueIntake, string> = {
  off: 'Off',
  collaborators: 'Issues from collaborators',
  labeled: 'Only issues with the intake label',
  everyone: 'Issues from anyone',
}

export const REPO_STATUS_LABEL: Record<RepoStatus, string> = {
  pending: 'Pending',
  creating: 'Creating',
  cloning: 'Cloning',
  active: 'Active',
  paused: 'Paused',
  auth_failed: 'Token problem',
  error: 'Error',
  disconnected: 'Disconnected',
}
export type RepoTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'accent'
export const REPO_STATUS_TONE: Record<RepoStatus, RepoTone> = {
  pending: 'neutral',
  creating: 'info',
  cloning: 'info',
  active: 'success',
  paused: 'warning',
  auth_failed: 'danger',
  error: 'danger',
  disconnected: 'neutral',
}
/** The binding is still being set up by the server. */
export const isSettingUp = (status: RepoStatus): boolean => status === 'pending' || status === 'creating' || status === 'cloning'
/** Terminal and broken: show the reason, offer the way out. */
export const isFailed = (status: RepoStatus): boolean => status === 'error' || status === 'auth_failed'

export const SYNC_STATE_LABEL: Record<RepoSyncState, string> = {
  idle: 'In sync',
  upstream_conflict: 'Upstream conflict',
  upstream_rewritten: 'Upstream history rewritten',
  push_blocked: 'Push blocked',
  rate_limited: 'GitHub rate limit',
  awaiting_approval: 'Waiting for an approval',
}
export const SYNC_STATE_TONE: Record<RepoSyncState, RepoTone> = {
  idle: 'success',
  upstream_conflict: 'danger',
  upstream_rewritten: 'danger',
  push_blocked: 'danger',
  rate_limited: 'warning',
  awaiting_approval: 'warning',
}

export const PULL_STAGE_LABEL: Record<PullStage, string> = {
  opening: 'Opening',
  reviewing: 'In review',
  changes_requested: 'Changes requested',
  approved: 'Approved',
  merging: 'Merging',
  awaiting_human: 'Waiting for a human to merge',
  merged: 'Merged',
  closed: 'Closed',
}
export const PULL_STAGE_TONE: Record<PullStage, RepoTone> = {
  opening: 'info',
  reviewing: 'info',
  changes_requested: 'warning',
  approved: 'success',
  merging: 'info',
  awaiting_human: 'warning',
  merged: 'success',
  closed: 'neutral',
}
/** The happy path, in order, for the timeline. `changes_requested` loops back to `reviewing`; `closed` ends it early. */
export const PULL_STAGE_PATH: readonly PullStage[] = ['opening', 'reviewing', 'approved', 'merging', 'merged']

/** Where a stage sits on the timeline (0-based), -1 when it is off the happy path. */
export function pullStageIndex(stage: PullStage): number {
  if (stage === 'changes_requested') return PULL_STAGE_PATH.indexOf('reviewing')
  if (stage === 'awaiting_human') return PULL_STAGE_PATH.indexOf('merging')
  return PULL_STAGE_PATH.indexOf(stage)
}

export const ITEM_KIND_LABEL: Record<RepoItemKind, string> = {
  issue: 'Issue',
  review: 'Review',
  internal_review: 'Internal review',
  check_failure: 'CI failure',
  alert: 'Security alert',
}

/* --------------------------------- permission checklist --------------------------------- */

export interface PermissionRow {
  /** Key in `GithubInspectResult.repository.capabilities`, or null when the server cannot tell. */
  capability: string | null
  permission: string
  /** What to select on GitHub's token page. */
  access: string
  used: string
  /** What stops working without it. */
  missing: string
  optional?: boolean
}

/** The fine-grained token permissions (spec §2.2), per choice. */
export function permissionChecklist(choice: RepoChoice): PermissionRow[] {
  const create = choice === 'new'
  const rows: PermissionRow[] = [
    { capability: null, permission: 'Metadata', access: 'Read (added automatically)', used: 'Everything', missing: 'Nothing works' },
    { capability: 'contents', permission: 'Contents', access: 'Read and write', used: 'Clone, push, merge, releases', missing: 'The repository cannot be connected' },
    { capability: 'pull_requests', permission: 'Pull requests', access: 'Read and write', used: 'Open, update, comment and merge pull requests', missing: 'Only direct push is possible' },
    {
      capability: 'issues',
      permission: 'Issues',
      access: create ? 'Read and write' : 'Read and write (Read if issue intake is off)',
      used: 'Issue intake, comments, labels',
      missing: 'Issue intake and comments are turned off',
    },
    { capability: 'statuses', permission: 'Commit statuses', access: 'Read', used: 'CI state', missing: 'CI is read from Actions and check runs only' },
    { capability: 'actions', permission: 'Actions', access: 'Read', used: 'Workflow runs and failure logs', missing: 'CI fix tasks have no logs' },
    { capability: 'checks', permission: 'Checks', access: 'Read, if the token page offers it', used: 'Check runs', missing: 'Falls back to statuses and Actions' },
    { capability: 'dependabot', permission: 'Dependabot alerts', access: 'Read', used: 'Security alerts', missing: 'Security alerts are off' },
  ]
  if (create) {
    rows.push({
      capability: 'administration',
      permission: 'Administration',
      access: 'Read and write, only to create the repository (remove it afterwards)',
      used: 'Creating the repository',
      missing: 'Creating a repository is refused; create an empty one on github.com and connect it instead',
    })
  }
  rows.push({ capability: 'webhooks', permission: 'Webhooks', access: 'Read and write, only if you enable webhooks', used: 'Faster syncs on a stable public address', missing: 'Polling only', optional: true })
  return rows
}

export type PermissionState = 'ok' | 'missing' | 'unknown'

/** Whether the token has the permission, from the `capabilities` map. `unknown` when not checked or when the server cannot tell. */
export function permissionState(row: PermissionRow, capabilities: Record<string, unknown> | null | undefined): PermissionState {
  if (!capabilities || !row.capability || !(row.capability in capabilities)) return 'unknown'
  return capabilities[row.capability] ? 'ok' : 'missing'
}

/** `token_kind` -> words, with the fine-grained recommendation. */
export function tokenKindText(kind: GithubInspectResult['token_kind']): string {
  if (kind === 'fine_grained') return 'Fine-grained token'
  if (kind === 'classic') return 'Classic token (reaches every repository of the account; a fine-grained token is safer)'
  return 'Token of another kind'
}

/** Whole days until `iso`, or null. Negative once expired. */
export function daysUntil(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return Math.floor((t - now) / 86_400_000)
}

export function tokenExpiryText(iso: string | null | undefined, now: number = Date.now()): string {
  const d = daysUntil(iso, now)
  if (d === null) return 'No expiry reported'
  if (d < 0) return 'Expired'
  if (d === 0) return 'Expires today'
  return d === 1 ? 'Expires tomorrow' : `Expires in ${d} days`
}
/** Warn from two weeks out (the server alerts as well). */
export const tokenExpiresSoon = (iso: string | null | undefined, now: number = Date.now()): boolean => {
  const d = daysUntil(iso, now)
  return d !== null && d <= 14
}

/* ------------------------------- references and names ------------------------------- */

const OWNER_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/
const NAME_RE = /^[A-Za-z0-9._-]{1,100}$/
const FULL_NAME_RE = /^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100})$/
const URL_RE = /^https?:\/\/[^/]+\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/

export const validOwner = (v: string): boolean => OWNER_RE.test(v)
export const validRepoName = (v: string): boolean => NAME_RE.test(v) && v !== '.' && v !== '..' && !v.endsWith('.git')

/** `owner/name` or a github.com URL -> `{owner, name}`, else null (the same rules as the server). */
export function parseRepoRef(text: string): { owner: string; name: string } | null {
  const value = text.trim()
  const m = value.includes('://') ? URL_RE.exec(value) : FULL_NAME_RE.exec(value)
  if (!m) return null
  const owner = m[1]!
  const name = m[2]!
  if (!validRepoName(name)) return null
  return { owner, name }
}

// eslint-disable-next-line no-control-regex
const BAD_REF = /[\x00-\x20\x7f~^:?*[\\]/

/** git ref-format rules for a branch name (simplified, like the server's). */
export function validBranchName(name: string): boolean {
  if (!name || name.length > 255 || name === '@' || name === 'HEAD') return false
  if (BAD_REF.test(name) || name.includes('..') || name.includes('@{') || name.includes('//')) return false
  if (name.startsWith('/') || name.startsWith('-') || /(\/|\.|\.lock)$/.test(name)) return false
  return !name.split('/').some((part) => part.startsWith('.') || part.endsWith('.lock'))
}

/** "Acme Labs!" -> "acme-labs": a repository name suggestion from a company or project name. */
export function slugRepoName(text: string): string {
  const slug = text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '')
    .slice(0, 100)
  return slug === '' || slug === '.' || slug === '..' ? '' : slug
}

/* -------------------------------------- the form -------------------------------------- */

export interface RepoFormValues {
  /** Empty means "use the repository name". */
  projectName: string
  /** New repositories: the owner or organisation. Empty means the token's own login. */
  owner: string
  /** New: the name. Existing: `owner/name` or a github.com URL. */
  repo: string
  visibility: 'private' | 'public'
  confirmPublic: boolean
  description: string
  /** Empty means the default (`main` for a new repository; the repository's own for an existing one). */
  defaultBranch: string
  /** `''` = Recommended for this choice; the server applies its default. */
  mergeMode: '' | MergeMode
  ciMode: '' | CiMode
  issueIntake: '' | IssueIntake
  intakeLabel: string
  /** Existing project + empty remote: push the project's local history. */
  publishLocal: boolean
}

export function emptyRepoForm(seed: { projectName?: string; repo?: string; description?: string } = {}): RepoFormValues {
  return {
    projectName: seed.projectName ?? '',
    owner: '',
    repo: seed.repo ?? '',
    visibility: 'private',
    confirmPublic: false,
    description: seed.description ?? '',
    defaultBranch: '',
    mergeMode: '',
    ciMode: '',
    issueIntake: '',
    intakeLabel: 'foundry',
    publishLocal: false,
  }
}

export type RepoFormErrors = Partial<Record<'projectName' | 'owner' | 'repo' | 'description' | 'defaultBranch' | 'confirmPublic' | 'intakeLabel', string>>

export interface RepoFormContext {
  choice: RepoChoice
  /** Binding an existing project: no project name needed. */
  projectId?: string | null
  /** The inspected repository, when there is one (exists / archived / can_push ...). */
  inspected?: GithubInspectResult | null
}

export function validateRepoForm(v: RepoFormValues, ctx: RepoFormContext): RepoFormErrors {
  const e: RepoFormErrors = {}
  if (!ctx.projectId && v.projectName.trim().length > 200) e.projectName = 'Use at most 200 characters'
  const repo = v.repo.trim()
  if (ctx.choice === 'new') {
    if (!repo) e.repo = 'Give the repository a name'
    else if (!validRepoName(repo)) e.repo = "Use letters, digits, '.', '_' and '-' (at most 100), and no '.git' ending"
    const owner = v.owner.trim()
    if (owner && !validOwner(owner)) e.owner = 'Not a valid GitHub user or organisation name'
    if (v.description.trim().length > 350) e.description = 'Use at most 350 characters'
    if (v.visibility === 'public' && !v.confirmPublic) e.confirmPublic = 'Confirm that your agents’ code will be public'
  } else if (!repo) e.repo = 'Enter owner/name or the repository’s address'
  else if (!parseRepoRef(repo)) e.repo = 'Expected owner/name or https://github.com/owner/name'
  const branch = v.defaultBranch.trim()
  if (branch && !validBranchName(branch)) e.defaultBranch = 'Not a valid branch name'
  if (v.issueIntake === 'labeled' && !v.intakeLabel.trim()) e.intakeLabel = 'Name the label'
  else if (v.intakeLabel.trim().length > 50) e.intakeLabel = 'Use at most 50 characters'
  return e
}

/** The project name sent for a new project: the typed one, else the repository's name. */
export function projectNameFor(v: RepoFormValues): string {
  const typed = v.projectName.trim()
  if (typed) return typed
  const ref = parseRepoRef(v.repo)
  return ref ? ref.name : v.repo.trim()
}

/** The request body for `POST /repositories`. Policy fields left on "Recommended" are omitted. */
export function toRepositoryCreate(v: RepoFormValues, ctx: { choice: RepoChoice; accountId: string; projectId?: string | null }): RepositoryCreate {
  const body: RepositoryCreate = { mode: choiceMode(ctx.choice), connected_account_id: ctx.accountId }
  if (ctx.projectId) body.project_id = ctx.projectId
  else body.project_name = projectNameFor(v)
  const repo = v.repo.trim()
  if (ctx.choice === 'new') {
    body.name = repo
    if (v.owner.trim()) body.owner = v.owner.trim()
    body.visibility = v.visibility
    if (v.visibility === 'public') body.confirm_public = v.confirmPublic
    if (v.description.trim()) body.description = v.description.trim()
  } else {
    body.full_name = repo
    if (v.publishLocal && ctx.projectId) body.publish_local = true
  }
  if (v.defaultBranch.trim()) body.default_branch = v.defaultBranch.trim()
  const policy: NonNullable<RepositoryCreate['policy']> = {}
  if (v.mergeMode) policy.merge_mode = v.mergeMode
  if (v.ciMode) policy.ci_mode = v.ciMode
  if (v.issueIntake) policy.issue_intake = v.issueIntake
  if (v.issueIntake === 'labeled' && v.intakeLabel.trim()) policy.intake_label = v.intakeLabel.trim()
  if (Object.keys(policy).length) body.policy = policy
  return body
}

/** What "Recommended" means for the choice (the server's `default_policy`, stated in words). */
export function recommendedText(choice: RepoChoice, field: 'merge' | 'ci' | 'issues', hasWorkflows = false): string {
  if (field === 'merge') return choice === 'new' ? 'Recommended: merge automatically (your company owns this repository)' : 'Recommended: a human merges (people already work in this repository)'
  if (field === 'ci') return choice === 'new' ? 'Recommended: if present' : hasWorkflows ? 'Recommended: required (this repository has workflows)' : 'Recommended: if present'
  return choice === 'new' ? 'Recommended: issues from collaborators' : 'Recommended: only issues with the foundry label'
}

/* -------------------------------- error text for the form -------------------------------- */

/** Plain-English next steps for the error codes of `POST /repositories` (spec §6.3). Null: show the server's message. */
export function createErrorHint(code: string, details?: unknown): string | null {
  const missing = details && typeof details === 'object' && Array.isArray((details as { missing_permissions?: unknown }).missing_permissions) ? ((details as { missing_permissions: unknown[] }).missing_permissions.map(String).join(', ')) : ''
  switch (code) {
    case 'project_has_repository':
      return 'This project already has a repository. Disconnect it first to connect another.'
    case 'no_account':
      return 'Connect a GitHub account first.'
    case 'confirm_public_required':
      return 'Public repositories need your explicit confirmation.'
    case 'repository_exists':
      return 'A repository with this name already exists. Pick another name, or connect the existing one.'
    case 'repository_not_accessible':
      return 'The token cannot see this repository. Add it to the token’s repository access, or check the name.'
    case 'repository_archived':
      return 'This repository is archived on GitHub, so nothing can be pushed to it.'
    case 'token_cannot_push':
      return `The token cannot push${missing ? ` (missing: ${missing})` : ''}. Give it Contents: Read and write.`
    case 'repository_too_large':
      return 'This repository is larger than Foundry accepts.'
    case 'administration_permission_missing':
      return 'The token lacks Administration permission, which creating a repository needs. Add it temporarily, or create an empty repository on github.com and connect that.'
    case 'branch_protected':
      return 'The default branch is protected, so direct push is not possible. Choose pull requests.'
    case 'git_unavailable':
      return 'Git is not available on this machine.'
    default:
      return null
  }
}

/* ------------------------------ onboarding "Code" summary ------------------------------ */

/** `owner/repo · auto-merge`, the Team step's Code line. */
export function codeSummary(repo: Pick<ProjectRepository, 'full_name'> & { policy: { merge_mode: MergeMode } }): string {
  return `${repo.full_name} · ${MERGE_MODE_SHORT[repo.policy.merge_mode]}`
}

/* ------------------------------------- policy editing ------------------------------------- */

/** Whether `direct_push` can be chosen, and if not why not (it is also refused by the server). */
export function directPushBlockedReason(repo: Pick<ProjectRepository, 'protection'>): string | null {
  const p = repo.protection as { protected?: boolean; require_pr?: boolean } | undefined
  if (p?.protected || p?.require_pr) return 'The default branch is protected or requires pull requests.'
  return null
}

/** One line for the protection summary on the overview. */
export function protectionLines(protection: ProjectRepository['protection']): string[] {
  const p = protection as {
    branch?: string
    protected?: boolean
    require_pr?: boolean
    required_checks?: string[]
    linear_history?: boolean
    required_signatures?: boolean
    non_fast_forward?: boolean
    required_approvals?: number
    rules_known?: boolean
    checked_at?: string
  }
  if (!p || (p.protected === undefined && p.rules_known === undefined)) return ['Not checked yet']
  if (!p.protected) return [`${p.branch ?? 'The default branch'} is not protected`]
  const out = [`${p.branch ?? 'The default branch'} is protected`]
  if (p.require_pr) out.push('Changes need a pull request')
  if (p.required_approvals) out.push(`${p.required_approvals} approving review${p.required_approvals === 1 ? '' : 's'} required`)
  if (p.required_checks?.length) out.push(`Required checks: ${p.required_checks.join(', ')}`)
  if (p.linear_history) out.push('Linear history required')
  if (p.required_signatures) out.push('Signed commits required')
  if (p.non_fast_forward) out.push('Force pushes blocked')
  if (p.rules_known === false) out.push('Rulesets could not be read, so this may be incomplete')
  return out
}

/** The typed word the owner must enter to reset the integration branch. */
export const RESET_CONFIRM_WORD = 'reset'
export const DISCONNECT_CONFIRM_WORD = 'disconnect'
export const confirmsWord = (typed: string, word: string): boolean => typed.trim().toLowerCase() === word

/** The policy fields that differ between the saved policy and the draft: the body of `PATCH /repositories/{id}`. */
export function diffPolicy(current: RepoPolicy, draft: RepoPolicy): RepoPolicyInput {
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(draft) as Array<keyof RepoPolicy>) {
    if (JSON.stringify(current[key]) !== JSON.stringify(draft[key])) out[key] = draft[key]
  }
  return out as RepoPolicyInput
}

/** Protected paths are edited as one glob per line. */
export const pathsToText = (paths: string[]): string => paths.join('\n')
export const textToPaths = (text: string): string[] => text.split('\n').map((l) => l.trim()).filter(Boolean)
