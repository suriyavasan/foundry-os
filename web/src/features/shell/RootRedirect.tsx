import { Navigate } from 'react-router'
import { useCompanies } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { getLastCompanyId } from '@/lib/storage'

/** `/` -> onboarding when there are no companies; otherwise the last-used (or first) company. */
export function RootRedirect() {
  const companies = useCompanies()

  if (companies.isPending) {
    return (
      <div className="mx-auto max-w-md pt-24">
        <LoadingState label="Loading companies" rows={3} />
      </div>
    )
  }
  if (companies.isError) {
    return (
      <div className="mx-auto max-w-md pt-24">
        <ErrorState error={companies.error} onRetry={() => companies.refetch()} retrying={companies.isFetching} />
      </div>
    )
  }
  const list = companies.data
  if (list.length === 0) return <Navigate to="/onboarding" replace />
  const last = getLastCompanyId()
  const target = list.find((c) => c.id === last) ?? list[0]!
  return <Navigate to={`/c/${target.id}`} replace />
}
