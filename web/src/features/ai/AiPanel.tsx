import { Plug } from 'lucide-react'
import { useCallback, useState } from 'react'
import { errorMessage, isNotDeployed, useAiSetup, useUpdateAiSetup } from '@/api'
import { Button, Card, CardBody, CardHeader, ErrorState, FormError, LoadingState, NotAvailable, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { aiSetupUpdate } from '@/lib/aiSetup'
import { ConnectAiSection } from './ConnectAiSection'
import { WorkModeSection, type WorkChoice } from './WorkModeSection'

/** `?panel=ai`: revisit the two onboarding decisions any time: the management model, and how work gets done. */
export function AiPanel() {
  const { companyId, openPanel } = useCanvas()
  const ai = useAiSetup(companyId)
  const role = useCompanyRole(companyId)
  const canEdit = role === undefined ? false : canEditPolicy(role)
  const save = useUpdateAiSetup(companyId)
  const toast = useToast()
  const [choice, setChoice] = useState<WorkChoice>({ mode: 'api', runtime: null, problem: null })
  const [error, setError] = useState<string | null>(null)
  const onChoice = useCallback((c: WorkChoice) => setChoice(c), [])
  const unavailable = ai.isError && isNotDeployed(ai.error)

  const body = ai.data ? aiSetupUpdate(ai.data, choice.mode, choice.runtime) : null
  const dirty = !!ai.data && (choice.mode !== ai.data.work_mode || (choice.mode === 'hybrid' && choice.runtime !== ai.data.work_runtime))

  async function saveWork() {
    if (!body) return
    setError(null)
    try {
      await save.mutateAsync(body)
      toast.success('Saved how work gets done')
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <PanelFrame title="AI settings" subtitle="The model that runs the company, and how employees do their work">
      {ai.isPending ? (
        <Card>
          <LoadingState rows={5} label="Loading AI settings" />
        </Card>
      ) : unavailable ? (
        <Card>
          <NotAvailable milestone="M10" title="AI settings are not built yet" description="This backend predates the AI setup. Model providers are still managed under Integrations." />
        </Card>
      ) : ai.isError ? (
        <Card>
          <ErrorState error={ai.error} onRetry={() => void ai.refetch()} retrying={ai.isFetching} />
        </Card>
      ) : (
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Management model"
              description="Used by the AI Co-founder, HR, leads and reviews: everything that runs the company. It must pass a real test (reachable, signed in, tool calling, enough context, model available)."
              actions={
                <Button size="sm" icon={<Plug className="size-3.5" />} onClick={() => openPanel({ kind: 'integrations' })}>
                  All providers
                </Button>
              }
            />
            <CardBody>
              <ConnectAiSection companyId={companyId} canEdit={canEdit} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="How work gets done" description="API only, or the API runs the company while coding CLIs do the hands-on work." />
            <CardBody className="space-y-4">
              <WorkModeSection companyId={companyId} canManage={canEdit} onChange={onChoice} />
              <FormError message={error} />
              {canEdit && (
                <div className="flex justify-end">
                  <Button variant="primary" loading={save.isPending} disabled={!dirty || !body || choice.problem !== null} onClick={() => void saveWork()}>
                    Save
                  </Button>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      )}
    </PanelFrame>
  )
}
