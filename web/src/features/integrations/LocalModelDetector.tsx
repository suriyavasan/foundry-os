import { ScanSearch } from 'lucide-react'
import { useState } from 'react'
import { errorMessage, isNotDeployed, useDetectProviders, useModelProviders } from '@/api'
import type { DetectResult, ModelProvider } from '@/api/types'
import { Badge, Button } from '@/components/ui'
import { formFromDetected, type ProviderFormValues } from '@/lib/providers'
import { ProviderDialog } from './ProviderDialog'

export function DetectResultView({
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

/**
 * Detect a local Ollama and register one of its models as a provider: the same probe, result list and register
 * dialog the Integrations panel uses, in a stand-alone block (used by the first-run wizard).
 */
export function LocalModelDetector({ companyId }: { companyId: string }) {
  const providers = useModelProviders(companyId)
  const detect = useDetectProviders(companyId)
  const [dialog, setDialog] = useState<{ initial: ProviderFormValues } | null>(null)
  const ran = detect.isSuccess || detect.isError
  return (
    <div>
      <Button icon={<ScanSearch className="size-3.5" />} loading={detect.isPending} onClick={() => detect.mutate()}>
        {ran ? 'Detect again' : 'Detect local models'}
      </Button>
      {ran && (
        <div className="mt-3 overflow-hidden rounded-md border border-line">
          <DetectResultView result={detect.data} error={detect.error} registered={providers.data ?? []} onRegister={(initial) => setDialog({ initial })} onDismiss={() => detect.reset()} />
        </div>
      )}
      {dialog && <ProviderDialog companyId={companyId} initial={dialog.initial} onClose={() => setDialog(null)} />}
    </div>
  )
}
