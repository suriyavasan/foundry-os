import { useCallback, useId, useState, type ReactNode } from 'react'
import { isNotDeployed, useAiSetup, useUpdateAiSetup } from '@/api'
import { Badge, Button, FormError } from '@/components/ui'
import { COMPANY_AGENT_WORK_RUNTIME, aiSetupUpdate, putFailure } from '@/lib/aiSetup'
import { canContinue } from '@/lib/onboarding'
import { ConnectAiSection } from './ConnectAiSection'
import { WorkModeSection, type WorkChoice } from './WorkModeSection'

function Section({ title, badge, tone, purpose, children }: { title: string; badge: string; tone: 'accent' | 'neutral'; purpose: string; children: ReactNode }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="rounded-lg border border-line bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={id} className="text-[15px] font-semibold text-fg">
          {title}
        </h2>
        <Badge tone={tone}>{badge}</Badge>
      </div>
      <p className="mt-1 mb-4 text-[12.5px] leading-snug text-fg-3">{purpose}</p>
      {children}
    </section>
  )
}

/**
 * The whole AI setup in one form with one save, used by the onboarding step and the AI settings panel:
 *   1. Model API (required): runs the company; it must pass a real test. Testing saves nothing.
 *   2. Coding CLIs (optional): who does the hands-on work, with the CLIs on this computer always visible.
 * The one button writes both parts in a single `PUT /ai-setup`, and only when something changed.
 */
export function AiSetupForm({
  companyId,
  canEdit,
  saveLabel,
  requireChange = false,
  onSaved,
  footerExtra,
}: {
  companyId: string
  canEdit: boolean
  saveLabel: string
  /** Settings: keep the button disabled while nothing differs from what is saved. */
  requireChange?: boolean
  onSaved?: () => Promise<void> | void
  footerExtra?: ReactNode
}) {
  const ai = useAiSetup(companyId)
  const save = useUpdateAiSetup(companyId)
  const [tested, setTested] = useState<{ providerId: string; model: string } | null>(null)
  const [choice, setChoice] = useState<WorkChoice>({ mode: 'api', runtime: null, problem: null })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const onChoice = useCallback((c: WorkChoice) => setChoice(c), [])
  const onTested = useCallback((sel: { providerId: string; model: string } | null) => setTested(sel), [])

  const unavailable = ai.isError && isNotDeployed(ai.error)
  const status = ai.data
  const apiOk = !!tested || status?.ready === true
  const managementChanged = !!status && !!tested && (tested.providerId !== status.management.provider_id || tested.model !== status.management.model)
  const workChanged = !!status && (choice.mode !== status.work_mode || (choice.mode === 'hybrid' && choice.runtime !== status.work_runtime))
  const changed = managementChanged || workChanged
  // Also save when the server does not yet call the setup ready, so that it is the one to check readiness.
  const needsPut = changed || status?.ready !== true

  const disabled = unavailable ? false : !status || !canContinue('ai', { aiReady: apiOk, aiAvailable: true, workProblem: choice.problem }) || (requireChange && !changed)

  const hint = unavailable ? null : !apiOk && status ? 'Test the model API to continue.' : choice.problem

  async function submit() {
    setError(null)
    setBusy(true)
    try {
      if (!unavailable && status && needsPut) {
        const body = aiSetupUpdate(status, choice.mode, choice.mode === 'api' ? COMPANY_AGENT_WORK_RUNTIME : choice.runtime, tested)
        if (!body) {
          setError('Test the model API first.')
          return
        }
        await save.mutateAsync(body)
      }
      setTested(null)
      await onSaved?.()
    } catch (e) {
      setError(putFailure(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Section title="Model API" badge="Required" tone="accent" purpose="Runs the company: your AI Co-founder, HR, leads and reviews think through it. It must pass a real test.">
        <ConnectAiSection companyId={companyId} canEdit={canEdit} deferSave onTested={onTested} />
      </Section>

      <Section
        title="Coding CLIs"
        badge="Optional"
        tone="neutral"
        purpose="Employees can do hands-on work (writing code, editing files, running commands) through a coding CLI on this computer. Skip it and they do everything through the model API."
      >
        {unavailable ? (
          <p role="status" className="rounded-md border border-line bg-raised p-4 text-[13px] text-fg-2">
            This server predates the AI setup (M10), so there is nothing to choose yet. Every employee works through your model provider.
          </p>
        ) : (
          <WorkModeSection companyId={companyId} canManage={canEdit} onChange={onChoice} />
        )}
      </Section>

      <FormError message={error} />
      <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line pt-4">
        {hint && (
          <p role="status" className="mr-auto text-[12.5px] text-fg-3">
            {hint}
          </p>
        )}
        {footerExtra}
        {canEdit && (
          <Button variant="primary" className="h-10 px-5" loading={busy || save.isPending} disabled={disabled} onClick={() => void submit()}>
            {saveLabel}
          </Button>
        )}
      </div>
    </div>
  )
}
