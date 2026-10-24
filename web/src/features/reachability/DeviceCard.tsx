import { Laptop, Lock, Wifi } from 'lucide-react'
import { useId, useState } from 'react'
import { errorMessage, useSetLanAccess } from '@/api'
import type { ReachabilityStatus } from '@/api/types'
import { Badge, Button, Card, CardHeader, ConfirmDialog, SectionLabel, Switch, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { lanToggle, lanUrls } from '@/lib/reachability'
import { UrlRow } from './shared'

/** "This device": the local addresses, and the opt-in switch that opens the install to the local network. */
export function DeviceCard({ status, canManage }: { status: ReachabilityStatus; canManage: boolean }) {
  const { openPanel } = useCanvas()
  const toast = useToast()
  const setLan = useSetLanAccess()
  const [confirming, setConfirming] = useState(false)
  const reasonId = useId()
  const toggle = lanToggle(status)
  const urls = lanUrls(status)
  const blocked = !toggle.checked && toggle.disabled
  const disabled = !canManage || toggle.disabled

  function change(next: boolean) {
    if (next) setConfirming(true)
    else apply(false)
  }
  function apply(next: boolean) {
    setLan.mutate(next, {
      onSuccess: () => {
        setConfirming(false)
        toast.success(next ? 'LAN access saved. Restart Company OS to start listening on your network.' : 'LAN access turned off. Restart Company OS to stop listening on your network.')
      },
      onError: (e) => {
        setConfirming(false)
        toast.error(errorMessage(e))
      },
    })
  }

  return (
    <Card aria-label="This device">
      <CardHeader title="This device" description={`${status.device_name}${status.hostname && status.hostname !== status.device_name ? ` · ${status.hostname}` : ''}`} />
      <div className="space-y-4 p-4">
        <section aria-label="On this computer" className="space-y-2">
          <SectionLabel className="flex items-center gap-1.5">
            <Laptop className="size-3" aria-hidden /> On this computer
          </SectionLabel>
          {status.local_urls.length === 0 ? (
            <p className="text-[13px] text-fg-3">The server did not report a local address.</p>
          ) : (
            status.local_urls.map((u) => <UrlRow key={u} label="Local" url={u} />)
          )}
        </section>

        <section aria-label="On your network" className="space-y-2">
          <SectionLabel className="flex items-center gap-1.5">
            <Wifi className="size-3" aria-hidden /> On your network
          </SectionLabel>
          <div className="flex items-start justify-between gap-3 rounded-md border border-line px-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
                Enable LAN access
                <Badge tone={toggle.live ? 'info' : 'neutral'}>{toggle.live ? 'On' : 'Off'}</Badge>
                {toggle.restartRequired && <Badge tone="warning">Restart required</Badge>}
              </div>
              <p id={reasonId} className="mt-0.5 text-[12px] text-fg-3">
                {blocked && toggle.reason ? (
                  <span className="flex items-start gap-1.5 text-fg-2">
                    <Lock className="mt-0.5 size-3 shrink-0" aria-hidden />
                    <span>{toggle.reason}</span>
                  </span>
                ) : toggle.checked ? (
                  'Anyone on this network can reach the sign-in page at the addresses below.'
                ) : (
                  'Lets phones and other computers on your network open this app. The server then listens on all network interfaces, and sign-in is required.'
                )}
              </p>
              {toggle.restartRequired && (
                <p role="status" className="mt-1 text-[12px] text-fg-2">
                  {toggle.checked
                    ? 'Saved. The server is still listening on this computer only; restart Company OS to apply it.'
                    : 'Saved. The server is still listening on your network; restart Company OS to stop it.'}
                </p>
              )}
              {!canManage && <p className="mt-1 text-[12px] text-fg-3">Only the owner can change this.</p>}
              {blocked && (
                <Button size="sm" className="mt-2" onClick={() => openPanel({ kind: 'account' })}>
                  Open Account
                </Button>
              )}
            </div>
            <Switch label="Enable LAN access" checked={toggle.checked} disabled={disabled} loading={setLan.isPending} describedBy={reasonId} onCheckedChange={change} />
          </div>

          {toggle.live &&
            (urls.length === 0 ? (
              <p role="status" className="text-[13px] text-fg-3">
                LAN access is on, but no network address was found. Check that this computer is connected to a network.
              </p>
            ) : (
              urls.map((u, i) => <UrlRow key={u.url} label={u.label} url={u.url} qr qrOpen={i === 0} note={i === 0 && status.lan.mdns_url === u.url ? 'Stays the same when the numeric address changes.' : undefined} />)
            ))}
        </section>
      </div>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Turn on LAN access?"
        confirmLabel="Turn on"
        destructive={false}
        loading={setLan.isPending}
        error={setLan.isError ? errorMessage(setLan.error) : null}
        description="The server will restart its listener on all network interfaces. Anyone on this network will see the sign-in page. They still need your password."
        onConfirm={() => apply(true)}
      />
    </Card>
  )
}
