import { isNotDeployed, useRepository } from '@/api'
import { EmptyState, ErrorState, LoadingState, NotAvailable, Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { RepoActivity } from './RepoActivity'
import { RepoStatusBadge } from './RepoBadges'
import { RepoDangerZone } from './RepoDangerZone'
import { RepoOverview } from './RepoOverview'
import { RepoPolicyForm } from './RepoPolicyForm'

/** `?panel=repo:<id>`: one repository binding. */
export function RepoPanel({ repositoryId }: { repositoryId: string }) {
  const { companyId, openPanel } = useCanvas()
  const repo = useRepository(companyId, repositoryId)
  const role = useCompanyRole(companyId)
  const canEdit = canEditPolicy(role)
  const r = repo.data

  return (
    <PanelFrame title={r ? r.full_name : 'Repository'} subtitle={r ? r.project_name : undefined} leading={r ? <RepoStatusBadge status={r.status} /> : undefined} onBack={() => openPanel({ kind: 'repos' })}>
      {repo.isPending ? (
        <LoadingState rows={6} label="Loading the repository" />
      ) : isNotDeployed(repo.error) && !r ? (
        <EmptyState title="Repository not found" description="It may have been removed, or you are not a member of this company." />
      ) : repo.isError && !r ? (
        <ErrorState error={repo.error} onRetry={() => void repo.refetch()} retrying={repo.isFetching} />
      ) : r ? (
        <Tabs defaultValue="overview">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            <TabsTrigger value="activity">Activity</TabsTrigger>
            <TabsTrigger value="danger">Danger zone</TabsTrigger>
          </TabsList>
          <TabsContent value="overview">
            <RepoOverview companyId={companyId} repo={r} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="settings">
            {/* Re-keyed by what was saved, not by the sync clock, so a background sync does not discard unsaved edits. */}
            <RepoPolicyForm key={`${r.default_branch}|${r.is_company_default}|${JSON.stringify(r.policy)}`} companyId={companyId} repo={r} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="activity">
            <RepoActivity companyId={companyId} repositoryId={r.id} />
          </TabsContent>
          <TabsContent value="danger">
            <RepoDangerZone companyId={companyId} repo={r} canEdit={canEdit} />
          </TabsContent>
        </Tabs>
      ) : (
        <NotAvailable milestone="M14" />
      )}
    </PanelFrame>
  )
}
