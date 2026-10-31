import { isNotDeployed, useIntensity } from '@/api'
import type { Intensity } from '@/api/types'
import { Badge, CardBody, ErrorState, Skeleton } from '@/components/ui'
import { canEditPolicy, useCompanyRole } from '@/features/canvas/hooks'
import { IntensityDetails, IntensitySteps, useIntensityChooser } from './IntensityControl'

/** Settings -> Policy: the same intensity control as the top bar, above the individual fields it writes. */
export function IntensitySetting({ companyId }: { companyId: string }) {
  const q = useIntensity(companyId)
  const role = useCompanyRole(companyId)
  if (q.isPending) {
    return (
      <CardBody className="border-b border-line">
        <Skeleton className="h-16 w-full" />
      </CardBody>
    )
  }
  if (q.error && isNotDeployed(q.error)) return null // a backend without M23
  if (q.error || !q.data) {
    return (
      <CardBody className="border-b border-line">
        <ErrorState error={q.error} onRetry={() => void q.refetch()} compact />
      </CardBody>
    )
  }
  return <Body companyId={companyId} intensity={q.data} canEdit={canEditPolicy(role)} />
}

function Body({ companyId, intensity, canEdit }: { companyId: string; intensity: Intensity; canEdit: boolean }) {
  const chooser = useIntensityChooser(companyId, intensity, canEdit)
  return (
    <CardBody className="border-b border-line">
      <section aria-label="Company intensity" className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="text-[13px] font-semibold text-fg">Intensity</h3>
            {intensity.custom && <Badge tone="warning">Custom</Badge>}
          </div>
          <p className="text-[12px] text-fg-3">
            One dial for how hard the company works. It sets the pace fields below (check-ins, parallel runs, planning, initiative, meetings, self-check) and never a budget.
          </p>
          <IntensitySteps intensity={intensity} canEdit={canEdit} chooser={chooser} />
        </div>
        <IntensityDetails intensity={intensity} showName={false} />
      </section>
      {chooser.dialog}
    </CardBody>
  )
}
