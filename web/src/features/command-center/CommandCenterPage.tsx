import { ArrowUpRight, Pencil } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useOverview } from '@/api'
import type { Agent, AgentStatus, Overview, Subsystem } from '@/api/types'
import { Page } from '@/components/layout/Page'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  Dot,
  EmptyState,
  ErrorState,
  LoadingState,
  NotAvailable,
  Skeleton,
  Stat,
  StatusBadge,
  agentStatusLabel,
  agentStatusTone,
} from '@/components/ui'
import { useCompanyId } from '@/features/shell/useCompanyId'
import { ActivityList } from '@/features/activity/ActivityList'

const STATUS_ORDER: AgentStatus[] = ['active', 'paused', 'proposed', 'retiring', 'retired', 'rejected']

export function CommandCenterPage() {
  const companyId = useCompanyId()
  const overview = useOverview(companyId)

  if (overview.isPending) {
    return (
      <Page title="Command Center" description="Loading company overview…">
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2"><LoadingState rows={4} label="Loading company" /></Card>
          <Card><LoadingState rows={3} label="Loading founder" /></Card>
          <Card className="lg:col-span-3"><Skeleton className="m-4 h-24 w-auto" /></Card>
        </div>
      </Page>
    )
  }
  if (overview.isError) {
    return (
      <Page title="Command Center">
        <Card>
          <ErrorState error={overview.error} onRetry={() => overview.refetch()} retrying={overview.isFetching} />
        </Card>
      </Page>
    )
  }

  const o = overview.data
  const base = `/c/${companyId}`

  return (
    <Page
      title="Command Center"
      description={`Live state of ${o.company.name}.`}
      actions={
        <Link to={`${base}/organization`} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-raised px-3 text-[13px] font-medium text-fg hover:bg-hover">
          View organization <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <CompanyHeader overview={o} base={base} />
        <FounderCard founder={o.founder} base={base} />

        <AgentBreakdown overview={o} base={base} />
        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent activity"
            description="Newest audit events"
            actions={
              <Link to={`${base}/activity`} className="text-[12.5px] text-accent-fg hover:underline">
                View all
              </Link>
            }
          />
          {o.recent_activity.length === 0 ? (
            <EmptyState title="No activity yet" description="State changes in this company are recorded here." />
          ) : (
            <ActivityList events={o.recent_activity} />
          )}
        </Card>

        <SubsystemPanel title="Tasks" system={o.tasks} what="Task counts and execution status for this company." />
        <SubsystemPanel title="Spending & budget" system={o.spending} what="Verified and estimated spend against budgets." />
        <SubsystemPanel title="Approvals" system={o.approvals} what="Pending approvals that need a human decision." />
        <SubsystemPanel title="Artifacts" system={o.artifacts} what="Documents and files produced by agents." />
        <SubsystemPanel title="Scheduled routines" system={o.routines} what="Recurring work agents run on a schedule." />
      </div>
    </Page>
  )
}

function CompanyHeader({ overview, base }: { overview: Overview; base: string }) {
  const { company } = overview
  return (
    <Card className="lg:col-span-2">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {company.name}
            <Badge tone={company.status === 'active' ? 'success' : 'warning'}>
              <Dot tone={company.status === 'active' ? 'success' : 'warning'} />
              {company.status === 'active' ? 'Active' : 'Paused'}
            </Badge>
          </span>
        }
        description={company.mission ?? undefined}
        actions={
          <Link
            to={`${base}/settings`}
            className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12.5px] font-medium text-fg-2 hover:bg-hover hover:text-fg"
          >
            <Pencil className="size-3" aria-hidden /> Edit
          </Link>
        }
      />
      <CardBody className="space-y-4">
        <div>
          <div className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Strategic goal</div>
          {company.strategic_goal ? (
            <p className="mt-1 text-[14px] leading-snug text-fg">{company.strategic_goal}</p>
          ) : (
            <p className="mt-1 text-[13px] text-fg-3">
              No strategic goal set.{' '}
              <Link to={`${base}/settings`} className="text-accent-fg hover:underline">
                Set one in Settings
              </Link>
            </p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-4 border-t border-line pt-4 sm:grid-cols-4">
          <Stat label="Agents" value={overview.agents.total} />
          <Stat label="Departments" value={overview.departments.total} />
          <Stat label="Constraints" value={company.constraints.length} />
          <Stat label="Timezone" value={<span className="text-[14px]">{company.timezone}</span>} />
        </div>
      </CardBody>
    </Card>
  )
}

function founderModel(f: Agent): string {
  if (f.model_provider && f.model_name) return `${f.model_provider} / ${f.model_name}`
  return f.model_name ?? f.model_provider ?? 'No model configured'
}

function FounderCard({ founder, base }: { founder: Agent | null; base: string }) {
  return (
    <Card>
      <CardHeader title="AI Founder & CEO" />
      {!founder ? (
        <EmptyState title="No founder" description="This company has no AI Founder." />
      ) : (
        <CardBody className="space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <Link to={`${base}/organization/agents/${founder.id}`} className="text-[15px] font-semibold text-fg hover:underline">
                {founder.name}
              </Link>
              <p className="text-[12.5px] text-fg-3">{founder.role_title}</p>
            </div>
            <StatusBadge status={founder.status} />
          </div>
          <dl className="grid grid-cols-[5rem_1fr] gap-y-1.5 text-[13px]">
            <dt className="text-fg-3">Model</dt>
            <dd className="min-w-0 break-words">{founderModel(founder)}</dd>
            <dt className="text-fg-3">Runtime</dt>
            <dd>{founder.runtime === 'unassigned' ? <span className="text-fg-3">Not assigned</span> : founder.runtime}</dd>
          </dl>
        </CardBody>
      )}
    </Card>
  )
}

function AgentBreakdown({ overview, base }: { overview: Overview; base: string }) {
  const present = STATUS_ORDER.filter((s) => overview.agents.by_status[s] !== undefined)
  return (
    <Card>
      <CardHeader
        title="Workforce"
        description={`${overview.agents.total} agent${overview.agents.total === 1 ? '' : 's'} by lifecycle status`}
        actions={
          <Link to={`${base}/organization`} className="text-[12.5px] text-accent-fg hover:underline">
            Open
          </Link>
        }
      />
      <CardBody className="p-0">
        <ul className="divide-y divide-line">
          {present.map((s) => (
            <li key={s} className="flex items-center justify-between px-4 py-2 text-[13px]">
              <span className="flex items-center gap-2 text-fg-2">
                <Dot tone={agentStatusTone[s]} />
                {agentStatusLabel[s]}
              </span>
              <span className="font-medium tabular-nums">{overview.agents.by_status[s]}</span>
            </li>
          ))}
          <li className="flex items-center justify-between px-4 py-2 text-[13px]">
            <span className="text-fg-2">Idle / working / failed</span>
            <span className="text-[12px] text-fg-3">Not tracked until M3</span>
          </li>
        </ul>
      </CardBody>
    </Card>
  )
}

function SubsystemPanel({ title, system, what }: { title: string; system: Subsystem; what: string }): ReactNode {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>
        {system.available === false ? (
          <NotAvailable compact milestone={system.milestone} description={what} />
        ) : (
          <p className="text-[13px] text-fg-3">
            The API reports this subsystem as available, but this version of the console does not render it yet.
          </p>
        )}
      </CardBody>
    </Card>
  )
}
