import { ExternalLink, Globe, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useDomainPlan, useVerifyDomain } from '@/api'
import type { DnsRecord, DomainPlan } from '@/api/types'
import { Badge, Boundary, Button, Card, CardHeader, DefinitionList, Table, TBody, TD, TH, THead, TR, useToast } from '@/components/ui'
import type { Tone } from '@/components/ui/Badge'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatRelative, titleCase } from '@/lib/format'
import { DomainWizard } from './DomainWizard'

const CHECK_TONE: Record<DomainPlan['dns_status'], Tone> = { unknown: 'neutral', pending: 'warning', ok: 'success', error: 'danger' }
const CHECK_LABEL: Record<DomainPlan['dns_status'], string> = { unknown: 'Not checked', pending: 'Pending', ok: 'OK', error: 'Problem' }
const STATUS_LABEL: Record<DomainPlan['status'], string> = {
  none: 'No domain',
  awaiting_approval: 'Waiting for your approval',
  pr_open: 'Pull request open',
  awaiting_founder: 'Waiting for you to finish registration',
  pending_dns: 'Waiting for DNS',
  active: 'Active',
  error: 'Problem',
}
const RECORD_TONE: Record<NonNullable<DnsRecord['status']>, Tone> = { ok: 'success', missing: 'danger', mismatch: 'warning', pending: 'warning' }

export function DnsRecordsTable({ records }: { records: DnsRecord[] }) {
  if (records.length === 0) return null
  return (
    <Table aria-label="DNS records">
      <THead>
        <TR>
          <TH>Type</TH>
          <TH>Name</TH>
          <TH>Value</TH>
          <TH>Check</TH>
        </TR>
      </THead>
      <TBody>
        {records.map((r, i) => (
          <TR key={`${r.type}-${r.name}-${i}`}>
            <TD className="font-mono text-[12px]">{r.type}</TD>
            <TD className="font-mono text-[12px] break-all">{r.name}</TD>
            <TD className="font-mono text-[12px] break-all">{r.value}</TD>
            <TD>{r.status ? <Badge tone={RECORD_TONE[r.status]}>{titleCase(r.status)}</Badge> : <span className="text-fg-3">—</span>}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  )
}

/** "Domain": the company's current domain with DNS / TLS state, and the wizard that gets one. */
export function DomainCard({ companyId, canManage }: { companyId: string; canManage: boolean }) {
  const q = useDomainPlan(companyId)
  const verify = useVerifyDomain(companyId)
  const { openPanel } = useCanvas()
  const toast = useToast()
  const [wizard, setWizard] = useState(false)

  return (
    <Card aria-label="Domain">
      <CardHeader
        title="Domain"
        description="A name of your own for the public address, with DNS and a certificate set up for you."
        actions={
          q.data && canManage ? (
            <Button size="sm" variant={q.data.domain ? 'secondary' : 'primary'} icon={<Globe className="size-3.5" />} onClick={() => setWizard(true)}>
              {q.data.domain ? 'Get another domain' : 'Get a domain'}
            </Button>
          ) : undefined
        }
      />
      <Boundary query={q} milestone="M8" compact={false} unavailableTitle="Domains are not built yet" unavailableText="Free subdomains, assisted free domains and purchases arrive with reachability.">
        {(plan) => (
          <div className="space-y-4 p-4">
            {!plan.domain ? (
              <p className="text-[13px] text-fg-2">No domain yet. Without one, the public address is the tunnel’s own (random for a quick tunnel).</p>
            ) : (
              <DefinitionList
                items={[
                  { label: 'Domain', value: <span className="font-mono text-[12.5px]">{plan.domain}</span> },
                  { label: 'Status', value: <Badge tone={plan.status === 'active' ? 'success' : plan.status === 'error' ? 'danger' : 'info'}>{STATUS_LABEL[plan.status]}</Badge> },
                  { label: 'DNS', value: <Badge tone={CHECK_TONE[plan.dns_status]}>{CHECK_LABEL[plan.dns_status]}</Badge> },
                  { label: 'TLS certificate', value: <Badge tone={CHECK_TONE[plan.tls_status]}>{CHECK_LABEL[plan.tls_status]}</Badge> },
                  ...(plan.last_checked_at ? [{ label: 'Last checked', value: formatRelative(plan.last_checked_at) }] : []),
                ]}
              />
            )}
            {plan.error && (
              <p role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
                {plan.error}
              </p>
            )}
            {plan.registration?.pr_url && (
              <a href={plan.registration.pr_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 text-[13px] text-accent-fg hover:underline">
                Registration pull request <ExternalLink className="size-3" aria-hidden />
              </a>
            )}
            {plan.registration?.status === 'awaiting_approval' && (
              <p role="status" className="flex flex-wrap items-center gap-2 text-[13px] text-fg-2">
                This registration waits for your approval.
                <Button size="sm" onClick={() => openPanel({ kind: 'approvals' })}>
                  Open Approvals
                </Button>
              </p>
            )}
            {plan.steps.length > 0 && (
              <div>
                <h3 className="mb-1 text-[12px] font-medium text-fg-2">Steps for you</h3>
                <ol className="list-decimal space-y-1 pl-5 text-[12.5px] text-fg-2">
                  {plan.steps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ol>
              </div>
            )}
            <DnsRecordsTable records={plan.dns_records} />
            {plan.domain && canManage && (plan.status === 'awaiting_founder' || plan.status === 'pending_dns' || plan.status === 'error') && (
              <Button
                icon={<RefreshCw className="size-3.5" />}
                loading={verify.isPending}
                onClick={() => verify.mutate(undefined, { onSuccess: (p) => toast.success(p.dns_status === 'ok' ? 'DNS is correct' : 'Checked. DNS is not ready yet.'), onError: (e) => toast.error(errorMessage(e)) })}
              >
                Check DNS now
              </Button>
            )}
          </div>
        )}
      </Boundary>
      {wizard && <DomainWizard companyId={companyId} onClose={() => setWizard(false)} />}
    </Card>
  )
}
