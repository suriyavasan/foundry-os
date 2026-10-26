import { Download, Globe2, Lock, Play, Square } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useConnectedAccounts, useInstallTunnelBinary, useStartTunnel, useStopTunnel } from '@/api'
import type { ReachabilityStatus, TunnelBinary, TunnelProviderKind } from '@/api/types'
import { Badge, Button, Card, CardHeader, ConfirmDialog, Dot, Field, Input, SectionLabel, Select, useToast } from '@/components/ui'
import type { Tone } from '@/components/ui/Badge'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { formatBytes } from '@/lib/format'
import { formatRelative } from '@/lib/format'
import { TUNNEL_PROVIDER_HELP, TUNNEL_PROVIDER_LABEL, TUNNEL_STATE_LABEL, publicUrlOf, tunnelControls, tunnelStartBody } from '@/lib/reachability'
import { UrlRow } from './shared'

const STATE_TONE: Record<ReachabilityStatus['tunnel']['state'], Tone> = { stopped: 'neutral', starting: 'info', running: 'success', error: 'danger' }
const PROVIDERS: TunnelProviderKind[] = ['cloudflare_quick', 'cloudflare_named', 'tailscale_funnel']

/** "Public URL": the tunnel provider, its state, and start / stop. Nothing is exposed without the password gate. */
export function TunnelCard({ companyId, status, canManage }: { companyId: string; status: ReachabilityStatus; canManage: boolean }) {
  const t = status.tunnel
  const accounts = useConnectedAccounts(companyId)
  const { openPanel, openConnectAccount } = useCanvas()
  const toast = useToast()
  const start = useStartTunnel()
  const stop = useStopTunnel()
  const [provider, setProvider] = useState<TunnelProviderKind>(t.provider ?? 'cloudflare_quick')
  const [hostname, setHostname] = useState(t.hostname ?? '')
  const [confirmStart, setConfirmStart] = useState(false)
  const controls = tunnelControls(status, { provider, hostname, accounts: accounts.data ?? null })
  const url = publicUrlOf(status)
  const running = t.state === 'running' || t.state === 'starting'
  const needsHostname = provider !== 'cloudflare_quick'
  const accountNeed = t.providers.find((p) => p.kind === provider)?.requires_account ?? (provider === 'cloudflare_named' ? 'cloudflare' : provider === 'tailscale_funnel' ? 'tailscale' : null)

  function doStart() {
    start.mutate(tunnelStartBody(provider, hostname), {
      onSuccess: () => {
        setConfirmStart(false)
        toast.success('Starting the tunnel')
      },
      onError: () => undefined,
    })
  }

  return (
    <Card aria-label="Public URL">
      <CardHeader
        title="Public URL"
        description="A tunnel gives this computer an address on the internet without opening any ports on your router."
        actions={
          <Badge tone={STATE_TONE[t.state]} data-state={t.state}>
            <Dot tone={STATE_TONE[t.state]} />
            {TUNNEL_STATE_LABEL[t.state]}
          </Badge>
        }
      />
      <div className="space-y-4 p-4">
        {t.state === 'error' && (
          <div role="alert" className="rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[13px] text-danger">
            {t.error ?? 'The tunnel stopped with an error.'}
            {t.restarts > 0 && <span className="mt-0.5 block text-[12px] text-fg-2">Restarted {t.restarts} time{t.restarts === 1 ? '' : 's'} automatically.</span>}
          </div>
        )}

        {url ? (
          <div className="space-y-1.5">
            <UrlRow label="Public address" url={url} qr />
            <p className="px-1 text-[12px] text-fg-3">
              {t.healthy === false ? <span className="text-warning">Last health check failed. </span> : null}
              {t.last_health_check_at ? `Health checked ${formatRelative(t.last_health_check_at)}.` : 'Not health-checked yet.'}
              {t.restarts > 0 ? ` Restarted ${t.restarts} time${t.restarts === 1 ? '' : 's'}.` : ''}
            </p>
          </div>
        ) : t.state === 'starting' ? (
          <p role="status" className="text-[13px] text-fg-2">
            Waiting for the provider to hand out an address…
          </p>
        ) : null}

        {t.ephemeral && (
          <div data-testid="ephemeral-note" className="space-y-1 rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-2">
            <p className="font-medium text-fg">This address changes when Foundry restarts</p>
            <p>
              {t.url_changed_at ? `Address changed ${formatRelative(t.url_changed_at)}. ` : ''}
              Bookmarks and shared links stop working after a restart, and passkeys need a stable address. For a lasting address use a named tunnel or Tailscale Funnel.
            </p>
            {!url && t.last_url && (
              <p>
                Last address: <code className="font-mono text-[11.5px] break-all text-fg">{t.last_url}</code>
              </p>
            )}
          </div>
        )}

        {!running && (
          <div className="space-y-3">
            <Field label="Tunnel provider" hint={TUNNEL_PROVIDER_HELP[provider]}>
              {({ id, describedBy }) => (
                <Select id={id} aria-describedby={describedBy} value={provider} disabled={!canManage || start.isPending} onChange={(e) => setProvider(e.target.value as TunnelProviderKind)}>
                  {PROVIDERS.map((p) => (
                    <option key={p} value={p}>
                      {TUNNEL_PROVIDER_LABEL[p]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            {needsHostname && (
              <Field label="Public hostname" hint="A name on a domain your connected account controls.">
                {({ id, describedBy }) => <Input id={id} aria-describedby={describedBy} inputMode="url" spellCheck={false} placeholder="app.example.com" value={hostname} disabled={!canManage} onChange={(e) => setHostname(e.target.value)} />}
              </Field>
            )}
            {accountNeed && (
              <p className="text-[12px] text-fg-3">
                Needs a connected {accountNeed[0]!.toUpperCase() + accountNeed.slice(1)} account.{' '}
                <button type="button" className="font-medium text-accent-fg underline-offset-2 hover:underline" onClick={() => openConnectAccount(accountNeed as 'cloudflare' | 'tailscale')}>
                  Connect it
                </button>
              </p>
            )}
          </div>
        )}

        {controls.needsInstall && t.binary && <InstallBlock binary={t.binary} canManage={canManage} />}

        <div className="flex flex-wrap items-center gap-2">
          {(controls.primary === 'start' || controls.primary === 'retry') && (
            <Button variant="primary" icon={<Play className="size-3.5" />} disabled={!canManage || !controls.canStart} onClick={() => setConfirmStart(true)}>
              {controls.primary === 'retry' ? 'Start again' : 'Start tunnel'}
            </Button>
          )}
          {controls.canStop && (
            <Button icon={<Square className="size-3.5" />} loading={stop.isPending} disabled={!canManage} onClick={() => stop.mutate(undefined, { onSuccess: () => toast.success('Tunnel stopped'), onError: (e) => toast.error(errorMessage(e)) })}>
              {t.state === 'starting' ? 'Cancel' : 'Stop tunnel'}
            </Button>
          )}
          {!canManage && <span className="text-[12px] text-fg-3">Only the owner can change this.</span>}
        </div>
        {!controls.canStart && controls.startBlockedReason && !running && (
          <p role="status" data-testid="start-blocked" className="flex items-start gap-1.5 text-[12.5px] text-fg-2">
            <Lock className="mt-0.5 size-3 shrink-0 text-fg-3" aria-hidden />
            <span>
              {controls.startBlockedReason}
              {status.public_exposure_blocked_reason && controls.startBlockedReason === status.public_exposure_blocked_reason && (
                <>
                  {' '}
                  <button type="button" className="font-medium text-accent-fg underline-offset-2 hover:underline" onClick={() => openPanel({ kind: 'account' })}>
                    Open Account
                  </button>
                </>
              )}
            </span>
          </p>
        )}
      </div>

      <ConfirmDialog
        open={confirmStart}
        onOpenChange={setConfirmStart}
        title="Make this app reachable from the internet?"
        confirmLabel="Start tunnel"
        destructive={false}
        loading={start.isPending}
        error={start.isError ? errorMessage(start.error) : null}
        description={
          <>
            Anyone with the address can reach the sign-in page, so keep your password strong and turn on two-step verification. {provider === 'cloudflare_quick' ? 'A quick tunnel gets a new random address every time it starts.' : null}
          </>
        }
        onConfirm={doStart}
      />
    </Card>
  )
}

/** "cloudflared is not installed": the steps, and the checksum-verified download when the server offers one. */
function InstallBlock({ binary, canManage }: { binary: TunnelBinary; canManage: boolean }) {
  const install = useInstallTunnelBinary()
  const toast = useToast()
  const [confirming, setConfirming] = useState(false)
  return (
    <section aria-label={`${binary.name} is not installed`} className="space-y-2 rounded-md border border-line bg-raised p-3">
      <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
        <Globe2 className="size-3.5 text-fg-3" aria-hidden />
        {binary.name} is not installed
      </div>
      <p className="text-[12.5px] text-fg-2">The tunnel needs the {binary.name} program on this computer. Nothing is downloaded unless you ask for it.</p>
      {binary.install_instructions.length > 0 && (
        <div>
          <SectionLabel>Install it yourself</SectionLabel>
          <ol className="mt-1 list-decimal space-y-1 pl-5 text-[12.5px] text-fg-2">
            {binary.install_instructions.map((s, i) => (
              <li key={i} className="break-words">
                {s}
              </li>
            ))}
          </ol>
        </div>
      )}
      {binary.download_offered && (
        <div>
          <SectionLabel>Or let Foundry AI OS download it</SectionLabel>
          <p className="mt-1 text-[12.5px] text-fg-2">
            The file is checked against a SHA-256 checksum before it is used.
            {binary.download_sha256 && (
              <>
                {' '}
                Expected checksum: <code className="font-mono text-[11.5px] break-all text-fg">{binary.download_sha256}</code>
              </>
            )}
            {binary.download_size_bytes ? ` Size: ${formatBytes(binary.download_size_bytes)}.` : ''}
          </p>
          <Button size="sm" className="mt-2" icon={<Download className="size-3.5" />} disabled={!canManage} onClick={() => setConfirming(true)}>
            Download and verify {binary.name}
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Download ${binary.name}?`}
        confirmLabel="Download and verify"
        destructive={false}
        loading={install.isPending}
        error={install.isError ? errorMessage(install.error) : null}
        description={
          <>
            Downloads {binary.name}
            {binary.download_url ? (
              <>
                {' '}
                from <code className="font-mono text-[12px] break-all">{binary.download_url}</code>
              </>
            ) : null}{' '}
            and installs it for Foundry AI OS only. It is rejected if the checksum does not match.
          </>
        }
        onConfirm={() => install.mutate(undefined, { onSuccess: () => { setConfirming(false); toast.success(`${binary.name} installed and verified`) }, onError: () => undefined })}
      />
    </section>
  )
}
