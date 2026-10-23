import { useState } from 'react'
import { errorMessage, useDecideApproval } from '@/api'
import type { Agent, Approval, ApprovalDecision } from '@/api/types'
import { Badge, Button, Dialog, Field, FormError, Textarea, useToast } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import { ApprovalStatusBadge } from '@/features/shared/badges'
import { formatRelative, titleCase } from '@/lib/format'
import { formatUsd } from '@/lib/money'

const DECISION_LABEL: Record<ApprovalDecision, string> = {
  approve: 'Approve',
  reject: 'Reject',
  request_changes: 'Request changes',
}
const DECISION_PAST: Record<ApprovalDecision, string> = { approve: 'approved', reject: 'rejected', request_changes: 'sent back for changes' }

/** Only a human approver's pending approvals are decidable by the owner. */
export function isActionable(a: Pick<Approval, 'approver' | 'status'>): boolean {
  return a.approver === 'human' && a.status === 'pending'
}

export function ApprovalCard({ approval, agents, companyId, dense = false }: { approval: Approval; agents: Map<string, Agent>; companyId: string; dense?: boolean }) {
  const [deciding, setDeciding] = useState<ApprovalDecision | null>(null)
  const actionable = isActionable(approval)
  const riskTone = approval.risk === 'high' ? 'danger' : approval.risk === 'medium' ? 'warning' : 'neutral'

  return (
    <article className="px-3.5 py-3" aria-label={approval.title}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 text-[13px] font-medium break-words text-fg">{approval.title}</h3>
        {approval.status !== 'pending' ? <ApprovalStatusBadge status={approval.status} /> : null}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-fg-3">
        <Badge tone="neutral">{titleCase(approval.kind)}</Badge>
        {approval.risk && <Badge tone={riskTone}>{titleCase(approval.risk)} risk</Badge>}
        {approval.amount_micros !== null && (
          <span className="text-fg-2 tabular-nums">
            {formatUsd(approval.amount_micros)}
            {approval.recurrence && approval.recurrence !== 'one_time' ? ` / ${approval.recurrence === 'monthly' ? 'month' : 'year'}` : ''}
          </span>
        )}
        {approval.vendor && <span>{approval.vendor}</span>}
        <AgentChip agentId={approval.requested_by_agent_id} agents={agents} emptyLabel="System" />
        <time dateTime={approval.created_at}>{formatRelative(approval.created_at)}</time>
      </div>
      {approval.reason && <p className={dense ? 'mt-1.5 line-clamp-2 text-[12.5px] text-fg-2' : 'mt-1.5 text-[12.5px] whitespace-pre-wrap text-fg-2'}>{approval.reason}</p>}
      {approval.decision_note && (
        <p className="mt-1.5 text-[12px] text-fg-3">
          Decision note: <span className="text-fg-2">{approval.decision_note}</span>
        </p>
      )}

      {actionable ? (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <Button size="sm" variant="primary" onClick={() => setDeciding('approve')}>
            Approve
          </Button>
          <Button size="sm" onClick={() => setDeciding('request_changes')}>
            Request changes
          </Button>
          <Button size="sm" variant="danger" onClick={() => setDeciding('reject')}>
            Reject
          </Button>
        </div>
      ) : approval.status === 'pending' ? (
        <p className="mt-2 text-[12px] text-fg-3">
          {approval.approver === 'founder' ? 'The AI Co-founder decides this one. You are not asked.' : 'Waiting for a decision.'}
        </p>
      ) : null}

      {deciding && <DecisionDialog approval={approval} decision={deciding} companyId={companyId} onClose={() => setDeciding(null)} />}
    </article>
  )
}

function DecisionDialog({ approval, decision, companyId, onClose }: { approval: Approval; decision: ApprovalDecision; companyId: string; onClose: () => void }) {
  const mutate = useDecideApproval(companyId)
  const toast = useToast()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const noteRequired = decision !== 'approve'

  async function submit() {
    if (noteRequired && !note.trim()) {
      setError(decision === 'reject' ? 'Say why it is rejected so the requester can act on it.' : 'Say what needs to change.')
      return
    }
    setError(null)
    try {
      await mutate.mutateAsync({ id: approval.id, decision, note: note.trim() })
      toast.success(`${approval.title}: ${DECISION_PAST[decision]}`)
      onClose()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !mutate.isPending && onClose()}
      size="sm"
      title={`${DECISION_LABEL[decision]}: ${approval.title}`}
      description={approval.amount_micros !== null ? `This commits ${formatUsd(approval.amount_micros)}. The approval authorises exactly this one action.` : 'The approval authorises exactly this one action.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mutate.isPending}>
            Cancel
          </Button>
          <Button variant={decision === 'reject' ? 'danger' : 'primary'} loading={mutate.isPending} onClick={() => void submit()}>
            {DECISION_LABEL[decision]}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={noteRequired ? 'Note' : 'Note (optional)'} required={noteRequired} hint="Recorded with the decision and shown to the requester.">
          {({ id, invalid, describedBy }) => <Textarea id={id} autoFocus rows={3} value={note} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}
