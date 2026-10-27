import { useState } from 'react'
import { ApiError, errorMessage } from '@/api'
import { useCancelPurchase, useDecidePurchase, usePurchase, usePurchases, type PurchaseRequest, type PurchaseStatus } from '@/api/wallet'
import { Badge, Boundary, Button, ConfirmDialog, Dialog, Field, FormError, SectionLabel, Select, TBody, TD, TH, THead, TR, Table, Textarea, useToast } from '@/components/ui'
import { formatDateTime, formatRelative, titleCase } from '@/lib/format'
import { formatCost, formatUsd } from '@/lib/money'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PurchaseStatusBadge } from './badges'
import { CheckoutPacket } from './CheckoutPacket'
import { PURCHASE_FILTERS, canCancelPurchase, canDecidePurchase, cardDescription, expiry, needsCheckout, recurrenceSuffix, transactionProvenance } from './walletLogic'

export function PurchasesTab({ companyId }: { companyId: string }) {
  const [filter, setFilter] = useState<PurchaseStatus | 'all'>('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const q = usePurchases(companyId, filter)
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[12.5px] text-fg-3">Everything agents asked to buy. Item, vendor and justification are written by agents and shown as plain text.</p>
        <Select aria-label="Filter purchases" className="h-7 w-auto text-[12.5px]" value={filter} onChange={(e) => setFilter(e.target.value as PurchaseStatus | 'all')}>
          {PURCHASE_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </Select>
      </div>
      <div className="rounded-md border border-line bg-surface">
        <Boundary
          query={q}
          compact={false}
          milestone="M15"
          unavailableTitle="Purchases are not built yet"
          unavailableText="Agent purchase requests come with M15."
          isEmpty={(d) => d.length === 0}
          empty={{ title: filter === 'all' ? 'No purchase requests yet' : 'Nothing with this status', description: filter === 'all' ? 'When an agent needs to buy something, the request appears here with its justification.' : 'Choose another filter to see the rest.' }}
        >
          {(items) => (
            <Table>
              <THead>
                <TR>
                  <TH>Item</TH>
                  <TH>Vendor domain</TH>
                  <TH className="text-right">Amount</TH>
                  <TH>Status</TH>
                  <TH>Requested</TH>
                  <TH>
                    <span className="sr-only">Details</span>
                  </TH>
                </TR>
              </THead>
              <TBody>
                {items.map((p) => (
                  <TR key={p.id} interactive>
                    <TD className="max-w-[16rem] truncate font-medium">{p.item}</TD>
                    <TD className="font-mono text-[12px] text-fg-2">{p.vendor_domain}</TD>
                    <TD className="text-right tabular-nums">
                      {formatUsd(p.amount_micros)}
                      <span className="text-fg-3">{recurrenceSuffix(p.recurrence)}</span>
                    </TD>
                    <TD>
                      <PurchaseStatusBadge status={p.status} />
                    </TD>
                    <TD className="text-fg-3">
                      <time dateTime={p.created_at}>{formatRelative(p.created_at)}</time>
                    </TD>
                    <TD className="text-right">
                      <Button size="sm" aria-label={`Details of ${p.item}`} onClick={() => setOpenId(p.id)}>
                        Details
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Boundary>
      </div>
      {openId && <PurchaseDetail companyId={companyId} id={openId} onClose={() => setOpenId(null)} />}
    </div>
  )
}

interface TimelineEntry {
  at: string | null | undefined
  text: string
}

function PurchaseDetail({ companyId, id, onClose }: { companyId: string; id: string; onClose: () => void }) {
  const q = usePurchase(companyId, id)
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={q.data ? q.data.item : 'Purchase'} description={q.data ? `From ${q.data.vendor_domain}` : undefined}>
      <Boundary query={q} compact={false} milestone="M15" rows={6}>
        {(p) => <PurchaseDetailBody companyId={companyId} purchase={p} onClosed={onClose} />}
      </Boundary>
    </Dialog>
  )
}

function PurchaseDetailBody({ companyId, purchase: p, onClosed }: { companyId: string; purchase: NonNullable<ReturnType<typeof usePurchase>['data']>; onClosed: () => void }) {
  const { openPanel } = useCanvas()
  const agent = p.agent_name ?? 'An agent'
  const humanDecides = !!p.approval && p.approval.approver === 'human'
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const cancel = useCancelPurchase(companyId)
  const toast = useToast()
  const [cancelError, setCancelError] = useState<string | null>(null)

  const timeline: TimelineEntry[] = [
    { at: p.created_at, text: `${agent} requested it (${p.decision ? `decided by ${p.decision === 'cofounder' ? 'the AI Co-founder' : p.decision === 'report_only' ? 'a person, off-system' : p.decision}` : 'decision pending'})` },
    ...(p.approval?.decided_at ? [{ at: p.approval.decided_at, text: `Approval ${p.approval.status}${p.approval.decided_by_type ? ` by ${p.approval.decided_by_type}` : ''}` }] : []),
    ...(p.card ? [{ at: p.card.created_at, text: `Card ${cardDescription(p.card.brand, p.card.last4)} issued, limit ${formatUsd(p.card.limit_micros)}` }] : []),
    ...(p.transactions ?? []).map((t) => ({ at: t.occurred_at, text: `${titleCase(t.kind)} ${formatUsd(t.amount_micros)} (${t.status})` })),
    { at: p.updated_at, text: `Now: ${p.status.replace(/_/g, ' ')}` },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <PurchaseStatusBadge status={p.status} />
        <Badge tone="neutral">{p.recurrence === 'one_time' ? 'One time' : titleCase(p.recurrence)}</Badge>
        {p.quantity > 1 && <Badge tone="neutral">Qty {p.quantity}</Badge>}
        <span className="text-[13px] font-medium text-fg tabular-nums">
          {formatUsd(p.amount_micros)}
          {recurrenceSuffix(p.recurrence)}
        </span>
        {p.settled_micros != null && <span className="text-[12.5px] text-fg-3">Settled {formatCost(p.settled_micros, p.cost_kind ?? 'unknown')}</span>}
        {p.held_micros > 0 && !p.settled_micros && <span className="text-[12.5px] text-fg-3">{formatUsd(p.held_micros)} committed, not charged</span>}
      </div>

      <div>
        <SectionLabel>Why the agent wants it</SectionLabel>
        <p className="mt-1 text-[13px] whitespace-pre-wrap text-fg-2">{p.justification}</p>
        {p.alternatives && (
          <p className="mt-1.5 text-[12.5px] text-fg-3">
            Alternatives considered: <span className="text-fg-2">{p.alternatives}</span>
          </p>
        )}
        <p className="mt-1.5 text-[12px] text-fg-3">
          Requested by {agent} for{' '}
          <button type="button" className="underline" onClick={() => openPanel({ kind: 'task', id: p.task_id })}>
            its task
          </button>
          . This text was written by an agent.
        </p>
      </div>

      {(needsCheckout(p.status) || p.checkout_packet) && <CheckoutPacket companyId={companyId} purchase={p} />}

      {p.approval && (
        <div>
          <SectionLabel>Approval</SectionLabel>
          <p className="mt-1 text-[13px] text-fg-2">
            {titleCase(p.approval.status)} · decided by {p.approval.approver === 'founder' ? 'the AI Co-founder' : p.approval.approver}
            {p.approval.decision_note ? ` · "${p.approval.decision_note}"` : ''}
          </p>
        </div>
      )}

      {p.card && (
        <div>
          <SectionLabel>Card</SectionLabel>
          <p className="mt-1 text-[13px] text-fg-2">
            {cardDescription(p.card.brand, p.card.last4)}
            {expiry(p.card.exp_month, p.card.exp_year) ? ` · expires ${expiry(p.card.exp_month, p.card.exp_year)}` : ''} · limit {formatUsd(p.card.limit_micros)} · {titleCase(p.card.status)}
            {p.card.merchant_lock.domain ? ` · locked to ${p.card.merchant_lock.domain}` : ''}
          </p>
        </div>
      )}

      {(p.transactions?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Transactions</SectionLabel>
          <ul className="mt-1 space-y-1 text-[12.5px] text-fg-2">
            {p.transactions!.map((t) => (
              <li key={t.id} className="flex flex-wrap justify-between gap-2">
                <span>
                  {titleCase(t.kind)} · {t.merchant_name ?? 'merchant unknown'} · {transactionProvenance(t)}
                </span>
                <span className="tabular-nums">{formatCost(t.amount_micros, t.cost_kind)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <SectionLabel>Timeline</SectionLabel>
        <ol className="mt-1 space-y-1 text-[12.5px] text-fg-2">
          {timeline.map((t, i) => (
            <li key={`${i}-${t.text}`} className="flex gap-3">
              <span className="w-32 shrink-0 text-fg-3">{formatDateTime(t.at)}</span>
              <span>{t.text}</span>
            </li>
          ))}
        </ol>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
        {canDecidePurchase(p.status) && humanDecides && (
          <>
            <Button variant="primary" onClick={() => setDeciding('approve')}>
              Approve
            </Button>
            <Button variant="danger" onClick={() => setDeciding('reject')}>
              Reject
            </Button>
          </>
        )}
        {canDecidePurchase(p.status) && !humanDecides && <p className="text-[12.5px] text-fg-3">{p.approval?.approver === 'founder' ? 'The AI Co-founder decides this one. You are not asked.' : 'Waiting for a decision.'}</p>}
        {canCancelPurchase(p.status) && (
          <Button onClick={() => setCancelling(true)} className="ml-auto">
            Cancel purchase
          </Button>
        )}
      </div>

      {deciding && <DecideDialog companyId={companyId} purchase={p} decision={deciding} onClose={() => setDeciding(null)} />}
      <ConfirmDialog
        open={cancelling}
        onOpenChange={(o) => {
          setCancelling(o)
          if (!o) setCancelError(null)
        }}
        title="Cancel this purchase?"
        description="Any card issued for it is closed and the committed amount is released. The agent is told."
        confirmLabel="Cancel purchase"
        loading={cancel.isPending}
        error={cancelError}
        onConfirm={async () => {
          setCancelError(null)
          try {
            await cancel.mutateAsync({ id: p.id })
            toast.success('Purchase cancelled')
            setCancelling(false)
            onClosed()
          } catch (err) {
            setCancelError(err instanceof ApiError ? err.message : errorMessage(err))
          }
        }}
      />
    </div>
  )
}

function DecideDialog({ companyId, purchase, decision, onClose }: { companyId: string; purchase: PurchaseRequest; decision: 'approve' | 'reject'; onClose: () => void }) {
  const decide = useDecidePurchase(companyId)
  const toast = useToast()
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const approve = decision === 'approve'

  async function submit() {
    if (!approve && !note.trim()) {
      setError('Say why it is rejected so the agent can act on it.')
      return
    }
    setError(null)
    try {
      await decide.mutateAsync({ id: purchase.id, decision, note: note.trim() })
      toast.success(approve ? 'Purchase approved' : 'Purchase rejected')
      onClose()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !decide.isPending && onClose()}
      size="sm"
      title={`${approve ? 'Approve' : 'Reject'}: ${purchase.item}`}
      description={approve ? `This commits ${formatUsd(purchase.amount_micros)}${recurrenceSuffix(purchase.recurrence)} to ${purchase.vendor_domain}. The approval covers exactly this purchase.` : 'The agent is told why.'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={decide.isPending}>
            Cancel
          </Button>
          <Button variant={approve ? 'primary' : 'danger'} loading={decide.isPending} onClick={() => void submit()}>
            {approve ? 'Approve purchase' : 'Reject purchase'}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={approve ? 'Note (optional)' : 'Note'} required={!approve} hint="Recorded with the decision and shown to the agent.">
          {({ id, invalid, describedBy }) => <Textarea id={id} autoFocus rows={3} value={note} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        <FormError message={error} />
      </div>
    </Dialog>
  )
}
