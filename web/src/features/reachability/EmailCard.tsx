import { Mail, Plus } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { errorMessage, useConnectedAccounts, useCreateMailbox, useEmailIdentity, useMailboxes, useUpdateEmailIdentity } from '@/api'
import type { Agent, ConnectedAccount, ConnectedProvider, EmailIdentity, EmailProviderKind } from '@/api/types'
import { Badge, Boundary, Button, Card, CardHeader, Dialog, Field, FormError, Input, Select, Switch, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { AgentChip } from '@/features/shared/AgentChip'
import { EMAIL_PROVIDER_INFO, MAILBOX_STATUS_LABEL, MAILBOX_STATUS_TONE, emailProviderInfo, emailProviderReadiness, mailboxOwners, sortMailboxes, validateLocalPart } from '@/lib/email'
import { formatRelative } from '@/lib/format'
import { providerLabel } from '@/lib/connectedAccounts'

/** "Company email": the identity provider, the mailboxes the AI Co-founder keeps, and the AI disclosure footer. */
export function EmailCard({ companyId, canManage }: { companyId: string; canManage: boolean }) {
  const identity = useEmailIdentity(companyId)
  const mailboxes = useMailboxes(companyId)
  const accounts = useConnectedAccounts(companyId)
  const { map: agents, agents: agentList } = useAgentMap(companyId)
  const { openConnectAccount } = useCanvas()
  const [adding, setAdding] = useState(false)
  const connectedAny = identity.data?.status === 'connected'

  return (
    <Card aria-label="Company email">
      <CardHeader
        title="Company email"
        description="The company’s own addresses, so agents can write and receive as the company and not as you."
        actions={
          connectedAny && canManage ? (
            <Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>
              Add mailbox
            </Button>
          ) : undefined
        }
      />
      <Boundary query={identity} milestone="M8" compact={false} unavailableTitle="Company email is not built yet" unavailableText="Mailboxes and the email identity arrive with reachability.">
        {(id) => (
          <div className="space-y-4 p-4">
            <ProviderPicker identity={id} companyId={companyId} accounts={accounts.data ?? null} canManage={canManage} onConnect={openConnectAccount} />
            <p className="flex items-start gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-2">
              <Mail className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
              <span>The AI Co-founder creates mailboxes automatically once a provider is connected: <span className="font-mono text-[12px]">company@</span>, <span className="font-mono text-[12px]">cofounder@</span> and one per agent that needs one. Incoming mail is treated as untrusted input; outgoing mail is reviewed first.</span>
            </p>
            <Boundary
              query={mailboxes}
              milestone="M8"
              unavailableText="Mailboxes arrive with reachability."
              isEmpty={(d) => d.length === 0}
              empty={{ title: 'No mailboxes yet', description: connectedAny ? 'The AI Co-founder creates them as soon as it needs one.' : 'Connect an email provider above and they appear here.' }}
            >
              {(boxes) => (
                <Table aria-label="Mailboxes">
                  <THead>
                    <TR>
                      <TH>Address</TH>
                      <TH>Owner</TH>
                      <TH>Received</TH>
                      <TH>Sent</TH>
                      <TH>Status</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {sortMailboxes(boxes).map((m) => (
                      <TR key={m.id}>
                        <TD className="font-mono text-[12.5px]">{m.address}</TD>
                        <TD>
                          <AgentChip agentId={m.owner_agent_id} agents={agents} size="xs" emptyLabel="The company" />
                        </TD>
                        <TD className="tabular-nums" title={m.last_inbound_at ? `Last ${formatRelative(m.last_inbound_at)}` : undefined}>
                          {m.inbound_count}
                        </TD>
                        <TD className="tabular-nums" title={m.last_outbound_at ? `Last ${formatRelative(m.last_outbound_at)}` : undefined}>
                          {m.outbound_count}
                        </TD>
                        <TD>
                          <Badge tone={MAILBOX_STATUS_TONE[m.status] ?? 'neutral'}>{MAILBOX_STATUS_LABEL[m.status] ?? m.status}</Badge>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              )}
            </Boundary>
            <DisclosureSetting identity={id} companyId={companyId} canManage={canManage} />
          </div>
        )}
      </Boundary>
      {adding && <MailboxDialog companyId={companyId} agents={mailboxOwners(agentList)} domain={identity.data?.domain ?? null} onClose={() => setAdding(false)} />}
    </Card>
  )
}

function ProviderPicker({ identity, companyId, accounts, canManage, onConnect }: { identity: EmailIdentity; companyId: string; accounts: ConnectedAccount[] | null; canManage: boolean; onConnect: (p: ConnectedProvider) => void }) {
  const update = useUpdateEmailIdentity(companyId)
  const toast = useToast()
  const info = emailProviderInfo(identity.provider)
  const readiness = info ? emailProviderReadiness(info, accounts) : null
  return (
    <div className="space-y-2">
      <Field label="Identity provider" hint={info?.summary ?? 'Choose how the company sends and receives email.'}>
        {({ id, describedBy }) => (
          <Select
            id={id}
            aria-describedby={describedBy}
            value={identity.provider ?? ''}
            disabled={!canManage || update.isPending}
            onChange={(e) => update.mutate({ provider: (e.target.value || null) as EmailProviderKind | null }, { onError: (err) => toast.error(errorMessage(err)) })}
          >
            <option value="">Not chosen</option>
            {EMAIL_PROVIDER_INFO.map((p) => (
              <option key={p.kind} value={p.kind}>
                {p.label}
              </option>
            ))}
          </Select>
        )}
      </Field>
      {info && readiness && !readiness.ready && (
        <p role="status" className="text-[12.5px] text-fg-2">
          {readiness.failing.length > 0 ? `The connected ${readiness.failing.map(providerLabel).join(' and ')} account is not working. ` : null}
          {readiness.missing.length > 0 ? `Still to connect: ${readiness.missing.map(providerLabel).join(', ')}. ` : null}
          {canManage && (
            <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => onConnect((readiness.missing[0] ?? readiness.failing[0] ?? info.needs[0]) as ConnectedProvider)}>
              Connect {providerLabel(readiness.missing[0] ?? readiness.failing[0] ?? info.needs[0]!)}
            </button>
          )}
        </p>
      )}
      {identity.status === 'error' && identity.error && (
        <p role="alert" className="text-[12.5px] text-danger">
          {identity.error}
        </p>
      )}
      {identity.domain && <p className="text-[12.5px] text-fg-3">Sending domain: <span className="font-mono">{identity.domain}</span></p>}
    </div>
  )
}

function DisclosureSetting({ identity, companyId, canManage }: { identity: EmailIdentity; companyId: string; canManage: boolean }) {
  const update = useUpdateEmailIdentity(companyId)
  const toast = useToast()
  const helpId = useId()
  return (
    <div className="flex items-start justify-between gap-3 rounded-md border border-line px-3 py-2.5">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-fg">AI disclosure footer</div>
        <p id={helpId} className="mt-0.5 text-[12px] text-fg-3">
          {identity.ai_disclosure_footer ? 'Every outgoing message ends with a line saying it was written by an AI agent of this company.' : 'Off: recipients are not told that an AI wrote the message. Most places expect disclosure.'}
        </p>
      </div>
      <Switch
        label="AI disclosure footer"
        checked={identity.ai_disclosure_footer}
        disabled={!canManage}
        loading={update.isPending}
        describedBy={helpId}
        onCheckedChange={(next) => update.mutate({ ai_disclosure_footer: next }, { onSuccess: () => toast.success(next ? 'Footer on' : 'Footer off'), onError: (e) => toast.error(errorMessage(e)) })}
      />
    </div>
  )
}

function MailboxDialog({ companyId, agents, domain, onClose }: { companyId: string; agents: Agent[]; domain: string | null; onClose: () => void }) {
  const create = useCreateMailbox(companyId)
  const toast = useToast()
  const [local, setLocal] = useState('')
  const [owner, setOwner] = useState(agents[0]?.id ?? '')
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const localErr = touched ? validateLocalPart(local) : null

  async function submit(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (validateLocalPart(local) || !owner) return
    setError(null)
    try {
      const box = await create.mutateAsync({ local_part: local.trim().toLowerCase(), owner_agent_id: owner })
      toast.success(box.status === 'pending' ? `${box.address} requested` : `${box.address} created`)
      onClose()
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !create.isPending && onClose()}
      title="Add a mailbox"
      description="Normally the AI Co-founder does this. Use it for an address you want right now."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="mailbox-form" loading={create.isPending}>
            Create mailbox
          </Button>
        </>
      }
    >
      <form id="mailbox-form" onSubmit={submit} noValidate className="space-y-4">
        <Field label="Address" required error={localErr} hint={domain ? undefined : 'The domain comes from the email provider.'}>
          {({ id, invalid, describedBy }) => (
            <div className="flex items-center gap-2">
              <Input id={id} autoFocus spellCheck={false} autoCapitalize="none" invalid={invalid} aria-describedby={describedBy} placeholder="support" value={local} onChange={(e) => setLocal(e.target.value.toLowerCase())} />
              {domain && <span className="shrink-0 font-mono text-[12.5px] text-fg-2">@{domain}</span>}
            </div>
          )}
        </Field>
        <Field label="Owner" required hint="Mail to this address goes to this agent.">
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={owner} onChange={(e) => setOwner(e.target.value)}>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.role_title}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <FormError message={error} />
      </form>
    </Dialog>
  )
}
