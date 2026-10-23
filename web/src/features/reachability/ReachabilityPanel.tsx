import { Globe, Plug } from 'lucide-react'
import { useConnectedAccounts, useEmailIdentity, useReachability } from '@/api'
import type { ReachabilityStatus } from '@/api/types'
import { Badge, Boundary, Button, Card, CardBody, DefinitionList } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { deriveMode, globeModel, publicUrlOf, MODE_LABEL } from '@/lib/reachability'
import { ChecklistCard } from './ChecklistCard'
import { DeviceCard } from './DeviceCard'
import { DomainCard } from './DomainCard'
import { EmailCard } from './EmailCard'
import { HostedUiCard } from './HostedUiCard'
import { TunnelCard } from './TunnelCard'
import { UrlRow } from './shared'

/** "How to reach": this device, the public URL, the domain, the hosted UI, company email, and the one-time steps. */
export function ReachabilityPanel() {
  const { companyId } = useCanvas()
  const q = useReachability()
  const role = useCompanyRole(companyId)
  const canManage = role === 'owner'

  return (
    <PanelFrame title="Reachability" subtitle="How to reach this company from other devices, and the company’s own email address">
      <Boundary query={q} milestone="M8" compact={false} unavailableTitle="Reachability is not built yet" unavailableText="Network addresses, a public URL, domains, hosting and company email arrive in this milestone.">
        {(status) => <Landing companyId={companyId} status={status} canManage={canManage} />}
      </Boundary>
    </PanelFrame>
  )
}

function Landing({ companyId, status, canManage }: { companyId: string; status: ReachabilityStatus; canManage: boolean }) {
  const accounts = useConnectedAccounts(companyId)
  const email = useEmailIdentity(companyId)
  const { openPanel, openConnectAccount } = useCanvas()
  const globe = globeModel(status)
  const mode = deriveMode(status)
  const accountList = accounts.data ?? null
  const live = (accountList ?? []).filter((a) => a.status !== 'revoked')

  return (
    <div className="space-y-4">
      <Card aria-label="Where it is reachable">
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Globe className="size-4 text-fg-3" aria-hidden />
            <h2 className="text-[14px] font-semibold text-fg">{MODE_LABEL[mode]}</h2>
            <Badge tone={globe.tone === 'neutral' ? 'neutral' : globe.tone}>{mode === 'public' ? 'Reachable from the internet' : mode === 'lan' ? 'Reachable on your network' : 'Only this computer'}</Badge>
          </div>
          {globe.url ? <UrlRow label="Address to share" url={globe.url} qr={mode !== 'local'} /> : <p className="text-[13px] text-fg-3">No address reported.</p>}
          {globe.problem && (
            <p role="alert" className="text-[13px] text-warning">
              {globe.problem}
            </p>
          )}
          <DefinitionList
            items={[
              { label: 'Sign-in', value: status.auth_mode ? (status.auth_mode === 'password' ? 'Password' : status.auth_mode) : '—' },
              { label: 'Connected accounts', value: accounts.isPending ? '…' : accountList ? `${live.filter((a) => a.status === 'connected').length} working of ${live.length}` : 'Not available' },
            ]}
          />
        </CardBody>
      </Card>

      <ChecklistCard status={status} accounts={accountList} email={email.data} canManage={canManage} />
      <DeviceCard status={status} canManage={canManage} />
      <TunnelCard companyId={companyId} status={status} canManage={canManage} />
      <DomainCard companyId={companyId} canManage={canManage} />
      <HostedUiCard companyId={companyId} publicUrl={publicUrlOf(status)} canManage={canManage} />
      <EmailCard companyId={companyId} canManage={canManage} />

      <Card aria-label="Connected accounts link">
        <CardBody className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold text-fg">Connected accounts</h2>
            <p className="mt-0.5 text-[12.5px] text-fg-3">Manage, test and revoke the services the AI Co-founder operates for you.</p>
          </div>
          <div className="flex gap-2">
            {canManage && (
              <Button size="sm" variant="primary" onClick={() => openConnectAccount()}>
                Connect an account
              </Button>
            )}
            <Button size="sm" icon={<Plug className="size-3.5" />} onClick={() => openPanel({ kind: 'integrations' })}>
              Open Integrations
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  )
}
