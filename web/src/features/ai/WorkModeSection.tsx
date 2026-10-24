import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { errorMessage, isNotDeployed, useAiSetup, useCliRuntimes } from '@/api'
import type { CliRuntimeStatus, WorkMode, WorkRuntime } from '@/api/types'
import { Button, ErrorState, LoadingState } from '@/components/ui'
import { GlowCard } from '@/features/onboarding/fx/GlowCard'
import { WORK_MODE_BLURB, WORK_MODE_LABEL, cliReady, runtimeIdOf, workModeProblem } from '@/lib/aiSetup'
import { CliTile } from './CliTile'

export interface WorkChoice {
  mode: WorkMode
  runtime: WorkRuntime | null
  /** Why the current choice cannot be saved, or null. */
  problem: string | null
}

/**
 * How should work get done (spec §6 step 5): "API only" or "API + coding CLIs". With CLIs, the tiles show what is installed
 * and tested; hybrid needs one that tested OK and was picked for the work. The choice is reported upward on every change so
 * the parent can enable its Continue / Save.
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

  function pickMode(m: WorkMode) {
    setMode(m)
    if (m === 'hybrid' && (currentRuntime === null || currentRuntime === 'company_agent')) {
      const ready = clis.filter(cliReady)
      if (ready.length === 1) setRuntime(runtimeIdOf(ready[0]!))
    }
  }

  return (
    <section aria-label="How work gets done" className="space-y-4">
      <div role="radiogroup" aria-label="Work mode" className="grid gap-3 sm:grid-cols-2">
        {(['api', 'hybrid'] as const).map((m) => (
          <GlowCard key={m} selected={currentMode === m} onClick={() => pickMode(m)} disabled={!canManage}>
            <span className="block text-[14px] font-semibold text-fg">{WORK_MODE_LABEL[m]}</span>
            <span className="mt-1 block text-[12.5px] leading-snug text-fg-3">{WORK_MODE_BLURB[m]}</span>
          </GlowCard>
        ))}
      </div>
      {!canManage && <p className="text-[12px] text-fg-3">Only an owner or admin can change how work gets done.</p>}

      {currentMode === 'hybrid' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[12px] font-medium tracking-wide text-fg-3 uppercase">Coding CLIs on this computer</p>
            <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} loading={runtimes.isFetching && !runtimes.isPending} onClick={() => void runtimes.refetch()}>
              Refresh
            </Button>
          </div>
          {runtimes.isPending ? (
            <LoadingState rows={3} label="Looking for coding CLIs" />
          ) : runtimes.isError && !missing ? (
            <ErrorState compact error={runtimes.error} title="Could not read the coding CLIs" onRetry={() => void runtimes.refetch()} retrying={runtimes.isFetching} />
          ) : clis.length === 0 ? (
            <p role="status" className="rounded-md border border-line bg-raised p-3 text-[13px] text-fg-2">
              {missing ? 'This server does not report coding CLIs yet (M10).' : 'No coding CLI is known to this server.'} API only still works.
            </p>
          ) : (
            <div className="grid gap-3 lg:grid-cols-1">
              {clis.map((c) => (
                <CliTile key={c.id} cli={c} companyId={companyId} canManage={canManage} selectedForWork={currentRuntime === runtimeIdOf(c)} onSelectForWork={() => setRuntime(runtimeIdOf(c))} />
              ))}
            </div>
          )}
          {problem && (
            <p role="status" className="text-[12.5px] text-warning">
              {problem}
            </p>
          )}
        </div>
      )}
      {ai.isError && !isNotDeployed(ai.error) && <p className="text-[12px] text-danger">{errorMessage(ai.error)}</p>}
    </section>
  )
}
