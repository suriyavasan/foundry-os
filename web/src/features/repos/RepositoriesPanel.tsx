import { ExternalLink, Plus } from 'lucide-react'
import { useState } from 'react'
import { isNotDeployed, useRepoAvailability, useRepositories } from '@/api'
import { Button, Card, EmptyState, ErrorState, LoadingState, NotAvailable, Table, TBody, TD, TH, THead, TR } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { formatRelative } from '@/lib/format'
import { tokenExpiresSoon, tokenExpiryText } from '@/lib/repos'
import { ConnectRepoDialog } from './ConnectRepoDialog'
import { PullStageBadge, RepoStatusBadge, SyncStateBadge } from './RepoBadges'

/** `?panel=repos`: every repository of the company, with its status, sync state and open pull request. */
export function RepositoriesPanel() {
  const { companyId, openPanel } = useCanvas()
  const availability = useRepoAvailability(companyId)
  const repos = useRepositories(companyId)
  const canEdit = canEditPolicy(useCompanyRole(companyId))
  const [connecting, setConnecting] = useState(false)

  const notBuilt = isNotDeployed(availability.error) || isNotDeployed(repos.error)
  const unavailable = availability.data?.available === false
  const usable = !notBuilt && !unavailable
  const rows = repos.data ?? []

  return (
    <PanelFrame
      title="Repositories"
      subtitle="The GitHub repositories behind your projects"
      actions={
        usable && canEdit ? (
          <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setConnecting(true)}>
            Connect a repository
          </Button>
        ) : undefined
      }
    >
      {availability.isPending || (usable && repos.isPending) ? (
        <Card>
          <LoadingState rows={4} label="Loading repositories" />
        </Card>
      ) : notBuilt ? (
        <Card>
          <NotAvailable milestone="M14" title="GitHub projects are not built into this server" description="Projects keep their local repositories." />
        </Card>
      ) : unavailable ? (
        <Card>
          <NotAvailable milestone={availability.data?.milestone ?? 'M14'} title="Repositories are not available on this machine" description={availability.data?.reason ?? 'Git is missing here.'} />
        </Card>
      ) : repos.isError ? (
        <Card>
          <ErrorState error={repos.error} onRetry={() => void repos.refetch()} retrying={repos.isFetching} />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            title="No repositories yet"
            description="Connect a GitHub repository to a project and your team works in it: branches, pull requests, CI and issues."
            action={
              canEdit ? (
                <Button variant="primary" onClick={() => setConnecting(true)}>
                  Connect a repository
                </Button>
              ) : (
                <p className="text-[12.5px] text-fg-3">Ask an owner or admin to connect one.</p>
              )
            }
          />
        </Card>
      ) : (
        <Card>
          <Table aria-label="Repositories">
            <THead>
              <TR>
                <TH>Project</TH>
                <TH>Repository</TH>
                <TH>Status</TH>
                <TH>Sync</TH>
                <TH>Pull request</TH>
                <TH>Last sync</TH>
                <TH>Token</TH>
              </TR>
            </THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.id} interactive>
                  <TD className="max-w-48 truncate">{r.project_name ?? r.project_id}</TD>
                  <TD>
                    <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => openPanel({ kind: 'repo', id: r.id })}>
                      {r.full_name}
                    </button>
                    {r.html_url && (
                      <a className="ml-1.5 inline-flex align-middle text-fg-3 hover:text-fg" href={r.html_url} target="_blank" rel="noreferrer noopener" aria-label={`Open ${r.full_name} on GitHub`}>
                        <ExternalLink className="size-3" aria-hidden />
                      </a>
                    )}
                  </TD>
                  <TD>
                    <RepoStatusBadge status={r.status} />
                  </TD>
                  <TD>
                    <SyncStateBadge state={r.sync_state} />
                  </TD>
                  <TD>{r.open_pull_request ? <PullStageBadge stage={r.open_pull_request.stage} /> : <span className="text-fg-3">None</span>}</TD>
                  <TD className="whitespace-nowrap text-fg-2">{r.last_synced_at ? formatRelative(r.last_synced_at) : 'Never'}</TD>
                  <TD className={`whitespace-nowrap ${tokenExpiresSoon(r.token_expires_at) ? 'text-warning' : 'text-fg-2'}`}>{tokenExpiryText(r.token_expires_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      <ConnectRepoDialog companyId={companyId} open={connecting} onOpenChange={setConnecting} onConnected={(repo) => openPanel({ kind: 'repo', id: repo.id })} />
    </PanelFrame>
  )
}
