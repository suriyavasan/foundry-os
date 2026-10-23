import { Route, Routes } from 'react-router'
import { CanvasPage } from '@/features/canvas/CanvasPage'
import { OnboardingPage } from '@/features/onboarding/OnboardingPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { LegacyRedirect } from '@/features/shell/LegacyRedirect'
import { NotFoundPage } from '@/features/shell/NotFound'
import { RootRedirect } from '@/features/shell/RootRedirect'

export function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/onboarding" element={<OnboardingPage />} />
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<CanvasPage />} />
        <Route path="*" element={<LegacyRedirect />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
