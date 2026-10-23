import { ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { Badge, Dot, Tooltip } from '@/components/ui'
import { scanVerdictInfo, skillStatusInfo, sourceKindLabel, trustLabel } from '@/lib/skills'

/** Scan verdict. Caution and dangerous carry an icon as well as colour, so the warning is never colour-only. */
export function VerdictBadge({ verdict, bare }: { verdict: string; bare?: boolean }) {
  const v = scanVerdictInfo(verdict)
  const Icon = v.tone === 'success' ? ShieldCheck : v.flagged ? ShieldAlert : ShieldQuestion
  const badge = (
    <Badge tone={v.tone} data-verdict={verdict} title={bare ? v.help : undefined}>
      <Icon className="size-3" aria-hidden />
      {v.label}
    </Badge>
  )
  // `bare` (dense tables): no tooltip wrapper, so a column of badges is not a column of tab stops.
  return bare ? badge : <Tooltip content={v.help}>{badge}</Tooltip>
}

export function SkillStatusBadge({ status, bare }: { status: string; bare?: boolean }) {
  const s = skillStatusInfo(status)
  const badge = (
    <Badge tone={s.tone} data-status={status} title={bare ? s.help : undefined}>
      <Dot tone={s.tone} />
      {s.label}
    </Badge>
  )
  return bare ? badge : <Tooltip content={s.help || s.label}>{badge}</Tooltip>
}

export function SourceBadge({ kind }: { kind: string }) {
  return <Badge tone="neutral">{sourceKindLabel(kind)}</Badge>
}

export function TrustText({ trust }: { trust: string }) {
  return <span className="text-fg-2">{trustLabel(trust)}</span>
}
