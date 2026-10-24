import { Download, X } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useSetUpdateCheck, useUpdateStatus } from '@/api'
import { useToast } from '@/components/ui'
import { getDismissedUpdate, setDismissedUpdate } from '@/lib/storage'

/**
 * A slim, dismissible strip under the top bar when a newer Foundry AI OS release is known. Owner only: the endpoint
 * answers 403 to anyone else (and 404 on an older backend), and then nothing renders. Dismissing hides this version;
 * a newer one shows it again. "Turn off update checks" stops the once-a-day request to GitHub.
 */
export function UpdateBanner() {
  const q = useUpdateStatus()
  const setCheck = useSetUpdateCheck()
  const toast = useToast()
  const [dismissed, setDismissed] = useState<string | null>(() => getDismissedUpdate())
  const info = q.data
  if (!info || !info.update_available || !info.latest_version || dismissed === info.latest_version) return null
  const latest = info.latest_version

  function dismiss() {
    setDismissedUpdate(latest)
    setDismissed(latest)
  }
  async function turnOff() {
    try {
      await setCheck.mutateAsync(false)
      toast.success('Update checks are off. You can turn them back on with `foundryos update --auto-check`.')
    } catch (e) {
      toast.error(errorMessage(e))
    }
  }

  return (
    <div role="status" aria-label="Update available" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-raised px-3 py-1.5 text-[12.5px] text-fg-2">
      <Download className="size-4 shrink-0 text-accent-fg" aria-hidden />
      <span className="min-w-0 flex-1">
        <strong className="font-semibold text-fg">Foundry AI OS {latest} is available.</strong> You have {info.current_version}. To update, run{' '}
        <code className="rounded-sm bg-surface px-1 py-0.5 font-mono text-[12px] text-fg">{info.update_command}</code>
      </span>
      {info.release_url && (
        <a href={info.release_url} target="_blank" rel="noreferrer noopener" className="rounded-sm px-1.5 py-0.5 font-medium text-fg underline underline-offset-2 hover:bg-hover">
          What’s new
        </a>
      )}
      <button type="button" onClick={() => void turnOff()} disabled={setCheck.isPending} className="rounded-sm px-1.5 py-0.5 text-fg-3 underline underline-offset-2 hover:bg-hover hover:text-fg disabled:opacity-50">
        Turn off update checks
      </button>
      <button type="button" onClick={dismiss} aria-label="Dismiss update notice" className="inline-flex size-6 items-center justify-center rounded-sm text-fg-3 hover:bg-hover hover:text-fg">
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  )
}
