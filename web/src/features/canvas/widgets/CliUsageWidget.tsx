import clsx from 'clsx'
import { useState } from 'react'
import { useCliRuntimes, useCliUsage, type CliUsageRow } from '@/api'
import type { CliRuntimeStatus } from '@/api/types'
import { Badge, Boundary, Widget } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import { formatDuration } from '@/lib/dates'
import { formatTokens } from '@/lib/format'
import { formatCost } from '@/lib/money'
import { useCanvas } from '../CanvasContext'
import { useAgentMap, useCompanyTimezone } from '../hooks'
import { useCompanyDates } from '../spend'

type Period = 'today' | 'month'

const AUTH_LABEL: Record<string, string> = { api_key: 'API key', own_login: 'Own login (subscription)' }

/** Cost cell: the estimate first (labelled), then what was actually charged; subscription runs are never a dollar amount. */
function CostCell({ row }: { row: CliUsageRow }) {
  const parts: string[] = []
  if (row.estimated_micros > 0) parts.push(formatCost(row.estimated_micros, 'estimated'))
  if (row.verified_micros > 0) parts.push(`${formatCost(row.verified_micros, 'verified')} charged`)
  const unpriced = row.runs - row.subscription_runs > 0 && parts.length === 0
  return (
    <div className="space-y-0.5">
      {parts.length > 0 && <p className="text-fg">{parts.join(' · ')}</p>}
      {unpriced && <p className="text-fg-3">{formatCost(null, 'unknown')}</p>}
      {row.subscription_runs > 0 && (
        <p className="text-fg-3">
          {row.subscription_runs} {row.subscription_runs === 1 ? 'run' : 'runs'}: subscription — not billed here
        </p>
      )}
      {row.runs === 0 && <p className="text-fg-3">—</p>}
    </div>
  )
}

function StatusBadges({ status }: { status: CliRuntimeStatus | undefined }) {
  if (!status) return <span className="text-fg-3">Status unknown</span>
  const mode = status.auth.mode
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Badge tone={status.installed ? 'success' : 'neutral'}>{status.installed ? 'Installed' : 'Not installed'}</Badge>
      <Badge tone={status.auth.configured ? 'success' : 'warning'}>{status.auth.configured ? 'Connected' : 'Not connected'}</Badge>
      {mode && <Badge tone="info">{AUTH_LABEL[mode] ?? mode}</Badge>}
    </div>
  )
}

/** How much each coding CLI is used (Claude Code, Codex, …). The CLI list comes from the backend catalog, never a fixed list. */
export function CliUsageWidget({ className }: { className?: string }) {
  const { companyId } = useCanvas()
  const tz = useCompanyTimezone(companyId)
  const { today, monthStart } = useCompanyDates(tz)
  const [period, setPeriod] = useState<Period>('month')
  const usage = useCliUsage(companyId, period === 'today' ? today : monthStart, today)
  const clis = useCliRuntimes()
  const { map: agents } = useAgentMap(companyId)
  const statusById = new Map((clis.data ?? []).map((c) => [`cli:${c.id}`, c]))

  const toggle = (
    <div role="group" aria-label="Period" className="flex rounded-md border border-line p-0.5">
      {(['today', 'month'] as const).map((p) => (
        <button
          key={p}
          type="button"
          aria-pressed={period === p}
          onClick={() => setPeriod(p)}
          className={clsx('rounded px-2 py-0.5 text-[12px] font-medium transition-colors', period === p ? 'bg-accent-subtle text-accent-fg' : 'text-fg-3 hover:text-fg')}
        >
          {p === 'today' ? 'Today' : 'This month'}
        </button>
      ))}
    </div>
  )

  return (
    <Widget title="Coding CLIs" subtitle="Usage by tool" actions={toggle} className={className}>
      <Boundary query={usage} unavailableText="Per-CLI usage needs a newer backend.">
        {(data) => {
          const used = data.items.some((i) => i.runs > 0)
          return (
            <div className="space-y-2 p-3.5">
              {!used && (
                <p className="rounded-md border border-line bg-surface-2 p-2.5 text-[12px] text-fg-2">
                  No CLI runs {period === 'today' ? 'today' : 'this month'}. To use one, set an agent&apos;s runtime to a CLI (for example Claude Code) in its settings, after connecting it under AI setup.
                </p>
              )}
              <ul className="divide-y divide-line">
                {data.items.map((row) => {
                  const status = statusById.get(row.runtime)
                  const name = status?.display_name ?? row.runtime.replace(/^cli:/, '')
                  return (
                    <li key={row.runtime} className="grid gap-x-4 gap-y-1.5 py-2.5 text-[12px] md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)]">
                      <div className="min-w-0 space-y-1">
                        <p className="truncate text-[13px] font-medium text-fg">{name}</p>
                        <StatusBadges status={status} />
                      </div>
                      <div className="space-y-0.5 text-fg-2">
                        <p>
                          {row.runs} {row.runs === 1 ? 'run' : 'runs'}: <span className="text-success">✓ {row.succeeded}</span> · <span className={row.failed > 0 ? 'text-danger' : 'text-fg-3'}>✗ {row.failed}</span>
                        </p>
                        <p className="text-fg-3">
                          {formatTokens(row.input_tokens)} in / {formatTokens(row.output_tokens)} out
                        </p>
                        <p className="text-fg-3">{row.runs > 0 ? formatDuration(row.duration_seconds * 1000) : '0s'} spent</p>
                      </div>
                      <CostCell row={row} />
                      <div className="flex min-w-0 flex-wrap items-start gap-1">
                        {row.agents.length === 0 ? <span className="text-fg-3">No agents yet</span> : row.agents.map((id) => <AgentChip key={id} agentId={id} agents={agents} />)}
                      </div>
                    </li>
                  )
                })}
              </ul>
              {clis.isError && <p className="text-[11.5px] text-fg-3">Install and connection status could not be loaded.</p>}
            </div>
          )
        }}
      </Boundary>
    </Widget>
  )
}
