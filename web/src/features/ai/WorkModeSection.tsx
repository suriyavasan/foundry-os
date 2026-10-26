import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { errorMessage, isNotDeployed, useAiSetup, useCliRuntimes } from '@/api'
import type { CliRuntimeStatus, WorkMode, WorkRuntime } from '@/api/types'
import { Button, ErrorState, LoadingState } from '@/components/ui'
import { cliBrief, runtimeIdOf, workModeProblem } from '@/lib/aiSetup'
import { CliTile } from './CliTile'

export interface WorkChoice {
  mode: WorkMode
  runtime: WorkRuntime | null
  /** Why the current choice cannot be saved, or null. */
  problem: string | null
}

/**
 * Who does the hands-on work. The coding CLIs on this computer are always listed, so it is plain what exists and what is
 * installed: "Model API only" (the default) or one CLI that tested OK. One radio group: choosing a CLI means hybrid with that
 * runtime. The choice (and why it cannot be saved, if it cannot) is reported upward on every change; the parent saves it together with the model API.
 */
export function WorkModeSection({ companyId, canManage, onChange }: { companyId: string; canManage: boolean; onChange: (c: WorkChoice) => void }) {
  const ai = useAiSetup(companyId)
  const runtimes = useCliRuntimes()
  const [mode, setMode] = useState<WorkMode | null>(null)
  const [runtime, setRuntime] = useState<WorkRuntime | null | undefined>(undefined)

  // The CLI list is the installation-level one; the one embedded in the AI setup is the fallback.
  const missing = runtimes.isError && isNotDeployed(runtimes.error)
  const clis: CliRuntimeStatus[] = runtimes.data ?? (missing ? (ai.data?.clis ?? []) : [])
  const currentMode: WorkMode = mode ?? ai.data?.work_mode ?? 'api'
  const currentRuntime: WorkRuntime | null = runtime === undefined ? (ai.data?.work_runtime ?? null) : runtime
  const problem = workModeProblem(currentMode, currentRuntime, clis)

  useEffect(() => {
    onChange({ mode: currentMode, runtime: currentMode === 'api' ? null : currentRuntime, problem })
  }, [currentMode, currentRuntime, problem, onChange])

  return (
    <section aria-label="Hands-on work" className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        {runtimes.isPending ? (
          <p className="text-[12.5px] text-fg-3">Looking for coding CLIs on this computer…</p>
        ) : clis.length > 0 ? (
          <p className="text-[12.5px] leading-snug text-fg-2">
            <span className="font-medium text-fg">On this computer:</span> {clis.map((c) => `${c.display_name} — ${cliBrief(c)}`).join(' · ')}
          </p>
        ) : (
          <span />
        )}
        <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} loading={runtimes.isFetching && !runtimes.isPending} onClick={() => void runtimes.refetch()}>
          Refresh
        </Button>
      </div>

      {runtimes.isPending ? (
        <LoadingState rows={3} label="Looking for coding CLIs" />
      ) : runtimes.isError && !missing ? (
        <ErrorState compact error={runtimes.error} title="Could not read the coding CLIs" onRetry={() => void runtimes.refetch()} retrying={runtimes.isFetching} />
      ) : (
        <>
          {clis.length === 0 && (
            <p role="status" className="rounded-md border border-line bg-raised p-3 text-[13px] text-fg-2">
              {missing ? 'This server does not report coding CLIs yet (M10).' : 'No coding CLI is known to this server.'} The model API alone works.
            </p>
          )}
          <div role="radiogroup" aria-label="Who does the hands-on work" className="space-y-3">
            <label
              className={
                'flex items-start gap-3 rounded-lg border p-4 text-[13px] ' +
                (currentMode === 'api' ? 'border-accent bg-accent-subtle ' : 'border-line-strong bg-surface ') +
                (canManage ? 'cursor-pointer' : 'cursor-default')
              }
            >
              <input type="radio" name="work-runtime" className="mt-0.5 accent-(--accent)" checked={currentMode === 'api'} disabled={!canManage} onChange={() => setMode('api')} />
              <span>
                <span className="block text-[14px] font-semibold text-fg">Model API only</span>
                <span className="mt-0.5 block text-[12.5px] leading-snug text-fg-3">Every employee works through the model API above. Simplest, strongest isolation.</span>
              </span>
            </label>
            {clis.map((c) => (
              <CliTile
                key={c.id}
                cli={c}
                companyId={companyId}
                canManage={canManage}
                selectedForWork={currentMode === 'hybrid' && currentRuntime === runtimeIdOf(c)}
                onSelectForWork={() => {
                  setMode('hybrid')
                  setRuntime(runtimeIdOf(c))
                }}
              />
            ))}
          </div>
        </>
      )}
      {!canManage && <p className="text-[12px] text-fg-3">Only an owner or admin can change how work gets done.</p>}
      {ai.isError && !isNotDeployed(ai.error) && <p className="text-[12px] text-danger">{errorMessage(ai.error)}</p>}
    </section>
  )
}
