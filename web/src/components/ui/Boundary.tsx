import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { isNotDeployed } from '@/api/work'
import { EmptyState, ErrorState, LoadingState, NotAvailable } from './States'

/**
 * Standard loading / error / not-available / empty handling for a query.
 * A 404/405 from a collection endpoint means that subsystem is not deployed: say so, show nothing else.
 */
export function Boundary<T>({
  query,
  children,
  isEmpty,
  empty,
  rows = 4,
  milestone = 'M2',
  unavailableTitle,
  unavailableText,
  compact = true,
}: {
  query: Pick<UseQueryResult<T>, 'data' | 'error' | 'isPending' | 'isError' | 'isFetching' | 'refetch'>
  children: (data: T) => ReactNode
  isEmpty?: (data: T) => boolean
  empty?: { title: string; description?: ReactNode; action?: ReactNode }
  rows?: number
  milestone?: string
  unavailableTitle?: string
  unavailableText?: ReactNode
  compact?: boolean
}) {
  if (query.isPending) return <LoadingState rows={rows} label="Loading" />
  if (query.isError) {
    if (isNotDeployed(query.error)) {
      return compact ? (
        <NotAvailable compact className="p-4" milestone={milestone} description={unavailableText} />
      ) : (
        <NotAvailable milestone={milestone} title={unavailableTitle} description={unavailableText} />
      )
    }
    return <ErrorState compact={compact} error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />
  }
  const data = query.data as T
  if (isEmpty?.(data) && empty) return <EmptyState compact={compact} {...empty} />
  return <>{children(data)}</>
}
