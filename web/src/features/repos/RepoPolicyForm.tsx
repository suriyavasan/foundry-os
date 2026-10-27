import { AlertTriangle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { errorMessage, useUpdateRepository } from '@/api'
import type { MergeMode, ProjectRepository, RepoPolicy } from '@/api/types'
import { Button, Card, CardBody, CardHeader, Field, FormError, Input, Select, useToast } from '@/components/ui'
import { CI_MODE_LABEL, ISSUE_INTAKE_LABEL, MERGE_METHOD_LABEL, MERGE_MODE_HELP, MERGE_MODE_LABEL, diffPolicy, directPushBlockedReason, pathsToText, textToPaths, validBranchName } from '@/lib/repos'

type Draft = { policy: RepoPolicy; defaultBranch: string; isCompanyDefault: boolean; paths: string }

function CheckRow({ label, hint, checked, disabled, onChange, children }: { label: string; hint?: string; checked: boolean; disabled: boolean; onChange: (v: boolean) => void; children?: ReactNode }) {
  return (
    <label className="flex items-start gap-2 text-[13px] text-fg">
      <input type="checkbox" className="mt-0.5 accent-accent" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {hint && <span className="block text-[12px] text-fg-3">{hint}</span>}
        {children}
      </span>
    </label>
  )
}

function NumberField({ label, value, min, max, step = 1, hint, disabled, onChange }: { label: string; value: number; min: number; max: number; step?: number; hint?: string; disabled: boolean; onChange: (n: number) => void }) {
  return (
    <Field label={label} hint={hint ?? `${min} to ${max}`}>
      {({ id, describedBy }) => (
        <Input id={id} type="number" aria-describedby={describedBy} min={min} max={max} step={step} value={Number.isFinite(value) ? value : ''} disabled={disabled} onChange={(e) => onChange(e.target.value === '' ? Number.NaN : Number(e.target.value))} />
      )}
    </Field>
  )
}

/**
 * The repository policy. Owners and admins edit it; viewers see the same form read-only. Only the fields that changed are sent.
 * `direct_push` is disabled with its reason while the default branch is protected (the server refuses it as well).
 */
export function RepoPolicyForm({ companyId, repo, canEdit }: { companyId: string; repo: ProjectRepository; canEdit: boolean }) {
  const update = useUpdateRepository(companyId, repo.id)
  const toast = useToast()
  const [draft, setDraft] = useState<Draft>(() => ({ policy: repo.policy, defaultBranch: repo.default_branch, isCompanyDefault: repo.is_company_default, paths: pathsToText(repo.policy.protected_paths) }))
  const [error, setError] = useState<string | null>(null)
  const p = draft.policy
  const ro = !canEdit || repo.status === 'disconnected'
  const setP = <K extends keyof RepoPolicy>(key: K, value: RepoPolicy[K]) => setDraft((d) => ({ ...d, policy: { ...d.policy, [key]: value } }))
  const directBlocked = directPushBlockedReason(repo)

  const policy: RepoPolicy = { ...p, protected_paths: textToPaths(draft.paths) }
  const policyDiff = diffPolicy(repo.policy, policy)
  const branchChanged = draft.defaultBranch.trim() !== repo.default_branch
  const companyChanged = draft.isCompanyDefault !== repo.is_company_default
  const dirty = Object.keys(policyDiff).length > 0 || branchChanged || companyChanged
  const branchError = branchChanged && !validBranchName(draft.defaultBranch.trim()) ? 'Not a valid branch name' : null
  const numbersOk = [p.ci_wait_seconds, p.pr_batch_quiet_seconds, p.max_pr_revisions, p.max_ci_fix_attempts, p.max_open_intake_tasks, p.stale_branch_days, p.poll_interval_seconds].every(Number.isFinite)

  async function save() {
    setError(null)
    try {
      await update.mutateAsync({
        ...(Object.keys(policyDiff).length > 0 ? { policy: policyDiff } : {}),
        ...(branchChanged ? { default_branch: draft.defaultBranch.trim() } : {}),
        ...(companyChanged ? { is_company_default: draft.isCompanyDefault } : {}),
      })
      toast.success('Repository settings saved')
    } catch (e) {
      setError(errorMessage(e, 'Could not save the settings'))
    }
  }

  return (
    <div className="space-y-4">
      {!canEdit && <p className="text-[12.5px] text-fg-3">Only owners and admins can change these settings.</p>}
      <Card>
        <CardHeader title="Merging" description="How Foundry’s work reaches the default branch." />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Merge mode" hint={directBlocked && p.merge_mode !== 'direct_push' ? `Direct push is off: ${directBlocked}` : MERGE_MODE_HELP[p.merge_mode]}>
              {({ id, describedBy }) => (
                <Select id={id} aria-describedby={describedBy} disabled={ro} value={p.merge_mode} onChange={(e) => setP('merge_mode', e.target.value as MergeMode)}>
                  {(Object.keys(MERGE_MODE_LABEL) as MergeMode[]).map((m) => (
                    <option key={m} value={m} disabled={m === 'direct_push' && !!directBlocked && p.merge_mode !== 'direct_push'}>
                      {MERGE_MODE_LABEL[m]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Merge method">
              {({ id }) => (
                <Select id={id} disabled={ro} value={p.pr_merge_method} onChange={(e) => setP('pr_merge_method', e.target.value as RepoPolicy['pr_merge_method'])}>
                  {(Object.keys(MERGE_METHOD_LABEL) as Array<RepoPolicy['pr_merge_method']>).map((m) => (
                    <option key={m} value={m}>
                      {MERGE_METHOD_LABEL[m]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Default branch" error={branchError} hint="Pull requests are opened into this branch.">
              {({ id, invalid, describedBy }) => <Input id={id} disabled={ro} invalid={invalid} aria-describedby={describedBy} value={draft.defaultBranch} onChange={(e) => setDraft((d) => ({ ...d, defaultBranch: e.target.value }))} />}
            </Field>
            <Field label="Tests (CI)">
              {({ id }) => (
                <Select id={id} disabled={ro} value={p.ci_mode} onChange={(e) => setP('ci_mode', e.target.value as RepoPolicy['ci_mode'])}>
                  {(Object.keys(CI_MODE_LABEL) as Array<RepoPolicy['ci_mode']>).map((m) => (
                    <option key={m} value={m}>
                      {CI_MODE_LABEL[m]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <CheckRow label="Review every pull request internally before merging" checked={p.pr_internal_review} disabled={ro} onChange={(v) => setP('pr_internal_review', v)} />
          <CheckRow label="Fix failing CI" hint="Agents get a task when checks fail on their pull request." checked={p.fix_ci_failures} disabled={ro} onChange={(v) => setP('fix_ci_failures', v)} />
          <CheckRow label="Respond to review comments" checked={p.respond_to_reviews} disabled={ro} onChange={(v) => setP('respond_to_reviews', v)} />
          <div className="grid gap-4 sm:grid-cols-3">
            <NumberField label="Wait for CI (seconds)" value={p.ci_wait_seconds} min={60} max={7200} disabled={ro} onChange={(n) => setP('ci_wait_seconds', n)} />
            <NumberField label="Revisions per pull request" value={p.max_pr_revisions} min={0} max={10} disabled={ro} onChange={(n) => setP('max_pr_revisions', n)} />
            <NumberField label="CI fix attempts" value={p.max_ci_fix_attempts} min={0} max={10} disabled={ro} onChange={(n) => setP('max_ci_fix_attempts', n)} />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Issues and reviews from GitHub" description="GitHub text is untrusted: it is clipped, scanned and handed to agents in a marked block." />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Issue intake">
              {({ id }) => (
                <Select id={id} disabled={ro} value={p.issue_intake} onChange={(e) => setP('issue_intake', e.target.value as RepoPolicy['issue_intake'])}>
                  {(Object.keys(ISSUE_INTAKE_LABEL) as Array<RepoPolicy['issue_intake']>).map((m) => (
                    <option key={m} value={m}>
                      {ISSUE_INTAKE_LABEL[m]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Intake label" hint="Used when intake is “only issues with the intake label”.">
              {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} disabled={ro} maxLength={50} value={p.intake_label} onChange={(e) => setP('intake_label', e.target.value)} />}
            </Field>
            <NumberField label="Open intake tasks at most" value={p.max_open_intake_tasks} min={0} max={50} disabled={ro} onChange={(n) => setP('max_open_intake_tasks', n)} />
            <Field label="Whose reviews count">
              {({ id }) => (
                <Select id={id} disabled={ro} value={p.review_authors} onChange={(e) => setP('review_authors', e.target.value as RepoPolicy['review_authors'])}>
                  <option value="collaborators">Collaborators</option>
                  <option value="everyone">Everyone</option>
                </Select>
              )}
            </Field>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Maintenance" />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Security alerts from level">
              {({ id }) => (
                <Select id={id} disabled={ro} value={p.security_alerts} onChange={(e) => setP('security_alerts', e.target.value as RepoPolicy['security_alerts'])}>
                  {(['off', 'low', 'medium', 'high', 'critical'] as const).map((l) => (
                    <option key={l} value={l}>
                      {l === 'off' ? 'Off' : l[0]!.toUpperCase() + l.slice(1)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Releases">
              {({ id }) => (
                <Select id={id} disabled={ro} value={p.releases} onChange={(e) => setP('releases', e.target.value as RepoPolicy['releases'])}>
                  <option value="off">Off</option>
                  <option value="draft">Draft releases</option>
                  <option value="publish">Publish releases</option>
                </Select>
              )}
            </Field>
            <NumberField label="Stale branch cleanup (days)" value={p.stale_branch_days} min={0} max={365} hint="0 for never, otherwise 7 to 365" disabled={ro} onChange={(n) => setP('stale_branch_days', n)} />
            <NumberField label="Sync every (seconds)" value={p.poll_interval_seconds} min={60} max={3600} disabled={ro} onChange={(n) => setP('poll_interval_seconds', n)} />
          </div>
          <CheckRow label="Keep documentation up to date" checked={p.docs_upkeep} disabled={ro} onChange={(v) => setP('docs_upkeep', v)} />
          <CheckRow label="Allow changes to workflow files" checked={p.allow_workflow_changes} disabled={ro} onChange={(v) => setP('allow_workflow_changes', v)}>
            {p.allow_workflow_changes && (
              <span role="alert" className="mt-1 flex items-start gap-1.5 text-[12px] text-warning">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                Workflow files run with your repository’s secrets. The token also needs the Workflows permission. Changes still go through review.
              </span>
            )}
          </CheckRow>
          <Field label="Protected paths" hint="One glob per line. Agents cannot change these files; a push that touches them is blocked.">
            {({ id, describedBy }) => <textarea id={id} aria-describedby={describedBy} rows={4} disabled={ro} className="w-full rounded-md border border-line-strong bg-canvas px-2.5 py-1.5 font-mono text-[12.5px] text-fg focus:border-accent focus:outline-none disabled:opacity-60" value={draft.paths} onChange={(e) => setDraft((d) => ({ ...d, paths: e.target.value }))} />}
          </Field>
          <CheckRow label="This is the company’s default repository" hint="Later projects can get their own branch of it." checked={draft.isCompanyDefault} disabled={ro} onChange={(v) => setDraft((d) => ({ ...d, isCompanyDefault: v }))} />
        </CardBody>
      </Card>

      <FormError message={error} />
      {canEdit && (
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            disabled={!dirty || update.isPending}
            onClick={() => {
              setDraft({ policy: repo.policy, defaultBranch: repo.default_branch, isCompanyDefault: repo.is_company_default, paths: pathsToText(repo.policy.protected_paths) })
              setError(null)
            }}
          >
            Discard changes
          </Button>
          <Button variant="primary" loading={update.isPending} disabled={!dirty || ro || !!branchError || !numbersOk} onClick={() => void save()}>
            Save settings
          </Button>
        </div>
      )}
    </div>
  )
}
