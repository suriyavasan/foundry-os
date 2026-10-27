import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, useSetWebhook } from '@/api'
import type { ProjectRepository, RepoPullRequestSummary } from '@/api/types'
import { Badge, Card, CardBody, CardHeader, DefinitionList, FormError, Switch, useToast } from '@/components/ui'
import { formatDateTime, formatRelative } from '@/lib/format'
import { MERGE_MODE_LABEL, PULL_STAGE_LABEL, PULL_STAGE_PATH, protectionLines, pullStageIndex, tokenExpiresSoon, tokenExpiryText } from '@/lib/repos'
import { PermissionChecklist } from './PermissionChecklist'
import { PullStageBadge, RepoStatusBadge, SyncStateBadge } from './RepoBadges'

/** The stages of the pull request on a line, the current one marked. Off-path stages (changes requested, closed) are named next to it. */
function PullTimeline({ pr }: { pr: RepoPullRequestSummary }) {
  const at = pullStageIndex(pr.stage)
  return (
    <ol aria-label="Pull request stages" className="flex flex-wrap items-center gap-1.5 text-[12px]">
      {PULL_STAGE_PATH.map((stage, i) => (
        <li key={stage} aria-current={i === at ? 'step' : undefined} className={`rounded-sm px-1.5 py-0.5 ${i === at ? 'bg-accent-subtle font-medium text-accent-fg' : i < at ? 'text-fg-2' : 'text-fg-3'}`}>
          {PULL_STAGE_LABEL[stage]}
          {i < at && <span className="sr-only"> (done)</span>}
        </li>
      ))}
    </ol>
  )
}

function PullCard({ pr }: { pr: RepoPullRequestSummary }) {
  return (
    <Card>
      <CardHeader title="Open pull request" actions={<PullStageBadge stage={pr.stage} />} />
      <CardBody className="space-y-3">
        <p className="text-[13px] text-fg">
          {pr.url ? (
            <a className="inline-flex items-center gap-1 hover:underline" href={pr.url} target="_blank" rel="noreferrer noopener">
              #{pr.number} {pr.title}
              <ExternalLink className="size-3 text-fg-3" aria-label="opens GitHub" />
            </a>
          ) : (
            <>
              #{pr.number} {pr.title}
            </>
          )}
        </p>
        <PullTimeline pr={pr} />
        {(pr.stage === 'changes_requested' || pr.stage === 'awaiting_human' || pr.stage === 'closed') && (
          <p className="text-[12.5px] text-fg-2">
            {pr.stage === 'changes_requested' ? 'The reviewer asked for changes; a fix task is running.' : pr.stage === 'awaiting_human' ? 'Everything is green. A person has to merge it on GitHub.' : 'It was closed without merging.'}
          </p>
        )}
        <DefinitionList
          items={[
            { label: 'Revision', value: pr.revision },
            { label: 'Internal review', value: pr.verdict ?? 'Not decided yet' },
            { label: 'Checks', value: pr.checks_state },
            { label: 'GitHub says', value: pr.mergeable_state ?? 'Unknown' },
            { label: 'Opened', value: formatRelative(pr.opened_at) },
          ]}
        />
      </CardBody>
    </Card>
  )
}

function WebhookCard({ companyId, repo, canEdit }: { companyId: string; repo: ProjectRepository; canEdit: boolean }) {
  const setHook = useSetWebhook(companyId, repo.id)
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  async function toggle(next: boolean) {
    setError(null)
    try {
      await setHook.mutateAsync(next)
      toast.success(next ? 'Webhook enabled' : 'Webhook disabled')
    } catch (e) {
      setError(errorMessage(e, 'Could not change the webhook'))
    }
  }
  return (
    <Card>
      <CardHeader title="Webhook" description="Optional. GitHub tells Foundry when something happens, so it syncs sooner. Polling keeps working either way." />
      <CardBody className="space-y-2">
        <div className="flex items-center gap-3">
          <Switch label="Webhook" checked={repo.webhook.enabled} loading={setHook.isPending} disabled={!canEdit || repo.status === 'disconnected'} onCheckedChange={(v) => void toggle(v)} />
          <span className="text-[13px] text-fg-2">{repo.webhook.enabled ? 'Enabled' : 'Off: polling only'}</span>
          {repo.webhook.stale && <Badge tone="warning">Address changed: switch it off and on again</Badge>}
        </div>
        {repo.webhook.url && <p className="font-mono text-[12px] break-all text-fg-3">{repo.webhook.url}</p>}
        {!canEdit && <p className="text-[12px] text-fg-3">Only owners and admins can change this.</p>}
        <FormError message={error} />
      </CardBody>
    </Card>
  )
}

/** Status, sync, token, permissions, protection and the open pull request. */
export function RepoOverview({ companyId, repo, canEdit }: { companyId: string; repo: ProjectRepository; canEdit: boolean }) {
  return (
    <div className="space-y-4">
      {repo.status_reason && (
        <p role="status" className={`rounded-md border px-3 py-2 text-[13px] ${repo.status === 'error' || repo.status === 'auth_failed' ? 'border-danger/40 bg-danger-subtle text-danger' : 'border-line bg-surface text-fg-2'}`}>
          {repo.status_reason}
        </p>
      )}
      <Card>
        <CardHeader title="Repository" actions={<RepoStatusBadge status={repo.status} />} />
        <CardBody>
          <DefinitionList
            items={[
              {
                label: 'On GitHub',
                value: repo.html_url ? (
                  <a className="inline-flex items-center gap-1 text-accent-fg hover:underline" href={repo.html_url} target="_blank" rel="noreferrer noopener">
                    {repo.full_name} <ExternalLink className="size-3" aria-hidden />
                  </a>
                ) : (
                  repo.full_name
                ),
              },
              { label: 'Project', value: repo.project_name ?? repo.project_id },
              { label: 'Visibility', value: repo.visibility },
              { label: 'Default branch', value: <span className="font-mono text-[12.5px]">{repo.default_branch}</span> },
              { label: 'Work branch', value: <span className="font-mono text-[12.5px]">{repo.work_branch}</span> },
              { label: 'Merging', value: MERGE_MODE_LABEL[repo.policy.merge_mode] },
              { label: 'Sync', value: <SyncStateBadge state={repo.sync_state} /> },
              { label: 'Last synced', value: repo.last_synced_at ? `${formatRelative(repo.last_synced_at)} (${formatDateTime(repo.last_synced_at)})` : 'Never' },
              { label: 'Next sync', value: repo.status === 'paused' || repo.status === 'disconnected' ? '—' : repo.next_sync_at ? formatRelative(repo.next_sync_at) : 'Not scheduled' },
              { label: 'Token', value: <span className={tokenExpiresSoon(repo.token_expires_at) ? 'text-warning' : undefined}>{tokenExpiryText(repo.token_expires_at)}</span> },
              ...(repo.rate_limit_remaining != null ? [{ label: 'GitHub requests left', value: repo.rate_limit_remaining }] : []),
              { label: 'Codebase onboarding', value: repo.onboarding_status.replace(/_/g, ' ') },
            ]}
          />
        </CardBody>
      </Card>

      {repo.open_pull_request ? (
        <PullCard pr={repo.open_pull_request} />
      ) : (
        <Card>
          <CardHeader title="Open pull request" />
          <CardBody>
            <p className="text-[13px] text-fg-3">No pull request is open. One is opened when the team has finished work to propose.</p>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader title="Branch protection" description="Foundry reads it and respects it; it never creates or changes protection." />
        <CardBody>
          <ul className="list-disc space-y-0.5 pl-4 text-[13px] text-fg-2">
            {protectionLines(repo.protection).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="What the token can do" description="Read from GitHub on the last check. A missing permission turns off only the feature that needs it." />
        <CardBody>
          <PermissionChecklist choice="existing" capabilities={repo.capabilities} />
        </CardBody>
      </Card>

      <WebhookCard companyId={companyId} repo={repo} canEdit={canEdit} />
    </div>
  )
}
