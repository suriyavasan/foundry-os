import { Plug } from 'lucide-react'
import { Button } from '@/components/ui'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { AiStack } from './AiStack'
import { CodingTools } from './CodingTools'

/** `?panel=ai`: add, order and tune the AIs the company uses, any time. */
export function AiPanel() {
  const { companyId, openPanel } = useCanvas()
  const role = useCompanyRole(companyId)
  const canEdit = role === undefined ? false : canEditPolicy(role)

  return (
    <PanelFrame title="AI settings" subtitle="The AIs your company uses, and what each is for">
      <div className="space-y-4">
        <div className="flex justify-end">
          <Button size="sm" icon={<Plug className="size-3.5" />} onClick={() => openPanel({ kind: 'integrations' })}>
            All providers
          </Button>
        </div>
        <AiStack companyId={companyId} canEdit={canEdit} />
        <CodingTools canInstall={role === 'owner'} />
      </div>
    </PanelFrame>
  )
}
