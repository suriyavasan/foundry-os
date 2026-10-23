import { useRateLimits } from '@/api'
import type { RateLimitStatus } from '@/api/types'
import { Boundary, Card, CardHeader, ProgressBar } from '@/components/ui'
import { useAgentMap } from '@/features/canvas/hooks'
import { AgentChip } from '@/features/shared/AgentChip'
import { formatInterval } from '@/lib/dates'
import { formatRelative } from '@/lib/format'
import { groupRateLimits, rateFraction, rateLevel, rateLimitLabel } from '@/lib/guardrails'

const TONE = { ok: 'accent', warn: 'warning', full: 'danger' } as const

/** One usage-versus-limit bar. Wording says what is used, never colour alone. */
export function RateBar({ limit }: { limit: RateLimitStatus }) {
  const level = rateLevel(limit)
  const frac = rateFraction(limit)
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
        <span className="text-fg-2">{rateLimitLabel(limit.key)}</span>
        <span className="tabular-nums text-fg">
          {limit.used.toLocaleString()} / {limit.limit.toLocaleString()}
          {level === 'full' && <span className="ml-1.5 font-medium text-danger">at limit</span>}
          {level === 'warn' && <span className="ml-1.5 font-medium text-warning">nearly full</span>}
        </span>
      </div>
      <ProgressBar className="mt-1" value={limit.used} max={limit.limit} tone={TONE[level]} label={`${rateLimitLabel(limit.key)}: ${limit.used} of ${limit.limit}${frac !== null ? `, ${Math.round(frac * 100)}%` : ''}`} />
      {(limit.window_seconds > 0 || limit.resets_at) && (
        <div className="mt-0.5 text-[11.5px] text-fg-3">
          {limit.window_seconds > 0 ? `Window ${formatInterval(limit.window_seconds)}` : ''}
          {limit.window_seconds > 0 && limit.resets_at ? ' · ' : ''}
          {limit.resets_at ? `resets ${formatRelative(limit.resets_at)}` : ''}
        </div>
      )}
    </div>
  )
}

/** Sliding-window quotas per agent (and company-wide), current usage against the limit. */
export function RateLimitsSection({ companyId }: { companyId: string }) {
  const q = useRateLimits(companyId)
  const { map } = useAgentMap(companyId)
  return (
    <Card>
      <CardHeader title="Rate limits" description="Per-agent and company-wide windows. Exceeding one returns 429 rate_limited and logs an event." />
      <Boundary
        query={q}
        milestone="M6"
        compact={false}
        unavailableTitle="Rate limits are not built yet"
        unavailableText="Quotas and their current usage come with guardrails & governance."
        isEmpty={(d) => d.length === 0}
        empty={{ title: 'No rate-limit windows yet', description: 'Windows appear once agents start acting.' }}
      >
        {(items) => (
          <div className="grid gap-px bg-line md:grid-cols-2">
            {groupRateLimits(items).map((g) => (
              <section key={g.agentId ?? 'company'} aria-label={g.agentId ? (map.get(g.agentId)?.name ?? 'Agent') : 'Company-wide'} className="space-y-2.5 bg-surface p-4">
                <h3 className="text-[13px] font-medium text-fg">{g.agentId ? <AgentChip agentId={g.agentId} agents={map} size="sm" /> : 'Company-wide'}</h3>
                {g.limits.map((l) => (
                  <RateBar key={l.key} limit={l} />
                ))}
              </section>
            ))}
          </div>
        )}
      </Boundary>
    </Card>
  )
}
