import { useEffect } from 'react'
import { Outlet } from 'react-router'
import { ApiError, useCompany } from '@/api'
import { AppShell } from '@/components/layout/AppShell'
import { ErrorState, LoadingState } from '@/components/ui'
import { setLastCompanyId } from '@/lib/storage'
import { NotFound } from './NotFound'
import { useCompanyId } from './useCompanyId'

/** Resolves the company in the URL, remembers it, and renders the persistent shell. */
export function CompanyLayout() {
  const companyId = useCompanyId()
  const company = useCompany(companyId)

  useEffect(() => {
    if (company.data) setLastCompanyId(company.data.id)
  }, [company.data])

  if (company.isError && company.error instanceof ApiError && company.error.status === 404) {
    return (
      <div className="min-h-screen bg-canvas">
        <NotFound
          title="Company not found"
          description="This company does not exist, or you are not a member of it."
          backLabel="Choose another company"
        />
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
    <AppShell companyId={companyId} companyName={company.data.name}>
      <Outlet />
    </AppShell>
  )
}
