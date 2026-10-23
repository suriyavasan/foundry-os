import { Navigate, Route, Routes } from 'react-router'
import { ActivityPage } from '@/features/activity/ActivityPage'
import { CommandCenterPage } from '@/features/command-center/CommandCenterPage'
import { IntegrationsPage } from '@/features/integrations/IntegrationsPage'
import { OnboardingPage } from '@/features/onboarding/OnboardingPage'
import { OrganizationPage } from '@/features/organization/OrganizationPage'
import { AgentProfilePage } from '@/features/organization/profile/AgentProfilePage'
import {
  ApprovalsPage,
  ArtifactsPage,
  FinancePage,
  ProjectsPage,
  TasksPage,
} from '@/features/placeholders/PlaceholderPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { CompanyLayout } from '@/features/shell/CompanyLayout'
import { NotFound, NotFoundPage } from '@/features/shell/NotFound'
import { RootRedirect } from '@/features/shell/RootRedirect'

export function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/onboarding" element={<OnboardingPage />} />
      <Route path="/c/:companyId" element={<CompanyLayout />}>
        <Route index element={<Navigate to="command-center" replace />} />
        <Route path="command-center" element={<CommandCenterPage />} />
        <Route path="organization" element={<OrganizationPage />} />
        <Route path="organization/agents/:agentId" element={<AgentProfilePage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="artifacts" element={<ArtifactsPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="finance" element={<FinancePage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFound backLabel="Back to Command Center" backTo="command-center" />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
