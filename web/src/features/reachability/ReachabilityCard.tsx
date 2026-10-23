import { Globe, X } from 'lucide-react'
import { useState } from 'react'
import { useConnectedAccounts, useReachability } from '@/api'
import { QrCode } from '@/components/qr/QrCode'
import { Button, IconButton } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { buildChecklist, canRenderQr, checklistProgress, dismissReachCard, isReachCardDismissed, lanUrls, publicUrlOf } from '@/lib/reachability'
import { CopyButton } from './shared'

function Line({ label, url, empty }: { label: string; url: string | null; empty: string }) {
  return (
    <div className="flex items-center gap-2 text-[13px]">
      <span className="w-28 shrink-0 text-fg-3">{label}</span>
      {url ? (
        <>
          <span className="min-w-0 truncate font-mono text-[12.5px] text-fg" title={url}>
            {url}
          </span>
          <CopyButton value={url} label={`Copy ${label.toLowerCase()} address`} />
        </>
      ) : (
        <span className="text-fg-3">{empty}</span>
      )}
    </div>
  )
}

/**
 * First-run card on the canvas: the three ways in (this computer, the local network, the internet) and how far the
 * one-time steps are. Dismissible per company on this browser; the full picture stays in the Reachability panel.
 * Renders nothing while loading, when reachability is not built yet, or after dismissal: it never blocks the canvas.
 */
export function ReachabilityCard() {
  const { companyId, openPanel } = useCanvas()
  const q = useReachability()
  const accounts = useConnectedAccounts(companyId)
  const [dismissed, setDismissed] = useState(() => isReachCardDismissed(companyId))
  if (dismissed || !q.data) return null

  const status = q.data
  const lan = status.lan.enabled ? lanUrls(status)[0]?.url ?? null : null
  const pub = publicUrlOf(status)
  const progress = checklistProgress(buildChecklist({ reachability: status, accounts: accounts.data ?? null }))

  return (
    <section aria-label="How to reach your company" className="mb-3 rounded-md border border-line bg-surface">
      <div className="flex items-start gap-3 p-3.5">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-accent-subtle text-accent-fg">
          <Globe className="size-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <h2 className="text-[13.5px] font-semibold text-fg">How to reach your company</h2>
            <p className="text-[12.5px] text-fg-3">Open it from your phone or another computer, or share a public address. {progress.done} of {progress.total} one-time steps done.</p>
          </div>
          <div className="space-y-1">
            <Line label="This computer" url={status.local_urls[0] ?? null} empty="Not reported" />
            <Line label="Local network" url={lan} empty="Off. Turn it on in Reachability." />
            <Line label="Internet" url={pub} empty="No public address yet." />
          </div>
          <div className="flex flex-wrap gap-2 pt-0.5">
            <Button size="sm" variant="primary" onClick={() => openPanel({ kind: 'reachability' })}>
              Open reachability
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { dismissReachCard(companyId); setDismissed(true) }}>
              Dismiss
            </Button>
          </div>
        </div>
        {lan && canRenderQr(lan) && (
          <div className="hidden shrink-0 sm:block">
            <QrCode value={lan} size={88} label={`QR code for ${lan}`} />
          </div>
        )}
        <IconButton label="Dismiss" className="-mt-1 -mr-1 size-7" onClick={() => { dismissReachCard(companyId); setDismissed(true) }}>
          <X className="size-4" aria-hidden />
        </IconButton>
      </div>
    </section>
  )
}
