import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'
import { LoadingState } from '@/components/ui'
import { AuthGate } from '@/features/auth/AuthGate'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { LegacyRedirect } from '@/features/shell/LegacyRedirect'
import { NotFoundPage } from '@/features/shell/NotFound'
import { RootRedirect } from '@/features/shell/RootRedirect'

// Rarely visited routes are separate chunks (the canvas is the page almost everyone lands on).
const InvitePage = lazy(() => import('@/features/auth/InvitePage').then((m) => ({ default: m.InvitePage })))
const OnboardingPage = lazy(() => import('@/features/onboarding/OnboardingPage').then((m) => ({ default: m.OnboardingPage })))

function RouteLoading() {
  return (
    <div className="mx-auto max-w-md pt-24">
      <LoadingState label="Loading" rows={3} />
    </div>
  )
}

function AppRoutes() {
  return (
    <AuthGate>
      <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/c/:companyId" element={<CompanyLayout />}>
          <Route index element={<CanvasPage />} />
          <Route path="*" element={<LegacyRedirect />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AuthGate>
  )
}

export function App() {
  return (
    <Suspense fallback={<RouteLoading />}>
    <Routes>
      {/* Outside the auth gate: the person opening an invitation is, by definition, not a member yet. */}
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route path="*" element={<AppRoutes />} />
    </Routes>
    </Suspense>
  )
}
