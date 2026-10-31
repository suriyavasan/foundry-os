import { Download } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { errorMessage, useCliInstallLog, useCliInvalidate, useCliRuntimes, useInstallCli } from '@/api'
import type { CliInstallMethodInfo, CliInstallMethodKind } from '@/api/types'
import { Button, ConfirmDialog, FormError } from '@/components/ui'

/** The installer's host, for the confirm text ("downloads … from cursor.com"). */
export function scriptHost(url: string | null): string {
  if (!url) return 'the vendor'
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/**
 * Install a coding CLI after the person confirms the exact method (nothing runs before that), then tail the install log.
 * Methods come from the server (`install.methods`): npm, or the vendor's official installer script. A method choice is
 * shown when two can run here. Only when none can run does the person get the reason and the manual command.
 * `command` is a fallback for callers that only know the stack option's install command.
 */
export function CliInstall({
  companyId,
  cliId,
  name,
  command,
  canEdit,
  onStarted,
}: {
  companyId?: string
  cliId: string
  name: string
  command?: string | null
  canEdit: boolean
  onStarted?: () => void
}) {
  const runtimes = useCliRuntimes()
  const info = runtimes.data?.find((c) => c.id === cliId)?.install
  const install = useInstallCli(companyId)
  const refresh = useCliInvalidate(companyId)
  const [confirm, setConfirm] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<CliInstallMethodKind | null>(null)
  const log = useCliInstallLog(installing ? cliId : null, installing)
  const logDone = !!log.data && (log.data.state === 'succeeded' || log.data.state === 'failed')

  // Without the CLI list (still loading, or an older server) fall back to the one command the caller knows.
  const methods: CliInstallMethodInfo[] =
    info?.methods && info.methods.length > 0
      ? info.methods
      : command
        ? [{ method: command.startsWith('Download ') ? 'script' : 'npm', command, url: null, available: true, reason: null }]
        : []
  const runnable = methods.filter((m) => m.available)
  const chosen = runnable.find((m) => m.method === picked) ?? runnable.find((m) => m.method === info?.default_method) ?? runnable[0] ?? null

  const refreshed = useRef(false)
  useEffect(() => {
    if (logDone && !refreshed.current) {
      refreshed.current = true
      refresh() // the CLI is installed now: every status flips without a reload
    }
  }, [logDone, refresh])

  async function run() {
    if (!chosen) return
    setError(null)
    refreshed.current = false
    try {
      await install.mutateAsync({ id: cliId, method: chosen.method })
      setConfirm(false)
      setInstalling(true)
      onStarted?.()
    } catch (e) {
      setConfirm(false)
      setError(errorMessage(e))
    }
  }

  const manual = info?.manual ?? null

  return (
    <div className="space-y-2">
      {!installing &&
        (chosen ? (
          <>
            <p className="text-[12.5px] text-fg-2">{name} is not installed on this computer. Foundry can install it after you confirm.</p>
            {runnable.length > 1 ? (
              <fieldset className="space-y-1">
                <legend className="text-[12px] font-medium text-fg-2">How to install</legend>
                {runnable.map((m) => (
                  <label key={m.method} className="flex items-start gap-2 text-[12.5px] text-fg-2">
                    <input type="radio" name={`install-method-${cliId}`} className="mt-0.5" checked={chosen.method === m.method} onChange={() => setPicked(m.method)} />
                    <span>
                      <span className="font-medium text-fg">{m.method === 'npm' ? 'npm' : "Vendor's installer"}</span>
                      <span className="block font-mono text-[11.5px] break-all text-fg-3">{m.command}</span>
                    </span>
                  </label>
                ))}
              </fieldset>
            ) : (
              <p className="font-mono text-[11.5px] break-all text-fg-3">{chosen.command}</p>
            )}
            {canEdit ? (
              <Button icon={<Download className="size-3.5" />} onClick={() => setConfirm(true)} disabled={install.isPending}>
                Install {name}
              </Button>
            ) : (
              <p className="text-[12px] text-fg-3">Only the installation owner can install tools.</p>
            )}
          </>
        ) : (
          <div className="space-y-1">
            <p className="text-[12.5px] text-fg-2">{name} is not installed, and Foundry cannot install it on this computer.</p>
            {methods.map((m) => (
              <p key={m.method} className="text-[12px] text-fg-3">
                {m.method === 'npm' ? 'npm' : "Vendor's installer"}: {m.reason ?? 'cannot run here'}.
              </p>
            ))}
            {manual && (
              <>
                <p className="text-[12px] text-fg-3">Run this yourself in a terminal, then check again:</p>
                <pre aria-label="Manual install command" className="overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[12px] text-fg">
                  {manual}
                </pre>
              </>
            )}
            {!manual && <p className="text-[12px] text-fg-3">Install it yourself from its official instructions, then check again.</p>}
          </div>
        ))}
      {installing && (
        <div>
          <p className="mb-1 text-[12px] font-medium text-fg-2">{log.data?.state === 'failed' ? 'Install failed' : logDone ? 'Install finished' : 'Installing…'}</p>
          <pre
            aria-label={`${name} install log`}
            tabIndex={0}
            className="max-h-40 overflow-auto rounded-md border border-line bg-canvas p-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-fg-2"
          >
            {log.data && log.data.lines.length > 0 ? log.data.lines.join('\n') : 'Waiting for output…'}
          </pre>
          {log.data?.sha256 && <p className="mt-1 font-mono text-[11px] break-all text-fg-3">Installer SHA-256: {log.data.sha256}</p>}
          {logDone && (
            <Button size="sm" className="mt-2" variant="ghost" onClick={() => setInstalling(false)}>
              Hide log
            </Button>
          )}
        </div>
      )}
      <FormError message={error} />
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Install ${name}?`}
        destructive={false}
        confirmLabel={chosen?.method === 'script' ? 'Download and run installer' : 'Run this command'}
        loading={install.isPending}
        description={
          <div className="space-y-2">
            {chosen?.method === 'script' ? (
              <p>
                This downloads the vendor&apos;s official installer from {scriptHost(chosen.url)} and runs it on this computer. Foundry records its SHA-256. It does not use sudo.
              </p>
            ) : (
              <p>This will run exactly the command below on the computer that runs Foundry AI OS. It does not use sudo.</p>
            )}
            <pre aria-label="Install command" className="overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[12.5px] text-fg">
              {chosen?.command}
            </pre>
          </div>
        }
        onConfirm={() => void run()}
      />
    </div>
  )
}
