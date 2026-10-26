import { Plug } from 'lucide-react'
import { isNotDeployed, useAiSetup } from '@/api'
import { Button, Card, ErrorState, LoadingState, NotAvailable, useToast } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { AiSetupForm } from './AiSetupForm'

/** `?panel=ai`: revisit the AI setup any time: the model API that runs the company, and the optional coding CLIs. One save. */
export function AiPanel() {
  const { companyId, openPanel } = useCanvas()
  const ai = useAiSetup(companyId)
  const role = useCompanyRole(companyId)
  const canEdit = role === undefined ? false : canEditPolicy(role)
  const toast = useToast()
  const unavailable = ai.isError && isNotDeployed(ai.error)

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
          <div className="flex justify-end">
            <Button size="sm" icon={<Plug className="size-3.5" />} onClick={() => openPanel({ kind: 'integrations' })}>
              All providers
            </Button>
          </div>
          <AiSetupForm companyId={companyId} canEdit={canEdit} saveLabel="Save" requireChange onSaved={() => toast.success('Saved your AI setup')} />
        </div>
      )}
    </PanelFrame>
  )
}
