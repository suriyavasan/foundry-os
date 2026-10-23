import { CheckCircle2, Plus, ScanSearch, TriangleAlert, XCircle } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, isNotDeployed, useDetectProviders, useModelProviders, useTestProvider, useUpdateModelProvider } from '@/api'
import type { DetectResult, ModelProvider, ProviderTestResult } from '@/api/types'
import { Badge, Boundary, Button, Card, CardHeader, Chip, TBody, TD, TH, THead, TR, Table, useToast } from '@/components/ui'
import { describePrices, emptyProviderForm, formFromDetected, isFreeProvider, providerKindLabel, type ProviderFormValues } from '@/lib/providers'
import { ProviderDialog } from './ProviderDialog'

export function ProvidersSection({ companyId }: { companyId: string }) {
  const q = useModelProviders(companyId)
  const detect = useDetectProviders(companyId)
  const [dialog, setDialog] = useState<{ initial?: ProviderFormValues } | null>(null)

  return (
    <>
      <Card>
        <CardHeader
          title="Model providers"
          description="The language models your employees run on. Local models are free; cloud providers are billed by the provider."
          actions={
            <>
              <Button size="sm" icon={<ScanSearch className="size-3.5" />} loading={detect.isPending} onClick={() => detect.mutate()}>
                Detect local Ollama
              </Button>
              <Button size="sm" variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setDialog({})}>
                Add provider
              </Button>
            </>
          }
        />
        {(detect.isSuccess || detect.isError) && (
          <DetectResultView
            result={detect.data}
            error={detect.error}
            registered={q.data ?? []}
            onRegister={(initial) => setDialog({ initial })}
            onDismiss={() => detect.reset()}
          />
        )}
        <Boundary
          query={q}
          milestone="M3b"
          compact={false}
          unavailableTitle="Model providers are not built yet"
          unavailableText="Registering providers and models arrives with the Company Agent runtime (M3b)."
          isEmpty={(d) => d.length === 0}
          empty={{ title: 'No model providers', description: 'Detect a local Ollama, or add a cloud provider with an API key. Agents need a provider to run real work.' }}
        >
          {(items) => <ProviderTable companyId={companyId} items={items} />}
        </Boundary>
      </Card>
      {dialog && <ProviderDialog companyId={companyId} initial={dialog.initial ?? emptyProviderForm()} onClose={() => setDialog(null)} />}
    </>
  )
}

function DetectResultView({
  result,
  error,
  registered,
  onRegister,
  onDismiss,
}: {
  result: DetectResult | undefined
  error: unknown
  registered: ModelProvider[]
  onRegister: (v: ProviderFormValues) => void
  onDismiss: () => void
}) {
  const isRegistered = (name: string) => registered.some((p) => p.kind === 'ollama' && p.default_model === name)
  return (
    <div className="border-b border-line bg-raised px-4 py-3" role="status" aria-label="Local Ollama detection">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 text-[13px]">
          {error ? (
            <p className="text-fg-2">
              {isNotDeployed(error) ? 'Detection is not built yet on this backend (M3b).' : <span className="text-danger">Could not check for Ollama: {errorMessage(error)}</span>}
            </p>
          ) : result && !result.reachable ? (
            <p className="text-fg-2">
              No local Ollama answered{result.base_url ? ` at ${result.base_url}` : ''}.{' '}
              {result.message ?? 'Start it with `ollama serve`, then detect again.'}
            </p>
          ) : result && result.models.length === 0 ? (
            <p className="text-fg-2">Ollama is running but has no models. Pull one, e.g. `ollama pull qwen2.5:7b`.</p>
          ) : result ? (
            <>
              <p className="font-medium text-fg">
                Found {result.models.length} local model{result.models.length === 1 ? '' : 's'}
                {result.base_url ? ` at ${result.base_url}` : ''}
              </p>
              <ul className="mt-2 space-y-1.5">
                {result.models.map((m) => (
                  <li key={m.name} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-fg">{m.name}</span>
                    {m.size_bytes != null && <span className="text-[11.5px] text-fg-3">{(m.size_bytes / 1e9).toFixed(1)} GB</span>}
                    {isRegistered(m.name) ? (
                      <Badge tone="success">Registered</Badge>
                    ) : (
                      <Button size="sm" onClick={() => onRegister(formFromDetected(result.base_url, m))}>
                        Register
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  )
}

function ProviderTable({ companyId, items }: { companyId: string; items: ModelProvider[] }) {
  const test = useTestProvider(companyId)
  const update = useUpdateModelProvider(companyId)
  const toast = useToast()
  const [results, setResults] = useState<Record<string, ProviderTestResult | { error: string }>>({})
  const [testing, setTesting] = useState<string | null>(null)

  async function runTest(p: ModelProvider) {
    setTesting(p.id)
    try {
      const r = await test.mutateAsync(p.id)
      setResults((m) => ({ ...m, [p.id]: r }))
    } catch (e) {
      setResults((m) => ({ ...m, [p.id]: { error: errorMessage(e, 'The test could not run') } }))
    } finally {
      setTesting(null)
    }
  }
  async function toggle(p: ModelProvider) {
    try {
      await update.mutateAsync({ id: p.id, body: { enabled: !p.enabled } })
      toast.success(`${p.name} ${p.enabled ? 'disabled' : 'enabled'}`)
    } catch (e) {
      toast.error(errorMessage(e, 'Could not update the provider'))
    }
  }

  return (
    <Table>
      <THead>
        <TR>
          <TH>Provider</TH>
          <TH>Address</TH>
          <TH>Model</TH>
          <TH>Price per 1M tokens</TH>
          <TH>Status</TH>
          <TH className="text-right">Actions</TH>
        </TR>
      </THead>
      <TBody>
        {items.map((p) => {
          const r = results[p.id]
          return (
            <ProviderRow key={p.id} p={p} result={r} testing={testing === p.id} onTest={() => void runTest(p)} onToggle={() => void toggle(p)} toggling={update.isPending && update.variables?.id === p.id} />
          )
        })}
      </TBody>
    </Table>
  )
}

function ProviderRow({ p, result, testing, onTest, onToggle, toggling }: { p: ModelProvider; result: ProviderTestResult | { error: string } | undefined; testing: boolean; onTest: () => void; onToggle: () => void; toggling: boolean }) {
  return (
    <>
      <TR className={result ? 'border-b-0' : undefined}>
        <TD className="align-top">
          <div className="font-medium">{p.name}</div>
          <div className="text-[11.5px] text-fg-3">{providerKindLabel(p.kind)}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {p.is_local && <Badge tone="success">Local · no provider charge</Badge>}
            {isFreeProvider(p) && <Badge tone="success">Free</Badge>}
            {p.credential_id && <Badge tone="neutral">Key stored</Badge>}
          </div>
        </TD>
        <TD className="max-w-56 align-top font-mono text-[12px] break-all text-fg-2">{p.base_url ?? <span className="font-sans text-fg-3">standard address</span>}</TD>
        <TD className="align-top">
          {p.default_model ? <Chip>{p.default_model}</Chip> : <span className="text-fg-3">none</span>}
          {p.context_length != null && <div className="mt-1 text-[11.5px] text-fg-3">{p.context_length.toLocaleString()} ctx</div>}
        </TD>
        <TD className="align-top text-[12.5px] text-fg-2">{p.is_local ? <span className="text-fg-3">not charged</span> : describePrices(p)}</TD>
        <TD className="align-top">{p.enabled ? <Badge tone="success">Enabled</Badge> : <Badge tone="neutral">Disabled</Badge>}</TD>
        <TD className="align-top">
          <div className="flex justify-end gap-1.5">
            <Button size="sm" loading={testing} onClick={onTest}>
              Test
            </Button>
            <Button size="sm" variant="ghost" loading={toggling} onClick={onToggle}>
              {p.enabled ? 'Disable' : 'Enable'}
            </Button>
          </div>
        </TD>
      </TR>
      {result && (
        <TR>
          <TD colSpan={6} className="h-auto pt-0 pb-3">
            <TestResultView result={result} name={p.name} />
          </TD>
        </TR>
      )}
    </>
  )
}

function TestResultView({ result, name }: { result: ProviderTestResult | { error: string }; name: string }) {
  if ('error' in result) {
    return (
      <div role="alert" className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger-subtle px-3 py-2 text-[12.5px] text-danger">
        <XCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden /> Test of {name} failed to run: {result.error}
      </div>
    )
  }
  return (
    <div role="status" className={`space-y-1.5 rounded-md border px-3 py-2 text-[12.5px] ${result.ok ? 'border-success/40 bg-success-subtle' : 'border-danger/40 bg-danger-subtle'}`}>
      <div className="flex items-center gap-2 text-fg">
        {result.ok ? <CheckCircle2 className="size-3.5 text-success" aria-hidden /> : <XCircle className="size-3.5 text-danger" aria-hidden />}
        <span className="font-medium">{result.ok ? `${name} answered` : `${name} did not pass`}</span>
        {result.model && <Chip>{result.model}</Chip>}
        {result.latency_ms != null && <span className="text-fg-3">{result.latency_ms} ms</span>}
      </div>
      {result.message && <p className="text-fg-2">{result.message}</p>}
      {result.warnings.map((w) => (
        <p key={w} className="flex items-start gap-1.5 text-fg">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden /> {w}
        </p>
      ))}
    </div>
  )
}

