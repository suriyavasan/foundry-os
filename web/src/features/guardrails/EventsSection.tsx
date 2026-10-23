import { useMemo, useState } from 'react'
import { useGuardrailEvents } from '@/api'
import type { GuardrailEventDecision, GuardrailEventFilters, ActionTier } from '@/api/types'
import { useStream } from '@/api/stream'
import { Boundary, Button, Card, CardHeader, Select, TBody, TD, TH, THead, TR, Table } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { useAgentMap } from '@/features/canvas/hooks'
import { AgentChip } from '@/features/shared/AgentChip'
import { formatDateTime, formatRelative } from '@/lib/format'
import { EVENT_DECISIONS, TIERS, decisionLabel, tierLabel } from '@/lib/guardrails'
import { DecisionChip, TierBadge } from './DecisionChip'

const linkBtn = 'text-accent-fg hover:underline focus-visible:underline'

/** What the policy engine decided recently: filterable, newest first, and live (`guardrail.event`). */
export function EventsSection({ companyId, onFocusRule }: { companyId: string; onFocusRule: (ruleId: string) => void }) {
  const { openPanel } = useCanvas()
  const stream = useStream()
  const { map, agents } = useAgentMap(companyId)
  const [decision, setDecision] = useState<GuardrailEventDecision | ''>('')
  const [tier, setTier] = useState<ActionTier | ''>('')
  const [agentId, setAgentId] = useState('')
  const filters = useMemo<GuardrailEventFilters>(() => ({ ...(decision ? { decision } : null), ...(tier ? { tier } : null), ...(agentId ? { agent_id: agentId } : null) }), [decision, tier, agentId])
  const q = useGuardrailEvents(companyId, filters)
  const items = q.data?.pages.flatMap((p) => p.items) ?? []
  const filtered = decision !== '' || tier !== '' || agentId !== ''

  return (
    <Card>
      <CardHeader
        title="Recent guardrail events"
        description={stream.status === 'live' ? 'Every decision the policy engine made. Updates live.' : 'Every decision the policy engine made.'}
        actions={
          <>
            <Select aria-label="Filter by decision" className="h-7 w-auto text-[12.5px]" value={decision} onChange={(e) => setDecision(e.target.value as GuardrailEventDecision | '')}>
              <option value="">All decisions</option>
              {EVENT_DECISIONS.map((d) => (
                <option key={d} value={d}>
                  {decisionLabel(d)}
                </option>
              ))}
            </Select>
            <Select aria-label="Filter by tier" className="h-7 w-auto text-[12.5px]" value={tier} onChange={(e) => setTier(e.target.value as ActionTier | '')}>
              <option value="">All tiers</option>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {tierLabel(t)}
                </option>
              ))}
            </Select>
            <Select aria-label="Filter by agent" className="h-7 w-auto max-w-40 text-[12.5px]" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              <option value="">All agents</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>
          </>
        }
      />
      <Boundary
        query={q}
        milestone="M6"
        compact={false}
        unavailableTitle="Guardrail events are not built yet"
        unavailableText="The decision log comes with guardrails & governance."
        isEmpty={(d) => d.pages.every((p) => p.items.length === 0)}
        empty={{ title: filtered ? 'No events match these filters' : 'No guardrail events yet', description: filtered ? 'Clear a filter to see more.' : 'Decisions appear here as agents act.' }}
      >
        {() => (
          <>
            <Table>
              <THead>
                <TR>
                  <TH>Time</TH>
                  <TH>Agent</TH>
                  <TH>Action</TH>
                  <TH>Tier</TH>
                  <TH>Decision</TH>
                  <TH>Rule</TH>
                  <TH>Links</TH>
                </TR>
              </THead>
              <TBody>
                {items.map((e) => (
                  <TR key={e.id}>
                    <TD className="whitespace-nowrap text-fg-3">
                      <time dateTime={e.created_at} title={formatDateTime(e.created_at)}>
                        {formatRelative(e.created_at)}
                      </time>
                    </TD>
                    <TD>
                      <AgentChip agentId={e.agent_id} agents={map} emptyLabel="System" />
                    </TD>
                    <TD className="font-mono text-[12.5px]" title={e.reason ?? (e.payload_digest ? `payload digest ${e.payload_digest}` : undefined)}>
                      {e.action}
                    </TD>
                    <TD>
                      <TierBadge tier={e.tier} />
                    </TD>
                    <TD>
                      <DecisionChip decision={e.decision} />
                    </TD>
                    <TD>
                      {e.rule_id ? (
                        <button type="button" className={linkBtn} onClick={() => onFocusRule(e.rule_id!)}>
                          View rule
                        </button>
                      ) : (
                        <span className="text-fg-3">Default</span>
                      )}
                    </TD>
                    <TD>
                      <span className="flex flex-wrap gap-x-3">
                        {e.run_id && e.task_id ? (
                          <button type="button" className={linkBtn} onClick={() => openPanel({ kind: 'task', id: e.task_id!, run: e.run_id! })}>
                            Run
                          </button>
                        ) : null}
                        {e.approval_id && (
                          <button type="button" className={linkBtn} onClick={() => openPanel({ kind: 'approvals' })}>
                            Approval
                          </button>
                        )}
                        {!(e.run_id && e.task_id) && !e.approval_id && <span className="text-fg-3">{'—'}</span>}
                      </span>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {q.hasNextPage && (
              <div className="border-t border-line p-3 text-center">
                <Button size="sm" loading={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
                  Load older events
                </Button>
              </div>
            )}
          </>
        )}
      </Boundary>
    </Card>
  )
}
