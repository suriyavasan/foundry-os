import { RefreshCw } from 'lucide-react'
import { useRuntimes } from '@/api'
import { PanelFrame } from '@/features/canvas/PanelFrame'
import {
  Button,
  Card,
  CardHeader,
  Chip,
  EmptyState,
  ErrorState,
  HealthBadge,
  LoadingState,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from '@/components/ui'
import { formatRelative, formatDateTime } from '@/lib/format'
import { COMPANY_AGENT_RUNTIME } from '@/lib/runtimes'
import { useCanvas } from '@/features/canvas/CanvasContext'
import { ConnectedAccountsSection } from './ConnectedAccountsSection'
import { CredentialsSection } from './CredentialsSection'
import { ProvidersSection } from './ProvidersSection'
import { RuntimeHealthCard } from './RuntimeHealthCard'

export function IntegrationsPanel() {
  const { companyId } = useCanvas()
  const q = useRuntimes()
  const items = q.data?.items ?? []

  return (
    <PanelFrame title="Integrations" subtitle="Runtimes execute work; model providers supply the language models. Health is checked live by the backend.">
      <div className="space-y-4">
        <RuntimeHealthCard
          runtime={items.find((r) => r.id === COMPANY_AGENT_RUNTIME)}
          loading={q.isPending}
          onRefresh={() => void q.refetch()}
          refreshing={q.isFetching && !q.isPending}
        />
        <ProvidersSection companyId={companyId} />
        <ConnectedAccountsSection companyId={companyId} />
        <CredentialsSection companyId={companyId} />
        <Card>
          <CardHeader
            title="Agent runtimes"
            description="Registered runtime adapters"
            actions={
              <Button
                size="sm"
                icon={<RefreshCw className="size-3.5" />}
                loading={q.isFetching && !q.isPending}
                onClick={() => q.refetch()}
              >
                Refresh
              </Button>
            }
          />
          {q.isPending ? (
            <LoadingState rows={3} label="Loading runtimes" />
          ) : q.isError ? (
            <ErrorState error={q.error} onRetry={() => q.refetch()} retrying={q.isFetching} />
          ) : items.length === 0 ? (
            <EmptyState title="No runtimes registered" description="The backend did not report any runtime adapters." />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Runtime</TH>
                  <TH>Health</TH>
                  <TH>Capabilities</TH>
                  <TH>Configuration</TH>
                </TR>
              </THead>
              <TBody>
                {items.map((r) => (
                  <TR key={r.id} className="align-top">
                    <TD className="align-top">
                      <div className="font-medium">{r.display_name}</div>
                      <div className="font-mono text-[11.5px] text-fg-3">
                        {r.id} · {r.kind}
                      </div>
                    </TD>
                    <TD className="align-top">
                      <HealthBadge status={r.health.status} />
                      {r.health.detail && <div className="mt-1 max-w-xs text-[12px] break-words text-fg-3">{r.health.detail}</div>}
                      <div className="mt-1 text-[11.5px] text-fg-3" title={formatDateTime(r.health.checked_at)}>
                        Checked {formatRelative(r.health.checked_at)}
                      </div>
                    </TD>
                    <TD className="align-top">
                      <div className="flex max-w-sm flex-wrap gap-1">
                        {r.capabilities.length === 0 ? (
                          <span className="text-fg-3">None declared</span>
                        ) : (
                          r.capabilities.map((c) => <Chip key={c}>{c}</Chip>)
                        )}
                      </div>
                    </TD>
                    <TD className="max-w-sm align-top text-[12.5px] break-words text-fg-2">
                      {r.config_hint ?? <span className="text-fg-3">—</span>}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Card>

      </div>
    </PanelFrame>
  )
}
