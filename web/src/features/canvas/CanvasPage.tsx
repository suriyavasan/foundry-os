import { Pause } from 'lucide-react'
import { useEffect } from 'react'
import { useLocation } from 'react-router'
import { useCompany } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { AlertsStrip } from '@/features/alerts/AlertsStrip'
import { ReachabilityCard } from '@/features/reachability/ReachabilityCard'
import { COMMAND_INPUT_ID } from '@/features/commandcenter/CommandInput'
import { useCanvas } from './CanvasContext'
import { ApprovalsWidget } from './widgets/ApprovalsWidget'
import { ArtifactsWidget } from './widgets/ArtifactsWidget'
import { GoalsWidget } from './widgets/GoalsWidget'
import { LiveActivityWidget } from './widgets/LiveActivityWidget'
import { CommandCenterWidget } from './widgets/CommandCenterWidget'
import { OrgWidget } from './widgets/OrgWidget'
import { ReflectionsWidget } from './widgets/ReflectionsWidget'
import { RoutinesWidget } from './widgets/RoutinesWidget'
import { SkillsWidget } from './widgets/SkillsWidget'
import { SpendWidget } from './widgets/SpendWidget'
import { WalletWidget } from './widgets/WalletWidget'
import { WorkWidget } from './widgets/WorkWidget'

/**
 * The company canvas: eleven widgets in one grid, all in the same Widget frame. Twelve columns on wide
 * screens, two on tablets, one on phones. Row one is tall: the organization (six columns), Reflections (three)
 * and the Command Center (three). Below 1440 px the organization takes the full width and Reflections and the
 * Command Center sit side by side under it; on phones everything stacks. Live activity and the goals & plan share
 * the next tall row (eight and four); the rest are three equal columns.
 */
export function CanvasPage() {
  const { companyId } = useCanvas()
  const company = useCompany(companyId)
  const location = useLocation()
  const focusCommandCenter = (location.state as { focus?: string } | null)?.focus === 'command-center'

  // After onboarding the canvas opens with the cursor in the Command Center.
  useEffect(() => {
    if (!focusCommandCenter || !company.data) return
    const t = setTimeout(() => document.getElementById(COMMAND_INPUT_ID)?.focus(), 60)
    return () => clearTimeout(t)
  }, [focusCommandCenter, company.data])

  return (
    <main id="main" className="mx-auto w-full max-w-[1760px] p-3 md:p-4">
      <h1 className="sr-only">{company.data?.name ?? 'Company'} canvas</h1>
      <AlertsStrip />
      <ReachabilityCard />
      {company.data?.status === 'paused' && (
        <div role="status" className="mb-3 flex items-center gap-2 rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-[13px] text-fg">
          <Pause className="size-3.5 shrink-0 text-warning" aria-hidden />
          <span>
            <strong className="font-semibold">Company paused.</strong> No work is being dispatched. Resume from the top bar to restart.
          </span>
        </div>
      )}
      {company.isPending ? (
        <LoadingState label="Loading company" rows={4} />
      ) : company.isError ? (
        <ErrorState error={company.error} onRetry={() => void company.refetch()} retrying={company.isFetching} />
      ) : (
        <div className="grid grid-cols-1 gap-(--widget-gap) md:grid-cols-2 xl:grid-cols-12">
          <OrgWidget className="md:col-span-2 xl:col-span-12 min-[1440px]:col-span-6" />
          <ReflectionsWidget className="xl:col-span-6 min-[1440px]:col-span-3" />
          <CommandCenterWidget className="xl:col-span-6 min-[1440px]:col-span-3" />
          <LiveActivityWidget className="md:col-span-2 xl:col-span-8" />
          <GoalsWidget className="md:col-span-2 xl:col-span-4" />
          <WorkWidget className="xl:col-span-4" />
          <ApprovalsWidget className="xl:col-span-4" />
          <SpendWidget className="xl:col-span-4" />
          <WalletWidget className="xl:col-span-4" />
          <ArtifactsWidget className="xl:col-span-4" />
          <RoutinesWidget className="xl:col-span-4" />
          <SkillsWidget className="xl:col-span-4" />
        </div>
      )}
    </main>
  )
}
