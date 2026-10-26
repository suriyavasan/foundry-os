import { CheckCircle2, Lock, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  errorMessage,
  isNotDeployed,
  useAiSetup,
  useCreateCredential,
  useCreateModelProvider,
  useDetectProviders,
  useModelProviders,
  useProviderModels,
  useProviderTest,
  useUpdateAiSetup,
  useUpdateModelProvider,
} from '@/api'
import type { ModelProvider, ProviderTest, WorkMode, WorkRuntime } from '@/api/types'
import { Badge, Button, Field, FormError, Input } from '@/components/ui'
import { GlowCard } from '@/features/onboarding/fx/GlowCard'
import { MultiStepLoader } from '@/features/onboarding/fx/MultiStepLoader'
import {
  COMPANY_AGENT_WORK_RUNTIME,
  OLLAMA_CARD,
  PROVIDER_CARDS,
  blockingReasonText,
  canTestProvider,
  cardFor,
  firstFailure,
  loaderSteps,
  providerFormFor,
  putFailure,
  testPassed,
  type ModelOption,
  type ProviderCardDef,
  type ProviderCardId,
} from '@/lib/aiSetup'
import { redactSecret, toCreateCredentialBody, toCreateProviderBody, validateProviderForm } from '@/lib/providers'
import { ModelPicker } from './ModelPicker'

const KIND_TO_CARD: Record<string, ProviderCardId> = { openrouter: 'openrouter', anthropic: 'anthropic', openai: 'openai', openai_compatible: 'openai_compatible', ollama: 'ollama' }

/**
 * Connect your AI (spec §6 step 4): choose a provider, give it a key (write-only), test it (real checks, shown by the
 * multi-step loader), pick the model, and save it as the management model. The platform cannot be used without this, so the
 * parent's Continue stays disabled until the server reports `ready`.
 *
 * With `deferSave` nothing is saved here: a passed test is reported through `onTested` and the parent saves the whole AI setup
 * (this model plus how work gets done) with its own button. Without it, a passed test is saved as the management model.
 *
 * The key follows the same path as everywhere else: it goes to the credential endpoint once, the field is emptied the moment
 * the server has it, the provider only references the credential's id, and no error text is allowed to echo it.
 */
export function ConnectAiSection({
  companyId,
  canEdit = true,
  deferSave = false,
  onTested,
}: {
  companyId: string
  canEdit?: boolean
  /** Do not save on a passed test; report the tested selection through `onTested` instead. */
  deferSave?: boolean
  /** The provider and model that just passed their test, or null when there is none (failed, changed or not run yet). */
  onTested?: (sel: { providerId: string; model: string } | null) => void
}) {
  const ai = useAiSetup(companyId)
  const providers = useModelProviders(companyId)
  const detect = useDetectProviders(companyId)
  const createCredential = useCreateCredential(companyId)
  const createProvider = useCreateModelProvider(companyId)
  const updateProvider = useUpdateModelProvider(companyId)
  const providerTest = useProviderTest(companyId)
  const saveAi = useUpdateAiSetup(companyId)

  const [changing, setChanging] = useState(false)
  const [cardId, setCardId] = useState<ProviderCardId | null>(null)
  const [existing, setExisting] = useState<ModelProvider | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [secret, setSecret] = useState('')
  const [contextLength, setContextLength] = useState('')
  // What this session already created, so a retry never stores the key or the provider twice.
  const [created, setCreated] = useState<{ providerId: string; credentialId: string | null; baseUrl: string; model: string } | null>(null)
  const [test, setTest] = useState<ProviderTest | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ model?: string; baseUrl?: string; secret?: string }>({})

  const status = ai.data
  const unavailable = ai.isError && isNotDeployed(ai.error)
  const hasManagement = !!status?.management.provider_id
  const picking = !hasManagement || changing
  const providerId = existing?.id ?? created?.providerId ?? null
  const models = useProviderModels(companyId, picking ? providerId : null)

  // Local Ollama: probe once when the picker first shows (a harmless request to this machine's own address).
  const probed = useRef(false)
  const { mutate: runDetect } = detect
  useEffect(() => {
    if (!picking || !canEdit || unavailable || ai.isPending || probed.current) return
    probed.current = true
    runDetect()
  }, [picking, canEdit, unavailable, ai.isPending, runDetect])

  const detected = detect.data && detect.data.reachable && detect.data.models.length > 0 ? detect.data : null
  const cards: ProviderCardDef[] = detected ? [...PROVIDER_CARDS, OLLAMA_CARD] : PROVIDER_CARDS
  const card = cardId ? cardFor(cardId) : null

  const modelOptions: ModelOption[] = useMemo(() => {
    if (models.data && models.data.length > 0) return models.data
    if (cardId === 'ollama' && detected) return detected.models.map((m) => ({ id: m.name, context_length: m.context_length }))
    return []
  }, [models.data, cardId, detected])

  const tested = (sel: { providerId: string; model: string } | null) => {
    if (deferSave) onTested?.(sel)
  }

  // After the parent saved (or anything else changed the connection), show the connection that is now in force.
  const mgmtKey = `${status?.management.provider_id ?? ''}|${status?.management.model ?? ''}`
  const [seenMgmtKey, setSeenMgmtKey] = useState(mgmtKey)
  if (seenMgmtKey !== mgmtKey) {
    setSeenMgmtKey(mgmtKey)
    setChanging(false)
    if (deferSave) {
      setTest(null)
      setSaved(false)
    }
  }

  function resetFlow() {
    tested(null)
    setTest(null)
    setSaved(false)
    setError(null)
    setFieldErrors({})
  }

  function chooseCard(id: ProviderCardId) {
    setCardId(id)
    setExisting(null)
    resetFlow()
    if (id === 'ollama') setBaseUrl(detected?.base_url ?? OLLAMA_CARD.baseUrlHint)
    else if (!cardFor(id).needsBaseUrl) setBaseUrl('')
    if (created && (created.baseUrl !== baseUrl || id !== cardId)) setCreated(null)
  }

  function chooseExisting(p: ModelProvider) {
    setExisting(p)
    setCardId(KIND_TO_CARD[p.kind] ?? 'openai_compatible')
    setBaseUrl(p.base_url ?? '')
    setModel(p.default_model ?? '')
    setSecret('')
    resetFlow()
  }

  /** Create what is missing, test, and on a pass save it as the management model. */
  async function run(target?: { providerId: string; model: string }) {
    tested(null)
    setError(null)
    setSaved(false)
    setTest(null)
    const typedSecret = secret
    try {
      let pid = target?.providerId ?? existing?.id ?? null
      const modelName = (target?.model ?? model).trim()
      if (!pid) {
        if (!card) return
        const form = providerFormFor(card, { baseUrl, model: modelName, secret, contextLength })
        const errs = validateProviderForm(form, { hasCredential: !!created?.credentialId })
        const mapped = { model: errs.default_model, baseUrl: errs.base_url, secret: errs.secret }
        setFieldErrors(mapped)
        if (mapped.model || mapped.baseUrl || mapped.secret) return
        setBusy(true)
        const reuse = created && created.baseUrl === baseUrl.trim() && !secret.trim()
        if (reuse) {
          pid = created.providerId
          if (created.model !== modelName) await updateProvider.mutateAsync({ id: pid, body: { default_model: modelName } })
          setCreated({ ...created, model: modelName })
        } else {
          // A changed address or a newly typed key makes a new provider; the one this session made before is switched off.
          if (created) await updateProvider.mutateAsync({ id: created.providerId, body: { enabled: false } }).catch(() => undefined)
          let credentialId: string | null = null
          const credBody = toCreateCredentialBody(form)
          if (credBody) {
            credentialId = (await createCredential.mutateAsync(credBody)).id
            setSecret('') // the server has it; the browser does not need to keep it
          }
          const provider = await createProvider.mutateAsync(toCreateProviderBody(form, credentialId))
          pid = provider.id
          setCreated({ providerId: pid, credentialId, baseUrl: baseUrl.trim(), model: modelName })
        }
      } else {
        if (!modelName) {
          setFieldErrors({ model: 'Choose or type the model to use' })
          return
        }
        setBusy(true)
        const known = existing ?? providers.data?.find((p) => p.id === pid)
        if (known && known.default_model !== modelName) await updateProvider.mutateAsync({ id: pid, body: { default_model: modelName } })
      }

      const result = await providerTest.mutateAsync(pid)
      setTest(result)
      if (testPassed(result) && deferSave) {
        tested({ providerId: pid, model: modelName })
        setSaved(true)
      } else if (testPassed(result)) {
        await saveAi.mutateAsync({
          management_provider_id: pid,
          management_model: modelName,
          work_mode: (status?.work_mode ?? 'api') as WorkMode,
          work_runtime: (status?.work_mode === 'hybrid' ? status.work_runtime : COMPANY_AGENT_WORK_RUNTIME) as WorkRuntime | null,
        })
        setSaved(true)
        setChanging(false)
      }
    } catch (err) {
      setError(redactSecret(putFailure(err), typedSecret))
    } finally {
      setBusy(false)
    }
  }

  if (ai.isPending) return <p role="status" className="text-[13px] text-fg-3">Checking your AI setup…</p>
  if (unavailable) {
    return (
      <div role="status" className="rounded-md border border-line bg-raised p-4 text-[13px] text-fg-2">
        <p className="font-medium text-fg">AI setup is not available on this server yet</p>
        <p className="mt-1">This backend predates the AI setup (M10). Connect a model provider under Integrations once you are in.</p>
      </div>
    )
  }
  if (ai.isError) return <FormError message={`Could not read your AI setup: ${errorMessage(ai.error)}`} />

  /* ------------------------------ connected: the summary ------------------------------ */
  if (!picking && status) {
    const m = status.management
    const steps = loaderSteps(busy ? null : m.last_test, busy)
    return (
      <section aria-label="Connected AI" className="space-y-3">
        <div className="rounded-lg border border-line-strong bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[14px] font-semibold text-fg">{m.provider_name ?? 'Provider'}</p>
              <p className="font-mono text-[12.5px] text-fg-2">{m.model ?? 'No model chosen'}</p>
            </div>
            <Badge tone={status.ready ? 'success' : 'warning'}>{status.ready ? 'Ready' : 'Not ready'}</Badge>
          </div>
          {!status.ready && <p className="mt-2 text-[12.5px] text-warning">{blockingReasonText(status.blocking_reason)}</p>}
          {(m.last_test || busy) && (
            <div className="mt-3">
              <MultiStepLoader steps={steps} label="Last test" />
            </div>
          )}
          {saved && !deferSave && <p role="status" className="mt-3 text-[12.5px] text-success">Tested and saved as your management model.</p>}
        </div>
        <FormError message={error} />
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <Button
              icon={<RefreshCw className="size-3.5" />}
              loading={busy}
              disabled={!m.provider_id || !m.model}
              onClick={() => m.provider_id && m.model && void run({ providerId: m.provider_id, model: m.model })}
            >
              Test again
            </Button>
            <Button variant="ghost" onClick={() => setChanging(true)} disabled={busy}>
              Change provider or model
            </Button>
          </div>
        )}
      </section>
    )
  }

  /* --------------------------------- the picker --------------------------------- */
  const usable = (providers.data ?? []).filter((p) => p.enabled && p.id !== created?.providerId)
  const canTest = canEdit && !busy && (existing ? model.trim().length > 0 : !!card && canTestProvider(card, { baseUrl, model, secret, hasCredential: !!created?.credentialId }))
  const failure = firstFailure(test)

  return (
    <section aria-label="Connect your AI" className="space-y-5">
      {status && !status.ready && <p className="text-[13px] text-fg-2">{blockingReasonText(status.blocking_reason)}</p>}

      {usable.length > 0 && (
        <div>
          <p className="mb-2 text-[12px] font-medium tracking-wide text-fg-3 uppercase">Already connected</p>
          <ul className="space-y-1.5">
            {usable.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  aria-pressed={existing?.id === p.id}
                  onClick={() => chooseExisting(p)}
                  className={'flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left text-[13px] transition-colors ' + (existing?.id === p.id ? 'border-accent bg-accent-subtle' : 'border-line-strong bg-surface hover:border-fg-3')}
                >
                  <span className="min-w-0 truncate font-medium text-fg">{p.name}</span>
                  <span className="shrink-0 font-mono text-[12px] text-fg-3">{p.default_model ?? 'no model'}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="mb-2 text-[12px] font-medium tracking-wide text-fg-3 uppercase">{usable.length > 0 ? 'Or connect a new one' : 'Choose a provider'}</p>
        <div role="radiogroup" aria-label="Provider" className="grid gap-3 sm:grid-cols-2">
          {cards.map((c) => (
            <GlowCard key={c.id} selected={!existing && cardId === c.id} onClick={() => chooseCard(c.id)} disabled={!canEdit || busy}>
              <span className="block text-[14px] font-semibold text-fg">{c.label}</span>
              <span className="mt-1 block text-[12.5px] leading-snug text-fg-3">
                {c.id === 'ollama' && detected ? `Found ${detected.models.length} model${detected.models.length === 1 ? '' : 's'} on this computer. ${c.blurb}` : c.blurb}
              </span>
            </GlowCard>
          ))}
        </div>
        {!detected && detect.isSuccess && (
          <p className="mt-2 flex items-center gap-2 text-[12px] text-fg-3">
            No local Ollama found on this computer.
            <button type="button" className="text-accent-fg hover:underline" onClick={() => runDetect()}>
              Look again
            </button>
          </p>
        )}
      </div>

      {card && (
        <form
          className="space-y-4 rounded-lg border border-line bg-surface p-4"
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault()
            if (canTest) void run()
          }}
        >
          {!existing && card.needsBaseUrl && (
            <Field label="Base URL" required error={fieldErrors.baseUrl} hint={card.id === 'ollama' ? 'Runs on this computer: free and private.' : 'The API address, usually ending in /v1.'}>
              {({ id, invalid, describedBy }) => (
                <Input id={id} className="h-10" inputMode="url" spellCheck={false} value={baseUrl} invalid={invalid} aria-describedby={describedBy} placeholder={card.baseUrlHint} onChange={(e) => setBaseUrl(e.target.value)} />
              )}
            </Field>
          )}
          {!existing && (card.needsKey || card.kind === 'openai_compatible') && (
            <Field label={card.keyLabel} required={card.needsKey} error={fieldErrors.secret}>
              {({ id, invalid, describedBy }) => (
                <div>
                  <Input
                    id={id}
                    className="h-10"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    data-lpignore="true"
                    value={secret}
                    invalid={invalid}
                    aria-describedby={describedBy}
                    placeholder={created?.credentialId ? 'Saved. Type a new key only to replace it' : 'Paste the key'}
                    onChange={(e) => setSecret(e.target.value)}
                  />
                  <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-fg-3">
                    <Lock className="size-3" aria-hidden /> Write-only. Stored encrypted on the server; never shown again, logged or given to agents.
                  </p>
                </div>
              )}
            </Field>
          )}
          <Field label="Model" required error={fieldErrors.model} hint={modelOptions.length > 0 ? 'Pick from what the provider offers, or type another name.' : 'The model your AI Co-founder will use to manage the company.'}>
            {({ id, invalid, describedBy }) => (
              <ModelPicker
                id={id}
                value={model}
                onChange={(v) => {
                  setModel(v)
                  if (test) resetFlow()
                }}
                options={modelOptions}
                loading={models.isFetching && !!providerId}
                suggestions={card.modelSuggestions}
                invalid={invalid}
                describedBy={describedBy}
                disabled={!canEdit || busy}
              />
            )}
          </Field>
          {card.id === 'openai_compatible' && !existing && (
            <Field label="Context length (tokens)" hint="Optional. What the server is set up to serve; the test measures it either way.">
              {({ id }) => <Input id={id} className="h-10" inputMode="numeric" value={contextLength} placeholder="e.g. 32768" onChange={(e) => setContextLength(e.target.value)} />}
            </Field>
          )}

          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" className="h-9" loading={busy} disabled={!canTest}>
              {test ? 'Test again' : 'Test connection'}
            </Button>
            {!canEdit && <p className="text-[12px] text-fg-3">Only an owner or admin can change the AI setup.</p>}
          </div>
        </form>
      )}

      {(busy || test) && (
        <div className="rounded-lg border border-line bg-surface p-4">
          <MultiStepLoader steps={loaderSteps(busy ? null : test, busy)} />
          {test && !testPassed(test) && (
            <p role="alert" className="mt-3 text-[12.5px] text-danger">
              {failure ? `${failure.name.replace(/_/g, ' ')} failed${failure.detail ? `: ${failure.detail}` : ''}. ` : ''}Fix it and test again. Nothing is saved until every check passes.
            </p>
          )}
          {test && test.served_context != null && (
            <p className="mt-2 text-[12px] text-fg-3">Served context window: {test.served_context.toLocaleString()} tokens.</p>
          )}
          {saved && (
            <p role="status" className="mt-3 flex items-center gap-1.5 text-[13px] font-medium text-success">
              <CheckCircle2 className="size-4" aria-hidden />
              {deferSave ? 'Test passed. It is saved with the button below.' : 'Connected. This is now your management model.'}
            </p>
          )}
        </div>
      )}
      <FormError message={error} />
    </section>
  )
}
