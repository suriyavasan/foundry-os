import type { PullStage, RepoStatus, RepoSyncState } from '@/api/types'
import { Badge, Dot } from '@/components/ui'
import { PULL_STAGE_LABEL, PULL_STAGE_TONE, REPO_STATUS_LABEL, REPO_STATUS_TONE, SYNC_STATE_LABEL, SYNC_STATE_TONE } from '@/lib/repos'

export function RepoStatusBadge({ status }: { status: RepoStatus }) {
  const tone = REPO_STATUS_TONE[status]
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {REPO_STATUS_LABEL[status]}
    </Badge>
  )
}

export function SyncStateBadge({ state }: { state: RepoSyncState }) {
  const tone = SYNC_STATE_TONE[state]
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {SYNC_STATE_LABEL[state]}
    </Badge>
  )
}

export function PullStageBadge({ stage }: { stage: PullStage }) {
  const tone = PULL_STAGE_TONE[stage]
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {PULL_STAGE_LABEL[stage]}
    </Badge>
  )
}
