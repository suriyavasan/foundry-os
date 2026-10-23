import clsx from 'clsx'
import type { ComponentProps } from 'react'
import type { AgentStatus, RuntimeHealthStatus } from '@/api/types'

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'accent'

const toneBadge: Record<Tone, string> = {
  success: 'bg-success-subtle text-success',
  warning: 'bg-warning-subtle text-warning',
  danger: 'bg-danger-subtle text-danger',
  info: 'bg-info-subtle text-info',
  neutral: 'bg-neutral-subtle text-fg-2',
  accent: 'bg-accent-subtle text-accent-fg',
}
const toneDot: Record<Tone, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-neutral',
  accent: 'bg-accent',
}

export function Badge({ tone = 'neutral', className, ...rest }: ComponentProps<'span'> & { tone?: Tone }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-[11.5px] leading-4 font-medium whitespace-nowrap',
        toneBadge[tone],
        className,
      )}
      {...rest}
    />
  )
}

export function Dot({ tone = 'neutral', className }: { tone?: Tone; className?: string }) {
  return <span aria-hidden className={clsx('inline-block size-1.5 shrink-0 rounded-full', toneDot[tone], className)} />
}

/** Subtle outlined chip for tags like capabilities or permissions. */
export function Chip({ className, ...rest }: ComponentProps<'span'>) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-sm border border-line bg-raised px-1.5 py-0.5 font-mono text-[11px] text-fg-2',
        className,
      )}
      {...rest}
    />
  )
}

export const agentStatusTone: Record<AgentStatus, Tone> = {
  proposed: 'info',
  active: 'success',
  paused: 'warning',
  retiring: 'warning',
  retired: 'neutral',
  rejected: 'danger',
}

export const agentStatusLabel: Record<AgentStatus, string> = {
  proposed: 'Proposed',
  active: 'Active',
  paused: 'Paused',
  retiring: 'Retiring',
  retired: 'Retired',
  rejected: 'Rejected',
}

export function StatusDot({ status, className }: { status: AgentStatus; className?: string }) {
  return <Dot tone={agentStatusTone[status]} className={className} />
}

export function StatusBadge({ status, className }: { status: AgentStatus; className?: string }) {
  return (
    <Badge tone={agentStatusTone[status]} className={className}>
      <Dot tone={agentStatusTone[status]} />
      {agentStatusLabel[status]}
    </Badge>
  )
}

const healthTone: Record<RuntimeHealthStatus, Tone> = {
  ok: 'success',
  degraded: 'warning',
  unavailable: 'danger',
  not_configured: 'neutral',
}
const healthLabel: Record<RuntimeHealthStatus, string> = {
  ok: 'Healthy',
  degraded: 'Degraded',
  unavailable: 'Unavailable',
  not_configured: 'Not configured',
}

export function HealthBadge({ status }: { status: RuntimeHealthStatus }) {
  const tone = healthTone[status] ?? 'neutral'
  return (
    <Badge tone={tone}>
      <Dot tone={tone} />
      {healthLabel[status] ?? status}
    </Badge>
  )
}
