import { lazy, Suspense, useEffect, useState } from 'react'
import { Outlet } from 'react-router'
import { ApiError, useAiSetup, useCompany } from '@/api'
import { StreamProvider } from '@/api/stream'
import { ErrorState, LoadingState } from '@/components/ui'
import { CanvasProvider } from '@/features/canvas/CanvasContext'
import { CommandPalette } from '@/features/canvas/CommandPalette'
import { CreateHost } from '@/features/canvas/CreateHost'
import { PanelHost } from '@/features/canvas/PanelHost'
import { ConnectAccountHost } from '@/features/integrations/ConnectAccountHost'
import { EmergencyStopDialog } from '@/features/guardrails/EmergencyStop'
import { TopBar } from '@/features/canvas/TopBar'
import { aiGateState } from '@/lib/aiSetup'
import { setLastCompanyId } from '@/lib/storage'
import { NotFound } from './NotFound'
import { useCompanyId } from './useCompanyId'

// The AI gate is the onboarding's AI steps; its chunk loads only when a company really is not ready.
const AiGate = lazy(() => import('@/features/onboarding/AiGate').then((m) => ({ default: m.AiGate })))

/**
 * Resolves the company in the URL and renders the one persistent shell: top bar, the routed page
 * (the canvas), the URL-driven detail drawer, create dialogs and the command palette. No sidebar.
 */
export function CompanyLayout() {
  const companyId = useCompanyId()
  const company = useCompany(companyId)
  // Asked in parallel with the company. Only an explicit `ready: false` blocks (a 404 from an older backend does not).
  const aiSetup = useAiSetup(companyId)
  // "Loading" means the FIRST answer for this company is outstanding. A failed query that is looked at again goes back to
  // `pending`; that must not unmount the canvas (it would remount, refetch, and loop), so the first answer is latched.
  const [aiSettledFor, setAiSettledFor] = useState<string | null>(null)
  if (!aiSetup.isPending && aiSettledFor !== companyId) setAiSettledFor(companyId)
  const gate = aiGateState({ isPending: aiSettledFor !== companyId, data: aiSetup.data })
  // Once the gate showed, it stays until its own steps are finished (saving the model flips `ready` before the work-mode step).
  const [gating, setGating] = useState<string | null>(null)
  if (gate === 'blocked' && gating !== companyId) setGating(companyId)

  useEffect(() => {
    if (company.data) setLastCompanyId(company.data.id)
  }, [company.data])

  if (company.isError && company.error instanceof ApiError && company.error.status === 404) {
    return (
      <div className="min-h-screen bg-canvas">
        <NotFound title="Company not found" description="This company does not exist, or you are not a member of it." backLabel="Choose another company" />
      </div>
    )
  }
  if (company.isPending || (gate === 'loading' && !company.isError)) {
    return (
      <div className="mx-auto max-w-md pt-24">
        <LoadingState label="Loading company" rows={4} />
      </div>
    )
  }
  if (company.isError) {
    return (
      <div className="mx-auto max-w-md pt-24">
        <ErrorState error={company.error} onRetry={() => company.refetch()} retrying={company.isFetching} />
      </div>
    )
  }

  if (gating === companyId) {
    return (
      <StreamProvider key={companyId} companyId={companyId}>
        <Suspense
          fallback={
            <div className="mx-auto max-w-md pt-24">
              <LoadingState label="Opening AI setup" rows={3} />
            </div>
          }
        >
          <AiGate companyId={companyId} onDone={() => setGating(null)} />
        </Suspense>
      </StreamProvider>
    )
  }

  return (
    <StreamProvider key={companyId} companyId={companyId}>
      <CanvasProvider companyId={companyId}>
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[80] focus:rounded-md focus:bg-surface focus:px-3 focus:py-1.5">
          Skip to canvas
        </a>
        <TopBar company={company.data} />
        <Outlet />
        <PanelHost />
        <CreateHost />
        <CommandPalette company={company.data} />
        <EmergencyStopDialog />
        <ConnectAccountHost />
      </CanvasProvider>
    </StreamProvider>
  )
}
