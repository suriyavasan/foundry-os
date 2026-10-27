import { AlertTriangle, ExternalLink, Info } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { ApiError, errorMessage, useConnectedAccounts, useCreateConnectedAccount, useCreateProviderSecret, useCreateRepository, useInspectGithub, useRepository } from '@/api'
import type { ConnectedAccount, GithubInspectResult, ProjectRepository } from '@/api/types'
import { Badge, Button, Field, FormError, Input, LoadingState, Select } from '@/components/ui'
import { formatDateTime } from '@/lib/format'
import {
  CI_MODE_LABEL,
  GITHUB_DOCS_PATH,
  GITHUB_TOKEN_URL,
  ISSUE_INTAKE_LABEL,
  MERGE_MODE_HELP,
  MERGE_MODE_LABEL,
  choiceMode,
  createErrorHint,
  directPushBlockedReason,
  emptyRepoForm,
  isFailed,
  isSettingUp,
  parseRepoRef,
  protectionLines,
  recommendedText,
  REPO_STATUS_LABEL,
  tokenExpiresSoon,
  tokenExpiryText,
  tokenKindText,
  toRepositoryCreate,
  validateRepoForm,
  type RepoChoice,
  type RepoFormErrors,
  type RepoFormValues,
} from '@/lib/repos'
import { PermissionChecklist } from './PermissionChecklist'

export interface RepoConnectFormProps {
  companyId: string
  choice: RepoChoice
  /** Bind an existing project (the dialog). Without it a project is created with the repository (onboarding). */
  projectId?: string | null
  seed?: { projectName?: string; repo?: string; description?: string }
  /** Label of the button that leaves the finished form. */
  doneLabel: string
  /** Called with the binding when the person leaves the form after it was created (also while it is still cloning). */
  onDone: (repo: ProjectRepository) => void
  onCancel?: () => void
  cancelLabel?: string
}

const NEW_TOKEN = 'new'

/**
 * Connect GitHub: token -> what the token can do -> the repository's details -> creating / cloning -> done.
 * It is the one form behind the onboarding step and the "Connect a repository" dialog, so the same HTTP calls happen from both
 * (and from `foundryos setup`): credential, connected account, inspect, create, then poll the binding.
 *
 * The token goes once to the credential store and is dropped from the page's state as soon as the account exists; the
 * inspect result never contains it. Policy fields left on "Recommended" are not sent: the server applies its own defaults.
 */
export function RepoConnectForm({ companyId, choice, projectId = null, seed, doneLabel, onDone, onCancel, cancelLabel = 'Back' }: RepoConnectFormProps) {
  const accounts = useConnectedAccounts(companyId)
  const createSecret = useCreateProviderSecret(companyId)
  const createAccount = useCreateConnectedAccount(companyId)
  const inspectMut = useInspectGithub(companyId)
  const create = useCreateRepository(companyId)

  const githubAccounts = (accounts.data ?? []).filter((a: ConnectedAccount) => a.provider === 'github' && a.status === 'connected')
  const [picked, setPicked] = useState<string | null>(null)
  const pickedId = picked ?? githubAccounts[0]?.id ?? NEW_TOKEN
  const [token, setToken] = useState('')
  const credentialRef = useRef<string | null>(null)
  const [tokenError, setTokenError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [accountId, setAccountId] = useState<string | null>(null)
  const [inspected, setInspected] = useState<GithubInspectResult | null>(null)

  const [values, setValues] = useState<RepoFormValues>(() => emptyRepoForm(seed))
  const [errors, setErrors] = useState<RepoFormErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [createdId, setCreatedId] = useState<string | null>(null)

  const set = <K extends keyof RepoFormValues>(key: K, value: RepoFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
    if (key in errors) setErrors((e) => ({ ...e, [key]: undefined }))
  }

  const mode = choiceMode(choice)
  const repoInfo = inspected?.repository ?? null
  const hasWorkflows = repoInfo?.has_workflows ?? false

  async function runInspect(id: string) {
    const ref = values.repo.trim()
    const owner = values.owner.trim()
    let fullName: string | undefined
    if (choice === 'existing') fullName = parseRepoRef(ref) ? ref : undefined
    else if (ref && owner) fullName = `${owner}/${ref}`
    const result = await inspectMut.mutateAsync({ connected_account_id: id, mode, ...(fullName ? { full_name: fullName } : {}), ...(choice === 'new' && !fullName && owner ? { owner } : {}) })
    setInspected(result)
    setAccountId(id)
    if (choice === 'new' && !values.owner) setValues((v) => ({ ...v, owner: result.login ?? result.owners[0]?.login ?? '' }))
    return result
  }

  async function checkToken() {
    setTokenError(null)
    setChecking(true)
    try {
      let id = pickedId === NEW_TOKEN ? null : pickedId
      if (!id) {
        const secret = token.trim()
        if (!secret) {
          setTokenError('Paste the GitHub token first.')
          return
        }
        credentialRef.current ??= (await createSecret.mutateAsync({ name: 'GitHub token', kind: 'api_key', value: secret })).id
        const account = await createAccount.mutateAsync({ provider: 'github', label: 'GitHub', credential_id: credentialRef.current })
        id = account.id
        // From here on only the account (which points at the stored secret) is needed: drop the token from the page.
        credentialRef.current = null
        setToken('')
        setPicked(account.id)
      }
      await runInspect(id)
    } catch (err) {
      setTokenError(errorMessage(err, 'Could not check the token'))
    } finally {
      setChecking(false)
    }
  }

  async function checkRepository() {
    if (!accountId) return
    setFormError(null)
    try {
      await runInspect(accountId)
    } catch (err) {
      setFormError(errorMessage(err, 'Could not check the repository'))
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!accountId) return
    setFormError(null)
    const found = validateRepoForm(values, { choice, projectId, inspected })
    setErrors(found)
    if (Object.keys(found).length > 0) return
    try {
      const repo = await create.mutateAsync(toRepositoryCreate(values, { choice, accountId, projectId }))
      setCreatedId(repo.id)
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === 'validation_error' || err.status === 422) {
          const fields = err.fieldErrors
          const mapped: RepoFormErrors = {}
          for (const [k, msg] of Object.entries(fields)) {
            if (k === 'name' || k === 'full_name') mapped.repo = msg
            else if (k === 'owner') mapped.owner = msg
            else if (k === 'default_branch') mapped.defaultBranch = msg
            else if (k === 'description') mapped.description = msg
            else if (k === 'project_name') mapped.projectName = msg
          }
          setErrors(mapped)
          if (Object.keys(mapped).length > 0) return
        }
        setFormError(createErrorHint(err.code, err.details) ?? err.message)
      } else setFormError(errorMessage(err, 'Could not connect the repository'))
    }
  }

  if (createdId) return <CreatedPanel companyId={companyId} id={createdId} choice={choice} doneLabel={doneLabel} onDone={onDone} />

  const directBlocked = repoInfo ? directPushBlockedReason({ protection: repoInfo.protection }) : null
  const tokenReady = pickedId !== NEW_TOKEN || token.trim().length > 0

  return (
    <div className="space-y-5">
      <section aria-label="Token and permissions" className="space-y-3">
        <div>
          <h2 className="text-[13px] font-semibold text-fg">{choice === 'new' ? 'Token that can create the repository' : 'Token that can read and push to the repository'}</h2>
          <p className="mt-1 text-[12.5px] leading-relaxed text-fg-3">
            Create a <strong className="font-medium text-fg-2">fine-grained</strong> personal access token on GitHub
            {choice === 'new' ? ' with access to all repositories (narrow it to the new one afterwards)' : ', limited to “Only select repositories” and this repository'}, with the permissions below.{' '}
            <a className="inline-flex items-center gap-0.5 text-accent-fg hover:underline" href={GITHUB_TOKEN_URL} target="_blank" rel="noreferrer noopener">
              Open GitHub’s token page <ExternalLink className="size-3" aria-hidden />
            </a>
          </p>
        </div>
        <PermissionChecklist choice={choice} capabilities={repoInfo?.capabilities} />
        <p className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2 text-[12.5px] text-fg-2">
          <Info className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
          <span>
            Recommended: use a free <strong className="font-medium">machine-user</strong> GitHub account for the token. GitHub does not let you approve pull requests opened under your own name, so a separate account lets you review
            what your agents propose. Details in <code className="font-mono text-[12px]">{GITHUB_DOCS_PATH}</code>. An organisation may need an admin to approve a fine-grained token, and SAML single sign-on may need the token to be authorised.
          </span>
        </p>

        {accounts.isPending ? (
          <LoadingState rows={2} label="Looking for a connected GitHub account" />
        ) : (
          <>
            {githubAccounts.length > 0 && (
              <fieldset className="space-y-1.5">
                <legend className="mb-1 text-[12px] font-medium text-fg-2">GitHub account</legend>
                {githubAccounts.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-[13px] text-fg">
                    <input
                      type="radio"
                      name="github-account"
                      className="accent-accent"
                      checked={pickedId === a.id}
                      onChange={() => {
                        setPicked(a.id)
                        setInspected(null)
                        setAccountId(null)
                      }}
                    />
                    Use the connected account “{a.label}”{a.metadata?.login ? ` (${a.metadata.login})` : ''}
                  </label>
                ))}
                <label className="flex items-center gap-2 text-[13px] text-fg">
                  <input
                    type="radio"
                    name="github-account"
                    className="accent-accent"
                    checked={pickedId === NEW_TOKEN}
                    onChange={() => {
                      setPicked(NEW_TOKEN)
                      setInspected(null)
                      setAccountId(null)
                    }}
                  />
                  Use a different token
                </label>
              </fieldset>
            )}
            {pickedId === NEW_TOKEN && (
              <Field label="GitHub token" hint="Stored in the credential store, never shown again, and never sent to your agents." error={tokenError}>
                {({ id, invalid, describedBy }) => (
                  <Input
                    id={id}
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="off"
                    className="h-10 font-mono"
                    value={token}
                    invalid={invalid}
                    aria-describedby={describedBy}
                    placeholder="github_pat_…"
                    onChange={(e) => {
                      credentialRef.current = null
                      setToken(e.target.value)
                      setTokenError(null)
                    }}
                  />
                )}
              </Field>
            )}
            {pickedId !== NEW_TOKEN && tokenError && <FormError message={tokenError} />}
            <div className="flex flex-wrap items-center gap-2">
              <Button variant={inspected ? 'secondary' : 'primary'} loading={checking} disabled={!tokenReady} onClick={() => void checkToken()}>
                {inspected ? 'Check again' : 'Check token'}
              </Button>
              {onCancel && (
                <Button variant="ghost" onClick={onCancel} disabled={checking}>
                  {cancelLabel}
                </Button>
              )}
            </div>
          </>
        )}
      </section>

      {inspected && accountId && (
        <>
          <InspectSummary inspect={inspected} />
          <form onSubmit={submit} noValidate className="space-y-4" aria-label={choice === 'new' ? 'New repository' : 'Existing repository'}>
            {choice === 'new' ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Owner" hint="You, or an organisation you can create repositories in." error={errors.owner}>
                  {({ id, invalid, describedBy }) =>
                    inspected.owners.length > 0 ? (
                      <Select id={id} value={values.owner} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('owner', e.target.value)}>
                        {inspected.owners.map((o) => (
                          <option key={o.login} value={o.login}>
                            {o.login} ({o.kind === 'org' ? 'organisation' : 'you'})
                          </option>
                        ))}
                      </Select>
                    ) : (
                      <Input id={id} value={values.owner} invalid={invalid} aria-describedby={describedBy} placeholder={inspected.login ?? 'owner'} onChange={(e) => set('owner', e.target.value)} />
                    )
                  }
                </Field>
                <Field label="Repository name" required error={errors.repo}>
                  {({ id, invalid, describedBy }) => <Input id={id} value={values.repo} invalid={invalid} aria-describedby={describedBy} placeholder="my-company" onChange={(e) => set('repo', e.target.value)} />}
                </Field>
              </div>
            ) : (
              <Field label="Repository" required error={errors.repo} hint="owner/name, or the address of the repository on GitHub.">
                {({ id, invalid, describedBy }) => (
                  <div className="flex gap-2">
                    <Input id={id} value={values.repo} invalid={invalid} aria-describedby={describedBy} placeholder="owner/name" onChange={(e) => set('repo', e.target.value)} />
                    <Button disabled={!parseRepoRef(values.repo)} loading={inspectMut.isPending} onClick={() => void checkRepository()}>
                      Check repository
                    </Button>
                  </div>
                )}
              </Field>
            )}
            {choice === 'new' && (
              <div className="flex">
                <Button size="sm" disabled={!values.repo.trim() || !values.owner.trim()} loading={inspectMut.isPending} onClick={() => void checkRepository()}>
                  Check the name is free
                </Button>
              </div>
            )}
            {repoInfo && <RepoFacts inspect={inspected} choice={choice} />}

            {!projectId && (
              <Field label="Project name" hint="Leave empty to use the repository’s name." error={errors.projectName}>
                {({ id, invalid, describedBy }) => <Input id={id} value={values.projectName} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('projectName', e.target.value)} />}
              </Field>
            )}

            {choice === 'new' && (
              <>
                <fieldset className="space-y-1.5">
                  <legend className="mb-1 text-[12px] font-medium text-fg-2">Visibility</legend>
                  <label className="flex items-center gap-2 text-[13px] text-fg">
                    <input type="radio" name="visibility" className="accent-accent" checked={values.visibility === 'private'} onChange={() => set('visibility', 'private')} />
                    Private (recommended)
                  </label>
                  <label className="flex items-center gap-2 text-[13px] text-fg">
                    <input type="radio" name="visibility" className="accent-accent" checked={values.visibility === 'public'} onChange={() => set('visibility', 'public')} />
                    Public
                  </label>
                  {values.visibility === 'public' && (
                    <div className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[12.5px] text-fg">
                      <label className="flex items-start gap-2">
                        <input type="checkbox" className="mt-0.5 accent-accent" checked={values.confirmPublic} onChange={(e) => set('confirmPublic', e.target.checked)} />
                        <span>I understand that my agents’ code will be public, readable by anyone on the internet.</span>
                      </label>
                      {errors.confirmPublic && (
                        <p role="alert" className="mt-1 text-[12px] text-danger">
                          {errors.confirmPublic}
                        </p>
                      )}
                    </div>
                  )}
                </fieldset>
                <Field label="Description" hint="Shown on GitHub. Up to 350 characters." error={errors.description}>
                  {({ id, invalid, describedBy }) => <Input id={id} value={values.description} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('description', e.target.value)} />}
                </Field>
              </>
            )}

            <Field
              label="Default branch"
              error={errors.defaultBranch}
              hint={choice === 'new' ? 'The first commit goes here. Leave empty for main.' : `Foundry works on its own foundry/ branch and opens pull requests into this one. Leave empty for ${repoInfo?.default_branch ?? 'the repository’s own default'}.`}
            >
              {({ id, invalid, describedBy }) => <Input id={id} value={values.defaultBranch} invalid={invalid} aria-describedby={describedBy} placeholder={repoInfo?.default_branch ?? 'main'} onChange={(e) => set('defaultBranch', e.target.value)} />}
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="How changes are merged" hint={MERGE_MODE_HELP[values.mergeMode || (choice === 'new' ? 'pr_auto_merge' : 'pr_human_merge')]}>
                {({ id, describedBy }) => (
                  <Select id={id} aria-describedby={describedBy} value={values.mergeMode} onChange={(e) => set('mergeMode', e.target.value as RepoFormValues['mergeMode'])}>
                    <option value="">{recommendedText(choice, 'merge')}</option>
                    <option value="pr_human_merge">{MERGE_MODE_LABEL.pr_human_merge}</option>
                    <option value="pr_auto_merge">{MERGE_MODE_LABEL.pr_auto_merge}</option>
                    <option value="direct_push" disabled={!!directBlocked}>
                      {MERGE_MODE_LABEL.direct_push}
                      {directBlocked ? ' (not possible: protected branch)' : ''}
                    </option>
                  </Select>
                )}
              </Field>
              <Field label="Tests (CI)" hint="Foundry merges only when GitHub reports the checks green.">
                {({ id, describedBy }) => (
                  <Select id={id} aria-describedby={describedBy} value={values.ciMode} onChange={(e) => set('ciMode', e.target.value as RepoFormValues['ciMode'])}>
                    <option value="">{recommendedText(choice, 'ci', hasWorkflows)}</option>
                    <option value="required">{CI_MODE_LABEL.required}</option>
                    <option value="if_present">{CI_MODE_LABEL.if_present}</option>
                    <option value="ignore">{CI_MODE_LABEL.ignore}</option>
                  </Select>
                )}
              </Field>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Issue intake" hint="Which GitHub issues become tasks. Their text is treated as untrusted.">
                {({ id, describedBy }) => (
                  <Select id={id} aria-describedby={describedBy} value={values.issueIntake} onChange={(e) => set('issueIntake', e.target.value as RepoFormValues['issueIntake'])}>
                    <option value="">{recommendedText(choice, 'issues')}</option>
                    <option value="off">{ISSUE_INTAKE_LABEL.off}</option>
                    <option value="collaborators">{ISSUE_INTAKE_LABEL.collaborators}</option>
                    <option value="labeled">{ISSUE_INTAKE_LABEL.labeled}</option>
                    <option value="everyone">{ISSUE_INTAKE_LABEL.everyone}</option>
                  </Select>
                )}
              </Field>
              {values.issueIntake === 'labeled' && (
                <Field label="Intake label" error={errors.intakeLabel} hint="Only issues with this label are picked up.">
                  {({ id, invalid, describedBy }) => <Input id={id} value={values.intakeLabel} invalid={invalid} aria-describedby={describedBy} onChange={(e) => set('intakeLabel', e.target.value)} />}
                </Field>
              )}
            </div>

            {projectId && choice === 'existing' && repoInfo?.empty && (
              <label className="flex items-start gap-2 text-[13px] text-fg">
                <input type="checkbox" className="mt-0.5 accent-accent" checked={values.publishLocal} onChange={(e) => set('publishLocal', e.target.checked)} />
                <span>Publish this project’s local history to the empty repository. Everything is scanned for secrets before it leaves this machine.</span>
              </label>
            )}
            {projectId && choice === 'existing' && repoInfo && !repoInfo.empty && (
              <p className="flex items-start gap-2 text-[12.5px] text-fg-3">
                <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                This repository already has content. If the project has local history, it is moved aside (kept, never deleted) and the clone takes its place.
              </p>
            )}

            <FormError message={formError} />
            <Button type="submit" variant="primary" className="h-10 w-full" loading={create.isPending}>
              {choice === 'new' ? 'Create the repository' : 'Connect the repository'}
            </Button>
          </form>
        </>
      )}
    </div>
  )
}

/** Who the token is, what kind it is, when it expires, and everything the server found wrong with it. */
function InspectSummary({ inspect }: { inspect: GithubInspectResult }) {
  const soon = tokenExpiresSoon(inspect.token_expires_at)
  return (
    <section aria-label="Token check" className="space-y-2 rounded-md border border-line bg-surface p-3 text-[13px]">
      <p className="text-fg">
        Signed in as <strong className="font-medium">{inspect.login ?? 'an unknown account'}</strong>
        <span className="text-fg-3"> · {tokenKindText(inspect.token_kind)}</span>
      </p>
      <p className={soon ? 'text-warning' : 'text-fg-3'}>
        {tokenExpiryText(inspect.token_expires_at)}
        {inspect.token_expires_at ? ` (${formatDateTime(inspect.token_expires_at)})` : ''}
      </p>
      {inspect.organisation_approval_pending && (
        <p role="status" className="flex items-start gap-2 text-warning">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Pending organisation approval: an admin of the organisation must approve this token before it can be used.
        </p>
      )}
      {inspect.sso_url && (
        <p role="status" className="flex items-start gap-2 text-warning">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            This organisation uses SAML single sign-on.{' '}
            <a className="text-accent-fg hover:underline" href={inspect.sso_url} target="_blank" rel="noreferrer noopener">
              Authorise the token
            </a>
            , then check again.
          </span>
        </p>
      )}
      {inspect.missing_permissions.length > 0 && (
        <div role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-danger">
          <p className="font-medium">The token is missing permissions</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {inspect.missing_permissions.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      {inspect.warnings.length > 0 && (
        <ul aria-label="Warnings" className="space-y-1 text-warning">
          {inspect.warnings.map((w) => (
            <li key={w} className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {w}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** What GitHub says about the repository that was typed. */
function RepoFacts({ inspect, choice }: { inspect: GithubInspectResult; choice: RepoChoice }) {
  const r = inspect.repository
  if (!r) return null
  const facts: string[] = []
  if (!r.exists) facts.push(choice === 'new' ? 'The name is free.' : 'No repository with this name was found.')
  else {
    facts.push(r.accessible ? 'The token can see it.' : 'The token cannot see it.')
    if (r.visibility) facts.push(`${r.visibility[0]!.toUpperCase()}${r.visibility.slice(1)}.`)
    if (r.default_branch) facts.push(`Default branch ${r.default_branch}.`)
    facts.push(r.empty ? 'Empty.' : r.size_kb != null ? `About ${Math.max(1, Math.round(r.size_kb / 1024))} MB.` : 'Has content.')
    facts.push(r.can_push ? 'The token can push.' : 'The token cannot push.')
    facts.push(r.has_workflows ? 'Has CI workflows.' : 'No CI workflows.')
  }
  const bad = (choice === 'new' && r.exists) || (choice === 'existing' && (!r.exists || !r.accessible || r.archived || (r.exists && !r.can_push)))
  return (
    <div role="status" className={`rounded-md border px-3 py-2 text-[12.5px] ${bad ? 'border-danger/40 bg-danger-subtle text-danger' : 'border-line bg-surface text-fg-2'}`}>
      <p className="font-medium text-fg">{r.full_name}</p>
      <p>
        {choice === 'new' && r.exists ? 'A repository with this name already exists. Choose another name, or connect it instead. ' : ''}
        {r.archived ? <Badge tone="warning">Archived</Badge> : null} {facts.join(' ')}
      </p>
      {r.exists && r.accessible && (
        <ul aria-label="Branch protection" className="mt-1 list-disc pl-4 text-fg-3">
          {protectionLines(r.protection).map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** After create: creating / cloning progress, then done, or the reason it failed. */
function CreatedPanel({ companyId, id, choice, doneLabel, onDone }: { companyId: string; id: string; choice: RepoChoice; doneLabel: string; onDone: (repo: ProjectRepository) => void }) {
  const repo = useRepository(companyId, id)
  const r = repo.data
  if (repo.isPending) return <LoadingState rows={3} label="Starting" />
  if (repo.isError && !r) {
    return (
      <div className="space-y-3">
        <FormError message={errorMessage(repo.error, 'Could not read the repository’s state')} />
        <Button onClick={() => void repo.refetch()} loading={repo.isFetching}>
          Retry
        </Button>
      </div>
    )
  }
  if (!r) return null
  const working = isSettingUp(r.status)
  const failed = isFailed(r.status)
  return (
    <section aria-label="Repository setup" className="space-y-4">
      <div role="status" aria-live="polite" className="rounded-md border border-line bg-surface p-4">
        <p className="text-[14px] font-semibold text-fg">{r.full_name}</p>
        <p className="mt-1 text-[13px] text-fg-2">
          {working
            ? r.status === 'cloning'
              ? 'Cloning the repository…'
              : choice === 'new'
                ? 'Creating the repository and pushing the first commit…'
                : 'Preparing the repository…'
            : failed
              ? `${REPO_STATUS_LABEL[r.status]}: setup did not finish.`
              : choice === 'new'
                ? 'The repository is ready.'
                : 'Connected.'}
        </p>
        {r.status_reason && <p className={`mt-1 text-[12.5px] ${failed ? 'text-danger' : 'text-fg-3'}`}>{r.status_reason}</p>}
        {r.status === 'active' && r.onboarding_status === 'pending' && (
          <p className="mt-1 text-[12.5px] text-fg-3">Your team will first learn this codebase and write an architecture note before other work on it starts.</p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => onDone(r)}>
          {working ? 'Continue while it finishes' : failed ? 'Continue anyway' : doneLabel}
        </Button>
        {failed && (
          <Button onClick={() => void repo.refetch()} loading={repo.isFetching}>
            Check again
          </Button>
        )}
      </div>
    </section>
  )
}
