import { useEffect } from 'react'
import { Outlet } from 'react-router'
import { ApiError, useCompany } from '@/api'
import { StreamProvider } from '@/api/stream'
import { ErrorState, LoadingState } from '@/components/ui'
import { CanvasProvider } from '@/features/canvas/CanvasContext'
import { CommandPalette } from '@/features/canvas/CommandPalette'
import { CreateHost } from '@/features/canvas/CreateHost'
import { PanelHost } from '@/features/canvas/PanelHost'
import { EmergencyStopDialog } from '@/features/guardrails/EmergencyStop'
import { TopBar } from '@/features/canvas/TopBar'
import { setLastCompanyId } from '@/lib/storage'
import { NotFound } from './NotFound'
import { useCompanyId } from './useCompanyId'

/**
 * Resolves the company in the URL and renders the one persistent shell: top bar, the routed page
 * (the canvas), the URL-driven detail drawer, create dialogs and the command palette. No sidebar.
 */
export function CompanyLayout() {
  const companyId = useCompanyId()
  const company = useCompany(companyId)

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
  if (company.isPending) {
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
      </CanvasProvider>
    </StreamProvider>
  )
}
