import { CheckCircle2, ExternalLink, ShieldAlert } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { errorMessage } from '@/api'
import { useMarkPurchasePaid, type CardProviderId, type PurchaseRequest } from '@/api/wallet'
import { Badge, Button, Dialog, Field, FormError, Input, SectionLabel, Textarea, useToast } from '@/components/ui'
import { formatUsd } from '@/lib/money'
import { PurchaseStatusBadge } from './badges'
import { RevealDialog } from './RevealDialog'
import { cardDescription, expiry, isPaid, needsCheckout, recurrenceSuffix } from './walletLogic'

const DEFAULT_STEPS = [
  'Open the vendor site shown below and check that the domain matches.',
  'Choose the plan and quantity listed here.',
  'Reveal the card (a link to the provider; you read the number there).',
  'Pay. The charge must not be more than the card limit.',
  'Come back: the payment is detected automatically, or press "Mark paid".',
]

/** The vendor address as a link only when it is plain https on the same domain the packet names. Otherwise it stays text. */
export function safeVendorLink(url: string, vendorDomain: string): string | null {
  try {
    const u = new URL(url)
    if (u.protocol !== 'https:' || u.username || u.password) return null
    const host = u.hostname.toLowerCase().replace(/^www\./, '')
    const domain = vendorDomain.toLowerCase().replace(/^www\./, '')
    return host === domain || host.endsWith(`.${domain}`) ? u.toString() : null
  } catch {
    return null
  }
}

/**
 * What the person needs to finish a purchase an agent asked for: the vendor domain (large, because the address itself is
 * text an agent wrote), what to buy, the exact amount and card limit, which company identity to use, what to bring back, the
 * numbered steps, "Reveal card" (provider link, never a number) and "Mark paid". Item, vendor and address are untrusted
 * agent text and are only ever rendered as text.
 */
export function CheckoutPacket({ companyId, purchase, provider }: { companyId: string; purchase: PurchaseRequest; provider?: CardProviderId }) {
  const packet = purchase.checkout_packet
  const [revealing, setRevealing] = useState(false)
  const [marking, setMarking] = useState(false)
  const paid = isPaid(purchase.status)
  const open = needsCheckout(purchase.status)

  if (!packet) {
    return (
      <section aria-label="Checkout packet" className="rounded-md border border-line bg-raised p-3 text-[13px] text-fg-2">
        <div className="flex flex-wrap items-center gap-2">
          <PurchaseStatusBadge status={purchase.status} />
          <span>This purchase has no checkout packet yet.</span>
        </div>
      </section>
    )
  }

  const link = safeVendorLink(packet.url, packet.vendor_domain)
  const steps = packet.steps.length > 0 ? packet.steps : DEFAULT_STEPS
  const cardLabel = cardDescription(packet.card_brand, packet.card_last4)
  const exp = expiry(packet.card_exp_month, packet.card_exp_year)

  return (
    <section aria-label="Checkout packet" className="space-y-3 rounded-md border border-line bg-raised p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <SectionLabel>Buy from</SectionLabel>
          <p className="mt-0.5 font-mono text-[18px] leading-6 font-semibold break-all text-fg" data-testid="vendor-domain">
            {packet.vendor_domain}
          </p>
        </div>
        <PurchaseStatusBadge status={purchase.status} />
      </div>

      {paid && (
        <p role="status" className="flex items-center gap-1.5 rounded-md bg-success-subtle px-2.5 py-1.5 text-[13px] font-medium text-success">
          <CheckCircle2 className="size-3.5" aria-hidden />
          Paid ✓ {purchase.status === 'settled' ? 'The charge is recorded.' : 'The payment was seen. It is being settled.'}
        </p>
      )}

      <dl className="grid grid-cols-[minmax(7rem,9rem)_1fr] gap-x-3 gap-y-1.5 text-[13px]">
        <dt className="text-fg-3">Item</dt>
        <dd className="min-w-0 break-words text-fg">
          {packet.item}
          {packet.quantity > 1 ? ` × ${packet.quantity}` : ''}
        </dd>
        <dt className="text-fg-3">Vendor name</dt>
        <dd className="min-w-0 break-words text-fg-2">{packet.vendor}</dd>
        <dt className="text-fg-3">Amount</dt>
        <dd className="text-fg tabular-nums">
          {formatUsd(packet.amount_micros)}
          {recurrenceSuffix(packet.recurrence)}
          {packet.recurrence !== 'one_time' && <Badge tone="warning" className="ml-2">Recurring</Badge>}
        </dd>
        {packet.card_limit_micros != null && (
          <>
            <dt className="text-fg-3">Card limit</dt>
            <dd className="text-fg tabular-nums">{formatUsd(packet.card_limit_micros)}</dd>
          </>
        )}
        {packet.card_last4 && (
          <>
            <dt className="text-fg-3">Card</dt>
            <dd className="text-fg">
              {cardLabel}
              {exp ? ` · expires ${exp}` : ''}
            </dd>
          </>
        )}
        {packet.company_email && (
          <>
            <dt className="text-fg-3">Sign up with</dt>
            <dd className="min-w-0 break-all text-fg">{packet.company_email}</dd>
          </>
        )}
        {packet.bring_back && (
          <>
            <dt className="text-fg-3">Bring back</dt>
            <dd className="min-w-0 break-words text-fg">{packet.bring_back}</dd>
          </>
        )}
        <dt className="text-fg-3">Address</dt>
        <dd className="min-w-0 break-all font-mono text-[12px] text-fg-2">{packet.url}</dd>
      </dl>

      <p className="flex items-start gap-1.5 text-[12px] text-fg-3">
        <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        <span>The item, vendor and address were written by an agent. Check the domain above before you pay.{link ? '' : ' The address does not match that domain, so it is not offered as a link.'}</span>
      </p>

      <div>
        <SectionLabel>Steps</SectionLabel>
        <ol className="mt-1 list-decimal space-y-1 pl-5 text-[13px] text-fg-2">
          {steps.map((s, i) => (
            <li key={`${i}-${s}`}>{s}</li>
          ))}
        </ol>
      </div>

      <div className="flex flex-wrap gap-2">
        {link && (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line-strong bg-raised px-2.5 text-[12.5px] font-medium text-fg hover:bg-hover"
          >
            <ExternalLink className="size-3.5" aria-hidden />
            Open {packet.vendor_domain} (external site)
          </a>
        )}
        {purchase.card_id && !paid && (
          <Button size="sm" onClick={() => setRevealing(true)}>
            Reveal card
          </Button>
        )}
        {open && (
          <Button size="sm" variant="primary" onClick={() => setMarking(true)}>
            Mark paid
          </Button>
        )}
      </div>

      {revealing && purchase.card_id && (
        <RevealDialog companyId={companyId} cardId={purchase.card_id} provider={provider} cardLabel={cardLabel} onClose={() => setRevealing(false)} />
      )}
      {marking && <MarkPaidDialog companyId={companyId} purchase={purchase} onClose={() => setMarking(false)} />}
    </section>
  )
}

const USD = /^\d{1,12}(\.\d{1,2})?$/

/** "Mark paid": the amount you actually paid is recorded as verified, source "confirmed by you". */
export function MarkPaidDialog({ companyId, purchase, onClose }: { companyId: string; purchase: PurchaseRequest; onClose: () => void }) {
  const mark = useMarkPurchasePaid(companyId)
  const toast = useToast()
  const [amount, setAmount] = useState('')
  const [inr, setInr] = useState('')
  const [note, setNote] = useState('')
  const [errors, setErrors] = useState<{ amount?: string; inr?: string }>({})
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    const next: typeof errors = {}
    const a = amount.trim().replace(/^\$/, '')
    if (!USD.test(a) || Number(a) <= 0) next.amount = 'Enter the amount charged in USD, like 24.50'
    const r = inr.trim().replace(/^₹/, '').replace(/,/g, '')
    if (r && (!USD.test(r) || Number(r) <= 0)) next.inr = 'Enter the amount your bank billed in INR, like 2050.00'
    setErrors(next)
    if (Object.keys(next).length) return
    try {
      await mark.mutateAsync({ id: purchase.id, body: { merchant_amount_usd: a, ...(r ? { billed_amount_inr: r } : null), ...(note.trim() ? { note: note.trim() } : null) } })
      toast.success('Recorded as paid')
      onClose()
    } catch (err) {
      setFormError(errorMessage(err, 'That did not go through'))
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !mark.isPending && onClose()}
      size="sm"
      title="Mark paid"
      description={`${purchase.item} from ${purchase.vendor_domain}. What you enter is recorded as a verified charge, confirmed by you.`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mark.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="mark-paid-form" loading={mark.isPending}>
            Record payment
          </Button>
        </>
      }
    >
      <form id="mark-paid-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Amount charged (USD)" required error={errors.amount} hint={`Approved for ${formatUsd(purchase.amount_micros)}. Enter what the vendor actually charged, including tax.`}>
          {({ id, invalid, describedBy }) => <Input id={id} autoFocus inputMode="decimal" value={amount} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setAmount(e.target.value)} />}
        </Field>
        <Field label="Billed by your bank (INR)" error={errors.inr} hint="Optional. For an Indian card, the rupee amount on your statement.">
          {({ id, invalid, describedBy }) => <Input id={id} inputMode="decimal" value={inr} invalid={invalid} aria-describedby={describedBy} onChange={(e) => setInr(e.target.value)} />}
        </Field>
        <Field label="Note" hint="Optional. Recorded with the payment.">
          {({ id, describedBy }) => <Textarea id={id} rows={2} value={note} aria-describedby={describedBy} onChange={(e) => setNote(e.target.value)} />}
        </Field>
        <FormError message={formError} />
      </form>
    </Dialog>
  )
}
