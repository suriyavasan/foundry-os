import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { errorMessage, useDeleteGuardrailRule, useGuardrailRules, usePolicy } from '@/api'
import type { ActionTier, GuardrailRule } from '@/api/types'
import { Badge, Boundary, Button, Card, CardBody, CardHeader, ConfirmDialog, Field, IconButton, Input, SectionLabel, Select, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { AgentChip } from '@/features/shared/AgentChip'
import { useAgentMap, useCompanyRole, canEditPolicy } from '@/features/canvas/hooks'
import { formatRelative } from '@/lib/format'
import {
  SOURCE_LABEL,
  TIERS,
  TIER_DEFAULTS,
  TIER_EXAMPLES,
  TIER_LABEL,
  conditionsSummary,
  overridesByTier,
  resolveDecision,
  scopeLabel,
  tierLabel,
} from '@/lib/guardrails'
import { formatUsd } from '@/lib/money'
import { inferRoleFamily } from '@/lib/roleFamily'
import { DecisionChip, TierBadge } from './DecisionChip'
import { RuleDialog } from './RuleDialog'

/** Rules table, the built-in baseline they override, and a "what would happen" preview. */
export function RulesSection({ companyId, focusRuleId }: { companyId: string; focusRuleId: string | null }) {
  const q = useGuardrailRules(companyId)
  const role = useCompanyRole(companyId)
  const canEdit = canEditPolicy(role)
  const [editing, setEditing] = useState<GuardrailRule | 'new' | null>(null)
  const [deleting, setDeleting] = useState<GuardrailRule | null>(null)
  const { map } = useAgentMap(companyId)

  return (
    <>
      <Card>
        <CardHeader
          title="Rules"
          description="Company policy that overrides the built-in defaults. A rule for an exact action wins over a rule for its tier; a narrower scope wins over a wider one."
          actions={
            canEdit && !q.isError ? (
              <Button size="sm" variant="primary" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => setEditing('new')}>
                Add rule
              </Button>
            ) : undefined
          }
        />
        <Boundary
          query={q}
          milestone="M6"
          compact={false}
          unavailableTitle="Guardrails are not built yet"
          unavailableText="Risk tiers, rules and the policy engine come with guardrails & governance. Until then no rule is enforced."
          isEmpty={(d) => d.length === 0}
          empty={{
            title: 'No custom rules',
            description: 'Every action follows the built-in defaults below.',
            action: canEdit ? (
              <Button size="sm" variant="primary" icon={<Plus className="size-3.5" aria-hidden />} onClick={() => setEditing('new')}>
                Add rule
              </Button>
            ) : undefined,
          }}
        >
          {(rules) => (
            <>
              {!canEdit && (
                <p role="note" className="border-b border-line bg-raised px-4 py-2 text-[12.5px] text-fg-3">
                  Read-only: only owners and admins can change rules.
                </p>
              )}
              <Table>
                <THead>
                  <TR>
                    <TH>Covers</TH>
                    <TH>Scope</TH>
                    <TH>Decision</TH>
                    <TH>Conditions</TH>
                    <TH>Updated</TH>
                    {canEdit && <TH className="text-right">Actions</TH>}
                  </TR>
                </THead>
                <TBody>
                  {sortRules(rules).map((r) => (
                    <RuleRow key={r.id} rule={r} focused={r.id === focusRuleId} canEdit={canEdit} agents={map} onEdit={() => setEditing(r)} onDelete={() => setDeleting(r)} />
                  ))}
                </TBody>
              </Table>
            </>
          )}
        </Boundary>
      </Card>

      <DefaultsCard companyId={companyId} rules={q.data ?? []} available={q.isSuccess} />
      {q.isSuccess && <RuleTester companyId={companyId} rules={q.data} />}

      {editing && <RuleDialog companyId={companyId} rule={editing === 'new' ? undefined : editing} onClose={() => setEditing(null)} />}
      {deleting && <DeleteRule companyId={companyId} rule={deleting} onClose={() => setDeleting(null)} />}
    </>
  )
}

/** Tier order, then specific actions after the tier-wide rule, then widest scope first. */
function sortRules(rules: GuardrailRule[]): GuardrailRule[] {
  const scopeOrder = { all: 0, family: 1, agent: 2 } as const
  return [...rules].sort(
    (a, b) =>
      TIERS.indexOf(a.tier) - TIERS.indexOf(b.tier) ||
      Number(!!a.action) - Number(!!b.action) ||
      (a.action ?? '').localeCompare(b.action ?? '') ||
      scopeOrder[a.scope_type] - scopeOrder[b.scope_type],
  )
}

function RuleRow({
  rule,
  focused,
  canEdit,
  agents,
  onEdit,
  onDelete,
}: {
  rule: GuardrailRule
  focused: boolean
  canEdit: boolean
  agents: ReturnType<typeof useAgentMap>['map']
  onEdit: () => void
  onDelete: () => void
}) {
  useEffect(() => {
    if (focused) document.getElementById(`rule-${rule.id}`)?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [focused, rule.id])
  const target = rule.action ? rule.action : `Any ${tierLabel(rule.tier).toLowerCase()} action`
  return (
    <TR id={`rule-${rule.id}`} className={focused ? 'bg-accent-subtle' : undefined} aria-current={focused || undefined}>
      <TD>
        <div className="flex flex-col gap-0.5">
          <span className={rule.action ? 'font-mono text-[12.5px]' : 'font-medium'}>{target}</span>
          <TierBadge tier={rule.tier} className="w-fit" />
        </div>
      </TD>
      <TD>
        {rule.scope_type === 'agent' && rule.scope_value ? (
          <span className="inline-flex items-center gap-1.5">
            <AgentChip agentId={rule.scope_value} agents={agents} />
          </span>
        ) : (
          <span className="text-fg-2">{scopeLabel(rule)}</span>
        )}
      </TD>
      <TD>
        <DecisionChip decision={rule.decision} />
      </TD>
      <TD className="max-w-56 text-[12.5px] text-fg-2">{conditionsSummary(rule.conditions)}</TD>
      <TD className="text-fg-3">{formatRelative(rule.updated_at)}</TD>
      {canEdit && (
        <TD className="text-right">
          <div className="inline-flex gap-0.5">
            <IconButton label={`Edit rule for ${target}`} className="size-7" onClick={onEdit}>
              <Pencil className="size-3.5" aria-hidden />
            </IconButton>
            <IconButton label={`Delete rule for ${target}`} className="size-7" onClick={onDelete}>
              <Trash2 className="size-3.5" aria-hidden />
            </IconButton>
          </div>
        </TD>
      )}
    </TR>
  )
}

function DeleteRule({ companyId, rule, onClose }: { companyId: string; rule: GuardrailRule; onClose: () => void }) {
  const del = useDeleteGuardrailRule(companyId)
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  async function confirm() {
    setError(null)
    try {
      await del.mutateAsync(rule.id)
      toast.success('Rule deleted')
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    }
  }
  return (
    <ConfirmDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Delete this rule?"
      confirmLabel="Delete rule"
      loading={del.isPending}
      error={error}
      onConfirm={() => void confirm()}
      description={
        <p>
          {rule.action ?? `Every ${tierLabel(rule.tier).toLowerCase()} action`} will fall back to the next matching rule or the built-in default. The change is recorded in the audit log.
        </p>
      }
    />
  )
}

/* ------------------------------------- baseline ------------------------------------- */

function DefaultsCard({ companyId, rules, available }: { companyId: string; rules: GuardrailRule[]; available: boolean }) {
  const policy = usePolicy(companyId)
  const overrides = useMemo(() => overridesByTier(rules), [rules])
  const spendLimit = policy.data?.auto_spend_limit_micros
  return (
    <Card>
      <CardHeader title="Built-in defaults" description="The baseline every action follows unless a rule above overrides it. Fail closed: an unclassified action is denied." />
      <Table>
        <THead>
          <TR>
            <TH>Tier</TH>
            <TH>Default</TH>
            <TH>Notes</TH>
            {available && <TH>Status</TH>}
          </TR>
        </THead>
        <TBody>
          {TIER_DEFAULTS.map((d) => {
            const over = overrides.get(d.tier) ?? []
            const note =
              d.tier === 'spend' && typeof spendLimit === 'number'
                ? `Human approval above ${formatUsd(spendLimit)} (the automatic spend limit); otherwise AI Co-founder approval`
                : d.note
            return (
              <TR key={d.tier}>
                <TD>
                  <div className="font-medium">{TIER_LABEL[d.tier]}</div>
                  <div className="max-w-64 text-[12px] text-fg-3">{TIER_EXAMPLES[d.tier]}</div>
                </TD>
                <TD>
                  <DecisionChip decision={d.decision} />
                </TD>
                <TD className="max-w-64 text-[12.5px] text-fg-2">{note ?? '—'}</TD>
                {available && (
                  <TD>
                    {over.length > 0 ? (
                      <Badge tone="accent">
                        Overridden by {over.length} rule{over.length === 1 ? '' : 's'}
                      </Badge>
                    ) : (
                      <span className="text-[12.5px] text-fg-3">Default applies</span>
                    )}
                  </TD>
                )}
              </TR>
            )
          })}
        </TBody>
      </Table>
    </Card>
  )
}

/* -------------------------------------- tester -------------------------------------- */

function RuleTester({ companyId, rules }: { companyId: string; rules: GuardrailRule[] }) {
  const { agents } = useAgentMap(companyId)
  const [tier, setTier] = useState<ActionTier>('external_communication')
  const [action, setAction] = useState('')
  const [agentId, setAgentId] = useState('')
  const agent = agents.find((a) => a.id === agentId) ?? null
  const result = useMemo(
    () => resolveDecision(rules, { tier, action }, agent ? { id: agent.id, family: inferRoleFamily(agent) } : null),
    [rules, tier, action, agent],
  )
  return (
    <Card>
      <CardHeader title="Try it" description="Which decision would apply? A preview from the rules above; the server decides at run time and also checks conditions." />
      <CardBody className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Tier">
            {({ id }) => (
              <Select id={id} value={tier} onChange={(e) => setTier(e.target.value as ActionTier)}>
                {TIERS.map((t) => (
                  <option key={t} value={t}>
                    {TIER_LABEL[t]}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Action (optional)">
            {({ id }) => <Input id={id} className="font-mono" placeholder="email.send" value={action} onChange={(e) => setAction(e.target.value)} />}
          </Field>
          <Field label="Agent (optional)">
            {({ id }) => (
              <Select id={id} value={agentId} onChange={(e) => setAgentId(e.target.value)}>
                <option value="">Any agent</option>
                {agents
                  .filter((a) => a.status !== 'retired' && a.status !== 'rejected')
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </Select>
            )}
          </Field>
        </div>
        <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-raised px-3 py-2 text-[13px]">
          <SectionLabel>Result</SectionLabel>
          <DecisionChip decision={result.decision} />
          <span className="text-fg-2">{SOURCE_LABEL[result.source]}</span>
          {result.rule && <span className="text-fg-3">· {scopeLabel(result.rule, (id) => agents.find((a) => a.id === id)?.name ?? null)}</span>}
        </div>
      </CardBody>
    </Card>
  )
}
