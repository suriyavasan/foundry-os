import { useMemo } from 'react'
import { useEffectivePermissions, useRateLimits } from '@/api'
import type { Agent, EffectivePermissions } from '@/api/types'
import { Boundary, Button, Card, CardBody, CardHeader, Chip, SectionLabel, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { TIERS, tierLabel } from '@/lib/guardrails'
import { DecisionChip } from './DecisionChip'
import { RateBar } from './RateLimitsSection'

/**
 * What this agent may actually do, as the policy engine resolves it: the decision for each risk tier and for each tool
 * the agent holds, plus how much of its rate-limit windows it has used. Read-only: only humans change rules.
 */
export function EffectivePermissionsCard({ agent }: { agent: Agent }) {
  const q = useEffectivePermissions(agent.company_id, agent.id)
  const { openPanel } = useCanvas()
  return (
    <Card>
      <CardHeader
        title="Effective permissions"
        description="The decision for each risk tier and tool, after company rules. Agents cannot change these."
        actions={
          <Button size="sm" onClick={() => openPanel({ kind: 'guardrails' })}>
            Open guardrails
          </Button>
        }
      />
      <Boundary
        query={q}
        milestone="M6"
        compact={false}
        unavailableTitle="Effective permissions are not built yet"
        unavailableText="The per-tier decisions and rate limits for this agent come with guardrails & governance."
        isEmpty={(d) => d.tiers.length === 0 && d.tools.length === 0}
        empty={{ title: 'Nothing to show', description: 'The policy engine returned no tiers for this agent.' }}
      >
        {(data) => <Effective agent={agent} data={data} />}
      </Boundary>
    </Card>
  )
}

function Effective({ agent, data }: { agent: Agent; data: EffectivePermissions }) {
  const limitsQ = useRateLimits(agent.company_id)
  const limits = data.rate_limits ?? limitsQ.data?.filter((l) => l.agent_id === agent.id) ?? []
  const tiers = useMemo(() => [...data.tiers].sort((a, b) => TIERS.indexOf(a.tier) - TIERS.indexOf(b.tier)), [data.tiers])
  const toolsByTier = useMemo(() => {
    const m = new Map<string, EffectivePermissions['tools']>()
    for (const t of data.tools) m.set(t.tier, [...(m.get(t.tier) ?? []), t])
    return [...m.entries()].sort((a, b) => TIERS.indexOf(a[0] as (typeof TIERS)[number]) - TIERS.indexOf(b[0] as (typeof TIERS)[number]))
  }, [data.tools])
  return (
    <>
      <Table>
        <THead>
          <TR>
            <TH>Risk tier</TH>
            <TH>Decision</TH>
            <TH>Decided by</TH>
          </TR>
        </THead>
        <TBody>
          {tiers.map((t) => (
            <TR key={t.tier}>
              <TD className="font-medium">{tierLabel(t.tier)}</TD>
              <TD>
                <DecisionChip decision={t.decision} />
              </TD>
              <TD className="text-[12.5px] text-fg-2">{t.source === 'rule' ? 'Company rule' : 'Built-in default'}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <CardBody className="space-y-4 border-t border-line">
        <div>
          <SectionLabel>Tools</SectionLabel>
          {toolsByTier.length === 0 ? (
            <p className="mt-1 text-[12.5px] text-fg-3">This agent holds no tools.</p>
          ) : (
            <div className="mt-2 space-y-2.5">
              {toolsByTier.map(([tier, tools]) => (
                <div key={tier} role="group" aria-label={`${tierLabel(tier)} tools`}>
                  <div className="mb-1 text-[12px] font-medium text-fg-2">{tierLabel(tier)}</div>
                  <ul className="flex flex-wrap gap-1.5">
                    {tools.map((t) => (
                      <li key={t.name} className="inline-flex items-center gap-1.5">
                        <Chip>{t.name}</Chip>
                        <DecisionChip decision={t.decision} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <SectionLabel>Rate limits</SectionLabel>
          {limits.length === 0 ? (
            <p className="mt-1 text-[12.5px] text-fg-3">No rate-limit usage recorded for this agent yet.</p>
          ) : (
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              {limits.map((l) => (
                <RateBar key={l.key} limit={l} />
              ))}
            </div>
          )}
        </div>
      </CardBody>
    </>
  )
}
