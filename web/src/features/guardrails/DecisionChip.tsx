import { Ban, Check, Gauge, Sparkles, UserCheck, Users, type LucideIcon } from 'lucide-react'
import { Badge, Tooltip } from '@/components/ui'
import { decisionHelp, decisionLabel, decisionTone, tierLabel } from '@/lib/guardrails'

const ICON: Record<string, LucideIcon> = {
  allow: Check,
  peer_review: Users,
  cofounder_approval: Sparkles,
  human_approval: UserCheck,
  deny: Ban,
  rate_limited: Gauge,
}

/**
 * A guardrail decision as a coloured chip. The meaning is never carried by colour alone: every decision has its own
 * icon and its own words (allow, peer review, AI Co-founder approval, human approval, deny).
 */
export function DecisionChip({ decision, className }: { decision: string; className?: string }) {
  const Icon = ICON[decision]
  const help = decisionHelp(decision)
  const chip = (
    <Badge tone={decisionTone(decision)} className={className} data-decision={decision}>
      {Icon && <Icon className="size-3" aria-hidden />}
      {decisionLabel(decision)}
    </Badge>
  )
  return help ? <Tooltip content={help}>{chip}</Tooltip> : chip
}

export function TierBadge({ tier, className }: { tier: string; className?: string }) {
  return (
    <Badge tone="neutral" className={className}>
      {tierLabel(tier)}
    </Badge>
  )
}
