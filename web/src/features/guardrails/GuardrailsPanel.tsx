import { useState } from 'react'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { EmergencyStopSection } from './EmergencyStop'
import { EventsSection } from './EventsSection'
import { RateLimitsSection } from './RateLimitsSection'
import { RulesSection } from './RulesSection'

/** `?panel=guardrails`: emergency stop, the rules that decide each action, rate limits and the decision log. */
export function GuardrailsPanel() {
  const { companyId } = useCanvas()
  const [focusRuleId, setFocusRuleId] = useState<string | null>(null)
  return (
    <PanelFrame title="Guardrails" subtitle="What agents may do on their own, and what needs a check first. Enforced in code, never by prompt.">
      <div className="space-y-4">
        <EmergencyStopSection />
        <RulesSection companyId={companyId} focusRuleId={focusRuleId} />
        <RateLimitsSection companyId={companyId} />
        <EventsSection companyId={companyId} onFocusRule={setFocusRuleId} />
      </div>
    </PanelFrame>
  )
}
