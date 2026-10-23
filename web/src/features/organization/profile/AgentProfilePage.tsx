import { useState } from 'react'
import { ApiError, errorMessage, useAgent, useAgentLifecycle, useAgents, useDepartments } from '@/api'
import type { Agent, AgentLifecycleAction } from '@/api/types'
import { Page } from '@/components/layout/Page'
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  FormError,
  LoadingState,
  StatusBadge,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  Field,
  useToast,
} from '@/components/ui'
import { NotFound } from '@/features/shell/NotFound'
import { useAgentId, useCompanyId } from '@/features/shell/useCompanyId'
import { availableLifecycleActions, pastTense, type LifecycleActionSpec } from '@/lib/lifecycle'
import { AgentActivityTab } from './AgentActivityTab'
import { BudgetTab } from './BudgetTab'
import { ConfigurationTab } from './ConfigurationTab'
import { OverviewTab } from './OverviewTab'
import { PermissionsTab } from './PermissionsTab'
import { TasksArtifactsTab } from './TasksArtifactsTab'

export function AgentProfilePage() {
  const companyId = useCompanyId()
  const agentId = useAgentId()
  const base = `/c/${companyId}`
  const agentQ = useAgent(companyId, agentId)
  const agentsQ = useAgents(companyId)
  const departmentsQ = useDepartments(companyId)

  if (agentQ.isError && agentQ.error instanceof ApiError && agentQ.error.status === 404) {
    return (
      <NotFound
        title="Agent not found"
        description="This agent does not exist in this company."
        backTo={`${base}/organization`}
        backLabel="Back to organization"
      />
    )
  }

  const crumbs = [
    { label: 'Organization', to: `${base}/organization` },
    { label: agentQ.data?.name ?? 'Agent' },
  ]

  if (agentQ.isPending) {
    return (
      <Page title="Agent" breadcrumbs={crumbs}>
        <Card>
          <LoadingState rows={6} label="Loading agent" />
        </Card>
      </Page>
    )
  }
  if (agentQ.isError) {
    return (
      <Page title="Agent" breadcrumbs={crumbs}>
        <Card>
          <ErrorState error={agentQ.error} onRetry={() => agentQ.refetch()} retrying={agentQ.isFetching} />
        </Card>
      </Page>
    )
  }

  const agent = agentQ.data
  const allAgents = agentsQ.data ?? []
  const departments = departmentsQ.data ?? []

  return (
    <Page
      title={
        <span className="flex flex-wrap items-center gap-2.5">
          {agent.name}
          <StatusBadge status={agent.status} />
        </span>
      }
      description={agent.role_title}
      breadcrumbs={crumbs}
      actions={<LifecycleActions agent={agent} companyId={companyId} />}
    >
      {agent.status === 'retired' && (
        <div className="mb-4 rounded-md border border-line bg-surface px-3 py-2 text-[13px] text-fg-2">
          This agent is retired and read-only. Its history is preserved.
        </div>
      )}
      <Tabs defaultValue="overview">
        <TabsList aria-label="Agent sections">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="work">Tasks &amp; Artifacts</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="configuration">Configuration</TabsTrigger>
          <TabsTrigger value="budget">Budget</TabsTrigger>
          <TabsTrigger value="permissions">Permissions</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewTab agent={agent} agents={allAgents} departments={departments} base={base} />
        </TabsContent>
        <TabsContent value="work">
          <TasksArtifactsTab />
        </TabsContent>
        <TabsContent value="activity">
          <AgentActivityTab companyId={companyId} agentId={agent.id} />
        </TabsContent>
        <TabsContent value="configuration">
          <ConfigurationTab key={agent.updated_at} agent={agent} agents={allAgents} departments={departments} />
        </TabsContent>
        <TabsContent value="budget">
          <BudgetTab key={agent.updated_at} agent={agent} />
        </TabsContent>
        <TabsContent value="permissions">
          <PermissionsTab key={agent.updated_at} agent={agent} />
        </TabsContent>
      </Tabs>
    </Page>
  )
}

function LifecycleActions({ agent, companyId }: { agent: Agent; companyId: string }) {
  const lifecycle = useAgentLifecycle(companyId, agent.id)
  const toast = useToast()
  const [pending, setPending] = useState<LifecycleActionSpec | null>(null)
  const [reason, setReason] = useState('')
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [inlineError, setInlineError] = useState<string | null>(null)
  const [running, setRunning] = useState<AgentLifecycleAction | null>(null)

  const actions = availableLifecycleActions(agent.status, agent.kind)

  async function run(spec: LifecycleActionSpec, withReason?: string) {
    setInlineError(null)
    setDialogError(null)
    setRunning(spec.action)
    try {
      await lifecycle.mutateAsync({ action: spec.action, reason: withReason?.trim() || undefined })
      toast.success(`${agent.name} ${pastTense[spec.action]}`)
      setPending(null)
      setReason('')
    } catch (err) {
      const msg =
        err instanceof ApiError && (err.status === 409 || err.status === 422)
          ? err.message
          : errorMessage(err)
      if (spec.confirm) setDialogError(msg)
      else setInlineError(msg)
    } finally {
      setRunning(null)
    }
  }

  if (actions.length === 0) return null

  return (
    <>
      {actions.map((spec) => (
        <Button
          key={spec.action}
          variant={spec.variant}
          loading={running === spec.action && !spec.confirm}
          disabled={running !== null}
          onClick={() => (spec.confirm ? (setPending(spec), setDialogError(null)) : void run(spec))}
        >
          {spec.label}
        </Button>
      ))}
      {inlineError && (
        <div className="basis-full">
          <FormError message={inlineError} />
        </div>
      )}
      {pending && (
        <ConfirmDialog
          open
          onOpenChange={(o) => {
            if (!o) {
              setPending(null)
              setReason('')
              setDialogError(null)
            }
          }}
          title={`${pending.label} ${agent.name}?`}
          confirmLabel={pending.label}
          destructive={pending.destructive}
          loading={running === pending.action}
          error={dialogError}
          onConfirm={() => void run(pending, reason)}
          description={
            <div className="space-y-3">
              <p>{pending.consequence}</p>
              {agent.kind === 'founder' && pending.action === 'retire' && (
                <p className="text-warning">The founder cannot be retired while it is the company&apos;s only founder.</p>
              )}
              <Field label="Reason (optional)" hint="Recorded in the audit log.">
                {({ id }) => <Textarea id={id} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
              </Field>
            </div>
          }
        />
      )}
    </>
  )
}
