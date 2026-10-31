import type {
  ApprovalStatus,
  ArtifactKind,
  ArtifactStatus,
  GoalStatus,
  Priority,
  ProjectStatus,
  RunStatus,
  TaskStatus,
} from '@/api/types'
import { Badge, Dot, type Tone } from '@/components/ui'
import {
  PRIORITY_LABEL,
  PRIORITY_TONE,
  RUN_STATUS_LABEL,
  RUN_STATUS_TONE,
  TASK_STATUS_LABEL,
  TASK_STATUS_TONE,
} from '@/lib/taskStatus'
import { EXPIRED_LABEL } from '@/lib/approvalExpiry'
import { titleCase } from '@/lib/format'

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const tone = TASK_STATUS_TONE[status] ?? 'neutral'
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {TASK_STATUS_LABEL[status] ?? titleCase(status)}
    </Badge>
  )
}

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <Badge tone={PRIORITY_TONE[priority] ?? 'neutral'}>{PRIORITY_LABEL[priority] ?? priority}</Badge>
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const tone = RUN_STATUS_TONE[status] ?? 'neutral'
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {RUN_STATUS_LABEL[status] ?? titleCase(status)}
    </Badge>
  )
}

const APPROVAL_TONE: Record<ApprovalStatus, Tone> = {
  pending: 'warning',
  approved: 'success',
  rejected: 'danger',
  changes_requested: 'info',
  expired: 'neutral',
}
export function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) {
  return <Badge tone={APPROVAL_TONE[status] ?? 'neutral'}>{status === 'expired' ? EXPIRED_LABEL : titleCase(status)}</Badge>
}

const ARTIFACT_TONE: Record<ArtifactStatus, Tone> = { draft: 'neutral', submitted: 'info', accepted: 'success', rejected: 'danger' }
export function ArtifactStatusBadge({ status }: { status: ArtifactStatus }) {
  return <Badge tone={ARTIFACT_TONE[status] ?? 'neutral'}>{titleCase(status)}</Badge>
}

const GOAL_TONE: Record<GoalStatus, Tone> = { proposed: 'info', active: 'success', achieved: 'accent', abandoned: 'neutral' }
export function GoalStatusBadge({ status }: { status: GoalStatus }) {
  return <Badge tone={GOAL_TONE[status] ?? 'neutral'}>{titleCase(status)}</Badge>
}

const PROJECT_TONE: Record<ProjectStatus, Tone> = { planned: 'neutral', active: 'success', paused: 'warning', completed: 'accent', cancelled: 'neutral' }
export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  return <Badge tone={PROJECT_TONE[status] ?? 'neutral'}>{titleCase(status)}</Badge>
}

export const ARTIFACT_KINDS: ArtifactKind[] = ['document', 'code', 'design', 'report', 'data', 'link', 'other']
