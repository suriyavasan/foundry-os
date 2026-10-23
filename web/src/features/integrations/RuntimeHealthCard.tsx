import { RefreshCw } from 'lucide-react'
import type { RuntimeInfo } from '@/api/types'
import { Button, Card, CardBody, CardHeader, Chip, HealthBadge, NotAvailable } from '@/components/ui'
import { formatDateTime, formatRelative } from '@/lib/format'
import { COMPANY_AGENT_RUNTIME, splitRuntimeDetail } from '@/lib/runtimes'

/** Health of the `company_agent` runtime (the Hermes fork), including what the backend says about the sandbox. */
export function RuntimeHealthCard({ runtime, loading, onRefresh, refreshing }: { runtime: RuntimeInfo | undefined; loading: boolean; onRefresh: () => void; refreshing: boolean }) {
  const parts = splitRuntimeDetail(runtime?.health.detail)
  return (
    <Card aria-label="Company Agent runtime health">
      <CardHeader
        title="Company Agent runtime"
        description="The employee runtime that executes tasks (Hermes fork). Health is checked live."
        actions={
          <Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={refreshing} onClick={onRefresh}>
            Check again
          </Button>
        }
      />
      {loading ? (
        <CardBody>
          <p className="text-[13px] text-fg-3">Checking…</p>
        </CardBody>
      ) : !runtime ? (
        <NotAvailable milestone="M3b" title={`The ${COMPANY_AGENT_RUNTIME} runtime is not registered`} description="This backend does not list it among its runtimes, so agents cannot run real work yet." />
      ) : (
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13.5px] font-medium text-fg">{runtime.display_name}</span>
            <HealthBadge status={runtime.health.status} />
            <span className="font-mono text-[11.5px] text-fg-3">{runtime.id}</span>
            <span className="ml-auto text-[11.5px] text-fg-3" title={formatDateTime(runtime.health.checked_at)}>
              Checked {formatRelative(runtime.health.checked_at)}
            </span>
          </div>
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Sandbox</dt>
              <dd className="mt-1 text-[13px] text-fg-2">
                {parts.sandbox.length ? parts.sandbox.map((l, i) => <p key={i}>{l}</p>) : <span className="text-fg-3">The runtime did not report sandbox status.</span>}
              </dd>
            </div>
            <div>
              <dt className="text-[11.5px] font-medium tracking-wide text-fg-3 uppercase">Health detail</dt>
              <dd className="mt-1 text-[13px] text-fg-2">{parts.rest.length ? parts.rest.map((l, i) => <p key={i}>{l}</p>) : <span className="text-fg-3">No further detail.</span>}</dd>
            </div>
          </dl>
          {runtime.config_hint && <p className="rounded-md border border-line bg-raised px-3 py-2 text-[12.5px] text-fg-2">{runtime.config_hint}</p>}
          {runtime.capabilities.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {runtime.capabilities.map((c) => (
                <Chip key={c}>{c}</Chip>
              ))}
            </div>
          )}
        </CardBody>
      )}
    </Card>
  )
}
