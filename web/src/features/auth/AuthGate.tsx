import { lazy, Suspense, useState, type ReactNode } from 'react'
import { isUnauthenticated, useAuthSession, useMe } from '@/api'
import { authStatusOf } from '@/lib/auth'
import { ErrorState, LoadingState } from '@/components/ui'
import { LoginGate } from './LoginGate'
import { LoginPage } from './LoginPage'

// The onboarding (animations included) is a separate chunk: the canvas never pays for it.
const OnboardingFlow = lazy(() => import('@/features/onboarding/OnboardingFlow').then((m) => ({ default: m.OnboardingFlow })))

/**
 * Holds the whole app behind a session. `GET /auth/session` decides:
 *   setup_required  -> the first-run onboarding (six full-screen steps) (stays up until the person finishes it, even though step 1 signs them in)
 *   unauthenticated -> the login page
 *   authenticated   -> the app
 * A backend that predates M7 answers 404 there; the gate then behaves exactly as it did under M3b-sec
 * (`LegacyGate`: a 401 from /me shows the "open the login link from your terminal" screen).
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const session = useAuthSession()
  const status = authStatusOf({ isPending: session.isPending, data: session.data, error: session.error })
  const [setup, setSetup] = useState<{ tokenRequired: boolean } | null>(null)

  // Entering setup is remembered, so signing in as the new owner in step 1 does not unmount the wizard mid-way.
  if (status.kind === 'setup_required' && !setup) setSetup({ tokenRequired: status.setupTokenRequired })
  if (setup) {
    return (
      <Suspense fallback={<FullPage><LoadingState label="Starting setup" rows={3} /></FullPage>}>
        <OnboardingFlow mode="setup" setupTokenRequired={setup.tokenRequired} onDone={() => setSetup(null)} />
      </Suspense>
    )
  }

  switch (status.kind) {
    case 'loading':
      return <FullPage><LoadingState label="Starting Company OS" rows={3} /></FullPage>
    case 'error':
      return (
        <FullPage>
          <ErrorState error={status.error} title="Could not reach Company OS" onRetry={() => void session.refetch()} retrying={session.isFetching} />
        </FullPage>
      )
    case 'legacy':
      return <LegacyGate>{children}</LegacyGate>
    case 'unauthenticated':
      return <LoginPage />
    default:
      return <>{children}</>
  }
}

function FullPage({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-md pt-24">{children}</div>
}

/**
 * M3b-sec: the local single-owner mode. The app is held behind a successful `/api/me`; a 401 shows the login-link
 * screen and nothing else is mounted, so no widget fires requests that would each fail with 401. Other errors
 * (backend down) fall through to the pages' own error states.
 */
function LegacyGate({ children }: { children: ReactNode }) {
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
