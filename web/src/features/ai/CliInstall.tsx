import { Download } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useCliInstallLog, useInstallCli } from '@/api'
import { Button, ConfirmDialog, FormError } from '@/components/ui'

/**
 * Install a coding CLI after the person confirms the exact command (nothing runs before that), then tail the install log.
 * Without an install command the person is told to install it themselves.
 */
export function CliInstall({ companyId, cliId, name, command, canEdit }: { companyId: string; cliId: string; name: string; command: string | null; canEdit: boolean }) {
  const install = useInstallCli(companyId)
  const [confirm, setConfirm] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const log = useCliInstallLog(installing ? cliId : null, installing)
  const logDone = !!log.data && (log.data.state === 'succeeded' || log.data.state === 'failed')

  async function run() {
    setError(null)
    try {
      await install.mutateAsync(cliId)
      setConfirm(false)
      setInstalling(true)
    } catch (e) {
      setConfirm(false)
      setError(errorMessage(e))
    }
  }

  return (
    <div className="space-y-2">
      {!installing &&
        (command ? (
          <>
            <p className="text-[12.5px] text-fg-2">{name} is not installed on this computer. Foundry can install it after you confirm the command.</p>
            {canEdit ? (
              <Button icon={<Download className="size-3.5" />} onClick={() => setConfirm(true)} disabled={install.isPending}>
                Install {name}
              </Button>
            ) : (
              <p className="text-[12px] text-fg-3">Only an owner or admin can install tools.</p>
            )}
          </>
        ) : (
          <p className="text-[12.5px] text-fg-2">{name} is not installed, and Foundry cannot install it here. Install it yourself from its official instructions, then check again.</p>
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
        confirmLabel="Run this command"
        loading={install.isPending}
        description={
          <div className="space-y-2">
            <p>This will run exactly the command below on the computer that runs Foundry AI OS. It does not use sudo.</p>
            <pre aria-label="Install command" className="overflow-x-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-[12.5px] text-fg">
              {command}
            </pre>
          </div>
        }
        onConfirm={() => void run()}
      />
    </div>
  )
}
