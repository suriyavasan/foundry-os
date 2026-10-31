import { ExternalLink } from 'lucide-react'
import { useMemo, useState } from 'react'
import { errorMessage, isNotDeployed, useOutsourceJobs, usePolicy, useUpdatePolicy } from '@/api'
import type { OutsourceJob } from '@/api/types'
import { Badge, Button, Card, CardHeader, Dialog, EmptyState, ErrorState, Field, FormError, Input, LoadingState, NotAvailable, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { formatRelative } from '@/lib/format'
import { formatUsd, microsToUsdString, usdToMicros } from '@/lib/money'
import { MARKETPLACE_LABEL, OUTSOURCE_STATUS_LABEL, OUTSOURCE_STATUS_TONE, isOpenJob } from '@/lib/outsourcing'

/**
 * Freelance jobs HR drafted. Foundry never posts, messages, hires or pays on Fiverr or Upwork: a person approves the draft, does the
 * outward-facing steps on the marketplace's own site and records them (open a job to do that). The jobs are written by an AI
 * employee: plain text.
 */
export function FreelancersTab({ companyId }: { companyId: string }) {
  const { openPanel } = useCanvas()
  const jobs = useOutsourceJobs(companyId)
  const { active, finished } = useMemo(() => {
    const data = jobs.data ?? []
    return { active: data.filter(isOpenJob), finished: data.filter((j) => !isOpenJob(j)) }
  }, [jobs.data])

  if (jobs.isPending) {
    return (
      <Card>
        <LoadingState rows={4} label="Loading freelance jobs" />
      </Card>
    )
  }
  if (jobs.isError) {
    return (
      <Card>
        {isNotDeployed(jobs.error) ? (
          <NotAvailable milestone="M22" title="Outsourcing is not built yet" description="Freelance jobs drafted by HR arrive with M22." />
        ) : (
          <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} retrying={jobs.isFetching} />
        )}
      </Card>
    )
  }
  if ((jobs.data ?? []).length === 0) {
    return (
      <Card>
        <EmptyState title="No freelance jobs yet" description="When the company lacks a skill or capacity that no skill or hire can cover, HR drafts a job here. You approve it, post it on Fiverr or Upwork yourself and record the result. Foundry never posts or pays on a marketplace." />
      </Card>
    )
  }
  return (
    <div className="space-y-4">
      <CeilingLine companyId={companyId} />
      <JobList title="Open jobs" description="Drafts waiting for approval and jobs in progress." jobs={active} onOpen={(id) => openPanel({ kind: 'outsource', id })} />
      {finished.length > 0 && <JobList title="Finished" description="Closed, cancelled or declined." jobs={finished} onOpen={(id) => openPanel({ kind: 'outsource', id })} />}
    </div>
  )
}

function JobList({ title, description, jobs, onOpen }: { title: string; description: string; jobs: OutsourceJob[]; onOpen: (id: string) => void }) {
  return (
    <Card aria-label={title}>
      <CardHeader title={title} description={description} actions={<Badge tone="neutral">{jobs.length}</Badge>} />
      {jobs.length === 0 ? (
        <p className="px-4 py-4 text-[12.5px] text-fg-3">Nothing here.</p>
      ) : (
        <ul className="divide-y divide-line">
          {jobs.map((j) => (
            <li key={j.id}>
              <button type="button" aria-label={`Open job ${j.title}`} onClick={() => onOpen(j.id)} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left transition-colors hover:bg-hover/60 focus-visible:bg-hover/60 focus-visible:outline-none">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-fg">{j.title}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-fg-3">
                    <Badge tone={OUTSOURCE_STATUS_TONE[j.status]}>{OUTSOURCE_STATUS_LABEL[j.status]}</Badge>
                    <span>{MARKETPLACE_LABEL[j.marketplace]}</span>
                    {j.freelancer_name && <span>with {j.freelancer_name}</span>}
                    <time dateTime={j.updated_at}>{formatRelative(j.updated_at)}</time>
                  </span>
                </span>
                <span className="text-right text-[12.5px] text-fg-2 tabular-nums">
                  <span className="block">Budget {formatUsd(j.budget_micros)}</span>
                  {j.agreed_price_micros != null && <span className="block text-fg-3">Agreed {formatUsd(j.agreed_price_micros)}</span>}
                  {j.paid_micros != null && <span className="block text-success">Paid {formatUsd(j.paid_micros)}</span>}
                </span>
                <ExternalLink className="size-3.5 shrink-0 text-fg-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/**
 * The most one freelance job may be budgeted at. Only owners and admins change it; HR can never draft a job above it, and no AI
 * employee can change it. Shown only when the server reports it.
 */
function CeilingLine({ companyId }: { companyId: string }) {
  const role = useCompanyRole(companyId)
  const policy = usePolicy(companyId)
  const [editing, setEditing] = useState(false)
  const ceiling = policy.data?.outsource_max_job_micros
  if (ceiling === undefined) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-line bg-surface px-3.5 py-2 text-[12.5px] text-fg-2">
      <span>
        HR may draft jobs up to <span className="font-medium text-fg tabular-nums">{formatUsd(ceiling)}</span> each.
      </span>
      {canEditPolicy(role) && (
        <Button size="sm" onClick={() => setEditing(true)}>
          Change limit
        </Button>
      )}
      {editing && <CeilingDialog companyId={companyId} current={ceiling} onClose={() => setEditing(false)} />}
    </div>
  )
}

function CeilingDialog({ companyId, current, onClose }: { companyId: string; current: number; onClose: () => void }) {
  const update = useUpdatePolicy(companyId)
  const toast = useToast()
  const [value, setValue] = useState(microsToUsdString(current))
  const [error, setError] = useState<string | null>(null)

  async function submit(e: { preventDefault: () => void }) {
    e.preventDefault()
    const micros = usdToMicros(value)
    if (micros === null || micros > 10_000_000_000) {
      setError('Enter an amount in dollars between 0 and 10,000')
      return
    }
    setError(null)
    try {
      await update.mutateAsync({ outsource_max_job_micros: micros })
      toast.success('Limit updated')
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'That did not go through'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !update.isPending && onClose()}
      size="sm"
      title="Limit per freelance job"
      description="The most HR may put in the budget of one job. Raising a job's budget later needs a new approval."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={update.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="job-ceiling-form" loading={update.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form id="job-ceiling-form" onSubmit={submit} noValidate className="space-y-3">
        <Field label="Limit per job (USD)" required>
          {({ id, invalid }) => <Input id={id} autoFocus inputMode="decimal" value={value} invalid={invalid} onChange={(e) => setValue(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}
