import type { ReactNode } from 'react'
import { isUnauthenticated, useMe } from '@/api'
import { LoadingState } from '@/components/ui'
import { LoginGate } from './LoginGate'

/**
 * Holds the whole app behind a successful `/api/me`. A 401 shows the login screen and nothing else is
 * mounted, so no widget fires requests that would each fail with 401. Other errors (backend down)
 * fall through to the pages' own error states.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const me = useMe()
  if (isUnauthenticated(me.error)) return <LoginGate onRetry={() => void me.refetch()} retrying={me.isFetching} />
  if (me.isPending) {
    return (
      <div className="mx-auto max-w-md pt-24">
        <LoadingState label="Signing in" rows={3} />
      </div>
    )
  }
  return <>{children}</>
}
