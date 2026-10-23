import { Route, Routes } from 'react-router'
import { AuthGate } from '@/features/auth/AuthGate'
import { InvitePage } from '@/features/auth/InvitePage'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { OnboardingPage } from '@/features/onboarding/OnboardingPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { LegacyRedirect } from '@/features/shell/LegacyRedirect'
import { NotFoundPage } from '@/features/shell/NotFound'
import { RootRedirect } from '@/features/shell/RootRedirect'

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
    <Routes>
      {/* Outside the auth gate: the person opening an invitation is, by definition, not a member yet. */}
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route path="*" element={<AppRoutes />} />
    </Routes>
  )
}
