import { CloudUpload, ExternalLink } from 'lucide-react'
import { errorMessage, useConnectedAccounts, useDeployHostedUi, useHostedUi } from '@/api'
import type { HostedUiDeployment, HostedUiStatus } from '@/api/types'
import { Badge, Boundary, Button, Card, CardHeader, Dot, useToast } from '@/components/ui'
import type { Tone } from '@/components/ui/Badge'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatRelative } from '@/lib/format'
import { accountState } from '@/lib/reachability'
import { UrlRow } from './shared'

const TONE: Record<HostedUiStatus, Tone> = { none: 'neutral', deploying: 'info', ready: 'success', error: 'danger' }
const LABEL: Record<HostedUiStatus, string> = { none: 'Not deployed', deploying: 'Deploying', ready: 'Live', error: 'Error' }

/** "Hosted control UI (Vercel)": optional. The page is hosted; the data and the agents stay on this device. */
export function HostedUiCard({ companyId, publicUrl, canManage }: { companyId: string; publicUrl: string | null; canManage: boolean }) {
  const q = useHostedUi(companyId)
  const accounts = useConnectedAccounts(companyId)
  const deploy = useDeployHostedUi(companyId)
  const { openConnectAccount, openPanel } = useCanvas()
  const toast = useToast()
  const vercel = accountState(accounts.data ?? null, 'vercel')

  return (
    <Card aria-label="Hosted control UI">
      <CardHeader title="Hosted control UI (Vercel)" description="Optional. A hosted copy of this web app that you can open from anywhere." />
      <Boundary query={q} milestone="M8" compact={false} unavailableTitle="Hosting the control UI is not built yet" unavailableText="Deploying the web app to Vercel arrives with reachability.">
        {(d: HostedUiDeployment | null) => {
          const status: HostedUiStatus = d?.status ?? 'none'
          const reason = !canManage
            ? 'Only the owner can deploy.'
            : vercel === 'missing' || vercel === 'unknown'
              ? 'Connect a Vercel account first.'
              : vercel === 'invalid'
                ? 'The connected Vercel account is not working. Test or reconnect it.'
                : !publicUrl
                  ? 'Start a tunnel first: the hosted UI reaches this device through the public URL.'
                  : null
          return (
            <div className="space-y-3 p-4">
              <div className="flex items-center gap-2">
                <Badge tone={TONE[status]}>
                  <Dot tone={TONE[status]} />
                  {LABEL[status]}
                </Badge>
                {d?.last_deployed_at && <span className="text-[12px] text-fg-3">Deployed {formatRelative(d.last_deployed_at)}</span>}
              </div>
              {d?.url && <UrlRow label="Hosted address" url={d.url} />}
              {d?.error && (
                <p role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
                  {d.error}
                </p>
              )}
              <p className="text-[12.5px] text-fg-2">
                The hosted page talks to <strong className="font-medium text-fg">this device</strong> through the public URL{d?.proxy_target ?? publicUrl ? <> (<span className="font-mono text-[12px]">{d?.proxy_target ?? publicUrl}</span>)</> : null}. If this computer is off or the tunnel is down, the hosted page loads but shows no data. Sign-in cookies stay first-party because <code className="font-mono text-[12px]">/api/*</code> is proxied.
              </p>
              {d?.approval_id && (
                <p role="status" className="flex flex-wrap items-center gap-2 text-[13px] text-fg-2">
                  This deploy waits for an approval.
                  <Button size="sm" onClick={() => openPanel({ kind: 'approvals' })}>
                    Open Approvals
                  </Button>
                </p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant={status === 'none' ? 'primary' : 'secondary'}
                  icon={<CloudUpload className="size-3.5" />}
                  loading={deploy.isPending || status === 'deploying'}
                  disabled={reason !== null}
                  onClick={() => deploy.mutate(undefined, { onSuccess: () => toast.success('Deploy started'), onError: (e) => toast.error(errorMessage(e)) })}
                >
                  {status === 'none' ? 'Deploy' : 'Redeploy'}
                </Button>
                {d?.url && (
                  <a href={d.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[12.5px] text-accent-fg hover:underline">
                    Open <ExternalLink className="size-3" aria-hidden />
                  </a>
                )}
              </div>
              {reason && (
                <p data-testid="hosted-blocked" className="text-[12.5px] text-fg-3">
                  {reason}{' '}
                  {(vercel === 'missing' || vercel === 'unknown') && canManage && (
                    <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => openConnectAccount('vercel')}>
                      Connect Vercel
                    </button>
                  )}
                </p>
              )}
            </div>
          )
        }}
      </Boundary>
    </Card>
  )
}
