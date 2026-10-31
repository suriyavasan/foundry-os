import { Check, Copy, ExternalLink, Info } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ApiError, errorMessage, isNotDeployed, useOutsourceJob, useOutsourceStep, useRecordDelivery, type OutsourceStep } from '@/api'
import type { OutsourceJob } from '@/api/types'
import { Badge, Button, Card, CardBody, CardHeader, ConfirmDialog, DefinitionList, Dialog, ErrorState, Field, FormError, Input, LoadingState, NotAvailable, Textarea, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { formatDateTime } from '@/lib/format'
import { formatUsd } from '@/lib/money'
import { validateAttachment } from '@/lib/myWork'
import { MARKETPLACE_LABEL, OUTSOURCE_STATUS_LABEL, OUTSOURCE_STATUS_TONE, availableActions, briefText, listingUrlProblem, nextStep, parseUsd, safeOpenUrl, type JobAction } from '@/lib/outsourcing'

const ACTION_LABEL: Record<JobAction, string> = {
  posted: 'I posted it',
  hired: 'I hired someone',
  delivered: 'It was delivered',
  paid: 'I paid',
  'raise-budget': 'Raise budget',
  close: 'Close job',
  cancel: 'Cancel job',
}

/**
 * `?panel=outsource:<id>`: one freelance job HR drafted. The brief, who approved it, and the steps a person records after doing
 * them on the marketplace's own site (post, hire, delivery, payment). Foundry posts, messages, hires and pays nothing: recording a
 * payment writes a verified expense, it does not move money. The brief was written by an AI employee: plain text.
 */
export function JobPanel({ jobId }: { jobId: string }) {
  const { companyId, openPanel } = useCanvas()
  const q = useOutsourceJob(companyId, jobId)
  const role = useCompanyRole(companyId)
  const [action, setAction] = useState<JobAction | null>(null)

  if (q.isPending) {
    return (
      <PanelFrame title="Freelance job" onBack={() => openPanel({ kind: 'people-ops' })}>
        <Card>
          <LoadingState rows={6} label="Loading the job" />
        </Card>
      </PanelFrame>
    )
  }
  if (q.isError) {
    return (
      <PanelFrame title="Freelance job" onBack={() => openPanel({ kind: 'people-ops' })}>
        <Card>
          {isNotDeployed(q.error) ? (
            <NotAvailable milestone="M22" title="This job is not available" description={q.error instanceof ApiError && q.error.status === 404 ? 'It does not exist in this company, or this server has no outsourcing yet.' : 'This server has no outsourcing yet.'} />
          ) : (
            <ErrorState error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          )}
        </Card>
      </PanelFrame>
    )
  }

  const job = q.data
  const actions = canEditPolicy(role) ? availableActions(job) : []
  const open = safeOpenUrl(job)
  return (
    <PanelFrame
      title={job.title}
      subtitle={
        <span className="inline-flex items-center gap-2">
          <Badge tone={OUTSOURCE_STATUS_TONE[job.status]}>{OUTSOURCE_STATUS_LABEL[job.status]}</Badge>
          {MARKETPLACE_LABEL[job.marketplace]}
        </span>
      }
      onBack={() => openPanel({ kind: 'people-ops' })}
    >
      <div className="space-y-4">
        <Card>
          <CardBody className="space-y-2">
            <p className="flex items-start gap-2 text-[13px] text-fg-2">
              <Info className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
              <span>{nextStep(job)}</span>
            </p>
            <p className="text-[12px] text-fg-3">Foundry never posts, messages, hires or pays on a marketplace. You do that on {MARKETPLACE_LABEL[job.marketplace]}&apos;s own site, then record it here.</p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="The brief" description={`Drafted by ${job.created_by_agent_name ?? 'HR'}. Plain text: copy it into the marketplace's form.`} actions={<CopyBrief job={job} />} />
          <CardBody className="space-y-3">
            <p data-testid="job-brief" className="text-[13px] whitespace-pre-wrap text-fg">
              {job.brief}
            </p>
            {job.skills.length > 0 && (
              <p className="flex flex-wrap gap-1.5">
                {job.skills.map((s) => (
                  <Badge key={s} tone="neutral">
                    {s}
                  </Badge>
                ))}
              </p>
            )}
            {open && (
              <a href={open} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-[13px] text-accent-fg hover:underline">
                <ExternalLink className="size-3.5" aria-hidden /> Open {MARKETPLACE_LABEL[job.marketplace]}
              </a>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Status" />
          <CardBody>
            <DefinitionList
              items={[
                { label: 'Budget', value: <span className="tabular-nums">{formatUsd(job.budget_micros)} (the most you approved)</span> },
                { label: 'Approval', value: <ApprovalLine job={job} /> },
                ...(job.task_id ? [{ label: 'Serves task', value: <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'task', id: job.task_id! })}>{job.task_title ?? 'Open the task'}</button> }] : []),
                ...(job.listing_url ? [{ label: 'Listing', value: <ExternalLinkText url={job.listing_url} /> }] : []),
                ...(job.freelancer_name ? [{ label: 'Freelancer', value: job.freelancer_profile_url ? <ExternalLinkText url={job.freelancer_profile_url} label={job.freelancer_name} /> : job.freelancer_name }] : []),
                ...(job.agreed_price_micros != null ? [{ label: 'Agreed price', value: <span className="tabular-nums">{formatUsd(job.agreed_price_micros)}</span> }] : []),
                ...(job.paid_micros != null ? [{ label: 'Paid', value: <span className="tabular-nums">{formatUsd(job.paid_micros)} <Badge tone="success">verified</Badge> <span className="text-fg-3">recorded by a person, not paid by Foundry</span></span> }] : []),
                ...(job.delivered_artifact_ids.length > 0 ? [{ label: 'Delivered files', value: <span className="inline-flex flex-wrap gap-2">{job.delivered_artifact_ids.map((id, n) => <button key={id} type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'artifact', id })}>File {n + 1}</button>)}</span> }] : []),
                ...(job.notes ? [{ label: 'Notes', value: <span className="whitespace-pre-wrap">{job.notes}</span> }] : []),
                { label: 'Updated', value: formatDateTime(job.updated_at) },
              ]}
            />
          </CardBody>
        </Card>

        {actions.length > 0 && (
          <Card aria-label="Record a step">
            <CardHeader title="Record what you did" description="Only owners and admins record steps. Each one is checked and written to the activity log." />
            <CardBody className="flex flex-wrap gap-2">
              {[...new Set(actions)].map((a) => (
                <Button key={a} size="sm" variant={a === 'cancel' ? 'danger' : a === 'close' ? 'secondary' : 'primary'} onClick={() => setAction(a)}>
                  {a === 'hired' && job.status === 'hired' ? 'Change the hire' : ACTION_LABEL[a]}
                </Button>
              ))}
            </CardBody>
          </Card>
        )}
      </div>
      {action && <StepDialog companyId={companyId} job={job} action={action} onClose={() => setAction(null)} />}
    </PanelFrame>
  )
}

function ApprovalLine({ job }: { job: OutsourceJob }) {
  const { openPanel } = useCanvas()
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span className="capitalize">{(job.approval_status ?? 'none').replace(/_/g, ' ')}</span>
      {job.approval_id && (
        <button type="button" className="text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'approvals' })}>
          Open approvals
        </button>
      )}
    </span>
  )
}

function ExternalLinkText({ url, label }: { url: string; label?: string }) {
  if (!/^https:\/\//.test(url)) return <span className="break-all">{label ?? url}</span>
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="break-all text-accent-fg hover:underline">
      {label ?? url}
    </a>
  )
}

function CopyBrief({ job }: { job: OutsourceJob }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(briefText(job))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable: nothing to confirm */
    }
  }
  return (
    <Button size="sm" icon={copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />} onClick={() => void copy()}>
      {copied ? 'Copied' : 'Copy brief'}
    </Button>
  )
}

/** One dialog per step. The server validates every transition again; its message is shown as it is. */
function StepDialog({ companyId, job, action, onClose }: { companyId: string; job: OutsourceJob; action: JobAction; onClose: () => void }) {
  const step = useOutsourceStep(companyId)
  const delivery = useRecordDelivery(companyId)
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  const [fields, setFields] = useState({ url: job.listing_url ?? '', name: job.freelancer_name ?? '', profile: job.freelancer_profile_url ?? '', price: job.agreed_price_micros != null ? String(job.agreed_price_micros / 1_000_000) : '', amount: job.agreed_price_micros != null ? String(job.agreed_price_micros / 1_000_000) : '', budget: '', reason: '', notes: '' })
  const [file, setFile] = useState<File | null>(null)
  const set = (k: keyof typeof fields) => (e: { target: { value: string } }) => setFields((f) => ({ ...f, [k]: e.target.value }))
  const busy = step.isPending || delivery.isPending

  const SUCCESS: Record<JobAction, string> = {
    posted: 'Recorded as posted',
    hired: 'Hire recorded',
    delivered: 'Delivery recorded',
    paid: 'Payment recorded as a verified expense',
    'raise-budget': 'Asked for a higher budget: a person has to approve it',
    close: 'Job closed',
    cancel: 'Job cancelled',
  }

  async function submit(e?: FormEvent) {
    e?.preventDefault()
    setError(null)
    let request: OutsourceStep | null = null
    if (action === 'posted') {
      const problem = listingUrlProblem(job.marketplace, fields.url)
      if (problem) return setError(problem)
      request = { step: 'posted', body: { listing_url: fields.url.trim() } }
    } else if (action === 'hired') {
      const price = parseUsd(fields.price, { allowZero: true })
      if (!fields.name.trim()) return setError('Who did you hire?')
      if (price === null) return setError('Enter the agreed price in dollars, for example 45.50')
      if (price * 1_000_000 > job.budget_micros) return setError(`The agreed price is above the approved budget of ${formatUsd(job.budget_micros)}. Use "Raise budget" to ask for a new approval first.`)
      if (fields.profile.trim()) {
        const problem = listingUrlProblem(job.marketplace, fields.profile)
        if (problem) return setError(problem)
      }
      request = { step: 'hired', body: { freelancer_name: fields.name.trim(), freelancer_profile_url: fields.profile.trim() || null, agreed_price_usd: price } }
    } else if (action === 'paid') {
      const amount = parseUsd(fields.amount)
      if (amount === null) return setError('Enter the amount you paid in dollars, for example 45.50')
      if (job.agreed_price_micros != null && amount * 1_000_000 > job.agreed_price_micros) return setError(`That is above the agreed price of ${formatUsd(job.agreed_price_micros)}. Record the hire again with the new price first.`)
      request = { step: 'paid', body: { amount_usd: amount } }
    } else if (action === 'raise-budget') {
      const budget = parseUsd(fields.budget)
      if (budget === null) return setError('Enter the new budget in dollars')
      if (budget * 1_000_000 <= job.budget_micros) return setError(`The new budget must be above the current ${formatUsd(job.budget_micros)}`)
      request = { step: 'raise-budget', body: { new_budget_usd: budget, reason: fields.reason.trim() || null } }
    } else if (action === 'close') {
      request = { step: 'close', body: {} }
    } else if (action === 'cancel') {
      request = { step: 'cancel', body: { reason: fields.reason.trim() || null } }
    }
    try {
      if (action === 'delivered') {
        const fileError = validateAttachment(file)
        if (fileError) return setError(fileError)
        await delivery.mutateAsync({ id: job.id, notes: fields.notes, file })
      } else if (request) {
        await step.mutateAsync({ id: job.id, ...request })
      }
      toast.success(SUCCESS[action])
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'That did not go through'))
    }
  }

  if (action === 'close') {
    return (
      <ConfirmDialog
        open
        onOpenChange={(o) => !o && onClose()}
        title="Close this job?"
        description="Nothing more can be recorded on a closed job."
        confirmLabel="Close job"
        destructive={false}
        loading={busy}
        error={error}
        onConfirm={() => void submit()}
      />
    )
  }

  const title = action === 'hired' && job.status === 'hired' ? 'Change the hire' : ACTION_LABEL[action]
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      size="sm"
      title={title}
      description={job.title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Not now
          </Button>
          <Button variant={action === 'cancel' ? 'danger' : 'primary'} type="submit" form="job-step-form" loading={busy}>
            {action === 'cancel' ? 'Cancel the job' : action === 'raise-budget' ? 'Ask for approval' : 'Record'}
          </Button>
        </>
      }
    >
      <form id="job-step-form" onSubmit={submit} noValidate className="space-y-4">
        {action === 'posted' && (
          <Field label="Link to the posted job" required hint={`The page on ${MARKETPLACE_LABEL[job.marketplace]} where you posted it. Foundry never opens or fetches it.`}>
            {({ id, invalid, describedBy }) => <Input id={id} autoFocus inputMode="url" placeholder="https://" value={fields.url} invalid={invalid} aria-describedby={describedBy} onChange={set('url')} />}
          </Field>
        )}
        {action === 'hired' && (
          <>
            <Field label="Freelancer" required>
              {({ id }) => <Input id={id} autoFocus value={fields.name} maxLength={200} onChange={set('name')} />}
            </Field>
            <Field label="Their profile link" hint="Optional.">
              {({ id }) => <Input id={id} inputMode="url" placeholder="https://" value={fields.profile} onChange={set('profile')} />}
            </Field>
            <Field label="Agreed price (USD)" required hint={`At most the approved budget of ${formatUsd(job.budget_micros)}.`}>
              {({ id }) => <Input id={id} inputMode="decimal" value={fields.price} onChange={set('price')} />}
            </Field>
          </>
        )}
        {action === 'delivered' && (
          <>
            <Field label="Files" hint="Optional. Saved as an artifact of the company (up to 25 MB).">
              {({ id }) => <input id={id} type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="min-w-0 text-[12.5px] text-fg-2 file:mr-2 file:rounded-md file:border file:border-line-strong file:bg-raised file:px-2 file:py-1 file:text-[12.5px] file:text-fg" />}
            </Field>
            <Field label="Notes" hint="Optional.">
              {({ id }) => <Textarea id={id} rows={3} value={fields.notes} onChange={set('notes')} />}
            </Field>
          </>
        )}
        {action === 'paid' && (
          <Field label="Amount you paid (USD)" required hint="You pay on the marketplace; this only records it as a verified expense. At most the agreed price.">
            {({ id }) => <Input id={id} autoFocus inputMode="decimal" value={fields.amount} onChange={set('amount')} />}
          </Field>
        )}
        {action === 'raise-budget' && (
          <>
            <Field label="New budget (USD)" required hint={`Now ${formatUsd(job.budget_micros)}. A person has to approve the difference; the budget stays as it is until then.`}>
              {({ id }) => <Input id={id} autoFocus inputMode="decimal" value={fields.budget} onChange={set('budget')} />}
            </Field>
            <Field label="Why" hint="Optional.">
              {({ id }) => <Textarea id={id} rows={2} value={fields.reason} onChange={set('reason')} />}
            </Field>
          </>
        )}
        {action === 'cancel' && (
          <Field label="Reason" hint="Optional. Kept on the job.">
            {({ id }) => <Textarea id={id} autoFocus rows={2} value={fields.reason} onChange={set('reason')} />}
          </Field>
        )}
        <FormError message={error} />
      </form>
    </Dialog>
  )
}
